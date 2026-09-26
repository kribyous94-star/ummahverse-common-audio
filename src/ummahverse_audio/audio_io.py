"""Accès aux signaux décodés avec une mémoire bornée.

Chaque entrée est décodée une fois en PCM float32 dans un fichier temporaire, puis
lue par `numpy.memmap`. Un film de deux heures n'est donc jamais chargé en RAM :
le pipeline travaille par blocs et le système gère le cache disque.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.signal import decimate

from .errors import IncompatibleLayoutError
from .ffmpeg_tools import AudioStreamInfo, decode_to_raw, probe_audio_streams, select_stream

#: Fréquence de travail par défaut (documentée dans les métadonnées).
DEFAULT_SAMPLE_RATE = 48_000
#: Fréquence de la piste d'analyse servant à l'alignement grossier.
ANALYSIS_RATE = 4_000


class Workspace:
    """Répertoire temporaire pour les PCM intermédiaires et les accumulateurs."""

    def __init__(self, base: Path | None = None) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ummahverse-", dir=str(base) if base else None))

    def file(self, name: str) -> Path:
        return self.root / name

    def memmap(self, name: str, shape: tuple[int, ...]) -> np.memmap:
        """Crée un tableau float32 sur disque, initialisé à zéro."""
        return np.memmap(self.file(name), dtype=np.float32, mode="w+", shape=shape)

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


@dataclass
class DecodedAudio:
    """Un flux audio décodé, accessible par blocs."""

    source: Path
    stream: AudioStreamInfo
    sample_rate: int
    channels: int
    frames: int
    raw_path: Path
    resampled: bool
    data: np.memmap = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.data = np.memmap(self.raw_path, dtype=np.float32, mode="r",
                              shape=(self.frames, self.channels))

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate

    def read(self, start: int, length: int) -> np.ndarray:
        """Lit `length` trames à partir de `start`, en complétant par des zéros aux bords."""
        out = np.zeros((length, self.channels), dtype=np.float32)
        lo = max(start, 0)
        hi = min(start + length, self.frames)
        if hi > lo:
            out[lo - start:hi - start] = self.data[lo:hi]
        return out

    def analysis_track(self, rate: int = ANALYSIS_RATE) -> tuple[np.ndarray, int]:
        """Piste mono décimée servant **uniquement** à l'alignement.

        La somme des canaux n'est jamais écrite dans une sortie : les canaux
        restent séparés et dans leur ordre d'origine pour tout le traitement.
        """
        factor = max(int(round(self.sample_rate / rate)), 1)
        effective_rate = self.sample_rate / factor
        chunk = self.sample_rate * 60
        pieces: list[np.ndarray] = []
        for start in range(0, self.frames, chunk):
            block = np.asarray(self.data[start:start + chunk], dtype=np.float64)
            mono = block.mean(axis=1)
            if factor > 1:
                if mono.size <= 27:  # trop court pour le filtre anti-repliement
                    mono = mono[::factor]
                else:
                    mono = decimate(mono, factor, ftype="fir", zero_phase=True)
            pieces.append(np.asarray(mono, dtype=np.float32))
        track = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
        return track, int(round(effective_rate))


def load_input(path: Path, workspace: Workspace, name: str,
               requested_stream: int | None = None,
               target_rate: int | None = None) -> DecodedAudio:
    """Sonde, sélectionne le flux et décode une entrée dans l'espace de travail."""
    path = Path(path)
    streams = probe_audio_streams(path)
    stream = select_stream(path, streams, requested_stream)
    rate = target_rate or stream.sample_rate or DEFAULT_SAMPLE_RATE
    raw_path = workspace.file(f"{name}.f32")
    frames = decode_to_raw(path, stream, rate, raw_path)
    return DecodedAudio(
        source=path, stream=stream, sample_rate=rate, channels=stream.channels,
        frames=frames, raw_path=raw_path, resampled=rate != stream.sample_rate,
    )


def choose_common_rate(streams: list[AudioStreamInfo], override: int | None = None) -> int:
    """Fréquence de travail commune : identique si les sources le sont, sinon la plus élevée."""
    if override:
        return int(override)
    rates = {s.sample_rate for s in streams if s.sample_rate}
    if not rates:
        return DEFAULT_SAMPLE_RATE
    return max(rates)


def check_layout_compatibility(a: AudioStreamInfo, b: AudioStreamInfo,
                               allow_unknown_layout: bool = False) -> list[str]:
    """Vérifie l'agencement des canaux. Refuse plutôt que de faire un downmix implicite.

    Retourne la liste des avertissements non bloquants.
    """
    warnings: list[str] = []
    layout_a, layout_b = a.effective_layout, b.effective_layout
    if a.channels != b.channels:
        raise IncompatibleLayoutError(
            f"Nombres de canaux différents : {a.channels} ({layout_a}) "
            f"contre {b.channels} ({layout_b}).",
            "Le POC ne mélange jamais les canaux automatiquement (pas de downmix 5.1 -> stéréo). "
            "Fournissez deux pistes de même agencement, ou préparez-les vous-même avec FFmpeg.",
        )
    if "inconnu" in (layout_a, layout_b):
        if not allow_unknown_layout:
            raise IncompatibleLayoutError(
                f"Agencement de canaux inconnu pour {a.channels} canaux "
                f"({layout_a} / {layout_b}).",
                "Au-delà de la stéréo, l'ordre des canaux ne peut pas être déduit du nombre "
                "de canaux. Relancez avec --allow-unknown-layout pour apparier les canaux "
                "dans l'ordre du fichier, ou déclarez l'agencement avec FFmpeg.",
            )
        warnings.append(
            f"Agencement inconnu ({layout_a} / {layout_b}) : canaux appariés dans l'ordre "
            "du fichier, sans vérification du mapping."
        )
    elif layout_a != layout_b:
        warnings.append(
            f"Agencements déclarés différents ({layout_a} / {layout_b}) "
            f"pour un même nombre de canaux : les canaux sont appariés dans l'ordre du fichier."
        )
    if a.sample_rate != b.sample_rate:
        warnings.append(
            f"Fréquences d'échantillonnage différentes ({a.sample_rate} / {b.sample_rate} Hz) : "
            "conversion soxr vers la fréquence de travail."
        )
    for stream, tag in ((a, "A"), (b, "B")):
        if not stream.lossless:
            warnings.append(
                f"Entrée {tag} en codec avec perte ({stream.codec}) : les artefacts de codage "
                "diffèrent d'une langue à l'autre et dégradent la séparation."
            )
    return warnings
