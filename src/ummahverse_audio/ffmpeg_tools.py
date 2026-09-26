"""Découverte de FFmpeg, inspection des flux, décodage sans perte et écriture WAV.

Tous les appels passent par une liste d'arguments (`subprocess` sans `shell=True`),
ce qui rend sûrs les chemins contenant espaces, accents, caractères Unicode et
lettres de lecteur Windows. Aucun downmix ni changement de loudness n'est demandé
à FFmpeg : seule la fréquence d'échantillonnage peut être convertie, explicitement.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .errors import FfmpegFailedError, FfmpegMissingError, StreamSelectionError

#: Variables d'environnement consultées avant le PATH.
ENV_FFMPEG = "UMMAHVERSE_FFMPEG"
ENV_FFPROBE = "UMMAHVERSE_FFPROBE"

_WINDOWS_CANDIDATES = (
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    r"C:\Program Files (x86)\ffmpeg\bin",
    r"C:\ProgramData\chocolatey\bin",
)

_INSTALL_HINT = (
    "Ubuntu : sudo apt install ffmpeg\n"
    "Windows : winget install --id Gyan.FFmpeg (puis rouvrir le terminal), "
    "ou définir UMMAHVERSE_FFMPEG / UMMAHVERSE_FFPROBE vers les exécutables."
)


def _resolve_tool(name: str, env_var: str) -> Path:
    override = os.environ.get(env_var)
    if override:
        candidate = Path(override)
        if candidate.is_dir():
            candidate = candidate / (name + (".exe" if os.name == "nt" else ""))
        if not candidate.is_file():
            raise FfmpegMissingError(
                f"{env_var} pointe vers « {override} » mais aucun exécutable n'y est trouvé.",
                _INSTALL_HINT,
            )
        return candidate
    found = shutil.which(name)
    if found:
        return Path(found)
    if os.name == "nt":
        for folder in _WINDOWS_CANDIDATES:
            candidate = Path(folder) / f"{name}.exe"
            if candidate.is_file():
                return candidate
    raise FfmpegMissingError(
        f"« {name} » est introuvable dans le PATH.",
        _INSTALL_HINT,
    )


def find_ffmpeg() -> Path:
    return _resolve_tool("ffmpeg", ENV_FFMPEG)


def find_ffprobe() -> Path:
    return _resolve_tool("ffprobe", ENV_FFPROBE)


def ffmpeg_version() -> str:
    """Première ligne de `ffmpeg -version`, pour les métadonnées et le diagnostic."""
    out = _run([str(find_ffmpeg()), "-hide_banner", "-version"])
    return out.splitlines()[0].strip() if out else "inconnu"


def _no_window_kwargs() -> dict:
    """Empêche l'ouverture d'une console Windows quand l'interface graphique appelle FFmpeg."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def _run(args: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            list(args),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            **_no_window_kwargs(),
        )
    except OSError as exc:  # exécutable supprimé entre-temps, droits, ...
        raise FfmpegFailedError(f"Impossible d'exécuter {args[0]} : {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        raise FfmpegFailedError(
            f"{Path(args[0]).name} a échoué (code {completed.returncode}).",
            stderr[-1500:],
        )
    return completed.stdout.decode("utf-8", "replace")


@dataclass(frozen=True)
class AudioStreamInfo:
    """Un flux audio tel que décrit par ffprobe."""

    audio_index: int
    """Index parmi les flux audio du fichier (0 = premier) ; c'est la valeur de `--stream-*`."""
    stream_index: int
    """Index absolu du flux dans le conteneur, à titre informatif."""
    codec: str
    sample_rate: int
    channels: int
    channel_layout: str
    sample_format: str
    duration_s: float | None
    language: str
    title: str

    @property
    def effective_layout(self) -> str:
        """Agencement retenu pour le traitement.

        Un WAV nu ne déclare aucun agencement. En mono et en stéréo la convention est
        sans ambiguïté, donc on la complète. Au-delà de deux canaux, l'ordre réel est
        ambigu (5.1 contre 5.1(side), 6.0, mappings personnalisés) : on reste sur
        « inconnu » et l'appelant exige un choix explicite.
        """
        if self.channel_layout not in ("inconnu", "", "unknown"):
            return self.channel_layout
        if self.channels == 1:
            return "mono"
        if self.channels == 2:
            return "stereo"
        return "inconnu"

    @property
    def lossless(self) -> bool:
        return self.codec in {
            "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le",
            "pcm_s16be", "pcm_s24be", "pcm_s32be", "flac", "alac", "truehd",
            "mlp", "wavpack", "tta",
        }

    def label(self) -> str:
        parts = [f"#{self.audio_index}", self.codec, f"{self.sample_rate} Hz",
                 f"{self.channels} canaux ({self.effective_layout})"]
        if self.language and self.language != "und":
            parts.append(self.language)
        if self.title:
            parts.append(self.title)
        if not self.lossless:
            parts.append("avec perte")
        return " — ".join(parts)


def probe_audio_streams(path: Path) -> list[AudioStreamInfo]:
    """Liste les flux audio de `path`. Lève une erreur claire si le fichier est illisible."""
    path = Path(path)
    if not path.is_file():
        raise FfmpegFailedError(f"Fichier introuvable : {path}")
    raw = _run([
        str(find_ffprobe()), "-hide_banner", "-v", "error",
        "-print_format", "json", "-show_streams", "-select_streams", "a",
        str(path),
    ])
    try:
        streams = json.loads(raw).get("streams", [])
    except json.JSONDecodeError as exc:
        raise FfmpegFailedError(f"Réponse ffprobe illisible pour {path.name} : {exc}") from exc

    result: list[AudioStreamInfo] = []
    for audio_index, stream in enumerate(streams):
        tags = {k.lower(): v for k, v in (stream.get("tags") or {}).items()}
        duration = stream.get("duration")
        result.append(AudioStreamInfo(
            audio_index=audio_index,
            stream_index=int(stream.get("index", audio_index)),
            codec=str(stream.get("codec_name", "inconnu")),
            sample_rate=int(stream.get("sample_rate", 0) or 0),
            channels=int(stream.get("channels", 0) or 0),
            channel_layout=str(stream.get("channel_layout") or "inconnu"),
            sample_format=str(stream.get("sample_fmt") or "inconnu"),
            duration_s=float(duration) if duration not in (None, "N/A") else None,
            language=str(tags.get("language", "")),
            title=str(tags.get("title", "")),
        ))
    if not result:
        raise StreamSelectionError(
            f"Aucun flux audio dans {path.name}.",
            "Vérifiez le fichier, ou fournissez la piste audio séparément.",
        )
    return result


def select_stream(path: Path, streams: Sequence[AudioStreamInfo],
                  requested: int | None) -> AudioStreamInfo:
    """Choisit un flux. Plusieurs flux sans `requested` -> refus, jamais de choix silencieux."""
    if requested is not None:
        for stream in streams:
            if stream.audio_index == requested:
                return stream
        available = ", ".join(str(s.audio_index) for s in streams)
        raise StreamSelectionError(
            f"Le flux audio #{requested} n'existe pas dans {Path(path).name}.",
            f"Flux audio disponibles : {available}.",
        )
    if len(streams) == 1:
        return streams[0]
    listing = "\n".join("  " + s.label() for s in streams)
    raise StreamSelectionError(
        f"{Path(path).name} contient {len(streams)} flux audio : le choix doit être explicite.",
        f"Ajoutez --stream-a/--stream-b/--stream avec l'un de ces index :\n{listing}",
    )


def decode_to_raw(path: Path, stream: AudioStreamInfo, target_rate: int,
                  destination: Path) -> int:
    """Décode un flux en PCM float32 entrelacé dans `destination`. Retourne le nombre de trames.

    Aucun `-ac` n'est passé : le nombre et l'ordre des canaux du flux source sont
    conservés. La seule conversion possible est la fréquence d'échantillonnage,
    faite avec soxr et signalée dans les métadonnées.
    """
    args = [
        str(find_ffmpeg()), "-hide_banner", "-nostdin", "-v", "error",
        "-i", str(Path(path)),
        "-map", f"0:a:{stream.audio_index}",
        "-vn", "-sn", "-dn",
    ]
    if target_rate and stream.sample_rate and target_rate != stream.sample_rate:
        args += ["-af", f"aresample=osr={target_rate}:resampler=soxr:precision=28"]
    args += ["-f", "f32le", "-acodec", "pcm_f32le", str(destination)]

    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(args)
    size = destination.stat().st_size
    frame_bytes = 4 * stream.channels
    if frame_bytes == 0 or size < frame_bytes:
        raise FfmpegFailedError(
            f"Le décodage de {Path(path).name} n'a produit aucun échantillon utilisable."
        )
    if size % frame_bytes:
        # Tronque une trame partielle plutôt que de décaler tous les canaux.
        with destination.open("r+b") as handle:
            handle.truncate(size - (size % frame_bytes))
        size -= size % frame_bytes
    return size // frame_bytes


class WavWriter:
    """Écriture WAV en flux via FFmpeg (mémoire bornée, RF64 automatique si > 4 Gio)."""

    def __init__(self, destination: Path, sample_rate: int, channels: int,
                 channel_layout: str = "", sample_format: str = "float32") -> None:
        codec = {"float32": "pcm_f32le", "pcm24": "pcm_s24le", "pcm16": "pcm_s16le"}
        if sample_format not in codec:
            raise ValueError(f"Format WAV inconnu : {sample_format}")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        args = [
            str(find_ffmpeg()), "-hide_banner", "-nostdin", "-v", "error", "-y",
            "-f", "f32le", "-ar", str(sample_rate), "-ac", str(channels),
        ]
        if channel_layout and channel_layout != "inconnu":
            args += ["-channel_layout", channel_layout]
        args += ["-i", "pipe:0", "-c:a", codec[sample_format],
                 "-rf64", "auto", str(destination)]
        self.destination = destination
        self._process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, **_no_window_kwargs(),
        )

    def write(self, block) -> None:
        """Écrit un bloc `(trames, canaux)` float32."""
        import numpy as np

        data = np.ascontiguousarray(np.asarray(block, dtype=np.float32))
        assert self._process.stdin is not None
        try:
            self._process.stdin.write(data.tobytes())
        except BrokenPipeError as exc:
            raise FfmpegFailedError(
                f"FFmpeg a fermé la sortie {self.destination.name} pendant l'écriture.",
                self._drain_stderr(),
            ) from exc

    def close(self) -> None:
        if self._process.poll() is None and self._process.stdin is not None:
            try:
                self._process.stdin.close()
            except BrokenPipeError:
                pass
        stderr = self._drain_stderr()
        code = self._process.wait()
        if code != 0:
            raise FfmpegFailedError(
                f"Échec de l'écriture de {self.destination.name} (code {code}).", stderr)

    def abort(self) -> None:
        """Interrompt l'écriture et supprime le fichier partiel (annulation, erreur)."""
        if self._process.poll() is None:
            self._process.kill()
        self._process.wait()
        self.destination.unlink(missing_ok=True)

    def _drain_stderr(self) -> str:
        if self._process.stderr is None:
            return ""
        return self._process.stderr.read().decode("utf-8", "replace").strip()[-1500:]

    def __enter__(self) -> "WavWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()


def describe_environment() -> dict:
    """Environnement d'exécution, journalisé dans les métadonnées de sortie."""
    try:
        version = ffmpeg_version()
    except (FfmpegMissingError, FfmpegFailedError) as exc:
        version = f"indisponible : {exc}"
    return {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "ffmpeg": version,
    }
