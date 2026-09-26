"""Interface graphique de bureau (PySide6), Ubuntu et Windows.

Le moteur est exactement celui de la CLI : l'interface ne fait que collecter des
`ExtractOptions` / `SubtractOptions`, les exécuter dans un thread de travail et
afficher progression, avertissements et résultats.
"""

from __future__ import annotations


def launch(argv: list[str] | None = None) -> int:
    """Ouvre la fenêtre. Retourne le code de sortie du processus Qt."""
    from .app import run

    return run(argv)


__all__ = ["launch"]
