# Note technique

Comment le POC aligne les deux versions, estime la bande commune, calcule les
résidus, réutilise le commun sur une nouvelle langue, et ce que contiennent les
métadonnées. Les mesures correspondantes sont dans [results.md](results.md), les
limites dans [limitations.md](limitations.md).

## 1. Modèle et ce qu'il est possible de déduire

Deux pistes du même montage, dans deux langues :

```
A = C_A + D_A
B = C_B + D_B          avec   C_B ≈ H(f) · C_A  décalée de τ
```

`C` est la bande commune (musique, bruitages, ambiance), `D` ce qui diffère
(dialogues, et tout ce que le mixage y attache). `C_B` n'est **pas** identique à
`C_A` : gain différent, égalisation de mastering, retard, parfois remix.

Deux observations ne déterminent pas exactement trois sources arbitraires. Ce qui
rend le problème traitable est une hypothèse **statistique**, pas une identité :

> sur un voisinage temps-fréquence, `D_A` et `D_B` sont décorrélés entre eux et du
> commun.

Sous cette seule hypothèse, l'inter-spectre moyenné vaut

```
S_AB = E[A · conj(B)] = conj(H) · S_CC
```

parce que les trois termes croisés `E[C·conj(D_B)]`, `E[D_A·conj(C)]` et
`E[D_A·conj(D_B)]` s'annulent. **La puissance du commun est donc mesurable même
là où le dialogue le recouvre**, sans jamais détecter la parole. C'est le point
central : un détecteur de passages parlés / non parlés ne répondrait pas au besoin,
alors que cette relation est valable dans chaque case, dialogue ou pas.

Ce que cette hypothèse ne dit pas : elle ne distingue pas *ce qui est commun* de
*ce qui est de la musique*. Mêmes mots prononcés au même instant, voix originale
laissée en fond, respirations identiques, réverbération de dialogue, pompage d'un
compresseur piloté par la voix : tout cela est corrélé entre les deux versions et
sera capté comme commun. « Cohérence élevée » ne veut pas dire « musique et
bruitages ».

## 2. Décodage et canaux

* `ffprobe` liste les flux audio ; **le choix est explicite** dès qu'il y en a
  plusieurs (`--stream-a`, `--stream-b`, `--stream`, ou la liste de l'interface).
* Décodage en **PCM float32** vers un fichier temporaire, lu ensuite par
  `numpy.memmap`. Aucun `-ac` n'est passé à FFmpeg : le nombre et l'ordre des canaux
  du flux source sont conservés. Aucune normalisation de loudness n'est appliquée.
* Fréquence de travail : celle des deux entrées si elles concordent, sinon la plus
  élevée, convertie avec `soxr` et signalée dans les métadonnées.
* Agencement des canaux : `stereo` et `mono` sont déduits du nombre de canaux quand
  le conteneur ne déclare rien (un WAV nu ne déclare rien, et ces deux conventions
  sont sans ambiguïté). **Au-delà de deux canaux sans agencement déclaré, l'outil
  refuse** : l'ordre réel est ambigu (5.1 contre 5.1(side), 6.0, mappings maison), et
  `--allow-unknown-layout` est nécessaire pour apparier les canaux dans l'ordre du
  fichier. Nombres de canaux différents : refus, **jamais** de downmix implicite.
* Tous les canaux sont traités **indépendamment**, avec la même carte d'alignement
  (estimée sur une somme mono servant uniquement à l'analyse) et un `H(f)` propre à
  chaque canal.

## 3. Alignement

### Convention de signe

`delay > 0` signifie que **B est en retard sur A** : la trame `m` de A correspond à la
trame `m + delay` de B. La même convention est écrite dans `common.json`, sous
`alignment.sign_convention`.

### Décalage global

Sur une piste d'analyse mono décimée à 4 kHz, par **GCC-PHAT** : corrélation croisée à
phase blanchie, `r = IDFT(X·conj(Y) / |X·conj(Y)|)`. Le blanchiment évite qu'un
spectre dominé par les basses fréquences étale le pic.

Plusieurs **sondes** réparties dans le signal (9 fenêtres de 20 s par défaut) sont
corrélées séparément, et la médiane des décalages est retenue : un générique musical
ou un long silence ne peut pas imposer à lui seul un décalage faux. La fenêtre de
recherche est bornée par `--max-offset` (30 s) et, sur un extrait court, par la moitié
de sa durée.

Un décalage est jugé crédible si le **rapport pic/efficace** de la corrélation atteint
10, ou si la corrélation normalisée atteint 0,05. Le seuil de 10 est calibré : deux
bruits indépendants plafonnent vers 6,5, un alignement réel dépasse 20 — y compris
quand une dérive d'horloge effondre la corrélation à décalage fixe. Sinon, refus
explicite. Une entrée entièrement muette est refusée séparément, avec son propre
message.

### Décalages locaux, sous-échantillon, dérive et coupes

Par bloc (6 s moins le fondu, soit 5,75 s par défaut) :

1. décalage grossier par GCC-PHAT sur la piste à 4 kHz, dans `±--local-search`
   (0,35 s) autour du décalage global ;
2. raffinement **à pleine résolution** autour de ce point ;
3. partie **sous-échantillon** par interpolation du pic : la corrélation autour du
   maximum est suréchantillonnée ×32, ce qui donne une précision mesurée meilleure que
   0,1 échantillon (test `test_subsample_delay_precision`).

La partie entière sert à lire le bloc de B ; la partie fractionnaire est appliquée
comme **rampe de phase** sur son spectre, ce qui évite un rééchantillonnage.

Chaque bloc porte une **confiance** : la médiane des corrélations normalisées de
plusieurs sous-fenêtres de la zone recouverte. La médiane, et non une corrélation
unique sur tout le bloc, parce qu'une dérive ou un désaccord local effondrerait cette
dernière alors que l'alignement est juste.

Sous `--confidence-threshold` (0,12), le bloc est déclaré **non comparable** : coupe,
plan absent d'une version, ou absence de contenu commun. Les décalages localement
aberrants sont ramenés à la médiane et le bloc marqué. La pente des décalages en
fonction du temps donne la **dérive d'horloge en ppm**, signalée au-delà de 5 ppm ;
elle est absorbée bloc par bloc, mais un rééchantillonnage des sources reste
préférable. Pas de DTW libre : un alignement élastique piloté par les dialogues
déformerait précisément ce qu'on cherche à comparer.

## 4. Transformation entre versions : `H(f)`

`H(f)` est estimé **uniquement sur les cases temps-fréquence fiables**, définies par
une cohérence quadratique locale `γ² ≥ 0,9` — les cases où le commun domine, donc peu
influencées par les dialogues. Les sondes sont réparties sur tout le signal (32 blocs
par défaut).

Deux estimateurs d'amplitude, tous deux disponibles (`--transfer-estimator`) :

| Estimateur | Formule | Biais |
|---|---|---|
| `ls` | `\|H\| = \|ΣS_AB\| / ΣS_AA` | régression de B sur A : **biaisée vers le bas** d'un facteur `1/(1 + S_DA/S_CC)` |
| `power` (défaut) | `\|H\| = sqrt(ΣS_BB / ΣS_AA)` | moyenne géométrique de `ls` (biaisé bas par `D_A`) et de `ΣS_BB/\|ΣS_AB\|` (biaisé haut par `D_B`) : **exact quand les deux bruits relatifs sont égaux**, entre les deux sinon |

La phase vient de l'inter-spectre : `arg(H) = −arg(ΣS_AB)`. Mesuré sur le jeu
synthétique (gain vrai 0,870) : `power` donne 0,869, `ls` donne 0,848.

`H(f)` est lissé sur la fréquence (±4 bins) pour rester une enveloppe de mastering et
non suivre le dialogue, son amplitude est bornée au quart et au quadruple du gain
large bande, et les bins sans assez de cases fiables retombent sur ce gain sans
inventer de phase. `--transfer gain` réduit `H` à un scalaire par canal, `--transfer
none` le fixe à 1 ; les deux servent de points de comparaison.

Biais assumé : sélectionner les cases sur la cohérence mesurée dans les mêmes données
favorise les cases où le commun domine — c'est l'effet recherché, mais cela surestime
la fiabilité affichée.

## 5. Estimation du commun

STFT par défaut : fenêtre de Hann périodique de **8192 échantillons** (171 ms à
48 kHz), pas de **2048** (recouvrement 75 %), via `scipy.signal.ShortTimeFFT`. La
reconstitution STFT → ISTFT est exacte à 10⁻¹⁵ près, bords compris.

Les auto-spectres et l'inter-spectre sont moyennés sur un voisinage de **±3 trames et
±2 bins** (soit 299 ms × 29 Hz). C'est l'équivalent, en STFT, du moyennage de Welch :
sans lui, l'inter-spectre d'une case isolée n'a aucune valeur statistique ; avec lui,
la résolution temps-fréquence baisse. Ce compromis est le paramètre le plus sensible
de la méthode (`--smooth-frames`, `--smooth-bins`, `--win-length`).

### Puissance commune

```
S_CC = sqrt( max(|S_AB|² − b·S_AA·S_BB/N , 0) ) / |H|
S_CC ← min( S_CC , S_AA , S_BB/|H|² )
```

Le second terme du numérateur est une **correction du biais de moyennage** : sur `N`
cases indépendantes, `E|Ŝ_AB|² ≈ |S_AB|² + S_AA·S_BB/N`. Elle est implémentée
(`--bias-correction`) mais **désactivée par défaut** : mesurée, elle dégrade toutes les
métriques (commun 21,26 dB contre 21,70 ; musique dans le résidu −28,6 dB contre
−35,8 dB). L'explication est que la contrainte
`S_CC ≤ min(S_AA, S_BB/|H|²)` borne déjà la surestimation, alors que la correction,
elle, sous-estime la puissance commune et affaiblit l'annulation. C'est un résultat
mesuré, contraire à ce que la théorie seule suggérait.

### Filtre MMSE à deux observations

Avec `y = [A; B]`, `h = [1; H]` et un bruit `diag(S_DA, S_DB)` où
`S_DA = S_AA − S_CC` et `S_DB = S_BB − |H|²S_CC` (plancher positif), l'estimateur de
Wiener s'écrit :

```
        S_CC · ( A/S_DA + conj(H)·B/S_DB )
Ĉ = ───────────────────────────────────────────
        1 + S_CC · ( 1/S_DA + |H|²/S_DB )
```

Les deux mixages contribuent, pondérés par leur propre bruit local : là où A est
presque entièrement commun, le gain tend vers 1 ; là où le dialogue de A domine, c'est
B qui porte l'estimation. **L'amplitude et la phase** sont estimées, pas seulement un
masque réel.

Garde-fou contre le surajustement : `|Ĉ|` est plafonné à `--magnitude-cap` (1,5) fois
le plus faible de `|A|` et `|B/H|`. La fraction de cases plafonnées est journalisée.
Une annulation adaptative peut effacer une voix ; cette borne et la contrainte sur
`S_CC` limitent le risque sans le supprimer.

### Baselines conservées

`--method average` (moyenne complexe après compensation de `H`), `min-magnitude`
(module minimum, phase de A) et `coherence-mask` (`Ĉ = γ²·A`) restent disponibles et
sont comparées dans [results.md](results.md) et dans la suite de tests elle-même.

## 6. Résidus et reconstruction

Le commun est estimé dans le **repère de A**. Puis :

* `a_only = A − Ĉ` dans le repère de A ;
* pour B, le commun est rendu dans **son** repère : `Ĉ_B = H(f)·Ĉ` retardée de la
  partie fractionnaire, écrite à la position entière du bloc, puis
  `b_only = B − Ĉ_B`.

Les deux résidus sont donc, par construction, `entrée − commun rendu` : la somme
reconstitue exactement l'entrée. L'erreur mesurée est de l'ordre de **10⁻¹⁷**, c'est-à-
dire l'erreur d'arrondi du float32. Aucune « reconstruction » non mesurée ne vient
combler une perte d'identifiabilité.

### Zones non traitées

Blocs non comparables : le commun vaut **zéro** et le résidu est identique à l'entrée.
Dans le repère de B, un changement de décalage local peut aussi laisser une zone sans
couverture ; le poids cumulé y tombe sous `WEIGHT_FLOOR` et la même politique
s'applique. Ces intervalles sont listés dans les métadonnées
(`outputs.measurements.*.untreated_ranges`). Rien n'est extrapolé.

## 7. Traitement par blocs et mémoire

* Blocs de **6 s** (`--block-seconds`), avec une **marge d'analyse** de deux fenêtres
  de chaque côté (les bords d'une STFT sont mal estimés) et un **fondu croisé** de
  0,25 s entre blocs utiles. Les poids de fondu somment exactement à 1, ce qui évite
  tout clic ou saut de niveau aux raccords (test `test_block_plan_weights_sum_to_one`).
* Côté A, les blocs arrivent dans l'ordre : `common.wav` et `a_only.wav` sont écrits
  au fil de l'eau. Côté B, le commun rendu peut se chevaucher ou laisser un trou, il
  est accumulé sur disque puis `b_only.wav` est écrit en une passe.
* Les pages des PCM intermédiaires déjà consommées sont **rendues au système**
  (`MADV_DONTNEED` sur les cartographies partagées, sans effet là où l'avis n'existe
  pas, par exemple sous Windows). Sans cela le pic de mémoire résidente suivait la
  durée du film : mesuré à 2 843 Mio pour 30 minutes d'audio, contre **402 Mio, stable
  de 1 à 30 minutes**, une fois la libération en place. L'opération ne perd aucune
  écriture — les pages modifiées d'une cartographie partagée appartiennent au cache de
  fichiers — et `tests/test_memory.py` le vérifie.
* Disque temporaire : ≈ 1,4 Mio par seconde d'audio stéréo, supprimé à la fin.

## 8. Réutilisation du commun : `subtract`

`subtract` ne lit **ni A ni B** : `common.wav`, `common.json` et la nouvelle piste
suffisent. C'est vérifié par un test qui déplace A et B hors d'atteinte avant de
lancer la commande.

1. Alignement du commun sur la nouvelle piste, avec la même machinerie (décalage
   global multi-sondes, carte locale sous-échantillon, dérive, blocs non comparables).
2. Estimation de `G(f)` tel que `C ≈ G(f)·commun`, sur les cases fiables. L'ordre de
   la régression est choisi : le commun est la variable explicative, parce que son
   bruit propre est faible — l'inverse serait davantage biaisé.
3. Adaptation **prudente** : un gain scalaire par bloc, estimé sur les cases fiables
   du bloc, **borné** à `[1/1,3 ; 1,3]` autour de `G(f)`, et seulement si le bloc
   contient au moins 200 cases fiables. `--adapt global` désactive ce gain.
4. `residu = C − gain·G(f)·commun aligné`, dans le repère de la nouvelle piste, à sa
   durée exacte.

**Aucun masque adaptatif par case temps-fréquence n'est appliqué ici.** Ce serait le
moyen le plus rapide de faire disparaître la voix de la nouvelle langue en croyant
retirer la musique. Le rapport JSON donne la transformation appliquée, le gain et une
confiance locale par bloc, et les zones non traitées.

## 9. Format des métadonnées

`common.json` (`operation: "extract"`) est le contrat entre les deux commandes.

| Section | Contenu |
|---|---|
| `tool_version`, `schema_version`, `params_version` | versions de l'outil, du format, des paramètres de traitement |
| `environment` | plateforme, version de Python, version de FFmpeg |
| `inputs.a` / `inputs.b` | chemin, index de flux (relatif audio **et** absolu), codec, caractère sans perte, fréquence source et de travail, rééchantillonnage, canaux, agencement retenu **et** déclaré, trames, durée |
| `common_timebase` | repère de `common.wav` : référence `A`, origine, fréquence, canaux, trames |
| `alignment` | convention de signe, décalage global et sa confiance, définition de la confiance, seuil, dérive en ppm, fraction de blocs comparables, segments comparables et non comparables |
| `transfer` | mode, estimateur et sa formule, seuil de cohérence, fraction de cases fiables, gain large bande par canal, réponse par bande d'octave, avertissements |
| `separation` | méthode, correction de biais, plafond d'amplitude, paramètres STFT (fenêtre, pas, lissage en secondes et en Hz, cases indépendantes), géométrie des blocs, diagnostics |
| `outputs` | rôle et repère de chaque fichier, format, crêtes, erreur de reconstruction, zones non traitées |
| `timeline` | une entrée par bloc : instant, décalage, confiance d'alignement, comparabilité, cohérence, part du commun — décimée au-delà de 4000 entrées, avec le pas indiqué |
| `performance` | durée totale, durée par étape, pic de mémoire résidente |
| `warnings`, `limitations` | avertissements de l'exécution, limites rappelées systématiquement |

Le rapport de `subtract` (`operation: "subtract"`) a la même structure, plus
`applied_transform` (modèle, mode d'adaptation, bornes, réponse) et la trace des
entrées d'origine, reprise de `common.json` **sans les relire**.

### Confiances rapportées

Trois grandeurs, toutes calculées, aucune inventée :

| Champ | Définition | Domaine |
|---|---|---|
| `alignment.global_confidence`, `timeline[].alignment_confidence` | médiane des corrélations normalisées sur des sous-fenêtres, à la position retenue, sur la piste d'analyse décimée | `[0, 1]` |
| `diagnostics.energy_weighted_coherence`, `timeline[].coherence` | cohérence quadratique locale moyennée, pondérée par l'énergie de A | `[0, 1]` |
| `diagnostics.common_share_of_a_energy`, `timeline[].common_share` | part de l'énergie de A attribuée au commun | `≥ 0` |

Aucune n'est une probabilité de bonne séparation. Une valeur élevée signifie « les
deux versions se ressemblent ici », pas « le contenu extrait est de la musique ».

## 10. Mesures objectives

`ummahverse_audio.metrics` évalue une sortie par **décomposition aux moindres carrés
conjointe** sur les sources vraies du jeu synthétique :

```
y ≈ Σ cᵢ·sᵢ + e        c = argmin ‖y − Σ cᵢ sᵢ‖²
```

`cᵢ` en dB dit quelle fraction de la source `i` se retrouve dans `y` (0 dB = présente
en entier, −20 dB = atténuée de 20 dB) ; `artifacts_db` est l'énergie de `e` relative à
celle de `y`, c'est-à-dire ce qu'aucune source vraie n'explique. La décomposition doit
être conjointe : musique et voix synthétiques ne sont pas orthogonales, des
projections séparées se contamineraient. S'y ajoutent le SI-SDR usuel et un SNR sans
ajustement d'échelle.

## 11. Ce qui n'est pas fait ici

Le **retrait de la musique à l'intérieur du commun** n'est pas implémenté : c'est
l'étape suivante d'UmmahVerse, avec l'outil existant, suivie de la recombinaison des
bruitages avec les dialogues. Le POC s'arrête à la production d'un `common.wav`
mesuré, de résidus exacts, et des métadonnées nécessaires pour enchaîner.
