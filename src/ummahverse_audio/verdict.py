"""Verdict lisible : ces deux versions partagent-elles une bande commune ?

Un `common.wav` silencieux n'est pas une réponse. Quand l'outil ne trouve rien, il
doit dire **pourquoi**, avec un chiffre, et distinguer deux situations que rien ne
séparait jusqu'ici :

* les pistes ne se synchronisent pas — mauvais fichiers, mauvais flux, montages
  différents, décalage hors de la fenêtre de recherche ;
* les pistes se synchronisent parfaitement, mais leurs formes d'onde n'ont rien en
  commun — ce sont deux **mixages différents**, pas deux versions d'un même master
  musique/bruitages. Aucun réglage ne rattrapera cela.

La grandeur qui tranche est la **cohérence locale moyenne pondérée par l'énergie**,
mesurée sur des blocs répartis dans tout le signal, après alignement. Les seuils
ci-dessous viennent de mesures sur du matériel réel et synthétique, et sont indicatifs :
ils classent, ils ne décident pas à la place de l'oreille.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Seuils de cohérence pondérée observés (voir docs/results.md).
ABSENT = 0.03
WEAK = 0.12
PARTIAL = 0.35


@dataclass
class CommonBedVerdict:
    """Diagnostic de l'existence d'une bande commune exploitable."""

    level: str                 # "absente", "faible", "partielle" ou "bonne"
    headline: str
    detail: str
    advice: str = ""
    mean_coherence: float = 0.0
    reliable_fraction: float = 0.0
    comparable_fraction: float = 0.0
    alignment_method: str = ""
    alignment_confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.level in ("partielle", "bonne")

    def lines(self) -> list[str]:
        out = [self.headline, self.detail]
        if self.advice:
            out.append(self.advice)
        return out

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "headline": self.headline,
            "detail": self.detail,
            "advice": self.advice,
            "energy_weighted_coherence": round(self.mean_coherence, 4),
            "reliable_cell_fraction": round(self.reliable_fraction, 4),
            "comparable_block_fraction": round(self.comparable_fraction, 4),
            "alignment_method": self.alignment_method,
            "alignment_confidence": round(self.alignment_confidence, 4),
            "thresholds": {"absente": ABSENT, "faible": WEAK, "partielle": PARTIAL},
        }


def judge(mean_coherence: float, reliable_fraction: float,
          comparable_fraction: float, alignment_method: str,
          alignment_confidence: float) -> CommonBedVerdict:
    """Classe le résultat et rédige le diagnostic correspondant."""
    aligned = alignment_confidence >= 0.25 or alignment_method == "waveform"
    percent = f"{mean_coherence * 100:.1f} %"

    if mean_coherence < ABSENT:
        if aligned:
            headline = "Aucune bande commune détectée entre ces deux versions."
            detail = (
                f"Les deux pistes sont pourtant bien synchrones (alignement par "
                f"{_method_label(alignment_method)}, confiance "
                f"{alignment_confidence:.2f}) : ce sont les mêmes scènes, au même "
                f"instant. Mais leurs formes d'onde ne partagent que {percent} "
                f"d'énergie cohérente. Autrement dit, la musique et les bruitages ont "
                f"été **remixés** d'une version à l'autre : il n'existe pas de master "
                f"M&E commun à retrouver."
            )
            advice = (
                "Aucun réglage ne corrigera cela. Il faut deux pistes issues du même "
                "mixage M&E : typiquement deux pistes du même disque ou de la même "
                "édition, et non deux éditions ou deux pays différents. Le commun "
                "produit serait vide ou inexploitable."
            )
        else:
            headline = "Aucune bande commune détectée, et alignement peu sûr."
            detail = (
                f"Cohérence mesurée : {percent}. L'alignement lui-même n'est pas "
                f"fiable (confiance {alignment_confidence:.2f})."
            )
            advice = (
                "Vérifiez d'abord le flux audio choisi dans chaque fichier, puis que "
                "les deux pistes couvrent bien le même montage. Augmentez "
                "--max-offset si le décalage peut dépasser 30 s."
            )
    elif mean_coherence < WEAK:
        headline = "Bande commune très faible : résultat probablement inexploitable."
        detail = (
            f"Cohérence pondérée {percent}, {reliable_fraction * 100:.1f} % de cases "
            f"temps-fréquence fiables. Un peu de contenu est partagé — souvent le "
            f"générique ou quelques scènes — mais pas l'essentiel du film."
        )
        advice = (
            "Écoutez le commun produit avant d'aller plus loin. Si seules certaines "
            "scènes fonctionnent, traitez-les séparément plutôt que le film entier."
        )
    elif mean_coherence < PARTIAL:
        headline = "Bande commune partielle : séparation possible, qualité à vérifier."
        detail = (
            f"Cohérence pondérée {percent}, {reliable_fraction * 100:.1f} % de cases "
            f"fiables, {comparable_fraction * 100:.0f} % des blocs comparables."
        )
        advice = (
            "Lancez l'extraction, puis écoutez : le commun doit contenir musique et "
            "bruitages, les résidus les dialogues. Vérifiez les zones signalées non "
            "comparables."
        )
    else:
        headline = "Bande commune bien identifiée."
        detail = (
            f"Cohérence pondérée {percent}, {reliable_fraction * 100:.1f} % de cases "
            f"fiables, {comparable_fraction * 100:.0f} % des blocs comparables."
        )
        advice = "Conditions favorables : lancez l'extraction, puis contrôlez à l'oreille."

    return CommonBedVerdict(
        level=_level_for(mean_coherence), headline=headline, detail=detail,
        advice=advice, mean_coherence=mean_coherence,
        reliable_fraction=reliable_fraction, comparable_fraction=comparable_fraction,
        alignment_method=alignment_method, alignment_confidence=alignment_confidence,
    )


def _level_for(mean_coherence: float) -> str:
    if mean_coherence < ABSENT:
        return "absente"
    if mean_coherence < WEAK:
        return "faible"
    if mean_coherence < PARTIAL:
        return "partielle"
    return "bonne"


def _method_label(method: str) -> str:
    return {
        "waveform": "corrélation de forme d'onde",
        "envelope": "corrélation d'enveloppes d'énergie",
        "envelope+waveform": "enveloppes puis forme d'onde",
    }.get(method, method or "méthode inconnue")
