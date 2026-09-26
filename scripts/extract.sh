#!/usr/bin/env bash
set -euo pipefail
if (( $# < 3 )); then
  echo 'Usage: extract.sh A.wav B.wav OUT_DIR [options CLI additionnelles]' >&2
  exit 2
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
A="$1"; B="$2"; OUT="$3"; shift 3
PY="${PYTHON:-$ROOT/.venv/bin/python}"
"$PY" -m ummahverse_audio extract --input-a "$A" --input-b "$B" --out-dir "$OUT" "$@"
