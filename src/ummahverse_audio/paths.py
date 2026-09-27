"""Protection des fichiers d'entrée : ils ne doivent jamais être modifiés.

Le moteur lit les entrées et n'écrit que dans son espace de travail temporaire et
dans les fichiers de sortie demandés. Il restait une faille : rien n'empêchait un
chemin de **sortie** de désigner un fichier d'**entrée**. FFmpeg écrit avec `-y`, donc
l'entrée aurait été tronquée dès l'ouverture, avant même le moindre traitement.

Ce module compare les chemins avant toute écriture. La comparaison doit résister à
plusieurs formes d'aliasing :

* chemins relatifs contre absolus, `..` et `.` ;
* liens symboliques (résolus) ;
* liens durs, qui ne se voient pas dans le chemin (`os.path.samefile`) ;
* casse différente sur les systèmes de fichiers insensibles à la casse (Windows,
  macOS), où `Film.WAV` et `film.wav` sont le même fichier.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Sequence

from .errors import OutputCollisionError


def canonical(path: Path | str) -> str:
    """Forme comparable d'un chemin : absolu, liens résolus, casse normalisée.

    `strict=False` : un fichier de sortie n'existe pas encore, on veut quand même
    pouvoir le comparer à une entrée existante.
    """
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):  # boucle de liens symboliques, chemin invalide
        resolved = Path(os.path.abspath(str(candidate)))
    return os.path.normcase(str(resolved))


def same_file(left: Path | str, right: Path | str) -> bool:
    """Vrai si les deux chemins désignent le même fichier sur le disque."""
    left_path, right_path = Path(left), Path(right)
    if left_path.exists() and right_path.exists():
        try:
            return os.path.samefile(left_path, right_path)  # couvre les liens durs
        except OSError:
            pass
    return canonical(left_path) == canonical(right_path)


def ensure_inputs_untouched(inputs: Iterable[Path | str],
                            outputs: Iterable[Path | str]) -> None:
    """Refuse si une sortie écraserait une entrée.

    Appelé **avant** l'ouverture de la moindre sortie : au moment où FFmpeg ouvre un
    fichier de sortie, il l'a déjà tronqué.
    """
    protected = [Path(item) for item in inputs if item is not None]
    for output in outputs:
        if output is None:
            continue
        target = Path(output)
        for source in protected:
            if same_file(source, target):
                raise OutputCollisionError(
                    f"La sortie « {target} » désigne le fichier d'entrée "
                    f"« {source} ».",
                    "Les fichiers d'entrée ne sont jamais modifiés : choisissez un "
                    "autre nom ou un autre dossier de sortie.",
                )


def ensure_distinct_outputs(outputs: Sequence[Path | str]) -> None:
    """Refuse si deux sorties désignent le même fichier (l'une écraserait l'autre)."""
    seen: dict[str, Path] = {}
    for output in outputs:
        if output is None:
            continue
        target = Path(output)
        key = canonical(target)
        if key in seen:
            raise OutputCollisionError(
                f"Deux sorties désignent le même fichier : « {seen[key]} » et "
                f"« {target} ».",
                "Donnez des noms distincts, sans quoi la seconde écraserait la première.",
            )
        seen[key] = target


def ensure_writable_directory(directory: Path | str) -> Path:
    """Vérifie qu'un dossier de sortie est utilisable, avec un message clair sinon."""
    target = Path(directory)
    if target.exists() and not target.is_dir():
        raise OutputCollisionError(
            f"« {target} » existe déjà et n'est pas un dossier.",
            "Indiquez un dossier de sortie, pas un fichier.",
        )
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputCollisionError(
            f"Impossible de créer le dossier de sortie « {target} » : {exc.strerror}.",
            "Choisissez un emplacement où vous avez le droit d'écrire.",
        ) from exc
    if not os.access(target, os.W_OK):
        raise OutputCollisionError(
            f"Le dossier de sortie « {target} » n'est pas accessible en écriture.",
            "Choisissez un emplacement où vous avez le droit d'écrire.",
        )
    return target
