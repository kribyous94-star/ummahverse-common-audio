"""Progression et annulation, partagées par la CLI et l'interface graphique.

Le moteur ne connaît ni Qt ni le terminal : il appelle `Progress.step()` et
`Progress.check_cancelled()`. L'interface fournit une implémentation qui émet
des signaux Qt, la CLI une implémentation qui écrit sur stderr.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, TextIO

from .errors import Cancelled


class Progress:
    """Rapporteur de progression neutre (aucune sortie, jamais annulé)."""

    def stage(self, name: str) -> None:
        """Annonce le début d'une étape nommée."""

    def step(self, fraction: float, message: str = "") -> None:
        """`fraction` dans [0, 1] pour l'opération complète."""

    def log(self, message: str) -> None:
        """Message informatif (avertissement de compatibilité, mesure, ...)."""

    def check_cancelled(self) -> None:
        """Lève `Cancelled` si l'utilisateur a demandé l'arrêt."""


@dataclass
class CancelToken:
    """Drapeau d'annulation partageable entre threads."""

    _event: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass
class CallbackProgress(Progress):
    """Progression déléguée à des callables ; utilisée par l'interface graphique."""

    on_step: Callable[[float, str], None] | None = None
    on_log: Callable[[str], None] | None = None
    token: CancelToken | None = None

    def stage(self, name: str) -> None:
        self.step(-1.0, name)

    def step(self, fraction: float, message: str = "") -> None:
        self.check_cancelled()
        if self.on_step is not None:
            self.on_step(fraction, message)

    def log(self, message: str) -> None:
        if self.on_log is not None:
            self.on_log(message)

    def check_cancelled(self) -> None:
        if self.token is not None and self.token.cancelled:
            raise Cancelled()


@dataclass
class ConsoleProgress(Progress):
    """Progression texte sur stderr, limitée à une ligne rafraîchie."""

    stream: TextIO = field(default_factory=lambda: sys.stderr)
    quiet: bool = False
    min_interval_s: float = 0.2
    _last: float = field(default=0.0, init=False)
    _dirty: bool = field(default=False, init=False)

    def stage(self, name: str) -> None:
        if self.quiet:
            return
        self._newline()
        self.stream.write(f"[étape] {name}\n")
        self.stream.flush()

    def step(self, fraction: float, message: str = "") -> None:
        if self.quiet or fraction < 0:
            return
        now = time.monotonic()
        if now - self._last < self.min_interval_s and fraction < 1.0:
            return
        self._last = now
        self.stream.write(f"\r  {fraction * 100:5.1f} %  {message[:60]:<60}")
        self.stream.flush()
        self._dirty = True

    def log(self, message: str) -> None:
        if self.quiet:
            return
        self._newline()
        self.stream.write(f"  {message}\n")
        self.stream.flush()

    def _newline(self) -> None:
        if self._dirty:
            self.stream.write("\n")
            self._dirty = False


class SubProgress(Progress):
    """Sous-intervalle d'une progression parente, pour composer les étapes."""

    def __init__(self, parent: Progress, start: float, end: float) -> None:
        self._parent = parent
        self._start = start
        self._end = end

    def stage(self, name: str) -> None:
        self._parent.stage(name)

    def step(self, fraction: float, message: str = "") -> None:
        if fraction < 0:
            self._parent.step(-1.0, message)
            return
        span = self._end - self._start
        self._parent.step(self._start + span * min(max(fraction, 0.0), 1.0), message)

    def log(self, message: str) -> None:
        self._parent.log(message)

    def check_cancelled(self) -> None:
        self._parent.check_cancelled()
