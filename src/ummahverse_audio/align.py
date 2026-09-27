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


@dataclass(frozen=True)
class DelayEstimate:
    """Retard estimé de `y` par rapport à `x`, en trames."""

    delay: float
    integer_delay: int
    confidence: float
    peak_ratio: float

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


def estimate_global_delay(track_a: np.ndarray, track_b: np.ndarray, rate: int,
                          max_delay_s: float = 30.0,
                          probe_count: int = 9,
                          probe_seconds: float = 20.0) -> DelayEstimate:
    """Décalage global estimé sur plusieurs sondes réparties dans le signal.

    Plusieurs sondes évitent qu'un générique musical ou un silence prolongé
    impose un décalage faux, et permettent de rester en mémoire bornée sur un film.
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
    # est bornée à la moitié du signal, sans quoi le pic est estimé sur un
    # recouvrement minuscule et la confiance n'a plus de valeur.
    max_shift = max(min(int(round(max_delay_s * rate)), shortest // 2), 1)
    if shortest < 4 * max_shift or shortest < int(2 * probe_seconds * rate):
        estimate = gcc_phat(track_a, track_b, max_shift=max_shift)
        if estimate.peak_ratio < MIN_PEAK_RATIO and estimate.confidence < MIN_CONFIDENCE:
            raise AlignmentError(
                "Aucun décalage global n'a pu être estimé entre les deux entrées.",
                "Les deux pistes proviennent-elles du même montage ? "
                "Un décalage supérieur à --max-offset secondes est aussi possible.",
            )
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
        raise AlignmentError(
            "Aucune sonde d'alignement n'atteint une corrélation exploitable.",
            "Vérifiez que A et B sont deux versions du même montage, et que le décalage "
            "reste sous --max-offset secondes.",
        )
    delays = np.array([r.integer_delay for r in credible], dtype=np.float64)
    median = float(np.median(delays))
    # Raffine autour de la médiane sur la sonde la plus fiable.
    best = max(credible, key=lambda r: r.confidence)
    coherent = [r for r in credible if abs(r.integer_delay - median) <= rate * 0.5]
    if coherent:
        best = max(coherent, key=lambda r: r.confidence)
    spread = float(np.std(delays)) / rate
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
        block_a = audio_a.read(int(start), block_frames).mean(axis=1)
        center = int(round(coarse_delay))
        pad = fine_search + 64
        block_b = audio_b.read(int(start) + center - pad, block_frames + 2 * pad).mean(axis=1)
        fine = gcc_phat(block_a, block_b, max_shift=pad, refine=True)
        delays[position] = fine.delay + center - pad
        confidences[position] = max(fine.confidence, coarse.confidence * 0.5)
        # Cette passe parcourt tout le signal : on rend les pages déjà lues, sinon le
        # cache de fichiers du processus finit par contenir les deux films entiers.
        audio_a.release_until(int(start) - 2 * block_frames)
        audio_b.release_until(int(start) + center - 2 * block_frames)

    comparable = confidences >= confidence_threshold
    notes: list[str] = []

    # Décalages incohérents => on retombe sur la référence globale et on marque le bloc.
    if comparable.any():
        reference = float(np.median(delays[comparable]))
    else:
        reference = base
    outliers = np.abs(delays - reference) > max(fine_search * 4, audio_a.sample_rate * 0.5)
    if outliers.any():
        notes.append(
            f"{int(outliers.sum())} bloc(s) présentent un décalage local aberrant et sont "
            "traités comme non comparables."
        )
        comparable &= ~outliers
        delays[outliers] = reference

    drift_ppm = _estimate_drift_ppm(starts, delays, comparable)
    if abs(drift_ppm) > 5.0:
        notes.append(
            f"Dérive d'horloge estimée à {drift_ppm:.1f} ppm : les décalages locaux la "
            "suivent bloc par bloc, mais un rééchantillonnage des sources serait préférable."
        )
    if comparable.size and comparable.mean() < 0.5:
        notes.append(
            f"Seulement {comparable.mean() * 100:.0f} % des blocs sont comparables : "
            "montages probablement différents ou peu de contenu commun."
        )

    return OffsetMap(
        block_frames=block_frames, starts=starts, delays=delays,
        confidences=confidences, comparable=comparable,
        global_delay=float(global_delay.delay), drift_ppm=drift_ppm,
        threshold=confidence_threshold, notes=notes,
    )


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
