"""Démarrage de l'application Qt."""

from __future__ import annotations

import sys

from .. import __version__


def build_application(argv: list[str] | None = None):
    """Crée (ou réutilise) l'objet `QApplication`."""
    from PySide6.QtWidgets import QApplication

    existing = QApplication.instance()
    if existing is not None:
        return existing
    application = QApplication(list(argv) if argv is not None else sys.argv)
    application.setApplicationName("UmmahVerse Audio")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("UmmahVerse")
    return application


def run(argv: list[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication  # noqa: F401
    except ImportError as exc:
        print("PySide6 est requis pour l'interface graphique.\n"
              "Ubuntu : bash scripts/bootstrap.sh\n"
              "Windows : scripts\\bootstrap.bat\n"
              f"Détail : {exc}", file=sys.stderr)
        return 1

    from .main_window import MainWindow, check_ffmpeg

    application = build_application(argv)
    window = MainWindow()
    window.show()
    if not check_ffmpeg(window):
        return 2
    return application.exec()
