"""Mesure du temps et du pic mémoire, pour documenter le coût réel du traitement."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field


def peak_rss_mb() -> float | None:
    """Pic de mémoire résidente du processus, en Mio, ou None si indisponible."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if not ctypes.windll.psapi.GetProcessMemoryInfo(
                    handle, ctypes.byref(counters), counters.cb):
                return None
            return counters.PeakWorkingSetSize / (1024 * 1024)
        except Exception:
            return None
    try:
        import resource
    except ImportError:
        return None
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux rapporte des Kio, macOS des octets.
    return usage / (1024 * 1024) if sys.platform == "darwin" else usage / 1024


@dataclass
class Stopwatch:
    """Chronomètre avec étapes nommées."""

    start: float = field(default_factory=time.monotonic)
    stages: dict[str, float] = field(default_factory=dict)
    _last: float = field(default_factory=time.monotonic)

    def mark(self, name: str) -> None:
        now = time.monotonic()
        self.stages[name] = round(now - self._last, 3)
        self._last = now

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.start

    def as_dict(self) -> dict:
        payload: dict = {"elapsed_s": round(self.elapsed_s, 3), "stages_s": dict(self.stages)}
        peak = peak_rss_mb()
        if peak is not None:
            payload["peak_rss_mb"] = round(peak, 1)
        return payload
