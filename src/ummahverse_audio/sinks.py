"""Assemblage des blocs et écriture des sorties, à mémoire bornée.

Deux repères temporels coexistent :

* le repère de A, dans lequel le commun est estimé : les blocs arrivent dans
  l'ordre, `StreamingCommonSink` les recombine et écrit `common.wav` et `a_only.wav`
  au fil de l'eau ;
* le repère de B, où le commun rendu peut se chevaucher ou laisser un trou quand le
  décalage local change : `AccumulatorSink` l'accumule sur disque, puis calcule
  `b_only.wav` en une passe.

Dans les deux cas la sortie « only » vaut exactement `entrée − commun rendu`, donc la
somme reconstitue l'entrée à l'erreur numérique près, par construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .ffmpeg_tools import WavWriter

#: Sous ce poids cumulé, la zone est déclarée non traitée (commun nul, résidu = entrée).
WEIGHT_FLOOR = 0.05
#: Taille des tranches d'écriture finale, en trames.
FLUSH_CHUNK = 1 << 18


@dataclass
class UntreatedTracker:
    """Regroupe les trames sans commun rendu en intervalles lisibles."""

    sample_rate: int
    ranges: list[tuple[int, int]] = field(default_factory=list)

    def add_mask(self, start: int, mask: np.ndarray) -> None:
        if not mask.any():
            return
        flags = np.concatenate(([0], mask.astype(np.int8), [0]))
        edges = np.flatnonzero(np.diff(flags))
        for lo, hi in zip(edges[::2], edges[1::2]):
            self._append(start + int(lo), start + int(hi))

    def _append(self, lo: int, hi: int) -> None:
        if self.ranges and lo - self.ranges[-1][1] <= 1:
            self.ranges[-1] = (self.ranges[-1][0], hi)
        else:
            self.ranges.append((lo, hi))

    def as_list(self, minimum_frames: int = 1) -> list[dict]:
        return [
            {"start_s": round(lo / self.sample_rate, 3),
             "end_s": round(hi / self.sample_rate, 3)}
            for lo, hi in self.ranges if hi - lo >= minimum_frames
        ]

    @property
    def total_frames(self) -> int:
        return sum(hi - lo for lo, hi in self.ranges)


class StreamingCommonSink:
    """Recombine les blocs du repère de A et écrit `common.wav` puis `a_only.wav`."""

    def __init__(self, source, common_path: Path, residual_path: Path,
                 sample_format: str = "float32") -> None:
        self._source = source
        self._channels = source.channels
        self._total = source.frames
        self._common = WavWriter(common_path, source.sample_rate, source.channels,
                                 source.stream.effective_layout, sample_format)
        try:
            self._residual = WavWriter(residual_path, source.sample_rate, source.channels,
                                       source.stream.effective_layout, sample_format)
        except Exception:
            self._common.abort()
            raise
        self._base = 0
        self._written = 0
        self._acc = np.zeros((0, self._channels), dtype=np.float64)
        self._weights = np.zeros(0, dtype=np.float64)
        self.untreated = UntreatedTracker(source.sample_rate)
        self.peak_common = 0.0
        self.peak_residual = 0.0
        self.reconstruction_error = 0.0

    def add(self, start: int, block: np.ndarray, weights: np.ndarray) -> None:
        if start < self._base:
            raise ValueError("Les blocs du repère de A doivent arriver dans l'ordre.")
        self._flush_until(start)
        length = block.shape[0]
        self._ensure(length)
        self._acc[:length] += block * weights[:, None]
        self._weights[:length] += weights

    def finish(self) -> None:
        try:
            self._flush_until(self._total)
            if self._written < self._total:  # aucun bloc n'a couvert la fin
                self._write_plain(self._total - self._written)
            self._common.close()
            self._residual.close()
        except Exception:
            self._common.abort()
            self._residual.abort()
            raise

    def abort(self) -> None:
        self._common.abort()
        self._residual.abort()

    def _ensure(self, length: int) -> None:
        if self._acc.shape[0] >= length:
            return
        extra = length - self._acc.shape[0]
        self._acc = np.concatenate(
            (self._acc, np.zeros((extra, self._channels), dtype=np.float64)))
        self._weights = np.concatenate((self._weights, np.zeros(extra, dtype=np.float64)))

    def _flush_until(self, limit: int) -> None:
        limit = min(limit, self._total)
        count = limit - self._base
        if count <= 0:
            return
        count = min(count, self._acc.shape[0])
        if count <= 0:
            return
        weights = self._weights[:count]
        usable = weights >= WEIGHT_FLOOR
        common = np.zeros((count, self._channels), dtype=np.float64)
        np.divide(self._acc[:count], weights[:, None], out=common,
                  where=usable[:, None])
        self.untreated.add_mask(self._base, ~usable)
        self._emit(common)
        self._base += count
        self._acc = self._acc[count:]
        self._weights = self._weights[count:]

    def _write_plain(self, count: int) -> None:
        """Zone non couverte par un bloc : commun nul, résidu identique à l'entrée."""
        self.untreated.add_mask(self._base, np.ones(count, dtype=bool))
        self._emit(np.zeros((count, self._channels), dtype=np.float64))
        self._base += count

    def _emit(self, common: np.ndarray) -> None:
        count = common.shape[0]
        original = self._source.read(self._base, count).astype(np.float64)
        residual = original - common
        self.peak_common = max(self.peak_common, float(np.abs(common).max(initial=0.0)))
        self.peak_residual = max(self.peak_residual, float(np.abs(residual).max(initial=0.0)))
        error = float(np.abs(common + residual - original).max(initial=0.0))
        self.reconstruction_error = max(self.reconstruction_error, error)
        self._common.write(common.astype(np.float32))
        self._residual.write(residual.astype(np.float32))
        self._written += count


class AccumulatorSink:
    """Accumule le commun rendu dans un repère quelconque, sur disque."""

    def __init__(self, workspace, name: str, frames: int, channels: int,
                 sample_rate: int) -> None:
        self.frames = frames
        self.channels = channels
        self.sample_rate = sample_rate
        self._acc = workspace.memmap(f"{name}.acc", (frames, channels))
        self._weights = workspace.memmap(f"{name}.w", (frames,))
        self._released = 0

    def add(self, start: int, block: np.ndarray, weights: np.ndarray) -> None:
        lo = max(start, 0)
        hi = min(start + block.shape[0], self.frames)
        if hi <= lo:
            return
        offset = lo - start
        length = hi - lo
        piece = (block[offset:offset + length]
                 * weights[offset:offset + length, None]).astype(np.float32)
        self._acc[lo:hi] += piece
        self._weights[lo:hi] += weights[offset:offset + length].astype(np.float32)

    def release_until(self, frame: int) -> None:
        """Libère les pages de l'accumulateur derrière la position d'écriture.

        L'accumulation progresse dans l'ordre à la dérive près, donc les trames très en
        arrière sont définitives. `MADV_DONTNEED` sur une cartographie partagée ne perd
        aucune écriture : les pages modifiées restent celles du cache de fichiers.
        """
        from .audio_io import release_pages

        frame = max(min(frame, self.frames), 0)
        if frame <= self._released:
            return
        release_pages(self._acc, self._released * self.channels * 4,
                      frame * self.channels * 4)
        release_pages(self._weights, self._released * 4, frame * 4)
        self._released = frame

    def iter_common(self, chunk: int = FLUSH_CHUNK):
        """Parcourt le commun accumulé par tranches : `(start, common, usable)`.

        Les trames dont le poids cumulé est insuffisant sont rendues à zéro et
        signalées par `usable=False`, jamais extrapolées.
        """
        self._released = 0
        for start in range(0, self.frames, chunk):
            stop = min(start + chunk, self.frames)
            weights = np.asarray(self._weights[start:stop], dtype=np.float64)
            usable = weights >= WEIGHT_FLOOR
            common = np.zeros((stop - start, self.channels), dtype=np.float64)
            np.divide(np.asarray(self._acc[start:stop], dtype=np.float64),
                      weights[:, None], out=common, where=usable[:, None])
            yield start, common, usable
            self.release_until(stop)

    def write_common(self, destination: Path, channel_layout: str,
                     sample_format: str = "float32") -> Path:
        """Écrit le commun adapté seul, utile pour l'écoute de contrôle."""
        writer = WavWriter(destination, self.sample_rate, self.channels,
                           channel_layout, sample_format)
        try:
            for _, common, _ in self.iter_common():
                writer.write(common.astype(np.float32))
            writer.close()
        except Exception:
            writer.abort()
            raise
        return destination

    def write_residual(self, source, destination: Path, sample_format: str = "float32",
                       progress=None) -> tuple[UntreatedTracker, dict]:
        """Écrit `source − commun accumulé` et retourne les zones non traitées."""
        untreated = UntreatedTracker(self.sample_rate)
        peak_residual = 0.0
        peak_common = 0.0
        reconstruction_error = 0.0
        writer = WavWriter(destination, self.sample_rate, self.channels,
                           source.stream.effective_layout, sample_format)
        source.release_all()   # nouvelle passe séquentielle sur la source
        try:
            for start, common, usable in self.iter_common():
                source.release_until(start - 2 * FLUSH_CHUNK)
                if progress is not None:
                    progress.step(start / max(self.frames, 1), f"écriture {destination.name}")
                untreated.add_mask(start, ~usable)
                original = source.read(start, common.shape[0]).astype(np.float64)
                residual = original - common
                peak_common = max(peak_common, float(np.abs(common).max(initial=0.0)))
                peak_residual = max(peak_residual, float(np.abs(residual).max(initial=0.0)))
                reconstruction_error = max(
                    reconstruction_error,
                    float(np.abs(common + residual - original).max(initial=0.0)))
                writer.write(residual.astype(np.float32))
            writer.close()
        except Exception:
            writer.abort()
            raise
        return untreated, {
            "peak_common": peak_common,
            "peak_residual": peak_residual,
            "reconstruction_max_abs_error": reconstruction_error,
        }
