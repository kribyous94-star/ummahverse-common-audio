"""Alignement temporel : décalage global, carte de décalages locaux, dérive, coupes.

Conventions de signe, valables partout dans le code et dans `common.json` :

* `delay` est exprimé en trames de la fréquence de travail ;
* `delay > 0` signifie que **B est en retard sur A** : la trame `m` de A correspond
  à la trame `m + delay` de B.

La confiance rapportée est la **médiane des corrélations normalisées** de plusieurs
sous-fenêtres de la zone recouverte, dans `[0, 1]`, mesurée sur la piste d'analyse
décimée à la position estimée. Ce n'est pas une probabilité, et une valeur élevée ne
prouve pas que le contenu commun soit de la musique : des dialogues identiques ou une
voix originale en fond donnent aussi une corrélation élevée.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import resample

from .errors import AlignmentError

#: Nombre d'échantillons de corrélation retenus autour du pic pour l'interpolation.
_REFINE_HALF_WIDTH = 8
#: Facteur de suréchantillonnage de la corrélation locale (précision ~1/32 trame).
_REFINE_UPSAMPLE = 32
#: Rapport pic/efficace de la corrélation GCC-PHAT au-delà duquel un décalage est crédible.
#: Calibré sur du bruit indépendant, qui plafonne vers 6,5 ; un alignement réel dépasse 20,
#: même en présence d'une dérive d'horloge qui effondre la corrélation à décalage fixe.
MIN_PEAK_RATIO = 10.0
#: Corrélation normalisée minimale pour juger un décalage crédible.
MIN_CONFIDENCE = 0.05
#: Plancher de corrélation locale sous lequel un bloc est tenu pour sans contenu commun.
#: Deux versions simplement égalisées ou de polarité inversée descendent vers 0,05 sans
#: cesser d'être exploitables ; un plan réellement différent tombe sous 0,01.
MIN_LOCAL_CORRELATION = 0.02
#: Au-delà, la corrélation de forme d'onde est franche : elle prime sur l'enveloppe.
DECISIVE_CONFIDENCE = 0.30
DECISIVE_PEAK_RATIO = 60.0


@dataclass(frozen=True)
class DelayEstimate:
    """Retard estimé de `y` par rapport à `x`, en trames."""

    delay: float
    integer_delay: int
    confidence: float
    peak_ratio: float
    method: str = "waveform"
    """« waveform » (GCC-PHAT) ou « envelope » (corrélation d'enveloppes d'énergie)."""

    @property
    def fractional(self) -> float:
        return self.delay - self.integer_delay


#: Nombre de sous-fenêtres sur lesquelles la corrélation normalisée est médianisée.
_CONFIDENCE_WINDOWS = 8


def _normalized_correlation(x: np.ndarray, y: np.ndarray, integer_delay: int) -> float:
    """Corrélation normalisée entre `x` et `y` décalé, médiane sur des sous-fenêtres.

    Une corrélation calculée d'un seul bloc sur tout le signal s'effondre dès qu'il y a
    une dérive d'horloge ou un désaccord local, même quand l'alignement est juste : la
    médiane de plusieurs sous-fenêtres reste représentative de l'accord typique.
    """
    if integer_delay >= 0:
        xs, ys = x[: x.size - integer_delay], y[integer_delay:]
    else:
        xs, ys = x[-integer_delay:], y[: y.size + integer_delay]
    n = min(xs.size, ys.size)
    if n < 16:
        return 0.0
    xs, ys = xs[:n], ys[:n]
    windows = max(min(_CONFIDENCE_WINDOWS, n // 256), 1)
    scores: list[float] = []
    for piece_x, piece_y in zip(np.array_split(xs, windows), np.array_split(ys, windows)):
        centered_x = piece_x - piece_x.mean()
        centered_y = piece_y - piece_y.mean()
        denominator = float(np.linalg.norm(centered_x) * np.linalg.norm(centered_y))
        if denominator > 1e-20:
            scores.append(float(np.dot(centered_x, centered_y) / denominator))
    return float(np.median(scores)) if scores else 0.0


def gcc_phat(x: np.ndarray, y: np.ndarray, max_shift: int | None = None,
             center: int = 0, refine: bool = True) -> DelayEstimate:
    """Corrélation croisée à phase blanchie (GCC-PHAT) entre `x` et `y`.

    Le blanchiment rend le pic net même quand le spectre est dominé par la musique
    basse fréquence. La recherche est limitée à `center ± max_shift`.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if x.size < 32 or y.size < 32:
        return DelayEstimate(float(center), int(center), 0.0, 0.0)

    n = 1 << int(np.ceil(np.log2(x.size + y.size)))
    spectrum = np.fft.rfft(x, n) * np.conj(np.fft.rfft(y, n))
    magnitude = np.abs(spectrum)
    whitened = spectrum / np.maximum(magnitude, 1e-12 * magnitude.max() + 1e-30)
    correlation = np.fft.irfft(whitened, n)
    correlation = np.concatenate((correlation[n // 2:], correlation[: n // 2]))
    lags = np.arange(-(n // 2), n // 2)  # r[k] = sum_m x[m+k] y[m]

    limit = max_shift if max_shift is not None else n // 2 - 1
    # y[m] = x[m - delay] donne un pic en k = -delay : la fenêtre est centrée sur -center.
    low = np.searchsorted(lags, -center - limit)
    high = np.searchsorted(lags, -center + limit, side="right")
    if high - low < 3:
        low, high = 0, lags.size
    window = correlation[low:high]
    peak = int(np.argmax(window))
    peak_index = low + peak
    integer_delay = int(-lags[peak_index])

    delay = float(integer_delay)
    if refine:
        lo = max(peak_index - _REFINE_HALF_WIDTH, 0)
        hi = min(peak_index + _REFINE_HALF_WIDTH + 1, correlation.size)
        segment = correlation[lo:hi]
        if segment.size >= 5:
            fine = resample(segment, segment.size * _REFINE_UPSAMPLE)
            step = 1.0 / _REFINE_UPSAMPLE
            fine_peak = float(np.argmax(fine)) * step
            refined_lag = float(lags[lo]) + fine_peak
            if abs(-refined_lag - integer_delay) <= 1.5:
                delay = float(-refined_lag)

    rms = float(np.sqrt(np.mean(correlation ** 2))) or 1e-30
    peak_ratio = float(abs(correlation[peak_index]) / rms)
    confidence = max(_normalized_correlation(x, y, integer_delay), 0.0)
    return DelayEstimate(delay, integer_delay, confidence, peak_ratio)


#: Pas de l'enveloppe d'énergie utilisée en repli d'alignement, en secondes.
ENVELOPE_HOP_S = 0.01
#: Corrélation d'enveloppes en deçà de laquelle même le repli est jugé inexploitable.
MIN_ENVELOPE_CORRELATION = 0.25


def energy_envelope(track: np.ndarray, rate: int,
                    hop_s: float = ENVELOPE_HOP_S) -> tuple[np.ndarray, float]:
    """Enveloppe d'énergie en décibels, et sa fréquence de trame.

    L'enveloppe décrit *quand* ça joue fort, pas *quoi* : elle survit à un remixage,
    à une égalisation et à un changement de master. C'est ce qui permet d'aligner deux
    versions dont les formes d'onde ne se ressemblent plus.
    """
    hop = max(int(round(hop_s * rate)), 1)
    usable = (track.size // hop) * hop
    if usable < hop * 8:
        return np.zeros(0, dtype=np.float64), rate / hop
    frames = np.asarray(track[:usable], dtype=np.float64).reshape(-1, hop)
    power = np.mean(frames * frames, axis=1)
    floor = 1e-10 * float(np.mean(power)) + 1e-20
    return np.log(power + floor), rate / hop


def envelope_delay(track_a: np.ndarray, track_b: np.ndarray, rate: int,
                   max_delay_s: float = 30.0) -> DelayEstimate:
    """Décalage estimé par corrélation des enveloppes d'énergie.

    Repli utilisé quand la corrélation de forme d'onde ne donne rien de cohérent : il
    permet de distinguer deux situations que rien ne séparait jusqu'ici — « impossible
    d'aligner » et « aligné correctement, mais les deux versions ne partagent aucun
    contenu ». La résolution est celle du pas d'enveloppe (10 ms par défaut), donc
    suffisante pour amorcer l'affinage local, pas pour annuler quoi que ce soit.
    """
    envelope_a, frame_rate = energy_envelope(track_a, rate)
    envelope_b, _ = energy_envelope(track_b, rate)
    if envelope_a.size < 16 or envelope_b.size < 16:
        return DelayEstimate(0.0, 0, 0.0, 0.0, method="envelope")

    x = envelope_a - envelope_a.mean()
    y = envelope_b - envelope_b.mean()
    size = 1 << int(np.ceil(np.log2(x.size + y.size)))
    correlation = np.fft.irfft(np.fft.rfft(x, size) * np.conj(np.fft.rfft(y, size)), size)
    correlation = np.concatenate((correlation[size // 2:], correlation[: size // 2]))
    lags = np.arange(-(size // 2), size // 2)

    limit = max(int(round(max_delay_s * frame_rate)), 1)
    window = (lags >= -limit) & (lags <= limit)
    correlation, lags = correlation[window], lags[window]
    norm = float(np.linalg.norm(x) * np.linalg.norm(y))
    if norm <= 0 or correlation.size == 0:
        return DelayEstimate(0.0, 0, 0.0, 0.0, method="envelope")
    correlation = correlation / norm

    peak = int(np.argmax(correlation))
    frames_delay = float(-lags[peak])
    delay = frames_delay * rate / frame_rate
    rms = float(np.sqrt(np.mean(correlation ** 2))) or 1e-30
    return DelayEstimate(delay, int(round(delay)),
                         confidence=float(max(correlation[peak], 0.0)),
                         peak_ratio=float(abs(correlation[peak]) / rms),
                         method="envelope")


def estimate_global_delay(track_a: np.ndarray, track_b: np.ndarray, rate: int,
                          max_delay_s: float = 30.0,
                          probe_count: int = 9,
                          probe_seconds: float = 20.0) -> DelayEstimate:
    """Décalage global, en confrontant deux mesures indépendantes.

    La corrélation de forme d'onde (GCC-PHAT) est précise mais fragile : sur deux
    versions remixées elle ne trouve que des pics fortuits, d'autant plus facilement
    que la fenêtre de recherche est large devant la durée analysée. La corrélation
    d'enveloppes d'énergie est grossière (10 ms) mais robuste : elle survit à un
    remixage, parce qu'elle décrit *quand* ça joue et non *quoi*.

    Les deux sont donc calculées, et l'enveloppe sert d'**arbitre** : un décalage de
    forme d'onde qui la contredit est un faux pic. Aligner quand même, plutôt que
    refuser, est important — cela permet de distinguer « impossible d'aligner » de
    « bien aligné, mais aucun contenu commun », deux diagnostics très différents pour
    l'utilisateur.
    """
    shortest = min(track_a.size, track_b.size)
    silent = [name for name, track in (("A", track_a), ("B", track_b))
              if float(np.sqrt(np.mean(np.square(track, dtype=np.float64)))) < 1e-9]
    if silent:
        raise AlignmentError(
            "Entrée " + " et ".join(silent) + " entièrement silencieuse : aucun "
            "alignement n'est possible.",
            "Vérifiez le flux audio sélectionné (--stream-a / --stream-b) et le fichier "
            "source ; un flux de commentaire ou une piste muette est un cas fréquent.",
        )
    # Sur un extrait court, chercher ±30 s n'a pas de sens : la fenêtre de recherche
    # est bornée à une fraction du signal, sans quoi le pic est estimé sur un
    # recouvrement minuscule et la confiance n'a plus de valeur.
    max_shift = max(min(int(round(max_delay_s * rate)), shortest // 4), 1)

    envelope = envelope_delay(track_a, track_b, rate, max_delay_s=max_delay_s)
    waveform = _waveform_delay(track_a, track_b, rate, max_shift,
                               probe_count, probe_seconds)

    tolerance = max(int(round(0.25 * rate)), 1)
    envelope_usable = envelope.confidence >= MIN_ENVELOPE_CORRELATION
    if waveform is not None:
        # Une corrélation de forme d'onde franche se suffit à elle-même : elle est plus
        # précise que l'enveloppe, et l'enveloppe peut se tromper sur un signal
        # périodique (une musique en boucle, un motif de parole régulier).
        decisive = (waveform.confidence >= DECISIVE_CONFIDENCE
                    or waveform.peak_ratio >= DECISIVE_PEAK_RATIO)
        agrees = (not envelope_usable
                  or abs(waveform.integer_delay - envelope.integer_delay) <= tolerance)
        if decisive or agrees:
            return waveform

    if not envelope_usable:
        detail = (f"forme d'onde {'sans pic net' if waveform is None else 'incohérente'}, "
                  f"enveloppe {envelope.confidence:.3f}")
        raise AlignmentError(
            f"Aucun alignement exploitable entre les deux entrées ({detail}).",
            "Les deux pistes proviennent-elles du même montage ? Un décalage supérieur "
            "à --max-offset secondes est aussi possible.",
        )

    # L'enveloppe tranche. La forme d'onde peut encore l'affiner, mais seulement dans
    # une fenêtre étroite autour d'elle, où un pic ne peut plus être fortuit.
    window = max(int(round(0.05 * rate)), 8)
    refined = gcc_phat(track_a, track_b, max_shift=window, center=envelope.integer_delay)
    if refined.peak_ratio >= MIN_PEAK_RATIO:
        return DelayEstimate(refined.delay, refined.integer_delay,
                             max(refined.confidence, envelope.confidence),
                             refined.peak_ratio, method="envelope+waveform")
    return envelope


def _waveform_delay(track_a: np.ndarray, track_b: np.ndarray, rate: int,
                    max_shift: int, probe_count: int,
                    probe_seconds: float) -> DelayEstimate | None:
    """Décalage par GCC-PHAT, ou `None` si les sondes ne s'accordent sur rien."""
    shortest = min(track_a.size, track_b.size)
    if shortest < 4 * max_shift or shortest < int(2 * probe_seconds * rate):
        estimate = gcc_phat(track_a, track_b, max_shift=max_shift)
        if estimate.peak_ratio < MIN_PEAK_RATIO and estimate.confidence < MIN_CONFIDENCE:
            return None
        return estimate

    probe_len = int(probe_seconds * rate)
    usable = shortest - probe_len - max_shift
    starts = np.linspace(max_shift, max(max_shift + 1, usable), probe_count).astype(int)
    results: list[DelayEstimate] = []
    for start in starts:
        segment_a = track_a[start:start + probe_len]
        lo = max(start - max_shift, 0)
        hi = min(start + probe_len + max_shift, track_b.size)
        segment_b = track_b[lo:hi]
        if segment_a.size < probe_len // 2 or segment_b.size < probe_len // 2:
            continue
        local = gcc_phat(segment_a, segment_b, max_shift=max_shift, refine=False)
        # Le décalage mesuré est relatif au début de segment_b.
        results.append(DelayEstimate(local.delay + (lo - start),
                                     local.integer_delay + (lo - start),
                                     local.confidence, local.peak_ratio))

    credible = [r for r in results
                if r.confidence >= MIN_CONFIDENCE or r.peak_ratio >= MIN_PEAK_RATIO]
    if not credible:
        return None
    delays = np.array([r.integer_delay for r in credible], dtype=np.float64)
    median = float(np.median(delays))
    coherent = [r for r in credible if abs(r.integer_delay - median) <= rate * 0.5]
    # Des sondes qui ne s'accordent pas signalent des pics fortuits, pas un alignement.
    if len(coherent) < max(2, len(credible) // 2):
        return None
    spread = float(np.std(delays)) / rate
    best = max(coherent, key=lambda r: r.confidence)
    return DelayEstimate(best.delay, int(round(median)), best.confidence,
                         peak_ratio=best.peak_ratio if spread < 0.5 else 0.0)


@dataclass
class OffsetMap:
    """Décalages locaux par bloc, dans la base temporelle de A."""

    block_frames: int
    starts: np.ndarray            # début de chaque bloc, en trames de A
    delays: np.ndarray            # retard de B, en trames (flottant, sous-échantillon)
    confidences: np.ndarray       # corrélation normalisée par bloc
    comparable: np.ndarray        # bool : bloc jugé comparable
    global_delay: float
    drift_ppm: float
    threshold: float
    notes: list[str] = field(default_factory=list)

    @property
    def comparable_ratio(self) -> float:
        return float(self.comparable.mean()) if self.comparable.size else 0.0

    def segments(self, sample_rate: int, comparable: bool) -> list[dict]:
        """Regroupe les blocs consécutifs de même statut en segments lisibles."""
        out: list[dict] = []
        mask = self.comparable if comparable else ~self.comparable
        index = 0
        while index < mask.size:
            if not mask[index]:
                index += 1
                continue
            start = index
            while index + 1 < mask.size and mask[index + 1]:
                index += 1
            first, last = start, index
            out.append({
                "start_s": round(float(self.starts[first]) / sample_rate, 3),
                "end_s": round(float(self.starts[last] + self.block_frames) / sample_rate, 3),
                "mean_delay_s": round(float(np.mean(self.delays[first:last + 1])) / sample_rate, 6),
                "mean_confidence": round(float(np.mean(self.confidences[first:last + 1])), 4),
            })
            index += 1
        return out


def build_offset_map(audio_a, audio_b, block_frames: int, global_delay: DelayEstimate,
                     confidence_threshold: float = 0.12,
                     local_search_s: float = 0.35,
                     tracks: tuple[np.ndarray, np.ndarray, int] | None = None,
                     progress=None) -> OffsetMap:
    """Estime un décalage et une confiance par bloc, au niveau échantillon puis sous-échantillon.

    Les blocs dont la corrélation reste sous le seuil sont marqués **non comparables** :
    ce sont typiquement des coupes, des plans absents d'une version, ou des passages
    dont les mixages n'ont plus de contenu commun.
    """
    from .audio_io import analysis_weights

    weights = analysis_weights(audio_a.stream.effective_layout, audio_a.channels)
    if tracks is not None:
        track_a, track_b, analysis_rate = tracks
    else:
        track_a, analysis_rate = audio_a.analysis_track()
        track_b, _ = audio_b.analysis_track()
    ratio = audio_a.sample_rate / analysis_rate
    coarse_search = max(int(round(local_search_s * analysis_rate)), 2)
    fine_search = max(int(round(local_search_s * audio_a.sample_rate)), 8)

    starts = np.arange(0, max(audio_a.frames, 1), block_frames)
    delays = np.zeros(starts.size, dtype=np.float64)
    confidences = np.zeros(starts.size, dtype=np.float64)
    base = float(global_delay.delay)

    for position, start in enumerate(starts):
        if progress is not None and position % 8 == 0:
            progress.step(position / max(starts.size, 1), "alignement local")
        coarse_start = int(start / ratio)
        coarse_len = max(int(block_frames / ratio), 32)
        segment_a = track_a[coarse_start:coarse_start + coarse_len]
        coarse_center = int(round(base / ratio))
        lo = coarse_start + coarse_center - coarse_search
        hi = coarse_start + coarse_center + coarse_len + coarse_search
        segment_b = track_b[max(lo, 0):max(hi, 0)]
        if segment_a.size < 32 or segment_b.size < 32:
            delays[position] = base
            confidences[position] = 0.0
            continue
        coarse = gcc_phat(segment_a, segment_b, max_shift=coarse_search, refine=False)
        coarse_delay = (coarse.integer_delay + (max(lo, 0) - coarse_start)) * ratio

        # Raffinement pleine résolution autour du décalage grossier.
        # Même pondération que la piste d'analyse : moyenner les six canaux ferait
        # dominer le centre — le dialogue, donc ce qui diffère — et les surrounds, qui
        # peuvent porter leur propre retard. L'affinage échouerait alors sur des blocs
        # dont le contenu est pourtant parfaitement commun.
        block_a = audio_a.read(int(start), block_frames) @ weights
        center = int(round(coarse_delay))
        pad = fine_search + 64
        block_b = audio_b.read(int(start) + center - pad,
                               block_frames + 2 * pad) @ weights
        # `block_b` commence `pad` trames avant le décalage grossier : le décalage
        # attendu vaut donc `pad`, et c'est là qu'il faut centrer la recherche. Centrée
        # sur zéro, elle plaçait la bonne réponse au bord même de la fenêtre et
        # gaspillait la moitié de son étendue.
        fine = gcc_phat(block_a, block_b, max_shift=fine_search, center=pad, refine=True)
        measured = fine.delay - pad
        # Un pic collé au bord de la fenêtre n'est pas une mesure : c'est un échec de
        # recherche. Mieux vaut garder l'estimation grossière que cette valeur fausse.
        if abs(measured) >= 0.9 * fine_search:
            delays[position] = coarse_delay
            confidences[position] = coarse.confidence * 0.5
        else:
            delays[position] = measured + center
            confidences[position] = max(fine.confidence, coarse.confidence * 0.5)
        # Cette passe parcourt tout le signal : on rend les pages déjà lues, sinon le
        # cache de fichiers du processus finit par contenir les deux films entiers.
        audio_a.release_until(int(start) - 2 * block_frames)
        audio_b.release_until(int(start) + center - 2 * block_frames)

    notes: list[str] = []

    # Ce drapeau répond à « puis-je faire confiance au décalage local ici ? », pas à
    # « y a-t-il du contenu commun ? ». La seconde question est tranchée plus tard par
    # l'estimateur lui-même : là où rien n'est partagé, la puissance commune tombe
    # naturellement à zéro. Exiger en plus une corrélation de forme d'onde élevée
    # écarterait des blocs parfaitement alignés dont les mixages diffèrent un peu —
    # mesuré sur du vrai 5.1 : 19 % de blocs retenus pour 71 % de cohérence réelle.
    confident = confidences >= confidence_threshold
    anchor = float(np.median(delays[confident])) if confident.any() else base
    # Un décalage qui tombe sur la même valeur que ses voisins est une preuve
    # d'alignement en soi, même quand la corrélation brute reste modeste — c'est le cas
    # dès que les deux versions diffèrent par une égalisation ou une inversion de
    # polarité, qui effondrent la corrélation sans rien changer au contenu.
    # La tolérance doit rester très inférieure à la fenêtre de recherche : à l'échelle
    # de celle-ci, n'importe quel pic fortuit paraîtrait « cohérent ».
    consistent = np.abs(delays - anchor) <= max(audio_a.sample_rate * 0.01, 480)
    # ...mais une corrélation strictement nulle reste une absence de contenu commun :
    # sans ce plancher, un pic fortuit tombant près du décalage global suffirait à
    # faire passer pour comparable un plan totalement différent.
    comparable = confident | (consistent & (confidences >= MIN_LOCAL_CORRELATION))

    # Décalages aberrants : le bloc n'est pas mesuré de façon fiable.
    reference = float(np.median(delays[comparable])) if comparable.any() else anchor
    outliers = np.abs(delays - reference) > max(fine_search * 4, audio_a.sample_rate * 0.5)
    if outliers.any():
        notes.append(
            f"{int(outliers.sum())} bloc(s) présentent un décalage local aberrant : "
            "le décalage de leurs voisins leur est appliqué."
        )
        comparable &= ~outliers

    # Là où le décalage n'a pas pu être mesuré, on interpole celui des voisins fiables
    # plutôt que d'abandonner le bloc. Un décalage interpolé reste bien meilleur qu'un
    # commun mis à zéro : si le contenu est réellement partagé, l'estimateur le
    # retrouve ; s'il ne l'est pas, la puissance commune tombe d'elle-même. Laisser des
    # trous produisait un commun haché — musique, silence, musique — alors que le
    # contenu était là.
    if comparable.any() and not comparable.all():
        delays[~comparable] = np.interp(starts[~comparable].astype(np.float64),
                                        starts[comparable].astype(np.float64),
                                        delays[comparable])
    elif not comparable.any():
        # Aucun bloc ne confirme son décalage : on retient le décalage global, qui
        # vient de l'alignement d'enveloppes. C'est grossier, mais suffisant pour la
        # séparation en magnitude, qui ne dépend d'aucune relation de phase.
        delays[:] = base
        notes.append(
            "Aucun bloc ne confirme son décalage localement : le décalage global est "
            "appliqué partout. Suffisant pour une séparation en magnitude, insuffisant "
            "pour une annulation de phase."
        )

    drift_ppm = _estimate_drift_ppm(starts, delays, comparable)
    if abs(drift_ppm) > 5.0:
        notes.append(
            f"Dérive d'horloge estimée à {drift_ppm:.1f} ppm : les décalages locaux la "
            "suivent bloc par bloc, mais un rééchantillonnage des sources serait préférable."
        )
    if comparable.size and comparable.mean() < 0.5:
        notes.append(
            f"{100 - comparable.mean() * 100:.0f} % des blocs ont une corrélation locale "
            "trop faible pour confirmer leur décalage ; ils sont traités avec celui de "
            "leurs voisins. C'est normal si les deux versions diffèrent par une "
            "égalisation ou une inversion de polarité, et suspect si elles devraient "
            "être identiques."
        )

    return OffsetMap(
        block_frames=block_frames, starts=starts, delays=delays,
        confidences=confidences, comparable=comparable,
        global_delay=float(global_delay.delay), drift_ppm=drift_ppm,
        threshold=confidence_threshold, notes=notes,
    )


#: Décalage par canal recherché au-delà du décalage commun, en secondes.
CHANNEL_OFFSET_SEARCH_S = 0.12


def estimate_channel_offsets(audio_a, audio_b, offset_map, plan,
                             probe_blocks: int = 24,
                             search_s: float = CHANNEL_OFFSET_SEARCH_S,
                             progress=None) -> tuple[np.ndarray, np.ndarray]:
    """Décalage propre à chaque canal, en plus du décalage commun.

    Un mixage 5.1 n'est pas forcément synchrone canal par canal d'une version à
    l'autre : mesuré sur un film réel, les avants étaient à −46 ms et les surrounds à
    +22 ms entre la version anglaise et la version française. Avec un décalage unique,
    les surrounds ne s'annulent pas du tout — ils ressortent entiers dans les résidus.

    La mesure se fait sur des blocs répartis dans le signal ; la médiane pondérée par
    la netteté du pic sert de décalage retenu, et un canal sans pic net garde zéro,
    c'est-à-dire le décalage commun.

    Retourne `(décalages en trames, confiance par canal)`.
    """
    channels = audio_a.channels
    offsets = np.zeros(channels, dtype=np.float64)
    confidences = np.zeros(channels, dtype=np.float64)
    search = max(int(round(search_s * audio_a.sample_rate)), 16)
    limit = min(search, plan.margin // 2)

    indices = np.flatnonzero(offset_map.comparable)
    if indices.size == 0:
        return offsets, confidences
    picks = indices[np.linspace(0, indices.size - 1,
                                min(probe_blocks, indices.size)).astype(int)]

    per_channel: list[list[tuple[float, float]]] = [[] for _ in range(channels)]
    for position, index in enumerate(picks):
        if progress is not None:
            progress.step(position / max(picks.size, 1), "décalage par canal")
        start = int(offset_map.starts[index])
        integer, _ = int(np.rint(offset_map.delays[index])), 0.0
        block_a = audio_a.read(start, plan.block_frames)
        block_b = audio_b.read(start + integer - limit, plan.block_frames + 2 * limit)
        for channel in range(channels):
            local = gcc_phat(block_a[:, channel], block_b[:, channel],
                             max_shift=limit, center=limit)
            shift = local.delay - limit
            if abs(shift) <= limit:
                per_channel[channel].append((shift, local.peak_ratio))

    tolerance = max(0.008 * audio_a.sample_rate, limit * 0.1)
    for channel in range(channels):
        samples = per_channel[channel]
        if not samples:
            continue
        values = np.array([v for v, _ in samples])
        weights = np.array([w for _, w in samples])
        # Une sonde qui bute sur le bord de la fenêtre de recherche n'a rien mesuré :
        # c'est un échec, pas un décalage, et la garder fausse toute dispersion.
        usable = (weights >= MIN_PEAK_RATIO) & (np.abs(values) < 0.9 * limit)
        if usable.sum() < max(3, len(samples) // 4):
            continue
        candidates = values[usable]
        # Consensus plutôt qu'écart-type : les sondes qui échouent produisent des
        # valeurs dispersées qui feraient exploser une variance, mais ne déplacent pas
        # le groupe majoritaire.
        centre = float(np.median(candidates))
        agreeing = np.abs(candidates - centre) <= tolerance
        if agreeing.sum() < max(3, candidates.size // 3):
            continue
        offsets[channel] = float(np.median(candidates[agreeing]))
        confidences[channel] = float(agreeing.sum() / len(samples))
    return offsets, confidences


def _estimate_drift_ppm(starts: np.ndarray, delays: np.ndarray,
                        comparable: np.ndarray) -> float:
    """Pente des décalages locaux : une dérive d'horloge en parties par million."""
    if comparable.sum() < 4:
        return 0.0
    x = starts[comparable].astype(np.float64)
    y = delays[comparable].astype(np.float64)
    if np.ptp(x) < 1.0:
        return 0.0
    slope = float(np.polyfit(x, y, 1)[0])
    return slope * 1e6
