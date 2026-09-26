"""Format et lecture de `common.json` et du rapport de soustraction.

`common.json` est le contrat entre `extract` et `subtract` : il contient tout ce qui
permet de réutiliser `common.wav` sur une nouvelle langue **sans relire A ni B**.

Les champs de confiance sont explicitement définis :

* `confidence` d'alignement : coefficient de corrélation normalisé de la piste
  d'analyse décimée, dans `[0, 1]`, à la position retenue ;
* `energy_weighted_coherence` : cohérence quadratique locale moyennée, pondérée par
  l'énergie de A ;
* `common_share_of_a_energy` : part de l'énergie de A attribuée au commun.

Aucune de ces valeurs n'est une probabilité de bonne séparation, et aucune n'est
fabriquée : chacune est le résultat d'un calcul décrit dans `docs/technical-note.md`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import __version__
from .errors import MetadataError

#: Version du format de métadonnées. Incrémentée si la structure change.
SCHEMA_VERSION = "1.0"
#: Version des paramètres de traitement (change si les valeurs par défaut changent).
PARAMS_VERSION = "poc-1.0"


def stream_descriptor(audio) -> dict:
    """Décrit une entrée décodée : chemin, flux retenu, format, base temporelle."""
    stream = audio.stream
    return {
        "path": str(audio.source),
        "name": audio.source.name,
        "audio_stream_index": stream.audio_index,
        "container_stream_index": stream.stream_index,
        "codec": stream.codec,
        "lossless": stream.lossless,
        "source_sample_rate": stream.sample_rate,
        "source_sample_format": stream.sample_format,
        "language": stream.language,
        "title": stream.title,
        "working_sample_rate": audio.sample_rate,
        "resampled": audio.resampled,
        "channels": audio.channels,
        "channel_layout": stream.effective_layout,
        "channel_layout_declared": stream.channel_layout,
        "frames": audio.frames,
        "duration_s": round(audio.duration_s, 6),
    }


def write_json(path: Path, payload: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def load_common_metadata(path: Path) -> dict:
    """Charge et valide `common.json`."""
    path = Path(path)
    if not path.is_file():
        raise MetadataError(
            f"Métadonnées introuvables : {path}",
            "Indiquez le fichier common.json produit par `extract`.",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetadataError(f"Métadonnées illisibles ({path.name}) : {exc}") from exc
    if not isinstance(payload, dict) or payload.get("operation") != "extract":
        raise MetadataError(
            f"{path.name} n'est pas un fichier de métadonnées d'extraction.",
            "Attendu : le common.json écrit à côté de common.wav.",
        )
    version = str(payload.get("schema_version", ""))
    if version.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
        raise MetadataError(
            f"Version de métadonnées incompatible : {version or 'absente'} "
            f"(attendu {SCHEMA_VERSION}).",
            "Relancez `extract` avec cette version de l'outil.",
        )
    return payload


def common_reference(payload: dict) -> dict:
    """Extrait la description de la base temporelle du commun."""
    reference = payload.get("common_timebase")
    if not isinstance(reference, dict):
        raise MetadataError("`common_timebase` absent des métadonnées.")
    return reference


def base_payload(operation: str, environment: dict) -> dict[str, Any]:
    return {
        "operation": operation,
        "tool": "ummahverse_audio",
        "tool_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "params_version": PARAMS_VERSION,
        "environment": environment,
    }


def limitations_notice() -> list[str]:
    """Limites rappelées dans chaque fichier de métadonnées."""
    return [
        "Deux mixages ne déterminent pas exactement trois sources : `a_only` et `b_only` "
        "sont des résidus estimés, pas des voix isolées garanties.",
        "Tout contenu réellement identique aux deux versions est capté par le commun : "
        "musique et bruitages, mais aussi mêmes mots simultanés, voix originale laissée en "
        "fond, réverbération de dialogue et pompage de compression piloté par la voix.",
        "La cohérence est une statistique moyennée sur un voisinage temps-fréquence ; ce "
        "n'est pas un détecteur de source dans une case STFT isolée.",
        "Dans les zones marquées non comparables ou non traitées, le commun vaut zéro et le "
        "résidu est identique à l'entrée : aucune bande commune n'y est inventée.",
        "Le retrait de la musique du commun n'est pas réalisé par ce POC.",
    ]
