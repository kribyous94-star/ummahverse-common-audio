#!/usr/bin/env bash
set -euo pipefail
if (( $# < 3 )); then
  echo 'Usage: subtract.sh COMMON.wav NOUVELLE_LANGUE.wav OUTPUT.wav [--metadata common.json] [options]' >&2
  exit 2
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMON="$1"; INPUT="$2"; OUTPUT="$3"; shift 3
PY="${PYTHON:-$ROOT/.venv/bin/python}"
"$PY" -m ummahverse_audio subtract --common "$COMMON" --input "$INPUT" --output "$OUTPUT" "$@"
