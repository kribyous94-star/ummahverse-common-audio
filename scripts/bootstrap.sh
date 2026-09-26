#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
command -v ffmpeg >/dev/null || { echo 'ffmpeg requis' >&2; exit 1; }
command -v ffprobe >/dev/null || { echo 'ffprobe requis' >&2; exit 1; }
"${PYTHON:-python3}" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
if [[ ! -f pyproject.toml ]]; then
  echo 'Claude doit créer pyproject.toml et le module ummahverse_audio avant installation.' >&2
  exit 1
fi
.venv/bin/python -m pip install -e '.[dev,gui]'
