"""Exécution du moteur hors du thread de l'interface.

Le traitement d'un film dure plusieurs minutes : il ne doit jamais bloquer la boucle
d'événements Qt. Chaque opération part dans un `QThread` dédié et ne communique avec
la fenêtre que par signaux ; l'annulation passe par un `CancelToken` partagé, vérifié
à chaque bloc, ce qui garantit un arrêt propre sans `terminate()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal

from ..errors import Cancelled, UmmahAudioError
from ..progress import CallbackProgress, CancelToken


@dataclass
class JobOutcome:
    """Résultat d'une opération, tel que transmis à la fenêtre."""

    kind: str                 # "extract" ou "subtract"
    result: object | None
    error: str | None = None
    cancelled: bool = False


class Worker(QObject):
    """Encapsule un appel au moteur et relaie sa progression."""

    progressed = Signal(float, str)
    logged = Signal(str)
    finished = Signal(object)

    def __init__(self, kind: str, action: Callable[[CallbackProgress], object],
                 token: CancelToken) -> None:
        super().__init__()
        self._kind = kind
        self._action = action
        self._token = token

    def run(self) -> None:
        progress = CallbackProgress(
            on_step=lambda fraction, message: self.progressed.emit(fraction, message),
            on_log=lambda message: self.logged.emit(message),
            token=self._token,
        )
        try:
            result = self._action(progress)
        except Cancelled:
            self.finished.emit(JobOutcome(self._kind, None, cancelled=True))
        except UmmahAudioError as exc:
            self.finished.emit(JobOutcome(self._kind, None, error=exc.user_message()))
        except Exception as exc:  # défaut imprévu : message lisible plutôt qu'un plantage
            self.finished.emit(JobOutcome(
                self._kind, None,
                error=f"Erreur inattendue ({type(exc).__name__}) : {exc}"))
        else:
            self.finished.emit(JobOutcome(self._kind, result))


class JobRunner(QObject):
    """Cycle de vie du thread de travail.

    Les références au `QThread` et au `Worker` sont conservées jusqu'au signal
    `QThread.finished` : détruire un `QThread` encore en cours d'arrêt fait tomber le
    processus. L'état « occupé » suit en revanche la fin du *travail*, pour que
    l'interface redevienne utilisable dès que le résultat est connu.
    """

    progressed = Signal(float, str)
    logged = Signal(str)
    finished = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: Worker | None = None
        self._retiring: list[QThread] = []
        self._active = False
        self.token = CancelToken()

    @property
    def busy(self) -> bool:
        return self._active

    def start(self, kind: str, action: Callable[[CallbackProgress], object]) -> None:
        if self._active:
            raise RuntimeError("Un traitement est déjà en cours.")
        self.token = CancelToken()
        thread = QThread()
        worker = Worker(kind, action, self.token)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progressed.connect(self.progressed)
        worker.logged.connect(self.logged)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread, self._worker = thread, worker
        self._active = True
        thread.start()

    def cancel(self) -> None:
        self.token.cancel()

    def wait(self, milliseconds: int = 30_000) -> bool:
        """Attend la fin effective des threads (fermeture de fenêtre)."""
        deadline = milliseconds
        ok = True
        for thread in [self._thread, *self._retiring]:
            if thread is not None and thread.isRunning():
                ok = thread.wait(max(deadline, 1)) and ok
        return ok

    def _on_worker_finished(self, outcome: JobOutcome) -> None:
        self._active = False
        thread = self._thread
        if thread is not None and thread not in self._retiring:
            self._retiring.append(thread)
        self._thread = None
        self._worker = None
        self.finished.emit(outcome)

    def _on_thread_finished(self) -> None:
        sender = self.sender()
        if isinstance(sender, QThread):
            if sender in self._retiring:
                self._retiring.remove(sender)
            sender.deleteLater()
