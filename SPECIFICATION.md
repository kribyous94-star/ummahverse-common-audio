# Spécification technique — POC extraction commune à deux langues

## Contrat

`extract(A, B)` -> `common.wav`, `a_only.wav`, `b_only.wav`, `common.json`.

`subtract(common, C, common.json)` -> `c_only.wav`, `c_only.json`.

`gui()` -> interface de bureau locale PySide6 sous Ubuntu et Windows, utilisant ces deux fonctions du même moteur. La CLI reste utilisable sans interface.

Dans le modèle idéal, `A(t)=M(t)+F(t)` et `B(t)=M(t)+G(t)`. L'outil estime `M` même là où `F` et `G` le recouvrent, puis calcule `F` et `G` comme résidus. La deuxième commande aligne `M` sur `C` et soustrait une version adaptée. Aucune troisième langue n'est nécessaire à l'extraction initiale.

## Entrées et sorties

- Entrées locales audio ou vidéo via FFmpeg ; plusieurs pistes audio possibles, sélection par index explicite, sans choix silencieux ambigu.
- Décodage de calcul : PCM flottant à 48 kHz ou fréquence commune choisie et documentée, sans altération indépendante de loudness ; éviter les transcodages avec perte dans l'étape intermédiaire.
- Préserver l'agencement des canaux et enregistrer ses métadonnées. Lorsque les pistes sont incompatibles, refuser avec une erreur claire ou offrir un mapping demandé explicitement ; pas de downmix implicite.
- WAV de sortie, échantillons non écrêtés, durée et timebase documentées. `common.json` comprend : chemins ou identifiants d'entrées, flux choisis, fréquence et agencement, origine temporelle, offset/éventuelle dérive, cartes des segments comparables, paramètres de transformation et de séparation, scores de confiance **définis** et limites. Ne jamais fabriquer une confiance chiffrée arbitraire.
- `subtract` recale l'audio de nouvelle langue sur la référence du commun et adapte gain/égalisation/phase seulement si les données le permettent ; exporter un résidu de même durée et base temporelle que la nouvelle entrée, avec zones non traitées signalées.
- Interface : sélecteurs de fichiers et flux, deux opérations clairement distinctes, dossier de sortie, progression, interruption, erreurs et avertissements, résultat et rapport ouvrables, interface réactive pendant les longues opérations. Contrôler le fonctionnement avec des chemins comportant espaces, accents et caractères Unicode sur les deux systèmes.

## Risques scientifiques à vérifier

Deux mixages ne fournissent pas, à chaque échantillon, assez d'information pour connaître un commun et deux voix quelconques. L'hypothèse utile est que la bande M&E est hautement similaire et que les dialogues diffèrent. Évaluer cette hypothèse, notamment sous superposition en fréquence et en temps. Un même doublage en arrière-plan, des génériques, la réverbération, des musiques modifiées, la compression dynamique liée au dialogue, un montage différent ou un mastering différent réduisent l'identifiabilité. Une troisième langue peut aider à estimer la qualité mais ne doit pas être requise par `extract`.

Ne pas confondre les termes : `a_only` et `b_only` désignent des **résidus**, pas une séparation garantie des seules voix. Les gains d'annulation doivent être mesurés par niveau et par écoute, sur les deux sorties. Dans les zones non comparables, appliquer une politique explicite : silence dans le commun et résidu original, ou marquage non traité, sans inventer la bande M&E.

## Phases d'implémentation

1. Inspection des flux, contrôle compatibilité, décodage et alignement global ; validation du signe des offsets.
2. Diagnostic de dérive/édition par repères locaux basés si possible sur M&E et non la parole, avec seuils de confiance ; découper aux coupes et éviter le DTW libre sur les dialogues.
3. Baseline transparente (par exemple moyenne complexe calibrée avec traitement des zones fiables), puis estimateur commun contraint et lissé ; décrire mathématiques, fenêtre, latence et régularisation. SciPy `ShortTimeFFT` est un outil STFT/ISTFT possible ; `coherence`/`csd` peuvent servir au diagnostic statistique sur fenêtres multiples.
4. Résidu = mixage - commun adapté, reconstruction contrôlée. Pour une nouvelle langue, utiliser le commun enregistré et estimer sa transformation sur zones sûres, sans surajuster à la voix.
5. Traiter en blocs avec chevauchement et assembler sans clic, journaliser métriques. Des extraits courts d'abord, puis film complet après validation.
6. Relier la même bibliothèque aux commandes CLI et à l'application PySide6. Installer et lancer séparément sous Ubuntu et Windows ; éviter toute dépendance du moteur aux séparateurs de chemins ou scripts Bash.

## Références de documentation

- FFmpeg et sélection des flux : https://ffmpeg.org/ffmpeg.html
- ffprobe : https://ffmpeg.org/ffprobe.html
- SciPy ShortTimeFFT : https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.ShortTimeFFT.html
- SciPy coherence : https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.coherence.html
- PySide6 et déploiement : https://doc.qt.io/qtforpython-6/deployment/index.html

Ces références décrivent les outils ; elles ne constituent pas une preuve que la séparation sera de qualité cinéma.
