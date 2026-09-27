# Vérifications manuelles — ce qui a été fait, ce qui reste à faire

Ce document distingue strictement **réussi** (exécuté, avec le résultat observé) de
**non testé** (procédure fournie, jamais exécutée). Aucune vérification n'y est
présentée comme faite sans l'avoir été.

## Tableau récapitulatif

| Vérification | Ubuntu 24.04 | Windows |
|---|---|---|
| Installation (`install.sh` / `install.bat`) | ✅ réussi | ⬜ **non testé** |
| Installation confinée (rien hors du dossier) | ✅ réussi, mesuré | ⬜ **non testé** |
| Lancement par `run.sh` / `run.bat` | ✅ réussi | ⬜ **non testé** |
| Lancement de l'interface | ✅ réussi | ⬜ **non testé** |
| Sélection des deux entrées | ✅ réussi | ⬜ **non testé** |
| Choix explicite du flux audio | ✅ réussi | ⬜ **non testé** |
| « Analyser / synchroniser » | ✅ réussi | ⬜ **non testé** |
| « Extraire le commun et les résidus » | ✅ réussi | ⬜ **non testé** |
| Ouverture des sorties | ✅ réussi (boutons actifs, `QDesktopServices`) | ⬜ **non testé** |
| Deuxième langue et soustraction | ✅ réussi | ⬜ **non testé** |
| Annulation d'un long calcul | ✅ réussi (test automatisé) | ⬜ **non testé** |
| Message d'erreur si FFmpeg manque | ✅ réussi (test automatisé) | ⬜ **non testé** |
| Fenêtre réactive pendant le traitement | ✅ réussi (test automatisé) | ⬜ **non testé** |
| Chemins avec espaces et accents | ✅ réussi (test automatisé) | ⬜ **non testé** |
| Dossier de sortie non vide | ✅ réussi (écrasement des sorties, voir plus bas) | ⬜ **non testé** |
| Fichiers d'entrée jamais modifiés | ✅ réussi, mesuré | ⬜ **non testé** |
| Erreur de permissions | ✅ réussi (dossier en lecture seule refusé) | ⬜ **non testé** |
| CLI `extract` / `subtract` / `probe` / `analyze` | ✅ réussi | ⬜ **non testé** |
| `bash scripts/smoke.sh` | ✅ réussi | s. o. (script bash) |
| Suite `pytest` | ✅ réussi, 154 tests | ⬜ **non testé** |
| **Écoute réelle sur un film** | ⬜ **non fait** | ⬜ **non fait** |

## 1. Ubuntu — ce qui a été exécuté

### Environnement

| | |
|---|---|
| Système | Ubuntu 24.04, session X11 |
| Python | 3.12.3 |
| PySide6 | 6.11.2 |
| FFmpeg | 6.1.1-3ubuntu5 |

### Interface graphique, parcours complet

La fenêtre a été ouverte sur l'affichage X réel (pas seulement en mode « offscreen »),
et le parcours complet a été exécuté sur le jeu synthétique de
`scripts/generate_fixtures.py` :

1. sélection de `a.wav` et `b.wav`, détection du flux audio unique (liste verrouillée,
   aucun choix ambigu à faire) ;
2. **« Analyser / synchroniser »** → décalage 75,00 ms (valeur vraie : 75,00 ms),
   dérive 0,0 ppm, 100 % de blocs comparables, gains 0,8691 / 0,8694 (valeur vraie :
   0,870), aucun avertissement ;
3. **« Extraire le commun et les résidus »** → quatre fichiers produits en 1,1 s ;
   journal affichant cohérence pondérée 0,760, part du commun 77,6 % de l'énergie de A,
   crêtes, erreur de reconstruction 1,4 × 10⁻¹⁷ (A) et 0 (B), pic mémoire 451 Mio ;
4. bascule automatique des champs de l'onglet 2 vers `common.wav` et `common.json` ;
5. sélection de `third.wav`, **« Soustraire le commun »** → décalage 130,00 ms (valeur
   vraie : 130,00 ms), gain appliqué médian 0,991, 49,1 % de cases fiables, résidu et
   rapport écrits, avertissement correct sur 0,13 s non traitées.

Comportements vérifiés en plus, par les tests automatisés de `tests/test_gui.py`
(plateforme Qt « offscreen », 11 tests) :

* le traitement s'exécute **hors du thread d'interface** — la vérification exige
  plusieurs tours de boucle d'événements pendant le calcul ;
* **annulation** d'une extraction de 40 s : arrêt au bloc suivant, `common.wav` et
  `a_only.wav` **absents** (pas de fichier partiel), boutons réactivés immédiatement ;
* **erreur lisible** sur entrées incompatibles (stéréo contre 5.1) : message contenant
  l'explication du refus de downmix, **aucune trace Python** ;
* **FFmpeg absent** : boîte de dialogue avec la commande d'installation ;
* **glisser-déposer** d'un fichier sur un champ ;
* **plusieurs flux audio** : liste activée, choix exigé, sélection prise en compte.

### Installation autonome et confinement

`bash install.sh --clean` exécuté depuis une copie neuve du dossier : environnement
créé en 16 s, moteur et interface vérifiés à l'import. Confinement **mesuré**, et non
supposé :

| Contrôle | Résultat |
|---|---|
| Nombre de fichiers dans `~/.cache/pip` avant / après une installation complète | **4569 / 4569** — inchangé |
| Fichiers écrits dans le répertoire personnel par un traitement complet | **aucun** (les seuls fichiers touchés venaient d'autres applications) |
| Répertoires `ummahverse-*` restant dans `/tmp` après traitement | **0** |
| Contenu de `.cache/workspace/` après un traitement | **vide** (nettoyé à la fin) |
| Emplacement de tout ce qui est installé | `.venv` 893 Mio, `.cache` 301 Mio — dans le dossier |

`bash run.sh` lance bien l'interface depuis le `.venv` local, et sans session
graphique affiche le message attendu avec le code de sortie 1.

### Ligne de commande

```bash
.venv/bin/python -m ummahverse_audio probe    fixtures/a.wav
.venv/bin/python -m ummahverse_audio analyze  --input-a fixtures/a.wav --input-b fixtures/b.wav
.venv/bin/python -m ummahverse_audio extract  --input-a fixtures/a.wav --input-b fixtures/b.wav --out-dir result
.venv/bin/python -m ummahverse_audio subtract --common result/common.wav --input fixtures/third.wav \
                                              --metadata result/common.json --output result/third_only.wav
bash scripts/smoke.sh
```

Toutes exécutées avec succès. `smoke.sh` lance la suite `pytest` puis le scénario
synthétique de bout en bout et vérifie la présence des cinq fichiers attendus.

### Chemins particuliers

Vérifié automatiquement (`test_unicode_and_spaces_in_paths`) : un dossier nommé
`dossier accentué éàü 日本` contenant `piste de test ré.wav` est sondé et décodé
correctement. Aucun appel n'utilise de shell, les chemins passent en arguments séparés.

### Dossier de sortie non vide

Relancer `extract` vers un dossier contenant déjà des sorties **écrase** `common.wav`,
`a_only.wav`, `b_only.wav` et `common.json` sans demander confirmation (`ffmpeg -y`).
Comportement attendu pour un POC scriptable ; si vous tenez à vos résultats
précédents, utilisez un dossier différent.

En revanche, **aucun fichier d'entrée ne peut être écrasé** : si l'un d'eux se trouve
dans le dossier de sortie et porte le nom d'une sortie, l'opération est refusée avant
toute écriture.

### Fichiers d'entrée jamais modifiés

Vérifié par mesure, pas par principe :

| Contrôle | Résultat |
|---|---|
| Empreinte SHA-256, taille et date de modification de A et B après une extraction | **inchangées** |
| Idem pour `common.wav`, `common.json` et la troisième langue après une soustraction | **inchangées** |
| Traitement avec des entrées en **lecture seule** (`chmod 444`) | **réussit** |
| `--output` égal à `--input` | **refusé**, entrée intacte (vérifié aussi avant correction : elle était bien écrasée) |
| Sortie désignant une entrée via un lien symbolique, un lien dur ou un chemin relatif | **refusée** |
| Deux sorties désignant le même fichier | **refusées** |
| Dossier de sortie en lecture seule | **refusé** avec un message explicite |

Dans l'interface, le refus arrive **au clic** : aucun traitement ne démarre, le bouton
reste actif et l'entrée n'est pas touchée.

## 2. Windows — procédure à exécuter

**Aucune machine Windows n'était disponible.** Rien de ce qui suit n'a été exécuté, et
aucune de ces lignes ne doit être lue comme un succès. Ce qui justifie l'attente que
cela fonctionne : le moteur n'utilise que `pathlib`, tous les appels FFmpeg passent par
une liste d'arguments (jamais `shell=True`), les fenêtres de console FFmpeg sont
supprimées via `CREATE_NO_WINDOW`, la mesure de mémoire a une implémentation
`GetProcessMemoryInfo`, et la libération de pages est un no-op là où `MADV_DONTNEED`
n'existe pas. Cela **ne remplace pas** un essai.

### Préparation

```powershell
winget install --id Gyan.FFmpeg
# rouvrir le terminal, puis :
ffmpeg -version
ffprobe -version
```

Si FFmpeg n'est pas dans le `PATH` :

```powershell
$env:UMMAHVERSE_FFMPEG  = "C:\ffmpeg\bin\ffmpeg.exe"
$env:UMMAHVERSE_FFPROBE = "C:\ffmpeg\bin\ffprobe.exe"
```

### Installation et lancement

```bat
cd C:\chemin\vers\ummahverse-common-audio
install.bat
run.bat
```

ou, depuis PowerShell :

```powershell
.\install.ps1
.\run.ps1
```

### Scénario à dérouler

1. **Installation** : `install.bat` se termine sans erreur et crée `.venv\`,
   `.cache\pip\` et `.install-state`. Noter la version de Python (`py -3 --version`)
   et de PySide6 affichées par le script.
2. **Confinement** : relever la taille de `%LOCALAPPDATA%\pip` avant et après
   l'installation — elle ne doit pas bouger. Vérifier qu'aucun dossier
   `UmmahVerse` n'apparaît dans `%APPDATA%` ni `%LOCALAPPDATA%`.
3. **FFmpeg manquant** : renommer temporairement `ffmpeg.exe`, lancer `run.bat`,
   vérifier que le message cite `winget install --id Gyan.FFmpeg` et que l'application
   se referme proprement. Restaurer. Essayer aussi `install.bat -WithFfmpeg`, qui doit
   déposer `ffmpeg.exe` et `ffprobe.exe` dans `tools\ffmpeg\` et rendre le système
   inutile.
4. **Lancement** : `run.bat` ouvre la fenêtre, deux onglets en français. Tester aussi
   le double-clic depuis l'explorateur.
5. **Chemins Windows** : placer les fichiers de test dans
   `D:\Films & Séries\test accentué éàü\` — lettre de lecteur, espaces, esperluette et
   accents. Les sélectionner par « Parcourir… » **et** par glisser-déposer.
6. **Flux multiples** : préparer un `.mkv` à deux pistes audio
   (`ffmpeg -i a.wav -i b.wav -map 0:a -map 1:a -c copy deux.mka`) et vérifier que la
   liste s'active et exige un choix.
7. **Analyser / synchroniser** : noter décalage, dérive, blocs comparables, gains.
8. **Extraire** : vérifier la production des quatre fichiers, et que la fenêtre reste
   déplaçable et redimensionnable pendant le calcul.
9. **Annulation** : sur une entrée d'au moins deux minutes, cliquer « Annuler » à
   mi-parcours ; vérifier qu'aucun `common.wav` ni `a_only.wav` n'est laissé sur le
   disque, et que les boutons redeviennent actifs.
10. **Dossier de sortie en lecture seule** : viser `C:\Program Files\...` et vérifier que
   le message d'erreur est compréhensible (c'est le cas **non testé même sous Ubuntu**).
11. **Soustraction** : onglet 2, vérifier le pré-remplissage puis traiter une troisième
    langue.
12. **CLI** :
    ```powershell
    .\run.ps1 -Cli probe "D:\Films & Séries\test\a.wav"
    .\run.ps1 -Cli extract --input-a "D:\...\a.wav" `
        --input-b "D:\...\b.wav" --out-dir "D:\...\sorties"
    ```
13. **Tests** : `.\.venv\Scripts\python.exe -m pytest -q`. Les tests d'interface
    utilisent la plateforme « offscreen » et doivent passer sans écran.

14. **Désinstallation** : supprimer le dossier du projet, puis vérifier qu'aucune
    trace ne subsiste dans `%APPDATA%`, `%LOCALAPPDATA%` ni le menu Démarrer.

Pour chaque point, noter : système, version de Python, de PySide6 et de FFmpeg,
résultat observé, et capture ou copie du journal. Reporter le résultat dans le tableau
en tête de ce document, en remplaçant ⬜ par ✅ ou ❌ **uniquement après exécution**.

## 3. Écoute réelle — protocole, non exécuté

**Aucune écoute sur du matériel réel n'a été faite.** Aucun média sous droits n'a été
introduit dans ce dépôt et aucun ne doit l'être. Les mesures publiées portent sur des
timbres synthétiques et ne présagent pas de la qualité perçue sur un film.

### Préparation des extraits

Choisir **deux versions linguistiques du même montage**, de préférence sans perte
(PCM, FLAC, ou les pistes d'un même disque), et en vérifier l'agencement des canaux :

```bash
python -m ummahverse_audio probe film_FR.mkv
python -m ummahverse_audio probe film_AR.mkv
```

Extraire cinq à six passages de 15 à 30 s, avec la **même base temporelle** pour les
deux langues :

```bash
ffmpeg -ss 00:12:30 -t 25 -i film_FR.mkv -map 0:a:0 -c:a pcm_f32le fr_parole_sur_musique.wav
ffmpeg -ss 00:12:30 -t 25 -i film_AR.mkv -map 0:a:0 -c:a pcm_f32le ar_parole_sur_musique.wav
```

Passages à couvrir, un fichier par cas :

1. **parole sur musique** ;
2. **dialogue sur bruitages** ;
3. **musique seule** (sans dialogue) ;
4. **parole seule** (peu de fond) ;
5. **transition ou coupe** de montage ;
6. **passage calme**.

### Diagnostic avant écoute

```bash
python -m ummahverse_audio analyze --input-a fr_parole_sur_musique.wav \
                                   --input-b ar_parole_sur_musique.wav
```

Si la fraction de blocs comparables ou de cases fiables est basse, **le problème est en
amont** (masters ou montages différents) : inutile de régler la séparation, documentez
le cas.

### Traitement et écoute

```bash
python -m ummahverse_audio extract --input-a fr_parole_sur_musique.wav \
    --input-b ar_parole_sur_musique.wav --out-dir ecoute/parole_sur_musique
```

Au casque, **à volume égal**, comparer chaque sortie au mixage d'origine. Pour un
niveau strictement comparable, ne pas normaliser : lire les fichiers tels quels.

Noter, pour chaque extrait :

| Critère | À écouter |
|---|---|
| Voix perdue | des mots ou des syllabes manquent-ils dans `a_only` / `b_only` ? |
| Musique résiduelle | reste-t-il de la musique ou des bruitages dans les résidus ? |
| Effet de pompage | le fond respire-t-il au rythme de la parole ? |
| Clics et discontinuités | aux raccords de blocs, aux coupes de montage |
| Spatialisation | l'image stéréo (ou surround) est-elle conservée ? |
| Contenu du commun | entend-on des voix dans `common.wav` ? lesquelles ? |

Le dernier point est le plus informatif : si `common.wav` contient des mots
intelligibles, ce sont les mots identiques entre les deux versions, la voix originale
en fond, ou un surajustement. Voir [limitations.md § 2](limitations.md#2--cohérent--ne-veut-pas-dire--musique-et-bruitages).

### Réutilisation sur une troisième langue

```bash
python -m ummahverse_audio subtract --common ecoute/parole_sur_musique/common.wav \
    --metadata ecoute/parole_sur_musique/common.json \
    --input en_parole_sur_musique.wav --output ecoute/parole_sur_musique/en_only.wav \
    --export-adapted-common
```

Écouter `en_only.wav` **et** `en_only_adapted_common.wav` : le second montre ce qui a
été retiré, ce qui rend un éventuel surajustement immédiatement audible.

### Consigner le résultat

Pour chaque extrait : titre du cas, durée, codecs sources, valeurs clés du rapport JSON
(décalage, confiance, blocs comparables, cases fiables, gain), et les six critères
d'écoute. **Si la musique sous la voix n'est pas correctement séparée, l'écrire
explicitement avec les chiffres**, et enchaîner sur les expériences proposées en
[limitations.md § 8](limitations.md#8-si-la-musique-sous-la-voix-nest-pas-bien-séparée).
Ne pas ajuster les réglages jusqu'à obtenir le résultat espéré sans consigner les
essais intermédiaires.
