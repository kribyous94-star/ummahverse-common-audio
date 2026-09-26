#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
"$PY" -m pytest -q
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
"$PY" scripts/generate_fixtures.py "$TMP/fixtures"
bash scripts/extract.sh "$TMP/fixtures/a.wav" "$TMP/fixtures/b.wav" "$TMP/result"
bash scripts/subtract.sh "$TMP/result/common.wav" "$TMP/fixtures/third.wav" "$TMP/result/third_only.wav" --metadata "$TMP/result/common.json"
"$PY" - "$TMP/result" <<'PYCODE'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
for name in ('common.wav', 'a_only.wav', 'b_only.wav', 'common.json', 'third_only.wav'):
    file = root / name
    assert file.is_file() and file.stat().st_size > 0, file
print('Smoke test: fichiers produits ; vérifier les mesures dans VALIDATION.md.')
PYCODE
