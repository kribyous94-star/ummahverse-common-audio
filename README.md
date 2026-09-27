# UmmahVerse — bande commune de deux langues (POC)

Deux pistes audio du **même montage**, dans deux langues, servent à estimer la bande
commune (musique, bruitages, ambiance) — **y compris là où les dialogues la
recouvrent**. L'outil exporte le commun estimé et, pour chaque langue, le résidu.
Le commun est ensuite réutilisable pour retirer la bande commune d'une troisième
langue, sans tout recalculer.

Il existe deux façons de s'en servir, sur **le même moteur** :

* une **application de bureau** en français (Ubuntu et Windows) ;
* une **ligne de commande**, pour l'automatisation et les tests.

> **Ce que ce POC ne promet pas.** Deux mixages ne suffisent pas à déterminer
> exactement trois sources. `a_only.wav` et `b_only.wav` sont des **résidus estimés**,
> pas des voix isolées garanties. Tout ce qui est réellement identique dans les deux
> versions part dans le commun : musique et bruitages, mais aussi mêmes mots
> prononcés au même instant, voix originale laissée en fond, réverbération de
> dialogue, pompage d'un compresseur piloté par la voix. Les résultats mesurés et les
> limites sont dans [docs/results.md](docs/results.md) et
> [docs/limitations.md](docs/limitations.md).

Le retrait de la musique à l'intérieur du commun **n'est pas** réalisé ici : c'est
l'étape suivante d'UmmahVerse, avec votre outil existant.

## Sommaire

1. [Prérequis](#prérequis)
2. [Installation sur Ubuntu](#installation-sur-ubuntu)
3. [Installation sur Windows](#installation-sur-windows)
4. [Démarrer l'interface graphique](#démarrer-linterface-graphique)
5. [Parcours complet : deux langues, puis une troisième](#parcours-complet--deux-langues-puis-une-troisième)
6. [Utilisation en ligne de commande](#utilisation-en-ligne-de-commande)
7. [Les fichiers produits](#les-fichiers-produits)
8. [Quand l'outil refuse ou prévient](#quand-loutil-refuse-ou-prévient)
9. [Limites de la séparation](#limites-de-la-séparation)
10. [Performances](#performances)
11. [Dépannage](#dépannage)
12. [Tests et mesures](#tests-et-mesures)
13. [Documentation complémentaire](#documentation-complémentaire)

## Prérequis

* **Python 3.10 ou plus récent.**
* **FFmpeg et ffprobe** accessibles dans le `PATH` (tout le décodage passe par eux).
* Aucun GPU, aucun service en ligne. **Les fichiers ne quittent pas l'ordinateur.**
* Espace disque temporaire : prévoyez environ **1,4 Mio par seconde d'audio stéréo**
  (≈ 10 Gio pour un film de deux heures), libéré à la fin du traitement. Le dossier
  temporaire se choisit avec `--workspace-dir`.

### Installer FFmpeg

| Système | Commande |
|---|---|
| Ubuntu / Debian | `sudo apt update && sudo apt install ffmpeg` |
| Windows (winget) | `winget install --id Gyan.FFmpeg` puis **rouvrir** le terminal |
| Windows (chocolatey) | `choco install ffmpeg` |

Vérification, sur les deux systèmes :

```bash
ffmpeg -version
ffprobe -version
```

Si vous ne pouvez pas modifier le `PATH`, indiquez les exécutables directement :

```bash
# Ubuntu
export UMMAHVERSE_FFMPEG=/chemin/vers/ffmpeg
export UMMAHVERSE_FFPROBE=/chemin/vers/ffprobe
```

```powershell
# Windows PowerShell
$env:UMMAHVERSE_FFMPEG = "C:\ffmpeg\bin\ffmpeg.exe"
$env:UMMAHVERSE_FFPROBE = "C:\ffmpeg\bin\ffprobe.exe"
```

## Installation sur Ubuntu

```bash
cd ummahverse-common-audio
bash scripts/bootstrap.sh
```

Le script vérifie FFmpeg, crée `.venv/` et installe le projet avec ses extras
`dev` et `gui`. Pour le faire à la main :

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev,gui]'
```

## Installation sur Windows

Depuis l'invite de commandes, à la racine du projet :

```bat
scripts\bootstrap.bat
```

Depuis PowerShell :

```powershell
.\scripts\bootstrap.ps1
```

Équivalent manuel :

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,gui]"
```

> **Non vérifié sur Windows.** Le code n'utilise que `pathlib`, des appels FFmpeg par
> liste d'arguments (jamais de shell) et des chemins Unicode ; les scripts `.bat` et
> `.ps1` sont fournis. **Aucune machine Windows n'était disponible pour exécuter ce
> POC** : l'installation, le lancement et un traitement réel y restent à confirmer.
> La procédure pas à pas est dans
> [docs/manual-verification.md](docs/manual-verification.md), qui distingue
> explicitement « réussi » de « non testé ».

## Démarrer l'interface graphique

```bash
bash scripts/launch.sh          # Ubuntu
```

```bat
scripts\launch.bat              REM Windows (invite de commandes)
```

```powershell
.\scripts\launch.ps1            # Windows (PowerShell)
```

Sans les scripts :

```bash
.venv/bin/python -m ummahverse_audio gui        # Ubuntu
.\.venv\Scripts\python.exe -m ummahverse_audio gui   # Windows
```

La fenêtre s'ouvre avec deux onglets. Si FFmpeg manque, un message l'indique avec la
commande d'installation à lancer, et l'application se referme.

## Parcours complet : deux langues, puis une troisième

### Onglet 1 · Deux langues → commun

1. **Première langue (A)** et **Seconde langue (B)** : choisissez les deux fichiers,
   avec « Parcourir… » ou par glisser-déposer. Fichiers audio comme conteneurs vidéo
   sont acceptés.
2. **Flux audio** : si un fichier contient plusieurs pistes audio, la liste s'active
   et vous **devez** choisir. L'outil ne décide jamais à votre place.
3. **Dossier de sortie**.
4. **« Analyser / synchroniser »** : mesure le décalage, la dérive, le gain entre
   versions, repère les zones non comparables, et affiche les avertissements. Aucun
   fichier n'est écrit à cette étape. Lisez le journal avant d'aller plus loin.
5. **« Extraire le commun et les résidus »** : produit les quatre fichiers. La
   progression, la durée écoulée et les mesures de qualité s'affichent ; la fenêtre
   reste utilisable et **« Annuler »** arrête proprement au bloc suivant, sans laisser
   de fichier incomplet.
6. **« Ouvrir le dossier des sorties »** et **« Ouvrir le rapport JSON »** donnent
   accès aux résultats.

### Onglet 2 · Réutiliser le commun

Cet onglet est **pré-rempli automatiquement** après une extraction : `common.wav` et
`common.json` y sont déjà renseignés. Aucune ré-extraction n'est nécessaire, y
compris dans une session ultérieure — il suffit de désigner les deux fichiers.

1. **`common.wav`** et **`common.json`** issus de l'étape précédente.
2. **Piste d'une autre langue**, et son flux audio si elle en contient plusieurs.
3. **Fichier de sortie** (proposé automatiquement à partir du nom d'entrée).
4. **« Soustraire le commun »** : écrit le résidu et son rapport JSON. Cochez
   « Écrire aussi le commun adapté » pour obtenir le commun tel qu'il a été
   transformé, utile à l'écoute de contrôle.

## Utilisation en ligne de commande

Les trois commandes du contrat :

```bash
python -m ummahverse_audio extract --input-a A.wav --input-b B.wav --out-dir output [--stream-a N --stream-b N]
python -m ummahverse_audio subtract --common output/common.wav --input C.wav --metadata output/common.json --output output/c_only.wav [--stream N]
python -m ummahverse_audio gui
```

Avec l'environnement installé par `bootstrap`, remplacez `python` par
`.venv/bin/python` (Ubuntu) ou `.venv\Scripts\python.exe` (Windows), ou passez par les
scripts fournis :

```bash
bash scripts/extract.sh film_FR.wav film_AR.wav ./resultats
bash scripts/subtract.sh ./resultats/common.wav film_EN.wav ./resultats/EN_only.wav --metadata ./resultats/common.json
```

Deux commandes utiles en plus :

```bash
# Lister les pistes audio et leurs index, à utiliser avec --stream-a / --stream-b
python -m ummahverse_audio probe film_FR.mkv

# Contrôler compatibilité et alignement sans rien écrire
python -m ummahverse_audio analyze --input-a A.wav --input-b B.wav
```

### Options principales

| Option | Effet | Défaut |
|---|---|---|
| `--stream-a`, `--stream-b`, `--stream` | index du flux audio (voir `probe`) | exigé si plusieurs flux |
| `--sample-rate` | fréquence de travail imposée | la plus élevée des deux entrées |
| `--method` | `wiener` (retenue) ou une baseline de comparaison | `wiener` |
| `--transfer` | `band`, `gain` ou `none` : forme de la transformation entre versions | `band` |
| `--win-length`, `--hop` | fenêtre et pas de la STFT | 8192 / 2048 |
| `--smooth-frames`, `--smooth-bins` | voisinage de moyennage des spectres | 3 / 2 |
| `--max-offset` | décalage global maximal recherché, en secondes | 30 |
| `--block-seconds` | taille des blocs de traitement | 6 |
| `--allow-unknown-layout` | accepter un agencement de canaux non déclaré | refus |
| `--adapt`, `--block-gain-limit` | adaptation du commun dans `subtract` | `block-gain`, 1.3 |
| `--wav-format` | `float32`, `pcm24` ou `pcm16` | `float32` |
| `--workspace-dir` | dossier des fichiers temporaires | dossier système |
| `--json` | affiche les métadonnées produites sur la sortie standard | — |

`python -m ummahverse_audio extract --help` liste tout.

## Les fichiers produits

### `extract`

| Fichier | Base temporelle | Contenu |
|---|---|---|
| `common.wav` | celle de **A** (trame 0 = trame 0 de A) | bande commune estimée |
| `a_only.wav` | celle de **A** | `A − commun` |
| `b_only.wav` | celle de **B**, durée identique à B | `B − H(f)·commun` recalé dans B |
| `common.json` | — | alignement, gains, canaux, zones non comparables, confiances, versions |

Par construction, `common.wav + a_only.wav` redonne exactement A, et le commun
recalé plus `b_only.wav` redonne exactement B (erreur mesurée : ~10⁻¹⁷).

### `subtract`

| Fichier | Base temporelle | Contenu |
|---|---|---|
| `<sortie>.wav` | celle de la nouvelle piste, même durée | `C − gain·G(f)·commun aligné` |
| `<sortie>.json` | — | alignement, transformation appliquée, gain et confiance par bloc, zones non traitées |
| `<sortie>_adapted_common.wav` | celle de la nouvelle piste | commun transformé (option `--export-adapted-common`) |

Les WAV sont en **flottant 32 bits** par défaut : aucun écrêtage, même si un résidu
dépasse ±1. Convertissez en PCM entier seulement après vérification du niveau.

### Extrait de `common.json`

```json
{
  "operation": "extract",
  "tool_version": "0.1.0",
  "schema_version": "1.0",
  "params_version": "poc-1.0",
  "inputs": {
    "a": {
      "name": "a.wav", "audio_stream_index": 0, "codec": "pcm_f32le",
      "lossless": true, "channels": 2, "channel_layout": "stereo",
      "working_sample_rate": 48000, "resampled": false,
      "frames": 576000, "duration_s": 12.0
    }
  },
  "common_timebase": {
    "reference": "A", "sample_rate": 48000, "origin_s": 0.0,
    "note": "La trame 0 de common.wav correspond à la trame 0 de l'entrée A."
  },
  "alignment": {
    "sign_convention": "delay > 0 : B est en retard sur A ; trame m de A = trame m+delay de B",
    "global_delay_s": 0.075, "global_confidence": 0.86,
    "confidence_definition": "corrélation normalisée sur la piste d'analyse décimée",
    "drift_ppm": 0.0, "comparable_block_fraction": 1.0,
    "non_comparable_segments": []
  },
  "transfer": {
    "mode": "band", "estimator": "power",
    "amplitude_rule": "|H| = sqrt(ΣS_BB / ΣS_AA)",
    "coherence_threshold": 0.9, "reliable_cell_fraction": 0.31,
    "broadband_gain_per_channel": [0.8691, 0.8694]
  },
  "separation": {
    "method": "wiener", "bias_correction": 0.0, "magnitude_cap": 1.5,
    "stft": { "win_length": 8192, "hop": 2048, "smooth_frames": 3, "smooth_bins": 2 },
    "diagnostics": {
      "energy_weighted_coherence": 0.79, "common_share_of_a_energy": 0.75,
      "blocks_processed": 3, "blocks_skipped_not_comparable": 0
    }
  },
  "timeline": { "stride": 1, "entries": [
    { "t_s": 0.0, "delay_s": 0.075, "alignment_confidence": 0.87,
      "comparable": true, "coherence": 0.79, "common_share": 0.74 }
  ]},
  "warnings": [],
  "limitations": ["…"]
}
```

Les trois confiances sont **définies**, jamais inventées : voir
[docs/technical-note.md](docs/technical-note.md#confiances-rapportées).

## Quand l'outil refuse ou prévient

**Refus** (rien n'est produit, le message dit quoi faire) :

* **nombres de canaux différents** entre A et B — aucun downmix automatique, jamais ;
* **agencement de canaux inconnu au-delà de la stéréo** (6 canaux non déclarés, par
  exemple) : l'ordre réel est ambigu, relancez avec `--allow-unknown-layout` si vous
  savez que les canaux se correspondent dans l'ordre du fichier ;
* **plusieurs flux audio** sans `--stream-*` : le choix doit être explicite ;
* **entrée entièrement muette** ;
* **aucun contenu commun décelable** entre A et B, ou décalage au-delà de
  `--max-offset` ;
* **FFmpeg ou ffprobe introuvable**, fichier illisible, entrée trop courte.

**Avertissements** (le traitement continue, le journal et le JSON les conservent) :

* codec **avec perte** sur une entrée — ses artefacts diffèrent d'une langue à l'autre
  et dégradent la séparation ;
* **fréquences d'échantillonnage différentes** : conversion soxr explicite ;
* **agencements déclarés différents** à nombre de canaux égal ;
* **dérive d'horloge** au-delà de 5 ppm ;
* **moins de la moitié des blocs comparables** : montages probablement différents ;
* **très peu de cases temps-fréquence fiables** : transformation mal contrainte ;
* **gains très différents d'un canal à l'autre** : ordre des canaux à vérifier ;
* **échantillons au-delà de ±1** dans les sorties ;
* **zones non traitées** dans `subtract`.

## Limites de la séparation

Les mesures détaillées sont dans [docs/results.md](docs/results.md) ; en résumé, sur
le jeu synthétique à sources connues :

* le commun est retrouvé à **≈ 22 dB** de SI-SDR, avec **−35 dB** de fuite de voix ;
* la musique est atténuée de **≈ 36 dB** dans les résidus, **y compris sous la
  parole** ;
* la méthode retenue dépasse la meilleure baseline naïve de **+1,5 dB** sur le commun
  et de **+12 dB** sur la fuite de voix.

Ces chiffres viennent de **timbres synthétiques** : ils valident la mécanique, ils ne
prouvent rien sur un film. Ce qui dégrade le résultat sur du matériel réel :
masters M&E différents, remixage, réverbération de dialogue, compression pilotée par
la voix, montages différents, codecs avec perte, doublage laissant la voix originale
en fond. [docs/limitations.md](docs/limitations.md) détaille chaque cas, et
[docs/manual-verification.md](docs/manual-verification.md) donne le protocole d'écoute
à suivre avant toute conclusion sur un film.

## Performances

Mesuré sur Ubuntu 24.04, Python 3.12, 16 cœurs, entrées WAV stéréo 48 kHz
(`scripts/benchmark.py`, chiffres complets dans [docs/results.md](docs/results.md)) :

* environ **15 à 18 × le temps réel** pour `extract` : une heure d'audio en 3 à 4
  minutes ;
* mémoire résidente **dominée par les pages de fichiers cartographiés**, donc
  réclamables par le système ; le calcul par bloc lui-même reste borné quelle que soit
  la durée du film ;
* disque temporaire : ≈ 1,4 Mio par seconde d'audio stéréo.

## Dépannage

| Symptôme | Cause et solution |
|---|---|
| `« ffmpeg » est introuvable dans le PATH` | installez FFmpeg, rouvrez le terminal, ou définissez `UMMAHVERSE_FFMPEG` / `UMMAHVERSE_FFPROBE` |
| `contient N flux audio : le choix doit être explicite` | lancez `probe`, puis passez `--stream-a` / `--stream-b` (ou choisissez dans la liste de l'interface) |
| `Nombres de canaux différents` | préparez vous-même deux pistes de même agencement ; l'outil ne fera pas le downmix à votre place |
| `Agencement de canaux inconnu` | déclarez l'agencement avec FFmpeg, ou `--allow-unknown-layout` si l'ordre est sûr |
| `Aucun décalage global n'a pu être estimé` | pistes issues de montages différents, ou décalage > `--max-offset` : augmentez `--max-offset` |
| `Application non installée` au lancement | lancez `bash scripts/bootstrap.sh` (ou `scripts\bootstrap.bat`) |
| `PySide6 est requis pour l'interface graphique` | l'extra `gui` n'est pas installé : `pip install -e '.[gui]'` |
| Fenêtre vide ou erreur Qt sur un serveur sans écran | l'interface a besoin d'un environnement graphique ; en SSH utilisez la CLI |
| Beaucoup de « zones non comparables » | montages différents, ou une version contient des plans absents de l'autre |
| Résidu qui « pompe » ou voix abîmée | baissez `--magnitude-cap`, ou essayez `--transfer gain` ; voir [docs/limitations.md](docs/limitations.md) |
| Erreur de permissions sur le dossier de sortie | choisissez un dossier où vous pouvez écrire ; l'outil ne modifie jamais les entrées |
| Plus d'espace disque en cours de traitement | placez `--workspace-dir` sur un volume plus grand |

## Tests et mesures

```bash
# Suite complète (89 tests, ~35 s)
.venv/bin/python -m pytest -q

# Un domaine à la fois
.venv/bin/python -m pytest tests/test_separation_quality.py -q
.venv/bin/python -m pytest tests/test_align.py -q
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_gui.py -q

# Scénario synthétique de bout en bout
bash scripts/smoke.sh

# Tableau baseline / méthode retenue, et coût de traitement
.venv/bin/python scripts/benchmark.py --out-dir benchmarks --perf-seconds 60,300
```

Sous Windows :

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\benchmark.py --out-dir benchmarks
```

Les jeux de test sont **synthétiques et générés à la demande** : aucun média n'est
stocké dans le dépôt, et aucun film sous droits ne doit y entrer.

## Documentation complémentaire

| Document | Contenu |
|---|---|
| [docs/technical-note.md](docs/technical-note.md) | alignement, estimation du commun, soustraction, format des métadonnées |
| [docs/results.md](docs/results.md) | mesures baseline / méthode retenue, réglages, coût |
| [docs/limitations.md](docs/limitations.md) | ce que la méthode ne sait pas faire, et pourquoi |
| [docs/manual-verification.md](docs/manual-verification.md) | vérifications manuelles, écoute réelle, état Ubuntu / Windows |
| [SPECIFICATION.md](SPECIFICATION.md) | contrat et exigences d'origine |
| [VALIDATION.md](VALIDATION.md) | critères de validation d'origine |
