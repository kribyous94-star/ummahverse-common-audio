"""POC UmmahVerse : estimation de la bande commune entre deux versions linguistiques.

Le même moteur sert la CLI (`python -m ummahverse_audio`) et l'interface graphique
PySide6 (`python -m ummahverse_audio gui`). Aucun service distant, aucun GPU.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__", "extract", "subtract", "ExtractOptions", "SubtractOptions"]


def __getattr__(name: str):  # importation paresseuse : la CLI reste rapide à démarrer
    if name in {"extract", "ExtractOptions", "ExtractResult"}:
        from . import extract as module
        return getattr(module, name if name != "extract" else "run_extract")
    if name in {"subtract", "SubtractOptions", "SubtractResult"}:
        from . import subtract as module
        return getattr(module, name if name != "subtract" else "run_subtract")
    raise AttributeError(name)
