# Validation du POC

## Tests synthétiques à sources connues

Le générateur fourni produit `a.wav`, `b.wav`, `third.wav`, `common_truth.wav`, `a_voice_truth.wav`, `b_voice_truth.wav`, `third_voice_truth.wav` et `fixture.json`. Musique/ambiance et voix synthétiques jouent simultanément pendant une grande partie du signal. B et la troisième langue reçoivent des décalages et gains de commun connus. Ce jeu sert de régression technique ; des timbres synthétiques ne prouvent pas un résultat sur un film.

Tester : décodage, signe et précision de l'offset, gain, échantillonnage, mono/stéréo et absence de downmix, sorties finies, longueur, somme `commun adapté + only = entrée` dans le repère aligné, absence de clipping, zones d'échec indiquées, réutilisation de `common` pour `third` sans lecture de A/B. Ajouter cas négatifs : A et B sans M&E commun, coupes, niveaux inconnus, piste 5.1 incompatible, contenu commun et parole qui se recouvrent spectralement.

## Mesures à publier

Pour chaque source connue, mesurer erreur RMS ou SI-SDR de `common` versus `common_truth` après ajustement de base temporelle ; fuite de voix dans le commun ; fuite de musique et bruitages dans chaque résidu ; erreur de reconstruction et qualité de la troisième langue. Définir exactement les fenêtres comptées et comparer le baseline et la méthode retenue. Mesurer temps et pic mémoire pour 30 secondes puis un segment plus long. Garder les résultats bruts, reproductibles ; ne fixer un seuil d'acceptation chiffré qu'après un baseline reproductible.

## Écoute réelle

Faire écouter plusieurs extraits de 15 à 30 secondes : parole sur musique, dialogue sur bruitage, musique seule, parole seule, transition/coupe et passage calme. Comparer chaque sortie au mix d'origine à volume égal, sur écouteurs. Noter voix perdue, musique résiduelle, effet de pompage, clics et spatialisation. Pistes issues du même montage et codecs sans perte recommandés pour le premier essai. Documenter échecs si master M&E différent.

`scripts/smoke.sh` vérifie la commande et crée un exemple, mais **ne remplace pas** ces mesures ni l'écoute réelle.

## Interface graphique et systèmes

Sur Ubuntu et sur Windows, vérifier : installation, lancement, sélection des deux entrées, choix explicite de piste audio, analyse, extraction, ouverture des sorties, deuxième langue, soustraction, annulation d'un long calcul, messages d'erreur lorsque FFmpeg manque, fenêtre réactive pendant le traitement. Inclure chemins avec espaces et accents, dossier de sortie non vide et erreur de permissions. Noter OS, Python, PySide6, FFmpeg, résultat et preuve de chaque essai ; si Windows ou Linux n'est pas accessible, distinguer **non testé** de **réussi**.

## Documentation et historique Git

Vérifier que les commandes d'installation, de lancement, d'extraction, de soustraction et de test données dans le README correspondent au code livré ; contrôler la note technique, les exemples de sorties et les limites. Vérifier que les commits existent dans le dépôt sur la branche annoncée, que leurs fichiers correspondent au travail réalisé et que `git status` final est communiqué. Les fichiers audio d'essai et résultats volumineux ne doivent pas entrer dans les commits.
