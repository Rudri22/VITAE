# VITAE

VITAE is a cold-chain shipment monitoring prototype that combines deterministic
product-safety rules, durable telemetry and alert workflows, optional
journey-risk forecasting, operational decisions, and facility-aware rerouting.

## Run locally

Python 3.12 is recommended.

```powershell
python -m pip install -r SD/backend/requirements.txt
$env:VITAE_REPOSITORY_MODE="memory"
python -m SD.backend.app
```

Open `http://127.0.0.1:8000`. The backend serves both the API and the static
frontend. Memory mode is the default and requires no AWS resources.

## Verify

```powershell
python -m unittest discover -s SD/backend -p "test_*.py"
python -m compileall -q SD/backend
Get-ChildItem SD/frontend -Filter "test_*.js" | ForEach-Object { node $_.FullName }
Get-ChildItem SD/frontend -Filter "*.js" -Recurse | ForEach-Object { node --check $_.FullName }
docker build -t vitae:latest .
```

## Documentation

- [Engineering architecture, configuration, and storage](SD.md)
- [Local demonstration walkthrough](DEMO.md)
- [Outbox worker infrastructure](infrastructure/outbox-worker/template.json)

The checked-in users and passwords are local demonstration fixtures. Do not
reuse them as production credentials. Optional ML inference requires a locally
generated, explicitly trusted artifact; model binaries and generated datasets
are intentionally not committed.
