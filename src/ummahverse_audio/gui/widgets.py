"""Composants réutilisables de l'interface : champs de fichier, choix de flux, journal."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QWidget)

from ..errors import UmmahAudioError
from ..ffmpeg_tools import AudioStreamInfo, probe_audio_streams

#: Filtres des sélecteurs de fichiers (les conteneurs vidéo sont acceptés).
AUDIO_FILTER = ("Audio et vidéo (*.wav *.flac *.mka *.mkv *.mp4 *.mov *.m4a *.aac *.ac3 "
                "*.eac3 *.dts *.mp3 *.ogg *.opus *.avi *.ts);;Tous les fichiers (*)")
WAV_FILTER = "WAV (*.wav);;Tous les fichiers (*)"
JSON_FILTER = "JSON (*.json);;Tous les fichiers (*)"


class PathField(QWidget):
    """Champ de chemin avec bouton de sélection natif et glisser-déposer."""

    changed = Signal(str)

    def __init__(self, label: str, mode: str = "open",
                 name_filter: str = AUDIO_FILTER, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = mode                     # "open", "save" ou "directory"
        self._filter = name_filter
        self._label = QLabel(label)
        self._label.setMinimumWidth(170)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Glissez un fichier ici ou utilisez « Parcourir… »"
                                     if mode != "directory"
                                     else "Glissez un dossier ici ou utilisez « Parcourir… »")
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(self.changed.emit)
        button = QPushButton("Parcourir…")
        button.clicked.connect(self._browse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)
        layout.addWidget(self.edit, 1)
        layout.addWidget(button)
        self.setAcceptDrops(True)

    @property
    def path(self) -> Path | None:
        text = self.edit.text().strip()
        return Path(text) if text else None

    def set_path(self, path: Path | str) -> None:
        self.edit.setText(str(path))

    def _browse(self) -> None:
        start = str(self.path.parent if self.path and self.path.parent.exists()
                    else Path.home())
        if self._mode == "directory":
            chosen = QFileDialog.getExistingDirectory(self, self._label.text(), start)
        elif self._mode == "save":
            chosen, _ = QFileDialog.getSaveFileName(self, self._label.text(), start,
                                                    self._filter)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, self._label.text(), start,
                                                    self._filter)
        if chosen:
            self.set_path(chosen)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 (API Qt)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 (API Qt)
        urls = event.mimeData().urls()
        if not urls:
            return
        dropped = Path(urls[0].toLocalFile())
        if self._mode == "directory" and dropped.is_file():
            dropped = dropped.parent
        self.set_path(dropped)
        event.acceptProposedAction()


class StreamSelector(QWidget):
    """Liste des flux audio d'un fichier ; le choix reste explicite."""

    changed = Signal()

    def __init__(self, label: str = "Flux audio", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._streams: list[AudioStreamInfo] = []
        self._label = QLabel(label)
        self._label.setMinimumWidth(170)
        self.combo = QComboBox()
        self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo.currentIndexChanged.connect(lambda _: self.changed.emit())
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)
        layout.addWidget(self.combo, 1)
        self.clear()

    def clear(self) -> None:
        self._streams = []
        self.combo.clear()
        self.combo.addItem("—", None)
        self.combo.setEnabled(False)
        self.status.setText("")

    def load(self, path: Path | None) -> str:
        """Sonde `path` et remplit la liste. Retourne un message d'état."""
        self.clear()
        if path is None or not path.exists():
            return ""
        try:
            self._streams = probe_audio_streams(path)
        except UmmahAudioError as exc:
            self.status.setText(exc.user_message())
            return exc.user_message()
        self.combo.clear()
        for stream in self._streams:
            self.combo.addItem(stream.label(), stream.audio_index)
        self.combo.setEnabled(len(self._streams) > 1)
        if len(self._streams) > 1:
            message = (f"{len(self._streams)} flux audio : choisissez explicitement "
                       "lequel utiliser.")
        else:
            message = f"Un seul flux audio : {self._streams[0].label()}"
        self.status.setText(message)
        return message

    @property
    def selected_index(self) -> int | None:
        if not self._streams:
            return None
        value = self.combo.currentData()
        return int(value) if value is not None else None

    @property
    def streams(self) -> list[AudioStreamInfo]:
        return list(self._streams)


class LogView(QPlainTextEdit):
    """Journal en lecture seule : étapes, avertissements, mesures."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(4000)
        self.setPlaceholderText("Les étapes, avertissements et mesures apparaissent ici.")

    def append(self, message: str) -> None:
        self.appendPlainText(message)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


def open_in_file_manager(path: Path) -> bool:
    """Ouvre un dossier (ou le dossier parent d'un fichier) dans l'explorateur système."""
    target = path if path.is_dir() else path.parent
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


def open_document(path: Path) -> bool:
    """Ouvre un fichier avec l'application associée du système."""
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def elide(text: str, limit: int = 110) -> str:
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


ALIGN_TOP = Qt.AlignmentFlag.AlignTop
