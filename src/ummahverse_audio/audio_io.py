"""Accès aux signaux décodés avec une mémoire bornée.

Chaque entrée est décodée une fois en PCM float32 dans un fichier temporaire, puis
lue par `numpy.memmap`. Un film de deux heures n'est donc jamais chargé en RAM :
le pipeline travaille par blocs et le système gère le cache disque.
"""

from __future__ import annotations

import mmap
import os
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
#: Durée des tranches de décimation : borne la mémoire du filtrage anti-repliement.
ANALYSIS_CHUNK_SECONDS = 10


def release_pages(array: np.ndarray, first_byte: int, last_byte: int) -> None:
    """Rend au système les pages déjà consommées d'un `numpy.memmap`.

    Sans cela, le pic de mémoire résidente d'un film croît avec sa durée : les pages
    des PCM intermédiaires restent dans le cache tant que la cartographie vit. Ces
    pages sont adossées à un fichier, donc réclamables par le système — le processus
    ne tombe pas sans cette libération, il occupe simplement toute la RAM
    disponible. `MADV_DONTNEED` sur une cartographie partagée ne perd aucune écriture
    (les pages modifiées appartiennent au cache de pages et sont réécrites par le
    noyau) ; l'appel est sans effet là où l'avis n'existe pas, par exemple sous
    Windows.
    """
    memory = getattr(array, "_mmap", None)
    advice = getattr(mmap, "MADV_DONTNEED", None)
    if memory is None or advice is None or not hasattr(memory, "madvise"):
        return
    granularity = mmap.ALLOCATIONGRANULARITY
    start = max((first_byte // granularity) * granularity, 0)
    length = min(last_byte, len(memory)) - start
    if length <= 0:
        return
    try:
        memory.madvise(advice, start, length)
    except (OSError, ValueError):  # avis refusé : ce n'est qu'une optimisation
        pass


#: Variable d'environnement imposant l'emplacement des fichiers temporaires.
ENV_WORKSPACE = "UMMAHVERSE_WORKSPACE"


def default_workspace_root() -> Path | None:
    """Racine des fichiers temporaires : `UMMAHVERSE_WORKSPACE`, sinon le dossier système.

    Les scripts `run.*` pointent cette variable vers `.cache/workspace/` dans le dossier
    du projet, pour que supprimer ce dossier ne laisse rien derrière lui.
    """
    configured = os.environ.get(ENV_WORKSPACE, "").strip()
    if not configured:
        return None
    root = Path(configured)
    root.mkdir(parents=True, exist_ok=True)
    return root


class Workspace:
    """Répertoire temporaire pour les PCM intermédiaires et les accumulateurs."""

    def __init__(self, base: Path | None = None) -> None:
        base = base or default_workspace_root()
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
    _released: int = field(default=0, init=False, repr=False)

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

    def release_until(self, frame: int) -> None:
        """Libère les pages des trames antérieures à `frame`, déjà traitées.

        Seule la tranche nouvellement consommée est signalée, pour ne pas reparcourir
        la table des pages à chaque bloc.
        """
        frame = max(frame, 0)
        if frame <= self._released:
            return
        stride = self.channels * 4
        release_pages(self.data, self._released * stride, frame * stride)
        self._released = frame

    def release_all(self) -> None:
        """Libère tout, et réarme la libération progressive pour une nouvelle passe."""
        release_pages(self.data, 0, self.frames * self.channels * 4)
        self._released = 0

    def analysis_track(self, rate: int = ANALYSIS_RATE) -> tuple[np.ndarray, int]:
        """Piste mono décimée servant **uniquement** à l'alignement.

        La somme des canaux n'est jamais écrite dans une sortie : les canaux
        restent séparés et dans leur ordre d'origine pour tout le traitement.
        """
        factor = max(int(round(self.sample_rate / rate)), 1)
        effective_rate = self.sample_rate / factor
        if factor == 1:
            chunk = ANALYSIS_CHUNK_SECONDS * self.sample_rate
            pieces = []
            for start in range(0, self.frames, chunk):
                pieces.append(np.asarray(self.data[start:start + chunk],
                                         dtype=np.float32).mean(axis=1))
                self.release_until(start)
            self.release_all()
            track = (np.concatenate(pieces) if pieces
                     else np.zeros(0, dtype=np.float32))
            return track, int(round(effective_rate))

        # Tranches courtes (mémoire bornée quelle que soit la durée) et chevauchement
        # multiple du facteur : la grille décimée reste alignée et les transitoires du
        # filtre anti-repliement sont rognés au lieu d'être laissés dans la piste.
        chunk = ANALYSIS_CHUNK_SECONDS * self.sample_rate - (
            (ANALYSIS_CHUNK_SECONDS * self.sample_rate) % factor)
        overlap = 64 * factor
        pieces = []
        for start in range(0, self.frames, chunk):
            stop = min(start + chunk, self.frames)
            low = max(start - overlap, 0)
            high = min(stop + overlap, self.frames)
            mono = np.asarray(self.data[low:high], dtype=np.float64).mean(axis=1)
            if mono.size <= 27:  # trop court pour le filtre anti-repliement
                decimated = mono[::factor]
                head = tail = 0
            else:
                decimated = decimate(mono, factor, ftype="fir", zero_phase=True)
                head = (start - low) // factor
                tail = (high - stop) // factor
            useful = decimated[head:decimated.size - tail] if tail else decimated[head:]
            pieces.append(np.asarray(useful, dtype=np.float32))
            self.release_until(low)
        track = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
        self.release_all()  # l'analyse vient de parcourir tout le fichier
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
