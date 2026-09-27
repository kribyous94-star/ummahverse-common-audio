"""Erreurs du POC, formulées pour être affichées telles quelles à l'utilisateur."""

from __future__ import annotations


class UmmahAudioError(Exception):
    """Erreur prévue et explicable (message affichable dans la CLI et l'interface)."""

    #: Indice d'action proposé à l'utilisateur, affiché sous le message.
    hint: str = ""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        if hint:
            self.hint = hint

    def user_message(self) -> str:
        return f"{self}\n{self.hint}" if self.hint else str(self)


class FfmpegMissingError(UmmahAudioError):
    """`ffmpeg` ou `ffprobe` introuvable."""


class FfmpegFailedError(UmmahAudioError):
    """FFmpeg/ffprobe a retourné un code d'erreur."""


class StreamSelectionError(UmmahAudioError):
    """Aucun flux audio, ou plusieurs flux sans choix explicite."""


class IncompatibleLayoutError(UmmahAudioError):
    """Agencements de canaux incompatibles : refus explicite, pas de downmix implicite."""


class AlignmentError(UmmahAudioError):
    """Alignement impossible ou non fiable sur la totalité du signal."""


class OutputCollisionError(UmmahAudioError):
    """Une sortie écraserait un fichier d'entrée, ou une autre sortie.

    Les fichiers fournis en entrée ne sont jamais modifiés : la demande est refusée
    avant que quoi que ce soit ne soit écrit.
    """


class MetadataError(UmmahAudioError):
    """`common.json` absent, illisible ou de version incompatible."""


class Cancelled(UmmahAudioError):
    """Traitement interrompu à la demande de l'utilisateur."""

    def __init__(self, message: str = "Traitement annulé par l'utilisateur.") -> None:
        super().__init__(message)
