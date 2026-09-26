"""Mesures objectives sur sources connues, avec des définitions explicites.

Toutes les mesures reposent sur une **décomposition par moindres carrés** de la sortie
à évaluer sur les sources vraies du jeu synthétique. Pour une sortie `y` et des sources
`s_1..s_k` :

    y ≈ Σ_i c_i · s_i + e        (c = argmin ‖y − Σ c_i s_i‖²)

* `c_i` en dB indique quelle fraction de la source `i` se retrouve dans `y` : 0 dB =
  source entièrement présente, −20 dB = atténuée de 20 dB.
* `artifacts_db` = énergie de `e` relative à celle de `y` : ce qui n'est explicable par
  aucune source vraie (bruit d'estimation, effets de fenêtrage).

La décomposition conjointe est nécessaire : musique et voix synthétiques ne sont pas
orthogonales, des projections séparées se contamineraient l'une l'autre.

`si_sdr` est le rapport signal/distorsion invariant à l'échelle habituel, calculé après
recherche du meilleur facteur d'échelle.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .ffmpeg_tools import _no_window_kwargs, find_ffmpeg


def read_audio(path: Path) -> tuple[np.ndarray, int]:
    """Décode un fichier en `(échantillons (n, canaux) float64, fréquence)`."""
    from .ffmpeg_tools import probe_audio_streams

    stream = probe_audio_streams(Path(path))[0]
    args = [str(find_ffmpeg()), "-hide_banner", "-nostdin", "-v", "error",
            "-i", str(Path(path)), "-map", "0:a:0", "-f", "f32le",
            "-acodec", "pcm_f32le", "pipe:1"]
    completed = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                               check=True, **_no_window_kwargs())
    data = np.frombuffer(completed.stdout, dtype=np.float32).astype(np.float64)
    channels = stream.channels
    return data.reshape(-1, channels), stream.sample_rate


def delayed(signal: np.ndarray, offset: int) -> np.ndarray:
    """Retarde un signal de `offset` trames en conservant sa longueur (comme le générateur)."""
    if offset == 0:
        return signal.copy()
    out = np.zeros_like(signal)
    if offset > 0:
        out[offset:] = signal[:-offset]
    else:
        out[:offset] = signal[-offset:]
    return out


def _flatten(signal: np.ndarray, window: slice | None) -> np.ndarray:
    section = signal[window] if window is not None else signal
    return np.asarray(section, dtype=np.float64).reshape(-1)


def decompose(estimate: np.ndarray, sources: dict[str, np.ndarray],
              window: slice | None = None) -> dict:
    """Décompose `estimate` sur les sources vraies par moindres carrés."""
    y = _flatten(estimate, window)
    names = list(sources)
    matrix = np.stack([_flatten(sources[name], window) for name in names], axis=1)
    length = min(y.size, matrix.shape[0])
    y, matrix = y[:length], matrix[:length]
    coefficients, *_ = np.linalg.lstsq(matrix, y, rcond=None)
    residual = y - matrix @ coefficients
    energy = float(np.dot(y, y)) or 1e-30
    result = {f"{name}_db": round(float(20 * np.log10(max(abs(c), 1e-9))), 2)
              for name, c in zip(names, coefficients)}
    result.update({f"{name}_coefficient": round(float(c), 5)
                   for name, c in zip(names, coefficients)})
    result["artifacts_db"] = round(float(10 * np.log10(
        max(float(np.dot(residual, residual)) / energy, 1e-12))), 2)
    return result


def si_sdr(estimate: np.ndarray, target: np.ndarray,
           window: slice | None = None) -> float:
    """SI-SDR en dB, invariant à l'échelle."""
    y = _flatten(estimate, window)
    t = _flatten(target, window)
    length = min(y.size, t.size)
    y, t = y[:length], t[:length]
    energy = float(np.dot(t, t))
    if energy <= 0:
        return float("nan")
    scale = float(np.dot(y, t)) / energy
    projection = scale * t
    noise = y - projection
    noise_energy = float(np.dot(noise, noise))
    if noise_energy <= 0:
        return float("inf")
    return float(10 * np.log10(float(np.dot(projection, projection)) / noise_energy))


def snr_db(estimate: np.ndarray, target: np.ndarray, window: slice | None = None) -> float:
    """Rapport signal/bruit sans ajustement d'échelle (sensible au gain)."""
    y = _flatten(estimate, window)
    t = _flatten(target, window)
    length = min(y.size, t.size)
    y, t = y[:length], t[:length]
    error = y - t
    denominator = float(np.dot(error, error))
    if denominator <= 0:
        return float("inf")
    return float(10 * np.log10(max(float(np.dot(t, t)), 1e-30) / denominator))


def rms(signal: np.ndarray, window: slice | None = None) -> float:
    values = _flatten(signal, window)
    return float(np.sqrt(np.mean(values ** 2))) if values.size else 0.0


@dataclass(frozen=True)
class EvaluationWindow:
    """Fenêtre d'évaluation, bornes explicitement documentées dans les résultats."""

    sample_rate: int
    skip_start_s: float = 0.5
    skip_end_s: float = 0.5

    def slice_for(self, frames: int) -> slice:
        start = int(self.skip_start_s * self.sample_rate)
        stop = frames - int(self.skip_end_s * self.sample_rate)
        return slice(start, max(stop, start + 1))

    def as_dict(self) -> dict:
        return {"skip_start_s": self.skip_start_s, "skip_end_s": self.skip_end_s,
                "note": "les bords sont exclus : la STFT y dispose de moins de contexte"}
