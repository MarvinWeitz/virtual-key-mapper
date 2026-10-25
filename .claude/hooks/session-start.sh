#!/bin/bash
# Cloud sessions start from a fresh clone: install the package so the test suite can import it.
# Local sessions use your own .venv and are left alone.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"
python3 -m pip install --quiet -e .
