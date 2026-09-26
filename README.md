# UmmahVerse — extraction audio commune (POC)

Ce dossier est à transmettre à Claude avec le dépôt du projet. Commencer par `PROMPT_CLAUDE.md`.

L'application demandée est une **interface graphique de bureau pour Ubuntu et Windows**, avec un moteur commun en Python. Les commandes CLI restent disponibles pour l'automatisation et les tests.
Claude doit également écrire la documentation d'utilisation et la note technique dans le dépôt UmmahVerse, puis créer et récapituler les commits Git du travail réalisé.

## Objectif

Deux pistes du même montage, dans deux langues, servent à estimer la bande commune (musique, bruitages, ambiance), **y compris sous les dialogues simultanés**. Les différences estimées sont exportées pour chacune. Le commun est ensuite réutilisé pour soustraire la bande commune d'une nouvelle langue.

Ce POC doit prouver la qualité sur des sources connues. Deux mélanges ne garantissent pas une séparation exacte de trois sources. Les sorties « only » sont des résidus estimés, pas une promesse de voix parfaitement isolée.

## Fichiers

- `PROMPT_CLAUDE.md` : consigne de réalisation à copier dans Claude.
- `SPECIFICATION.md` : comportement, hypothèses, algorithme et livrables.
- `VALIDATION.md` : tests et critères de décision.
- `scripts/bootstrap.sh` : installation de l'environnement du futur code.
- `scripts/bootstrap.ps1`, `scripts/bootstrap.bat` : même installation sur Windows.
- `scripts/launch.sh`, `scripts/launch.ps1`, `scripts/launch.bat` : démarrage de l'interface graphique.
- `scripts/extract.sh` : commande de comparaison des deux premières pistes.
- `scripts/subtract.sh` : réutilisation du commun pour une autre langue.
- `scripts/smoke.sh` : scénario synthétique de bout en bout.
- `scripts/generate_fixtures.py` : mélanges et sources synthétiques connus.

Les scripts shell sont des **interfaces prévues** pour le programme que Claude doit écrire. Ils ne prétendent pas que le moteur de séparation existe déjà.

## Utilisation après implémentation

```bash
bash scripts/bootstrap.sh
bash scripts/launch.sh
bash scripts/smoke.sh
bash scripts/extract.sh film_FR.wav film_AR.wav ./resultats
bash scripts/subtract.sh ./resultats/common.wav film_EN.wav ./resultats/EN_only.wav --metadata ./resultats/common.json
```

Sous Windows, lancer `scripts\bootstrap.bat` puis `scripts\launch.bat` (ou les équivalents `.ps1` depuis PowerShell). Claude devra valider l'ouverture et un traitement réel sur les deux systèmes ; si un des systèmes n'est pas disponible dans son environnement, il devra le dire et fournir la procédure de vérification, sans annoncer ce test comme réussi.

Sur un film, préférer des sources WAV sans perte issues de la même édition et du même montage, avec agencement des canaux vérifié. Ne pas inclure de films ni de pistes sous droits dans les tests ou commits.
