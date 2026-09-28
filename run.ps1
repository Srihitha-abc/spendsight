# Windows: set up (first run) and start backend + frontend.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -q -r backend\requirements.txt
if (-not (Test-Path data\billing.csv)) { .\.venv\Scripts\python scripts\generate_data.py }
if (-not (Test-Path data\samples\nimbus_retail)) { .\.venv\Scripts\python scripts\generate_nimbus.py }
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env - add your GROQ_API_KEY and HINDSIGHT_API_KEY" }
if (-not (Test-Path frontend\node_modules)) { Push-Location frontend; npm install; Pop-Location }
Start-Process -NoNewWindow -FilePath .\.venv\Scripts\python -ArgumentList "-m","uvicorn","app.main:app","--port","8000","--app-dir","backend"
Push-Location frontend; npm run dev; Pop-Location
