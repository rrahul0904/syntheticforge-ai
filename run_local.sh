#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python -m venv .venv; fi
source .venv/bin/activate
python -m pip install -r requirements.txt
exec uvicorn app.main:app --reload --host 127.0.0.1 --port "${PORT:-8000}"
