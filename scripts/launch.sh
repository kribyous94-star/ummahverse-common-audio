#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  echo 'Application non installée. Lancer bash scripts/bootstrap.sh.' >&2
  exit 1
fi
"$PY" -m ummahverse_audio gui
