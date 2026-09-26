"""Découpage en blocs, marges d'analyse et recouvrement de reconstitution.

Le traitement d'un film se fait bloc par bloc pour borner la mémoire. Chaque bloc
est analysé avec une marge supplémentaire de chaque côté (les bords d'une STFT sont
mal estimés), et les blocs utiles se recouvrent d'un fondu qui absorbe les
variations de décalage local d'un bloc au suivant, sans clic.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .stft import StftConfig, fractional_delay_ramp


@dataclass(frozen=True)
class BlockPlan:
    """Géométrie du découpage, en trames de la fréquence de travail."""

    block_frames: int
    margin: int
    fade: int

    @classmethod
    def for_config(cls, config: StftConfig, block_seconds: float = 6.0,
                   fade_seconds: float = 0.25) -> "BlockPlan":
        block = max(int(round(block_seconds * config.sample_rate)), 8 * config.win_length)
        fade = max(int(round(fade_seconds * config.sample_rate)), config.win_length)
        fade = min(fade, block // 4)
        return cls(block_frames=block, margin=2 * config.win_length, fade=fade)

    @property
    def advance(self) -> int:
        return self.block_frames - self.fade

    def starts(self, total_frames: int) -> np.ndarray:
        if total_frames <= 0:
            return np.zeros(0, dtype=np.int64)
        count = max(int(np.ceil(total_frames / self.advance)), 1)
        return np.arange(count, dtype=np.int64) * self.advance

    def weights(self, start: int, total_frames: int) -> np.ndarray:
        """Poids de recouvrement du bloc utile ; la somme des poids vaut 1 partout."""
        weights = np.ones(self.block_frames, dtype=np.float64)
        fade = self.fade
        if fade > 0:
            ramp = np.linspace(0.0, 1.0, fade, endpoint=False)
            if start > 0:
                weights[:fade] = ramp
            if start + self.advance < total_frames:  # un bloc suivant prendra le relais
                weights[-fade:] = 1.0 - ramp
        return weights

    def as_dict(self, sample_rate: int) -> dict:
        return {
            "block_s": round(self.block_frames / sample_rate, 4),
            "analysis_margin_s": round(self.margin / sample_rate, 4),
            "crossfade_s": round(self.fade / sample_rate, 4),
        }


def read_extended(audio, start: int, plan: BlockPlan) -> np.ndarray:
    """Bloc `(trames, canaux)` avec marge d'analyse de part et d'autre."""
    return audio.read(start - plan.margin, plan.block_frames + 2 * plan.margin)


def align_spectrum(spectrum_b: np.ndarray, fractional: float, config: StftConfig) -> np.ndarray:
    """Avance le spectre de B de `fractional` trame pour le mettre dans le repère de A."""
    if abs(fractional) < 1e-6:
        return spectrum_b
    ramp = fractional_delay_ramp(spectrum_b.shape[0], config.n_fft, -fractional)
    return spectrum_b * ramp[:, None]


def delay_spectrum(spectrum: np.ndarray, fractional: float, config: StftConfig) -> np.ndarray:
    """Opération inverse : retarde un spectre de `fractional` trame (repère de B)."""
    if abs(fractional) < 1e-6:
        return spectrum
    ramp = fractional_delay_ramp(spectrum.shape[0], config.n_fft, fractional)
    return spectrum * ramp[:, None]


def split_delay(delay: float) -> tuple[int, float]:
    """Décompose un décalage en partie entière (lecture) et fractionnaire (phase)."""
    integer = int(np.rint(delay))
    return integer, float(delay - integer)
