"""Paramètres et utilitaires STFT partagés par l'estimation du commun et la soustraction."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import ShortTimeFFT, get_window


@dataclass(frozen=True)
class StftConfig:
    """Analyse temps-fréquence et lissage local des spectres.

    `smooth_frames` / `smooth_bins` définissent le voisinage (± n) sur lequel les
    auto-spectres et l'inter-spectre sont moyennés. C'est l'équivalent, en STFT, de
    la moyenne de Welch : sans ce moyennage, l'inter-spectre d'une seule case n'a
    aucune valeur statistique ; avec, la résolution temps-fréquence baisse.
    """

    sample_rate: int
    win_length: int = 8192
    hop: int = 2048
    smooth_frames: int = 3
    smooth_bins: int = 2

    @property
    def n_fft(self) -> int:
        return self.win_length

    @property
    def frame_s(self) -> float:
        return self.win_length / self.sample_rate

    @property
    def bin_hz(self) -> float:
        return self.sample_rate / self.win_length

    @property
    def smoothing_s(self) -> float:
        return (2 * self.smooth_frames + 1) * self.hop / self.sample_rate

    @property
    def smoothing_hz(self) -> float:
        return (2 * self.smooth_bins + 1) * self.bin_hz

    @property
    def independent_cells(self) -> float:
        """Nombre approximatif de cases indépendantes dans le voisinage de lissage.

        Les trames se recouvrent de `1 - hop/win`, elles ne sont donc pas
        indépendantes ; ce compte sert à corriger le biais de l'inter-spectre.
        """
        frames = 1.0 + 2 * self.smooth_frames * self.hop / self.win_length
        return float((2 * self.smooth_bins + 1) * frames)

    def build(self) -> ShortTimeFFT:
        window = get_window("hann", self.win_length, fftbins=True)
        return ShortTimeFFT(window.astype(np.float64), hop=self.hop,
                            fs=float(self.sample_rate), mfft=self.n_fft,
                            fft_mode="onesided")

    def as_dict(self) -> dict:
        return {
            "win_length": self.win_length,
            "hop": self.hop,
            "window": "hann périodique",
            "smooth_frames": self.smooth_frames,
            "smooth_bins": self.smooth_bins,
            "smoothing_time_s": round(self.smoothing_s, 4),
            "smoothing_freq_hz": round(self.smoothing_hz, 2),
            "independent_cells_estimate": round(self.independent_cells, 2),
        }


def smooth2d(values: np.ndarray, bins: int, frames: int) -> np.ndarray:
    """Moyenne glissante séparable sur (fréquence, trame), bords en mode « nearest »."""
    from scipy.ndimage import uniform_filter1d

    out = np.asarray(values)
    if bins > 0:
        out = uniform_filter1d(out, size=2 * bins + 1, axis=0, mode="nearest")
    if frames > 0:
        out = uniform_filter1d(out, size=2 * frames + 1, axis=1, mode="nearest")
    return out


def smooth_cross(values: np.ndarray, bins: int, frames: int) -> np.ndarray:
    """Version complexe de `smooth2d` (parties réelle et imaginaire séparément)."""
    return smooth2d(values.real, bins, frames) + 1j * smooth2d(values.imag, bins, frames)


def local_spectra(spectrum_a: np.ndarray, spectrum_b: np.ndarray,
                  config: StftConfig) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Auto-spectres et inter-spectre lissés sur le voisinage temps-fréquence.

    Retourne `(Saa, Sbb, Sab)` avec `Sab = <A conj(B)>`.
    """
    saa = smooth2d(np.abs(spectrum_a) ** 2, config.smooth_bins, config.smooth_frames)
    sbb = smooth2d(np.abs(spectrum_b) ** 2, config.smooth_bins, config.smooth_frames)
    sab = smooth_cross(spectrum_a * np.conj(spectrum_b), config.smooth_bins,
                       config.smooth_frames)
    return saa, sbb, sab


def magnitude_squared_coherence(saa: np.ndarray, sbb: np.ndarray,
                                sab: np.ndarray) -> np.ndarray:
    """Cohérence quadratique locale, bornée à [0, 1]."""
    floor = 1e-20 + 1e-9 * float(np.mean(saa) + np.mean(sbb))
    gamma = np.abs(sab) ** 2 / np.maximum(saa * sbb, floor)
    return np.clip(gamma, 0.0, 1.0)


def fractional_delay_ramp(n_bins: int, n_fft: int, delay: float) -> np.ndarray:
    """Rampe de phase appliquant un retard fractionnaire de `delay` trames.

    Multiplier un spectre par cette rampe retarde le signal de `delay` échantillons.
    """
    k = np.arange(n_bins, dtype=np.float64)
    return np.exp(-2j * np.pi * k * delay / n_fft)
