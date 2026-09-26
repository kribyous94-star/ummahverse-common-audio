"""Interface graphique : construction, sélection de flux, traitement hors thread UI,
annulation propre, et affichage d'une erreur compréhensible.

Ces tests utilisent la plateforme Qt « offscreen » : ils vérifient la logique de
l'interface sans écran, sur Ubuntu comme sur Windows. Ils ne remplacent pas l'essai
manuel décrit dans `docs/manual-verification.md`.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

pytest.importorskip("PySide6", reason="l'extra « gui » n'est pas installé")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ummahverse_audio.gui.app import build_application  # noqa: E402
from ummahverse_audio.gui.main_window import MainWindow  # noqa: E402

from .conftest import SAMPLE_RATE, build_scene, write_wav  # noqa: E402


@pytest.fixture(scope="module")
def application():
    app = build_application([])
    yield app
    app.processEvents()


@pytest.fixture
def window(application):
    window = MainWindow()
    yield window
    window.extract_tab.runner.cancel()
    window.subtract_tab.runner.cancel()
    window.extract_tab.runner.wait(20_000)
    window.subtract_tab.runner.wait(20_000)
    window.close()
    window.deleteLater()
    application.processEvents()


def wait_for(predicate, timeout_ms: int = 120_000) -> bool:
    """Boucle d'événements jusqu'à ce que `predicate` soit vrai : prouve que l'UI tourne."""
    loop = QEventLoop()
    state = {"done": False, "ticks": 0}

    def check() -> None:
        state["ticks"] += 1
        if predicate():
            state["done"] = True
            loop.quit()

    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(25)
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(timeout_ms)
    loop.exec()
    timer.stop()
    return state["done"] and state["ticks"] > 1


def test_window_has_two_operations_in_french(window):
    assert window.tabs.count() == 2
    assert "Deux langues" in window.tabs.tabText(0)
    assert "Réutiliser le commun" in window.tabs.tabText(1)
    assert "commun" in window.windowTitle()


def test_stream_selector_is_locked_for_single_stream(window, scene):
    window.extract_tab.field_a.set_path(scene.a)
    assert window.extract_tab.stream_a.selected_index == 0
    # Un seul flux : la liste est verrouillée, pas de choix ambigu à faire.
    assert not window.extract_tab.stream_a.combo.isEnabled()
    assert "Un seul flux" in window.extract_tab.stream_a.status.text()


def test_stream_selector_requires_a_choice_for_multiple_streams(window, tmp_path, scene):
    import subprocess

    from ummahverse_audio.ffmpeg_tools import find_ffmpeg

    merged = tmp_path / "multi.mka"
    subprocess.run([str(find_ffmpeg()), "-hide_banner", "-v", "error", "-y",
                    "-i", str(scene.a), "-i", str(scene.b), "-map", "0:a", "-map", "1:a",
                    "-c", "copy", str(merged)], check=True)
    window.extract_tab.field_a.set_path(merged)
    selector = window.extract_tab.stream_a
    assert selector.combo.isEnabled(), "le choix doit être offert quand il y a 2 flux"
    assert selector.combo.count() == 2
    assert "choisissez explicitement" in selector.status.text()
    selector.combo.setCurrentIndex(1)
    assert selector.selected_index == 1


def test_missing_input_is_reported_without_crashing(window, monkeypatch):
    shown = []
    monkeypatch.setattr("ummahverse_audio.gui.main_window.QMessageBox.warning",
                        lambda *args, **kwargs: shown.append(args[2]))
    window.extract_tab._extract()
    assert shown and "fichiers" in shown[0]


def test_extract_runs_off_the_ui_thread_and_fills_second_tab(window, scene, tmp_path):
    tab = window.extract_tab
    tab.field_a.set_path(scene.a)
    tab.field_b.set_path(scene.b)
    tab.field_out.set_path(tmp_path / "sorties")
    outcomes = []
    tab.runner.finished.connect(outcomes.append)
    tab._extract()
    # Le thread de travail tourne : l'UI reste vivante (wait_for exige plusieurs tours).
    assert wait_for(lambda: bool(outcomes)), "l'extraction n'a pas rendu la main"
    outcome = outcomes[0]
    assert outcome.error is None, outcome.error
    assert not outcome.cancelled
    assert (tmp_path / "sorties" / "common.wav").is_file()
    assert tab.progress.value() == 1000
    journal = tab.log.toPlainText()
    assert "Métadonnées de qualité" in journal
    assert "erreur de reconstruction" in journal
    assert tab.open_folder_button.isEnabled()
    # L'onglet 2 est pré-rempli : le commun est réutilisé sans nouvelle extraction.
    assert window.subtract_tab.field_common.path == tmp_path / "sorties" / "common.wav"
    assert window.subtract_tab.field_metadata.path == tmp_path / "sorties" / "common.json"


def test_subtract_runs_from_the_second_tab(window, scene, extracted, tmp_path):
    tab = window.subtract_tab
    tab.field_common.set_path(extracted.common_path)
    tab.field_metadata.set_path(extracted.metadata_path)
    tab.field_input.set_path(scene.third)
    tab.field_output.set_path(tmp_path / "troisieme_only.wav")
    outcomes = []
    tab.runner.finished.connect(outcomes.append)
    tab._subtract()
    assert wait_for(lambda: bool(outcomes))
    assert outcomes[0].error is None, outcomes[0].error
    assert (tmp_path / "troisieme_only.wav").is_file()
    assert "gain appliqué au commun" in tab.log.toPlainText()


def test_cancelling_a_long_run_stops_cleanly(window, tmp_path):
    """Annulation : arrêt au prochain bloc, sans fichier de sortie partiel conservé."""
    scene = build_scene(tmp_path / "long", seconds=40.0)
    tab = window.extract_tab
    tab.field_a.set_path(scene.a)
    tab.field_b.set_path(scene.b)
    tab.field_out.set_path(tmp_path / "annule")
    outcomes = []
    tab.runner.finished.connect(outcomes.append)
    tab._extract()
    assert wait_for(lambda: tab.progress.value() > 0, timeout_ms=60_000)
    tab._cancel()
    assert wait_for(lambda: bool(outcomes), timeout_ms=60_000)
    assert outcomes[0].cancelled, outcomes[0].error
    assert "Annulé" in tab.status.text()
    assert not (tmp_path / "annule" / "common.wav").exists()
    assert not (tmp_path / "annule" / "a_only.wav").exists()
    # L'interface est de nouveau utilisable immédiatement après l'annulation.
    assert tab.extract_button.isEnabled()
    assert not tab.runner.busy


def test_engine_error_is_shown_as_a_readable_message(window, tmp_path, monkeypatch):
    """Deux pistes incompatibles : message explicite, pas de trace Python."""
    stereo = build_scene(tmp_path / "stereo", seconds=3.0, channels=2)
    surround = build_scene(tmp_path / "surround", seconds=3.0, channels=6)
    critical = []
    monkeypatch.setattr("ummahverse_audio.gui.main_window.QMessageBox.critical",
                        lambda *args, **kwargs: critical.append(args[2]))
    tab = window.extract_tab
    tab.field_a.set_path(stereo.a)
    tab.field_b.set_path(surround.b)
    tab.field_out.set_path(tmp_path / "out")
    outcomes = []
    tab.runner.finished.connect(outcomes.append)
    tab._extract()
    assert wait_for(lambda: bool(outcomes))
    assert outcomes[0].error is not None
    assert "downmix" in outcomes[0].error
    assert critical and "downmix" in critical[0]
    assert "Traceback" not in critical[0]


def test_analysis_button_reports_warnings_without_writing_files(window, tmp_path):
    scene = build_scene(tmp_path / "src", seconds=5.0)
    tab = window.extract_tab
    tab.field_a.set_path(scene.a)
    tab.field_b.set_path(scene.b)
    tab.field_out.set_path(tmp_path / "vide")
    outcomes = []
    tab.runner.finished.connect(outcomes.append)
    tab._analyze()
    assert wait_for(lambda: bool(outcomes))
    assert outcomes[0].error is None, outcomes[0].error
    journal = tab.log.toPlainText()
    assert "Décalage global" in journal
    assert "Gain du commun" in journal
    assert not list((tmp_path / "vide").glob("*")) if (tmp_path / "vide").exists() else True


def test_missing_ffmpeg_is_reported_at_startup(application, monkeypatch):
    from ummahverse_audio.errors import FfmpegMissingError
    from ummahverse_audio.gui import main_window

    def boom():
        raise FfmpegMissingError("« ffmpeg » est introuvable dans le PATH.",
                                "Ubuntu : sudo apt install ffmpeg")

    monkeypatch.setattr("ummahverse_audio.ffmpeg_tools.find_ffmpeg", boom)
    shown = []
    monkeypatch.setattr(main_window.QMessageBox, "critical",
                        lambda *args, **kwargs: shown.append(args[2]))
    assert main_window.check_ffmpeg(None) is False
    assert shown and "introuvable" in shown[0]
    assert "apt install ffmpeg" in shown[0]


def test_drag_and_drop_sets_the_path(window, scene):
    from PySide6.QtCore import QMimeData, QPoint, QUrl
    from PySide6.QtGui import QDropEvent
    from PySide6.QtCore import Qt

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(scene.a))])
    event = QDropEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.extract_tab.field_a.dropEvent(event)
    assert window.extract_tab.field_a.path == scene.a
