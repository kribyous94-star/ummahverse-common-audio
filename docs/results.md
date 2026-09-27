# Résultats mesurés

Toutes les valeurs de cette page ont été produites sur la machine décrite ci-dessous,
par `scripts/benchmark.py`, sans sélection ni arrondi favorable. Chaque chiffre est
reproductible par la commande indiquée.

## Environnement de mesure

| | |
|---|---|
| Système | Ubuntu 24.04, noyau 6.x, 16 cœurs, 32 Gio de RAM |
| Python | 3.12.3 |
| NumPy / SciPy | 2.5.3 / 1.18.1 |
| PySide6 | 6.11.2 |
| FFmpeg | 6.1.1 (paquet Ubuntu) |
| Windows | **non testé** — voir [manual-verification.md](manual-verification.md) |

Reproduction :

```bash
.venv/bin/python scripts/benchmark.py --out-dir benchmarks --perf-seconds 60,300,900,1800
```

## Jeu de test

`scripts/generate_fixtures.py` : 12 s, stéréo, 48 kHz.

* Commun : trois partiels harmoniques (220 / 330 / 440 Hz), une ambiance large bande
  filtrée 120–7500 Hz, et quatre impacts percussifs.
* Trois voix synthétiques distinctes, **intermittentes et simultanées** au commun sur
  la quasi-totalité du signal — c'est le point qui compte : la séparation est évaluée
  en recouvrement, pas sur des passages sans dialogue.
* Vérités connues : décalages du commun 0 / +3600 / +6240 échantillons
  (0 / 75 / 130 ms), gains 1,00 / 0,870 / 1,060.

Fenêtre d'évaluation : **bords exclus**, 0,5 s au début et 0,5 s à la fin, où la STFT
dispose de moins de contexte. Les canaux sont évalués ensemble, sans remixage.

### Définition des mesures

Chaque sortie est décomposée par **moindres carrés conjoints** sur les sources vraies
(`commun`, `voix A`, `voix B`) — voir
[technical-note.md § 10](technical-note.md#10-mesures-objectives). Les coefficients sont
donnés en dB :

| Colonne | Signification | Bon résultat |
|---|---|---|
| commun SI-SDR | SI-SDR du commun estimé contre le commun vrai | élevé |
| voix A dans commun | fraction de la voix de A présente dans `common.wav` | très bas |
| musique dans `a_only` | fraction du commun vrai restant dans le résidu | très bas |
| voix A dans `a_only` | fraction de la voix de A conservée dans le résidu | proche de 0 dB |
| `a_only` / `b_only` / `third_only` SI-SDR | résidu contre la voix vraie correspondante | élevé |

## Baselines contre méthode retenue

| Variante | commun SI-SDR (dB) | voix A dans commun (dB) | musique dans `a_only` (dB) | voix A dans `a_only` (dB) | `a_only` SI-SDR (dB) | `b_only` SI-SDR (dB) | `third_only` SI-SDR (dB) |
|---|---|---|---|---|---|---|---|
| **wiener (retenue)** | **21,70** | **−35,04** | **−35,83** | **−0,16** | **15,95** | **17,14** | **15,46** |
| wiener avec correction de biais | 21,26 | −34,21 | −28,63 | −0,17 | 15,07 | 16,28 | 15,03 |
| wiener, gain large bande seul | 21,73 | −35,40 | −35,87 | −0,15 | 15,98 | 17,19 | 15,49 |
| wiener, aucune transformation | 18,84 | −45,14 | −17,54 | −0,05 | 9,94 | 14,48 | 12,66 |
| baseline moyenne complexe | 18,24 | −21,80 | −54,12 | −0,74 | 12,46 | 14,26 | 12,09 |
| baseline min-magnitude | 20,24 | −22,85 | −30,83 | −0,65 | 14,52 | 15,54 | 13,99 |
| baseline masque de cohérence | 16,60 | −22,95 | −21,97 | −0,64 | 10,03 | 11,58 | 10,52 |

### Lecture

**Gain de la méthode retenue sur la meilleure baseline** (min-magnitude) :

| | retenue | min-magnitude | écart |
|---|---|---|---|
| commun SI-SDR | 21,70 dB | 20,24 dB | **+1,46 dB** |
| voix A dans commun | −35,04 dB | −22,85 dB | **−12,19 dB de fuite** |
| `a_only` SI-SDR | 15,95 dB | 14,52 dB | **+1,43 dB** |
| `third_only` SI-SDR | 15,46 dB | 13,99 dB | **+1,47 dB** |

La méthode retenue est meilleure sur toutes les métriques agrégées. Ce test est
également dans la suite automatisée (`test_retained_method_beats_naive_baselines`), pour
chacune des trois baselines.

**La baseline « moyenne complexe » annule mieux la musique** (−54,12 dB contre
−35,83 dB) — et c'est une régression, pas un succès : elle y parvient en versant une
partie des voix dans le commun (−21,80 dB de fuite contre −35,04 dB), ce qui abîme le
résidu (12,46 dB contre 15,95 dB de SI-SDR) et retire 0,74 dB à la voix qu'on voulait
garder. C'est exactement le compromis sur-annulation / préservation de la voix qu'il
faut surveiller, et pourquoi juger sur la seule colonne « musique annulée » serait
trompeur.

**Ablations :**

* *Aucune transformation* (`H = 1`) : la fuite de voix est la plus faible du tableau
  (−45 dB) mais l'annulation de la musique s'effondre (−17,5 dB), parce qu'un `H` faux
  conduit à sous-estimer la puissance commune. Le gain entre versions n'est pas
  facultatif.
* *Gain large bande seul* (`--transfer gain`) est ici équivalent à l'égalisation par
  bande, à 0,03 dB près : le jeu synthétique n'a pas d'égalisation de mastering entre
  versions. Le mode `band` sert à du matériel réel ; il ne coûte rien sur celui-ci.
* *Correction de biais* : **mesurée comme défavorable** (−7,2 dB d'annulation de la
  musique), donc désactivée par défaut alors que la théorie du moyennage la suggérait.
  L'explication est dans [technical-note.md § 5](technical-note.md#puissance-commune).

## Estimation du gain entre versions

Gain vrai du commun de B : **0,870**.

| Estimateur | Gain mesuré (canaux G / D) | Erreur relative |
|---|---|---|
| `power` (défaut) | 0,8691 / 0,8694 | **0,1 %** |
| `ls` | 0,8476 / 0,8490 | 2,6 % |

L'estimateur par rapport de puissances est retenu pour cette raison. Toutes les
variantes du tableau principal utilisent `power`, sauf `--transfer none`.

## Alignement

| Mesure | Résultat |
|---|---|
| Décalage global mesuré (vrai : 75,00 ms) | **75,00 ms**, signe conforme à la convention |
| Précision sous-échantillon (retards fractionnaires 12,25 / 40,5 / 103,75) | erreur **< 0,1 échantillon** |
| Décalages locaux, erreur médiane sur le jeu de référence | **< 1 échantillon** |
| Blocs comparables sur un jeu sain | 100 % |
| Plan de 4 s remplacé dans B par du contenu sans rien de commun | zone **marquée non comparable**, reste du signal > 80 % comparable |
| Dérive d'horloge de −200 ppm | **détectée** et rapportée, avec avertissement |
| Deux bruits indépendants, ou une entrée muette | **refusés** avec un message explicite |

Le rapport pic/efficace de la corrélation GCC-PHAT sépare nettement les deux cas : ≈ 5
à 6,5 pour du bruit indépendant, > 20 pour un alignement réel. Le seuil est fixé à 10.

## Reconstruction et intégrité des sorties

| Mesure | Résultat |
|---|---|
| `commun + a_only − A`, maximum absolu | **1,4 × 10⁻¹⁷** |
| `commun recalé + b_only − B` | **0** (en flottant 64 bits) |
| `commun adapté + third_only − C` | **0** |
| Échantillons non finis | aucun |
| Écrêtage | aucun (sorties en flottant 32 bits) |
| Longueur et nombre de canaux des sorties | identiques à l'entrée correspondante |
| Clic aux raccords de blocs | aucun (poids de fondu de somme exactement 1) |

L'exactitude de la reconstruction n'est pas un résultat de qualité : elle découle de la
construction (`résidu = entrée − commun rendu`). Elle garantit seulement qu'aucune
énergie n'est perdue ni inventée dans le passage par les blocs.

## Annulation sous la parole, et transitoires

Deux mesures ciblées, parce que ce sont les deux cas où un détecteur de passages parlés
échouerait :

| Mesure | Résultat |
|---|---|
| Musique restant dans `a_only`, **sur les seules trames où la voix de A est active** | **< −12 dB** (seuil du test ; valeur typique −20 dB et au-delà) |
| Voix de A conservée sur ces mêmes trames | **> −1,5 dB** |
| Commun vrai capté sur les fenêtres de bruitages brefs (20 ms) | **> −3 dB** |
| Même commun restant dans le résidu sur ces fenêtres | **8 dB de moins** que dans le commun |

## Choix de la fenêtre d'analyse

Sur le jeu synthétique, les métriques s'améliorent **monotonement** avec la longueur de
fenêtre et le lissage. C'est un artefact : le jeu est quasi stationnaire. Un second jeu,
avec des bruitages de 5 ms, donne une image différente :

| Fenêtre (lissage 3×2) | commun SI-SDR global (dB) | commun SI-SDR sur transitoires (dB) | voix dans commun (dB) | crête des transitoires conservée |
|---|---|---|---|---|
| 4096 (85 ms) | 15,06 | 21,37 | −35,78 | 1,014 |
| **8192 (171 ms)** | **18,08** | 24,46 | **−47,24** | 1,005 |
| 16384 (341 ms) | 16,53 | **25,02** | −45,82 | 1,004 |
| 16384, lissage 4×3 | 17,38 | 23,70 | −41,87 | 1,004 |

**8192 est retenu** : meilleur résultat global sur le jeu à transitoires, deuxième
meilleur sur les transitoires isolés, et deux fois plus court que 16384. Sur du matériel
riche en transitoires rapides, `--win-length 4096` reste à essayer ; le réglage est
exposé pour cette raison.

## Coût de traitement

`extract`, entrées WAV stéréo 48 kHz, mesuré dans un **processus neuf** à chaque durée :

| Durée audio | `extract` | Facteur temps réel | Pic de mémoire résidente |
|---|---|---|---|
| 1 min | 3,74 s | 16,0 × | **407 Mio** |
| 5 min | 12,96 s | 23,2 × | **407 Mio** |
| 15 min | 31,27 s | 28,8 × | **407 Mio** |
| 30 min | 61,52 s | 29,3 × | **407 Mio** |

Extrapolation pour un film de deux heures : **environ 4 minutes** de traitement, à
mémoire constante. Disque temporaire : ≈ 1,4 Mio par seconde d'audio stéréo, soit
≈ 10 Gio pour deux heures, libéré à la fin.

Le pic mémoire est **constant** parce que les pages des PCM intermédiaires déjà
consommées sont rendues au système. Sans cette libération, la mesure montait à
**2 843 Mio pour 30 minutes** et suivait linéairement la durée.

### Un piège de mesure, à connaître

`ru_maxrss` d'un processus enfant **hérite du pic du parent** au moment du `fork`. Une
première version du banc construisait le signal long en mémoire avant de lancer la
commande : les pics rapportés (2 843 Mio à 30 min) étaient ceux du parent, et masquaient
complètement l'effet de l'optimisation. Le banc écrit désormais les fixtures en flux et
lit la durée via `ffprobe`. Les chiffres ci-dessus sont confirmés indépendamment par
`/usr/bin/time -v` :

```
long300  maxrss=389604 kB
long900  maxrss=410208 kB
long1800 maxrss=411052 kB
```

## Suite de tests

```
89 tests, ~35 s   (dont 11 tests d'interface graphique, en mode « offscreen »)
```

| Fichier | Ce qu'il quantifie |
|---|---|
| `test_separation_quality.py` | fuite de voix, annulation sous la parole, transitoires, reconstruction, comparaison aux trois baselines, silence, absence de bande commune, signal long |
| `test_align.py` | signe, précision sous-échantillon, dérive, plan remplacé, refus, somme des poids de fondu |
| `test_subtract.py` | résidu de la troisième langue, base temporelle, reconstruction, bornes du gain, **réutilisation sans A ni B**, métadonnées incohérentes |
| `test_channels_and_layouts.py` | mono / stéréo / 5.1 déclaré / 6 canaux sans agencement, codec avec perte, fréquences différentes, absence de normalisation de loudness |
| `test_metadata.py` | exhaustivité de `common.json`, définitions des confiances, décimation de la chronologie |
| `test_ffmpeg_tools.py` | découverte de FFmpeg, flux multiples, décodage sans perte, WAV non écrêté, chemins Unicode et espaces |
| `test_memory.py` | la libération de pages ne perd aucune donnée ; piste d'analyse découpée équivalente au calcul direct |
| `test_cli.py` | contrat CLI exact, scripts shell, concordance README / code |
| `test_gui.py` | traitement hors thread d'interface, annulation propre, erreurs lisibles, FFmpeg absent, glisser-déposer, choix de flux |

## Ce que ces chiffres ne disent pas

Ils portent sur des **timbres synthétiques**. Ils démontrent que la mécanique est juste
— alignement, gain, absence de downmix, reconstruction, supériorité sur les baselines —
et que la séparation opère bien en recouvrement avec la parole. Ils ne disent rien de la
qualité perçue sur un film réel, où les masters M&E diffèrent, où les dialogues
partagent des segments identiques et où le mixage réagit à la voix. Voir
[limitations.md](limitations.md) et le protocole d'écoute de
[manual-verification.md](manual-verification.md), **qui n'a pas encore été exécuté**.
