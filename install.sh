#!/usr/bin/env bash
#
# Installation complète et **entièrement contenue dans ce dossier** (Linux).
#
# Tout ce que ce script crée vit sous la racine du projet :
#
#   .venv/              environnement Python principal (moteur, CLI, tests, interface)
#   .venv-gui/          second environnement, créé seulement si l'interface ne peut pas
#                       être installée dans le premier (voir « compatibilité » plus bas)
#   .cache/pip/         cache de téléchargement pip — rien n'est écrit dans ~/.cache/pip
#   .cache/workspace/   fichiers temporaires de traitement, utilisés par run.sh
#   tools/ffmpeg/       ffmpeg et ffprobe locaux, uniquement avec --with-ffmpeg
#   .install-state      trace de ce qui a été installé
#
# Supprimer le dossier du projet supprime donc la totalité de l'installation.
# Aucun paquet système n'est installé, aucun sudo n'est demandé, rien n'est écrit
# dans votre répertoire personnel.
#
# Usage :
#   bash install.sh [options]
#
#   --python CHEMIN       interpréteur à utiliser pour l'environnement principal
#   --with-ffmpeg         télécharge un ffmpeg statique dans tools/ffmpeg/
#                         (sinon, celui du système est utilisé s'il existe)
#   --no-gui              n'installe pas PySide6 (CLI seule)
#   --clean               repart de zéro : supprime .venv, .venv-gui, .cache, tools
#   --offline             interdit tout accès réseau (échoue si un paquet manque)
#   --purge-user-cache    supprime aussi ~/.cache/pip, hors du dossier (voir --help)
#   -h, --help            affiche cette aide

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
VENV_GUI="$ROOT/.venv-gui"
CACHE="$ROOT/.cache"
PIP_CACHE="$CACHE/pip"
WORKSPACE="$CACHE/workspace"
TOOLS="$ROOT/tools"
FFMPEG_DIR="$TOOLS/ffmpeg"
STATE="$ROOT/.install-state"

#: Versions de Python acceptées, de la préférée à la moins préférée.
PREFERRED_PYTHONS=(python3.13 python3.12 python3.11 python3.10 python3 python3.14 python3.9)
#: Construction statique de FFmpeg pour --with-ffmpeg (Linux).
FFMPEG_BASE_URL="https://johnvansickle.com/ffmpeg/releases"

PYTHON_OVERRIDE=""
WITH_FFMPEG=0
WANT_GUI=1
DO_CLEAN=0
OFFLINE=0
PURGE_USER_CACHE=0

# --------------------------------------------------------------------------- #
# Affichage
# --------------------------------------------------------------------------- #
if [[ -t 1 ]]; then
  B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'
else
  B=""; G=""; Y=""; R=""; N=""
fi
step() { printf '%s==>%s %s\n' "$B" "$N" "$*"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*" >&2; }
fail() { printf '%sErreur :%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

usage() { sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//; $d'; }

while (( $# )); do
  case "$1" in
    --python)            PYTHON_OVERRIDE="${2:-}"; shift 2 ;;
    --python=*)          PYTHON_OVERRIDE="${1#*=}"; shift ;;
    --with-ffmpeg)       WITH_FFMPEG=1; shift ;;
    --no-gui)            WANT_GUI=0; shift ;;
    --clean)             DO_CLEAN=1; shift ;;
    --offline)           OFFLINE=1; shift ;;
    --purge-user-cache)  PURGE_USER_CACHE=1; shift ;;
    -h|--help)           usage; exit 0 ;;
    *)                   fail "Option inconnue : $1 (voir --help)" ;;
  esac
done

# --------------------------------------------------------------------------- #
# Confinement : tout ce que pip écrit reste dans le dossier
# --------------------------------------------------------------------------- #
export PIP_CACHE_DIR="$PIP_CACHE"
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_ROOT_USER_ACTION=ignore
# Empêche un environnement Python hérité (PYTHONPATH, --user) de fuiter dans l'install.
unset PYTHONPATH PYTHONHOME PIP_TARGET PIP_USER 2>/dev/null || true
export PYTHONNOUSERSITE=1
PIP_FLAGS=(--disable-pip-version-check)
(( OFFLINE )) && PIP_FLAGS+=(--no-index)

# --------------------------------------------------------------------------- #
# Nettoyage préalable
# --------------------------------------------------------------------------- #
if (( DO_CLEAN )); then
  step "Suppression de l'installation précédente"
  rm -rf "$VENV" "$VENV_GUI" "$CACHE" "$TOOLS" "$STATE"
  find "$ROOT/src" "$ROOT/tests" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  rm -rf "$ROOT/.pytest_cache" "$ROOT"/src/*.egg-info
  ok "dossier remis à l'état initial"
fi

mkdir -p "$PIP_CACHE" "$WORKSPACE"

# --------------------------------------------------------------------------- #
# Choix de l'interpréteur Python
# --------------------------------------------------------------------------- #
python_version() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true; }

python_supported() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (4, 0) else 1)
PY
}

has_venv_module() { "$1" -c 'import venv, ensurepip' >/dev/null 2>&1; }

discover_pythons() {
  local seen=() candidate resolved
  for candidate in "${PREFERRED_PYTHONS[@]}"; do
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    [[ -n "$resolved" ]] || continue
    resolved="$(readlink -f "$resolved" 2>/dev/null || echo "$resolved")"
    [[ " ${seen[*]-} " == *" $resolved "* ]] && continue
    seen+=("$resolved")
    printf '%s\n' "$resolved"
  done
}

step "Recherche d'un interpréteur Python"
CANDIDATES=()
if [[ -n "$PYTHON_OVERRIDE" ]]; then
  command -v "$PYTHON_OVERRIDE" >/dev/null 2>&1 || [[ -x "$PYTHON_OVERRIDE" ]] \
    || fail "--python : « $PYTHON_OVERRIDE » est introuvable ou non exécutable."
  CANDIDATES=("$PYTHON_OVERRIDE")
else
  mapfile -t CANDIDATES < <(discover_pythons)
fi
(( ${#CANDIDATES[@]} )) || fail $'Aucun interpréteur Python trouvé.\nUbuntu : sudo apt install python3 python3-venv'

PYTHON=""
for candidate in "${CANDIDATES[@]}"; do
  if python_supported "$candidate" && has_venv_module "$candidate"; then
    PYTHON="$candidate"
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  printf 'Interpréteurs examinés :\n' >&2
  for candidate in "${CANDIDATES[@]}"; do
    printf '  %s (Python %s)%s\n' "$candidate" "$(python_version "$candidate")" \
      "$(has_venv_module "$candidate" || echo ' — module venv absent')" >&2
  done
  fail $'Aucun interpréteur Python 3.10+ avec le module venv.\nUbuntu : sudo apt install python3-venv'
fi
ok "Python $(python_version "$PYTHON") — $PYTHON"

# --------------------------------------------------------------------------- #
# FFmpeg
# --------------------------------------------------------------------------- #
download() {
  local url="$1" destination="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 -o "$destination" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$destination" "$url"
  else
    fail "Ni curl ni wget : impossible de télécharger. Installez l'un des deux, ou utilisez le ffmpeg du système."
  fi
}

install_local_ffmpeg() {
  (( OFFLINE )) && fail "--with-ffmpeg est incompatible avec --offline."
  local arch tarball extracted
  case "$(uname -m)" in
    x86_64|amd64)  arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) fail "Architecture $(uname -m) non couverte par les binaires statiques ; installez ffmpeg par votre gestionnaire de paquets." ;;
  esac
  step "Téléchargement de FFmpeg statique ($arch) dans tools/ffmpeg"
  warn "source externe : $FFMPEG_BASE_URL — vérifiez que cela correspond à votre politique"
  mkdir -p "$FFMPEG_DIR"
  tarball="$(mktemp "$CACHE/ffmpeg-XXXXXX.tar.xz")"
  download "$FFMPEG_BASE_URL/ffmpeg-release-${arch}-static.tar.xz" "$tarball" \
    || fail "Téléchargement de FFmpeg impossible."
  extracted="$(mktemp -d "$CACHE/ffmpeg-XXXXXX")"
  tar -xJf "$tarball" -C "$extracted" --strip-components=1 \
    || fail "Archive FFmpeg illisible."
  install -m 0755 "$extracted/ffmpeg" "$extracted/ffprobe" "$FFMPEG_DIR/"
  rm -rf "$tarball" "$extracted"
  ok "$("$FFMPEG_DIR/ffmpeg" -version 2>/dev/null | head -1)"
}

step "Vérification de FFmpeg"
FFMPEG_SOURCE=""
if (( WITH_FFMPEG )); then
  install_local_ffmpeg
  FFMPEG_SOURCE="local"
elif [[ -x "$FFMPEG_DIR/ffmpeg" && -x "$FFMPEG_DIR/ffprobe" ]]; then
  ok "ffmpeg local déjà présent dans tools/ffmpeg"
  FFMPEG_SOURCE="local"
elif command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  ok "$(ffmpeg -version 2>/dev/null | head -1) (système)"
  FFMPEG_SOURCE="système"
else
  warn "ffmpeg/ffprobe introuvables."
  warn "  soit : sudo apt install ffmpeg"
  warn "  soit : bash install.sh --with-ffmpeg   (copie locale, rien hors du dossier)"
  FFMPEG_SOURCE="absent"
fi

# --------------------------------------------------------------------------- #
# Environnements Python
# --------------------------------------------------------------------------- #
create_venv() {
  local interpreter="$1" target="$2"
  rm -rf "$target"
  "$interpreter" -m venv "$target" >/dev/null \
    || fail "Création de l'environnement échouée ($target). Le module venv est-il installé ?"
  "$target/bin/python" -m pip install "${PIP_FLAGS[@]}" --quiet --upgrade pip setuptools wheel \
    || fail "Mise à jour de pip impossible dans $target."
}

install_into() {  # install_into <venv> <extras>
  "$1/bin/python" -m pip install "${PIP_FLAGS[@]}" --quiet -e ".[$2]"
}

step "Environnement principal (.venv)"
create_venv "$PYTHON" "$VENV"
install_into "$VENV" "dev" || fail "Installation du moteur et des tests impossible."
ok "moteur, CLI et tests installés"

GUI_VENV=""
if (( WANT_GUI )); then
  step "Interface graphique (PySide6)"
  if install_into "$VENV" "dev,gui" 2>/dev/null; then
    GUI_VENV="$VENV"
    ok "interface installée dans .venv"
  else
    warn "PySide6 ne s'installe pas pour Python $(python_version "$PYTHON")."
    warn "recherche d'un autre interpréteur pour un second environnement dédié…"
    for candidate in "${CANDIDATES[@]}"; do
      [[ "$candidate" == "$PYTHON" ]] && continue
      python_supported "$candidate" && has_venv_module "$candidate" || continue
      step "Essai avec Python $(python_version "$candidate") — $candidate"
      if create_venv "$candidate" "$VENV_GUI" && install_into "$VENV_GUI" "gui"; then
        GUI_VENV="$VENV_GUI"
        ok "interface installée dans .venv-gui (Python $(python_version "$candidate"))"
        break
      fi
      rm -rf "$VENV_GUI"
    done
    if [[ -z "$GUI_VENV" ]]; then
      warn "Aucun interpréteur disponible ne permet d'installer PySide6."
      warn "La CLI reste pleinement utilisable ; l'interface graphique ne le sera pas."
    fi
  fi
else
  ok "interface graphique ignorée (--no-gui)"
fi

# --------------------------------------------------------------------------- #
# État de l'installation, relu par run.sh
# --------------------------------------------------------------------------- #
{
  printf 'installed_at=%s\n' "$(date -Iseconds)"
  printf 'python=%s\n' "$PYTHON"
  printf 'python_version=%s\n' "$(python_version "$PYTHON")"
  printf 'venv=%s\n' "${VENV#"$ROOT"/}"
  printf 'gui_venv=%s\n' "${GUI_VENV:+${GUI_VENV#"$ROOT"/}}"
  printf 'ffmpeg=%s\n' "$FFMPEG_SOURCE"
} > "$STATE"

# --------------------------------------------------------------------------- #
# Vérification
# --------------------------------------------------------------------------- #
step "Vérification"
"$VENV/bin/python" -c 'import ummahverse_audio, numpy, scipy; print(f"  moteur {ummahverse_audio.__version__}, numpy {numpy.__version__}, scipy {scipy.__version__}")' \
  || fail "Le moteur ne s'importe pas."
if [[ -n "$GUI_VENV" ]]; then
  "$GUI_VENV/bin/python" -c 'import PySide6; print(f"  PySide6 {PySide6.__version__}")' \
    || warn "PySide6 est installé mais ne s'importe pas."
fi

if (( PURGE_USER_CACHE )); then
  step "Suppression du cache pip personnel (hors du dossier)"
  purge_target="${XDG_CACHE_HOME:-$HOME/.cache}/pip"   # seul emplacement hors projet touché
  rm -rf "$purge_target"
  ok "$purge_target supprimé"
fi

printf '\n%sInstallation terminée.%s Tout est dans : %s\n' "$B" "$N" "$ROOT"
printf '  interface  : bash run.sh\n'
printf '  ligne de commande : .venv/bin/python -m ummahverse_audio --help\n'
printf '  tests      : .venv/bin/python -m pytest -q\n'
if [[ "$FFMPEG_SOURCE" == "absent" ]]; then
  printf '\n%sFFmpeg manque encore%s : installez-le avant de traiter un fichier.\n' "$Y" "$N"
fi
printf "\nPour tout désinstaller : supprimez ce dossier. Rien n'est écrit ailleurs.\n"
