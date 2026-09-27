"""Estimation de la transformation `H(f)` entre les deux versions de la bande commune.

Modèle : `A = C + D_A` et `B = H·C + D_B`. `H` regroupe le gain, une éventuelle
égalisation de mastering et un résidu de phase. Il est estimé **uniquement sur les
cases temps-fréquence fiables**, définies comme celles dont la cohérence locale
dépasse un seuil : ce sont les cases dominées par du contenu commun, donc peu
influencées par les dialogues. Aucune normalisation de loudness indépendante n'est
appliquée : c'est bien le rapport entre les deux pistes qui est mesuré.

Biais assumé : sélectionner les cases sur la cohérence mesurée dans les mêmes données
favorise les cases où le commun domine, ce qui est l'effet recherché, mais surestime
légèrement la fiabilité affichée. Les dialogues identiques d'une version à l'autre
(mêmes mots, voix originale résiduelle) restent cohérents et biaisent `H` comme
l'estimation du commun.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .blocks import BlockPlan, align_spectrum, read_extended, split_delay
from .stft import (StftConfig, debias_coherence, local_spectra,
                   magnitude_squared_coherence, smooth_cross)

#: Bandes d'octave utilisées pour résumer `H` dans les métadonnées.
OCTAVE_EDGES = (0.0, 62.5, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0)


#: Estimateurs d'amplitude disponibles pour `H` (voir `estimate_transfer`).
ESTIMATORS = ("power", "ls")


@dataclass
class Transfer:
    """Réponse complexe par canal, plus ses diagnostics."""

    mode: str
    response: np.ndarray                  # (canaux, bins) complexe
    reliable_fraction: float
    reliable_cells: int
    coherence_threshold: float
    estimator: str = "power"
    mean_coherence: float = 0.0
    mean_coherence_debiased: float = 0.0
    """Cohérence locale moyenne, pondérée par l'énergie de A, sur les blocs sondés.

    C'est la mesure qui dit s'il existe une bande commune : proche de 0, les deux
    versions ne partagent rien, même parfaitement alignées.
    """
    broadband_gain: list[float] = field(default_factory=list)
    band_gain_db: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def channels(self) -> int:
        return self.response.shape[0]

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "estimator": self.estimator,
            "definition": "B ≈ H(f) · C, estimé sur les cases de cohérence locale élevée",
            "amplitude_rule": ("|H| = sqrt(ΣS_BB / ΣS_AA)" if self.estimator == "power"
                               else "|H| = |ΣS_AB| / ΣS_AA"),
            "coherence_threshold": self.coherence_threshold,
            "reliable_cell_fraction": round(self.reliable_fraction, 4),
            "energy_weighted_coherence": round(self.mean_coherence, 4),
            "energy_weighted_coherence_debiased": round(self.mean_coherence_debiased, 4),
            "reliable_cells": int(self.reliable_cells),
            "broadband_gain_per_channel": [round(g, 5) for g in self.broadband_gain],
            "band_gain_db_per_channel": self.band_gain_db,
            "warnings": list(self.warnings),
        }


def _octave_summary(response: np.ndarray, sample_rate: int) -> list[dict]:
    bins = response.shape[1]
    freqs = np.linspace(0.0, sample_rate / 2, bins)
    summary: list[dict] = []
    for channel in range(response.shape[0]):
        bands: dict[str, float] = {}
        magnitude = np.abs(response[channel])
        for low, high in zip(OCTAVE_EDGES[:-1], OCTAVE_EDGES[1:]):
            mask = (freqs >= low) & (freqs < min(high, sample_rate / 2))
            if not mask.any():
                continue
            value = float(np.median(magnitude[mask]))
            bands[f"{int(low)}-{int(high)}Hz"] = round(20 * np.log10(max(value, 1e-6)), 2)
        summary.append(bands)
    return summary


def _select_probe_blocks(offset_map, limit: int) -> list[int]:
    indices = np.flatnonzero(offset_map.comparable)
    if indices.size == 0:
        indices = np.arange(offset_map.starts.size)
    if indices.size <= limit:
        return [int(i) for i in indices]
    picks = np.linspace(0, indices.size - 1, limit).astype(int)
    return [int(indices[p]) for p in picks]


def estimate_transfer(audio_a, audio_b, offset_map, config: StftConfig, plan: BlockPlan,
                      mode: str = "band", coherence_threshold: float = 0.8,
                      probe_blocks: int = 32, min_cells_per_bin: int = 8,
                      estimator: str = "power", channel_offsets=None,
                      progress=None) -> Transfer:
    """Estime `H(f)` par canal sur des blocs répartis dans tout le signal.

    Deux estimateurs d'amplitude, tous deux calculés sur les cases fiables :

    * `ls` : régression `|H| = |ΣS_AB| / ΣS_AA`. C'est la régression des moindres
      carrés de B sur A ; elle est **biaisée vers le bas** par le bruit propre de A
      (facteur `1/(1 + S_DA/S_CC)`).
    * `power` : rapport de puissances `|H| = sqrt(ΣS_BB / ΣS_AA)`. Ce rapport est la
      moyenne géométrique de l'estimateur `ls` (biaisé bas par `D_A`) et de
      `ΣS_BB/|ΣS_AB|` (biaisé haut par `D_B`) : il est exact quand les deux bruits
      relatifs sont égaux, et reste entre les deux sinon.

    Dans les deux cas la phase vient de l'inter-spectre : `arg(H) = −arg(ΣS_AB)`.
    """
    if estimator not in ESTIMATORS:
        raise ValueError(f"Estimateur inconnu : {estimator}")
    channels = audio_a.channels
    bins = config.n_fft // 2 + 1
    if mode == "none":
        return Transfer(mode="none", response=np.ones((channels, bins), dtype=np.complex128),
                        reliable_fraction=float("nan"), reliable_cells=0,
                        coherence_threshold=coherence_threshold, estimator=estimator,
                        mean_coherence=0.0, broadband_gain=[1.0] * channels,
                        band_gain_db=[{} for _ in range(channels)],
                        warnings=["Transformation désactivée (--transfer none) : H = 1."])

    sft = config.build()
    # Chaque canal peut avoir son propre retard : l'ignorer ici reviendrait à estimer
    # H(f) sur des canaux désalignés, donc à en faire du bruit.
    offsets = (np.zeros(channels) if channel_offsets is None
               else np.asarray(channel_offsets, dtype=np.float64))
    shifts = np.rint(offsets).astype(int)
    span = int(np.abs(shifts).max(initial=0)) + 1
    numerator = np.zeros((channels, bins), dtype=np.complex128)
    denominator = np.zeros((channels, bins), dtype=np.float64)
    target_power = np.zeros((channels, bins), dtype=np.float64)
    counts = np.zeros((channels, bins), dtype=np.int64)
    total_cells = 0
    reliable_cells = 0
    coherence_weighted = 0.0
    coherence_weight = 0.0

    picks = _select_probe_blocks(offset_map, probe_blocks)
    for position, index in enumerate(picks):
        if progress is not None:
            progress.step(position / max(len(picks), 1), "estimation du gain et de l'égalisation")
        start = int(offset_map.starts[index])
        integer, fractional = split_delay(float(offset_map.delays[index]))
        block_a = read_extended(audio_a, start, plan)
        extended = plan.block_frames + 2 * plan.margin
        wide_b = audio_b.read(start - plan.margin + integer - span, extended + 2 * span)
        for channel in range(channels):
            lo = span + int(shifts[channel])
            extra = float(offsets[channel] - shifts[channel])
            spec_a = sft.stft(block_a[:, channel].astype(np.float64))
            spec_b = align_spectrum(
                sft.stft(wide_b[lo:lo + extended, channel].astype(np.float64)),
                fractional + extra, config)
            saa, sbb, sab = local_spectra(spec_a, spec_b, config)
            gamma = magnitude_squared_coherence(saa, sbb, sab)
            reliable = gamma >= coherence_threshold
            total_cells += gamma.size
            reliable_cells += int(reliable.sum())
            coherence_weighted += float(np.sum(gamma * saa))
            coherence_weight += float(np.sum(saa))
            if not reliable.any():
                continue
            numerator[channel] += np.sum(np.where(reliable, np.conj(sab), 0.0), axis=1)
            denominator[channel] += np.sum(np.where(reliable, saa, 0.0), axis=1)
            target_power[channel] += np.sum(np.where(reliable, sbb, 0.0), axis=1)
            counts[channel] += reliable.sum(axis=1)

    warnings: list[str] = []
    reliable_fraction = reliable_cells / total_cells if total_cells else 0.0
    mean_coherence = coherence_weighted / coherence_weight if coherence_weight else 0.0
    mean_coherence_debiased = debias_coherence(mean_coherence, config.independent_cells)
    response = np.ones((channels, bins), dtype=np.complex128)
    broadband: list[float] = []

    for channel in range(channels):
        energy = denominator[channel]
        total_energy = max(float(np.sum(energy)), 1e-30)
        if estimator == "power":
            gain_total = float(np.sqrt(float(np.sum(target_power[channel])) / total_energy))
        else:
            gain_total = float(np.sum(np.abs(numerator[channel]))) / total_energy
        gain_total = float(np.clip(gain_total, 0.02, 50.0))
        broadband.append(gain_total)
        if mode == "gain":
            response[channel, :] = gain_total
            continue
        usable = (counts[channel] >= min_cells_per_bin) & (energy > 0)
        raw = np.full(bins, gain_total, dtype=np.complex128)
        if estimator == "power":
            amplitude = np.sqrt(target_power[channel][usable] / energy[usable])
            phase = np.exp(1j * np.angle(numerator[channel][usable]))
            raw[usable] = amplitude * phase
        else:
            raw[usable] = numerator[channel][usable] / energy[usable]
        # Lissage fréquentiel : H doit rester une enveloppe, pas suivre le dialogue.
        smoothed = smooth_cross(raw[:, None], bins=4, frames=0)[:, 0]
        magnitude = np.abs(smoothed)
        limit_low, limit_high = gain_total / 4.0, gain_total * 4.0
        clipped = np.clip(magnitude, limit_low, limit_high)
        with np.errstate(invalid="ignore", divide="ignore"):
            direction = np.where(magnitude > 1e-12, smoothed / np.maximum(magnitude, 1e-12), 1.0)
        response[channel] = clipped * direction
        # Les bins non contraints retombent sur le gain large bande, sans phase inventée.
        response[channel][~usable] = gain_total
        if usable.mean() < 0.5:
            warnings.append(
                f"Canal {channel} : seuls {usable.mean() * 100:.0f} % des bins ont assez de "
                "cases fiables ; les autres utilisent le gain large bande."
            )

    if reliable_fraction < 0.02:
        warnings.append(
            f"Très peu de cases fiables ({reliable_fraction * 100:.2f} %) : la transformation "
            "entre les deux versions est mal contrainte, la séparation sera médiocre."
        )
    spread = max(broadband) / max(min(broadband), 1e-9) if broadband else 1.0
    if spread > 1.5:
        warnings.append(
            f"Gains très différents entre canaux (rapport {spread:.2f}) : vérifiez que les "
            "canaux sont appariés dans le bon ordre."
        )

    return Transfer(
        mode=mode, response=response, reliable_fraction=reliable_fraction,
        reliable_cells=reliable_cells, coherence_threshold=coherence_threshold,
        estimator=estimator, mean_coherence=mean_coherence,
        mean_coherence_debiased=mean_coherence_debiased, broadband_gain=broadband,
        band_gain_db=_octave_summary(response, config.sample_rate), warnings=warnings,
    )
