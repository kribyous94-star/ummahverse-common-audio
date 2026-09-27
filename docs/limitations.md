# Limites de la séparation

Ce document dit ce que la méthode **ne sait pas faire**, et pourquoi. Il est destiné à
être lu avant de conclure quoi que ce soit sur un film. Les chiffres cités viennent de
[results.md](results.md), la mécanique de [technical-note.md](technical-note.md).

## 1. La limite de fond : deux observations, trois sources

`A = C + D_A` et `B = H·C + D_B` : deux équations, trois inconnues par instant. Rien
ne permet, à partir de deux mixages seuls, de séparer exactement un commun et deux
voix arbitraires.

Ce que le POC exploite à la place est une hypothèse **statistique** — `D_A` et `D_B`
décorrélés entre eux et du commun sur un voisinage temps-fréquence. Elle est
raisonnable et elle donne une mesure de la puissance commune même sous les dialogues,
mais elle reste une hypothèse. Chaque fois qu'elle est fausse, le résultat se dégrade
de façon prévisible, et les sections suivantes disent comment.

**Conséquence de vocabulaire, à ne pas perdre de vue :** `a_only.wav` et `b_only.wav`
sont des **résidus**. Ils contiennent ce qui n'a pas été reconnu comme commun. Ce n'est
pas la même chose qu'une piste de dialogue.

## 2. « Cohérent » ne veut pas dire « musique et bruitages »

C'est l'erreur d'interprétation la plus coûteuse. Tout ce qui est réellement identique
entre les deux versions part dans le commun, y compris :

* **les mêmes mots au même instant** — noms propres, onomatopées, chiffres, cris,
  rires, interjections. Un doublage fidèle en produit beaucoup ;
* **la voix originale laissée en fond** d'une version doublée — fréquent sur les
  ambiances de foule, les voix lointaines, les annonces ;
* **les respirations, souffles et bruits de bouche** conservés du mixage d'origine ;
* **la réverbération et les départs d'écho du dialogue**, quand ils sont imprimés dans
  la bande M&E plutôt que recréés par langue ;
* **le pompage d'un compresseur ou d'un ducking piloté par la voix** : la musique n'est
  pas la même dans les deux versions, mais sa *modulation* est corrélée à la parole ;
* **les génériques**, souvent identiques d'une version à l'autre, dialogue chanté
  compris.

La cohérence mesurée est élevée dans tous ces cas. Le commun les prendra, et le résidu
en sera privé. C'est pourquoi les métadonnées ne présentent jamais la cohérence comme
une preuve de nature de source.

## 3. La cohérence est une statistique, pas un détecteur

`S_AB` est un inter-spectre **moyenné** sur un voisinage (±3 trames et ±2 bins par
défaut, soit ≈ 299 ms × 29 Hz). Deux conséquences :

* sur un voisinage court, l'estimation est bruitée et la valeur d'une case isolée ne
  veut rien dire ;
* sur un voisinage long, la résolution temps-fréquence baisse : un transitoire bref ou
  une syllabe courte est moyenné avec ce qui l'entoure.

Il n'existe pas de réglage universel. Sur le jeu synthétique, allonger la fenêtre et le
lissage améliore **monotonement** les métriques — mais ce jeu est quasi stationnaire.
Un test dédié avec des bruitages de 5 ms montre le compromis réel : une fenêtre de
16384 échantillons (341 ms) est meilleure sur les transitoires isolés mais **moins
bonne globalement** que 8192, retenue par défaut. Sur du matériel réel riche en
transitoires rapides, essayez `--win-length 4096`.

## 4. Le risque de surajustement : effacer une voix

Une annulation adaptative peut faire disparaître une voix en croyant retirer de la
musique. Trois garde-fous, tous mesurables et tous imparfaits :

* la puissance commune est bornée par `min(S_AA, S_BB/|H|²)` ;
* l'amplitude du commun estimé est plafonnée à `--magnitude-cap` (1,5) fois le plus
  faible des deux mixages ; la fraction de cases plafonnées est journalisée ;
* dans `subtract`, **aucun masque par case** n'est appliqué — seulement un gain scalaire
  par bloc, borné à `[1/1,3 ; 1,3]`.

Sur le jeu synthétique, la voix est préservée à **−0,16 dB** près et la fuite de voix
dans le commun reste à **−35 dB**. Ces chiffres ne se transposent pas tels quels à un
film. Si vous entendez du pompage, une voix amincie ou des débuts de mots mangés,
baissez `--magnitude-cap`, ou passez à `--transfer gain`.

## 5. Ce qui dégrade le résultat sur du matériel réel

Par ordre décroissant d'impact observé ou attendu :

| Situation | Effet | Signalé par l'outil ? |
|---|---|---|
| **Masters M&E différents** (remix, nouvelle version musicale) | `C_B` n'est plus une simple transformation de `C_A` : la cohérence chute, l'annulation devient partielle | oui, via la fraction de cases fiables et de blocs comparables |
| **Montages différents** (plans ajoutés, coupés, réordonnés) | zones non comparables ; au-delà d'un saut de `--local-search`, la suite décroche | oui, segments non comparables et avertissement |
| **Codecs avec perte, différents par langue** | les artefacts de codage ne sont pas corrélés, ils restent dans les résidus et brouillent `H(f)` | oui, avertissement « codec avec perte » |
| **Dialogue compressant la musique** (ducking, side-chain) | la modulation de la musique est corrélée à la parole : le commun capte une partie de l'enveloppe de voix | non directement mesurable |
| **Réverbération de dialogue imprimée dans la M&E** | queues de réverbération communes, absentes du résidu | non |
| **Voix originale résiduelle** dans une version doublée | captée comme commun | non |
| **Dérive d'horloge** (sources de cadences différentes) | décalage croissant ; absorbé bloc par bloc mais l'annulation se dégrade à l'intérieur d'un bloc | oui, dérive en ppm au-delà de 5 ppm |
| **Agencements de canaux mal appariés** | annulation nulle ou négative sur les canaux échangés | partiellement : gains très différents entre canaux |
| **Mixages 5.1 avec LFE ou surrounds traités par langue** | canaux moins corrélés que le L/R | non |
| **Niveaux très différents** | pas un problème en soi : mesuré comme gain, pas normalisé séparément | oui, gain reporté |

## 6. Ce que le POC refuse de faire

Un refus est une réponse, pas un échec :

* **mélanger les canaux** — jamais de downmix implicite, même 5.1 → stéréo ;
* **deviner l'agencement** au-delà de la stéréo quand le conteneur ne le déclare pas ;
* **choisir un flux audio** à votre place quand il y en a plusieurs ;
* **aligner du silence** ou deux pistes sans contenu commun décelable ;
* **inventer une bande commune** dans les zones non comparables : le commun y vaut zéro
  et le résidu reste l'entrée, intervalles listés dans les métadonnées ;
* **fabriquer une confiance** : les trois grandeurs rapportées sont des calculs
  définis, pas des scores arbitraires.

## 7. Limites de la validation elle-même

* **Les mesures publiées portent sur des timbres synthétiques.** Sinus, bruit filtré et
  voix harmonique artificielle valident la mécanique — signe et précision de
  l'alignement, gain, absence de downmix, reconstruction, comparaison aux baselines.
  Elles ne prouvent rien sur un film.
* **Aucune écoute réelle n'a été faite** dans le cadre de cette livraison : aucun média
  sous droits n'a été introduit. Le protocole d'écoute est dans
  [manual-verification.md](manual-verification.md) et reste à exécuter.
* **Windows n'a pas été testé.** Le code est portable par construction (uniquement
  `pathlib`, appels FFmpeg par liste d'arguments, aucune dépendance à un shell), les
  scripts `.bat` et `.ps1` sont fournis, mais aucune machine Windows n'était disponible.
  La différence entre « non testé » et « réussi » est tenue explicitement partout.
* **Le jeu synthétique favorise les fenêtres longues** parce qu'il est quasi
  stationnaire. Les défauts choisis par défaut tiennent compte d'un second jeu à
  transitoires brefs, mais un film reste plus varié que les deux.

## 8. Si la musique sous la voix n'est pas bien séparée

Dites-le avec les chiffres plutôt que de régler à l'oreille jusqu'à ce que ça passe.
Sur le jeu synthétique, la musique est atténuée de **35,8 dB** dans le résidu de A,
**y compris pendant la parole** (test dédié : atténuation mesurée sur les seules trames
où la voix est active). Si sur votre matériel vous obtenez nettement moins :

1. **Lisez d'abord le rapport.** `reliable_cell_fraction` faible, ou
   `comparable_block_fraction` faible, veut dire que le problème est en amont — masters
   différents ou montages différents — et aucun réglage de séparation ne le corrigera.
2. **Vérifiez l'alignement** : `global_confidence` et la colonne `delay_s` de la
   chronologie. Un décalage qui saute d'un bloc à l'autre indique un montage différent.
3. **Essayez `--win-length 4096`** si le matériel est riche en transitoires, ou
   `--smooth-frames 6` si le mixage est stationnaire et bruité.
4. **Comparez aux baselines** : `--method min-magnitude` et `--method average` sur le
   même extrait. Si une baseline fait mieux que la méthode retenue sur votre matériel,
   c'est une information, pas un accident — signalez-la.
5. **Expérience suivante à mener**, dans l'ordre de coût croissant :
   * une **troisième langue** en entrée de l'estimation (et non seulement en
     vérification) : trois observations pour trois sources lèvent une partie de
     l'indétermination, au prix d'une contrainte de disponibilité que le contrat actuel
     exclut pour `extract` ;
   * une **estimation de `H(f)` variable dans le temps** (par tranche de plusieurs
     minutes), pour suivre un mastering non stationnaire ;
   * une **résolution multi-échelle** : combiner deux tailles de fenêtre, la longue
     pour la musique stationnaire, la courte pour les transitoires, en choisissant par
     case celle dont l'estimation est la plus stable ;
   * un **rééchantillonnage explicite** des sources quand la dérive dépasse quelques
     dizaines de ppm, au lieu de l'absorber bloc par bloc.
