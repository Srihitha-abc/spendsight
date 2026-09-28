#!/usr/bin/env bash
# macOS/Linux: set up (first run) and start backend + frontend.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python
"$PY" -m pip install -q -r backend/requirements.txt
[ -f data/billing.csv ] || "$PY" scripts/generate_data.py
[ -d data/samples/nimbus_retail ] || "$PY" scripts/generate_nimbus.py
[ -f .env ] || { cp .env.example .env; echo "Created .env - add your GROQ_API_KEY and HINDSIGHT_API_KEY"; }
[ -d frontend/node_modules ] || (cd frontend && npm install)
"$PY" -m uvicorn app.main:app --port 8000 --app-dir backend &
trap 'kill %1' EXIT
(cd frontend && npm run dev)
