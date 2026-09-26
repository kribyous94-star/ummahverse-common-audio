"""Fixtures partagées : signaux synthétiques à sources connues, courts et reproductibles.

Les jeux sont volontairement courts (3 à 12 s) pour que la suite reste rapide, mais ils
reproduisent les situations qui comptent : voix **et** musique/bruitages simultanés,
décalage et gain connus, coupe de montage, silence, transitoires brefs, 5.1.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

SAMPLE_RATE = 48_000
ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class SyntheticScene:
    """Un jeu complet, avec ses sources vraies."""

    directory: Path
    sample_rate: int
    common: np.ndarray
    voice_a: np.ndarray
    voice_b: np.ndarray
    voice_c: np.ndarray
    offset_b: int
    offset_c: int
    gain_b: float
    gain_c: float

    @property
    def a(self) -> Path:
        return self.directory / "a.wav"

    @property
    def b(self) -> Path:
        return self.directory / "b.wav"

    @property
    def third(self) -> Path:
        return self.directory / "third.wav"

    def common_in_b(self) -> np.ndarray:
        return self.gain_b * shift(self.common, self.offset_b)

    def common_in_third(self) -> np.ndarray:
        return self.gain_c * shift(self.common, self.offset_c)


def shift(signal: np.ndarray, offset: int) -> np.ndarray:
    """Retarde de `offset` trames, longueur conservée."""
    if offset == 0:
        return signal.copy()
    out = np.zeros_like(signal)
    out[offset:] = signal[:-offset]
    return out


def write_wav(path: Path, data: np.ndarray, sample_rate: int = SAMPLE_RATE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, sample_rate, np.ascontiguousarray(data, dtype=np.float32))
    return path


def make_music(n: int, rng: np.random.Generator, sample_rate: int = SAMPLE_RATE,
               transients: bool = True) -> np.ndarray:
    """Musique harmonique + ambiance large bande + impacts : le contenu « commun »."""
    t = np.arange(n) / sample_rate
    music = (0.10 * np.sin(2 * np.pi * 220 * t)
             + 0.06 * np.sin(2 * np.pi * 330 * t + 0.15)
             + 0.035 * np.sin(2 * np.pi * 440 * t))
    noise = sosfilt(butter(3, [120, 7500], btype="bandpass", fs=sample_rate, output="sos"),
                    rng.normal(size=n))
    ambience = 0.008 * noise / max(float(np.std(noise)), 1e-12)
    impacts = np.zeros(n)
    if transients:
        for sec in np.arange(0.6, n / sample_rate - 0.4, 1.3):
            start = int(sec * sample_rate)
            length = min(int(0.02 * sample_rate), n - start)
            if length > 4:
                impacts[start:start + length] += (
                    0.2 * rng.normal(size=length) * np.hanning(length))
    return music + ambience + impacts


def make_voice(n: int, pitch: float, shift_phase: float,
               sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Voix synthétique intermittente qui recouvre explicitement la musique."""
    t = np.arange(n) / sample_rate
    gate = (np.sin(2 * np.pi * (1.7 + shift_phase) * t) > -0.3).astype(float)
    gate *= ((t > 0.2) & (t < n / sample_rate - 0.2)).astype(float)
    gate = np.convolve(gate, np.ones(321) / 321, mode="same")
    phase = 2 * np.pi * (pitch * t + 6 * np.sin(2 * np.pi * 0.7 * t))
    return 0.08 * gate * (np.sin(phase) + 0.4 * np.sin(2 * phase) + 0.15 * np.sin(3 * phase))


def build_scene(directory: Path, seconds: float = 6.0, channels: int = 2,
                offset_b: int = 3600, offset_c: int = 6240,
                gain_b: float = 0.87, gain_c: float = 1.06,
                seed: int = 20260926) -> SyntheticScene:
    """Construit `a.wav`, `b.wav`, `third.wav` et retourne les sources vraies."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE)
    mono = make_music(n, rng)
    weights = np.linspace(1.0, 0.9, channels) if channels > 1 else np.array([1.0])
    common = np.column_stack([weight * mono for weight in weights])
    voices = []
    for index, (pitch, phase) in enumerate(((143, 0.0), (187, 0.16), (223, 0.27))):
        mono_voice = make_voice(n, pitch, phase)
        voices.append(np.column_stack([
            (1.0 - 0.1 * channel) * mono_voice for channel in range(channels)]))
    voice_a, voice_b, voice_c = voices

    write_wav(directory / "a.wav", common + voice_a)
    write_wav(directory / "b.wav", gain_b * shift(common, offset_b) + voice_b)
    write_wav(directory / "third.wav", gain_c * shift(common, offset_c) + voice_c)
    return SyntheticScene(directory=directory, sample_rate=SAMPLE_RATE, common=common,
                          voice_a=voice_a, voice_b=voice_b, voice_c=voice_c,
                          offset_b=offset_b, offset_c=offset_c,
                          gain_b=gain_b, gain_c=gain_c)


@pytest.fixture(scope="session")
def scene(tmp_path_factory) -> SyntheticScene:
    """Jeu standard de 6 s, stéréo, réutilisé par plusieurs tests."""
    return build_scene(tmp_path_factory.mktemp("scene"))


@pytest.fixture(scope="session")
def extracted(scene, tmp_path_factory):
    """Résultat d'une extraction avec les réglages par défaut, calculé une seule fois."""
    from ummahverse_audio.extract import ExtractOptions, run_extract

    out_dir = tmp_path_factory.mktemp("extract")
    result = run_extract(ExtractOptions(input_a=scene.a, input_b=scene.b, out_dir=out_dir))
    return result


@pytest.fixture(scope="session")
def official_fixtures(tmp_path_factory) -> Path:
    """Jeu produit par `scripts/generate_fixtures.py`, tel que livré dans le dépôt."""
    directory = tmp_path_factory.mktemp("official")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_fixtures.py"),
                    str(directory)], check=True, stdout=subprocess.DEVNULL)
    return directory
