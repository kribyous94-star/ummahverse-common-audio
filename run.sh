#!/usr/bin/env bash
#
# Lance l'interface graphique (Linux), en gardant tout dans le dossier du projet.
#
#   bash run.sh                      ouvre l'interface
#   bash run.sh -- extract --help    passe la main à la CLI plutôt qu'à l'interface
#
# Le script choisit l'environnement installé par install.sh (.venv, ou .venv-gui si
# l'interface a dû être installée séparément), pointe FFmpeg vers tools/ffmpeg s'il
# y a été téléchargé, et place les fichiers temporaires de traitement dans
# .cache/workspace/ — de sorte que supprimer ce dossier ne laisse rien derrière.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
VENV_GUI="$ROOT/.venv-gui"
FFMPEG_DIR="$ROOT/tools/ffmpeg"

die() { printf 'Erreur : %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------- #
# Environnement à utiliser
# --------------------------------------------------------------------------- #
MODE="gui"
if [[ "${1:-}" == "--" ]]; then
  MODE="cli"
  shift
fi

if [[ "$MODE" == "gui" && -x "$VENV_GUI/bin/python" ]]; then
  PYTHON="$VENV_GUI/bin/python"      # interface installée dans un second environnement
elif [[ -x "$VENV/bin/python" ]]; then
  PYTHON="$VENV/bin/python"
else
  die $'Application non installée.\nLancez : bash install.sh'
fi

# --------------------------------------------------------------------------- #
# Confinement : rien en dehors du dossier
# --------------------------------------------------------------------------- #
# Fichiers temporaires de traitement (plusieurs Gio sur un film).
export UMMAHVERSE_WORKSPACE="${UMMAHVERSE_WORKSPACE:-$ROOT/.cache/workspace}"
mkdir -p "$UMMAHVERSE_WORKSPACE"
# Caches éventuels de Qt et de fontconfig.
export XDG_CACHE_HOME="${XDG_CACHE_HOME_OVERRIDE:-$ROOT/.cache/xdg}"
mkdir -p "$XDG_CACHE_HOME"
# Aucun paquet hérité du site utilisateur ne doit masquer celui du venv.
export PYTHONNOUSERSITE=1
unset PYTHONPATH PYTHONHOME 2>/dev/null || true

# FFmpeg local, s'il a été installé par install.sh --with-ffmpeg.
if [[ -x "$FFMPEG_DIR/ffmpeg" && -x "$FFMPEG_DIR/ffprobe" ]]; then
  export UMMAHVERSE_FFMPEG="$FFMPEG_DIR/ffmpeg"
  export UMMAHVERSE_FFPROBE="$FFMPEG_DIR/ffprobe"
fi

if [[ "$MODE" == "cli" ]]; then
  exec "$PYTHON" -m ummahverse_audio "$@"
fi

# --------------------------------------------------------------------------- #
# Diagnostic avant d'ouvrir la fenêtre
# --------------------------------------------------------------------------- #
if ! "$PYTHON" -c 'import PySide6' >/dev/null 2>&1; then
  die $'PySide6 n\'est pas installé : l\'interface graphique est indisponible.\nRelancez « bash install.sh », ou utilisez la CLI : bash run.sh -- --help'
fi

if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  die $'Aucun environnement graphique détecté (ni DISPLAY ni WAYLAND_DISPLAY).\nEn session distante, utilisez la CLI : bash run.sh -- extract --help'
fi

exec "$PYTHON" -m ummahverse_audio gui "$@"
