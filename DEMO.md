# VITAE Demo Runbook

This is a repeatable 3-5 minute engineering demonstration. It is not clinical
validation and does not establish real-world model accuracy.

## Before Demo

From the repository root, use Python 3.12 when available:

```powershell
python -m pip install -r SD/backend/requirements.txt
$env:VITAE_REPOSITORY_MODE="memory"
$env:VITAE_DEVICE_INGEST_TOKEN=[guid]::NewGuid().ToString("N")
python SD/backend/app.py
```

Open `http://127.0.0.1:8000`. The backend serves the frontend; there is no
separate frontend command. Sign in as the checked-in local demo Organization
user `organization` with password `organization123`. These credentials and the
device token are local demo values only.

Optional journey forecasting requires a previously generated, trusted local
artifact and its manifest hash. Do not train during the presentation. Configure
`VITAE_JOURNEY_RISK_MODE=artifact`, `VITAE_JOURNEY_RISK_ARTIFACT_DIR`, and
`VITAE_JOURNEY_RISK_MANIFEST_SHA256` only when that reviewed artifact is already
available. Optional road-route evidence requires
`VITAE_ROUTE_PROVIDER=google_routes` and `VITAE_GOOGLE_ROUTES_API_KEY`; the demo
works with explicit unavailable/fallback states when the provider is disabled.

The built-in V2 prototype assignment uses device `device-sim-001`. A manual
gateway-shaped reading can be submitted without pretending it is physical:

```powershell
$headers = @{ Authorization = "Bearer $env:VITAE_DEVICE_INGEST_TOKEN" }
$body = @{
  sample_id = "manual-demo-001"
  device_id = "device-sim-001"
  timestamp = (Get-Date).ToUniversalTime().ToString("o")
  temperature = 6.0
  source = "MANUAL_TEST"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v2/sensor-data -Headers $headers -ContentType application/json -Body $body
```

For a physical demonstration, set `VITAE_DEVICE_INGEST_URL` to the same endpoint,
set `VITAE_DEVICE_ID`, and run the hardware-specific launcher that constructs
`gateway_from_environment(sensor)`. VITAE intentionally does not include a fake
physical driver; select and test that launcher only after the actual sensor is
known.

## Demo Flow

1. **Shipment overview (30 seconds).** Open the Organization dashboard and the
   monitored V2 shipment. Point out origin, destination, and trip lifecycle.
2. **Current condition (45 seconds).** Submit a normal `MANUAL_TEST` reading
   through the authenticated endpoint above so the live dashboard updates. Show
   that ProductRules alone answer:
   "Is the shipment outside configured conditions now?"
3. **Future journey risk (45 seconds).** With a trusted artifact and route
   evidence configured, show `Risk before destination`, its declared horizon,
   and the separate 30-minute compatibility forecast under Supporting details.
   Without them, show the honest unavailable state rather than a made-up value.
4. **Recommended action (45 seconds).** Show the backend-generated action and
   reason. Explain that ML estimates future deterioration while the deterministic
   operational engine answers: "What should the operator do?"
5. **Rerouting (45 seconds).** When the existing evidence supports it, compare
   current route time, compatible alternative, and time saved. Otherwise show
   `No better eligible destination found` or `Rerouting unavailable`.
6. **Traceability (30 seconds).** Expand Supporting details to show telemetry
   provenance/freshness, current temperature, journey context, and the engineering
   limitation statement.
7. **Physical sensor (optional, 30 seconds).** Use non-medical material and a
   safe ambient temperature change. Show `REAL DEVICE` only when an actual sensor
   produced the reading through `DeviceTelemetryGateway`.

The useful ML moment is a SAFE or MONITOR current condition paired with elevated
future journey risk. Never force this result with a special demo override. Use
only a reviewed deterministic simulator trajectory and the real inference path.
`python SD/backend/simulator.py` is a separate terminal-only deterministic
preview; it does not inject readings into the running dashboard process.

## Talking Points

- **ProductRules:** Is the shipment outside configured conditions now?
- **Journey ML:** Given accepted history and trustworthy remaining journey time,
  how likely is deterioration before destination?
- **Operational decision:** What should the operator do now?
- **Rerouting:** If action is needed, is there a meaningfully better compatible
  destination supported by route and capability evidence?
- The selected model was evaluated on controlled synthetic engineering data.
  Simulator results are not real-world performance and not clinical validation.
- Real-device telemetry uses the same accepted pipeline; it does not weaken or
  bypass ProductRules.

## Failure Fallbacks

- **No route credential or internet:** keep the route provider disabled. Show the
  explicit unavailable or straight-line distance fallback; never call distance
  an ETA.
- **No physical sensor:** use `MANUAL_TEST` through the authenticated endpoint
  for the live dashboard. Use the built-in simulator only as a separate terminal
  preview. Never label either path `REAL_DEVICE`.
- **No trusted ML artifact:** demonstrate deterministic monitoring, alerts,
  provenance, completion, and the explicit forecast-unavailable state.
- **Internet unavailable:** the entire memory-mode application and deterministic
  simulator remain local. Do not depend on Render or AWS.

## Final Check

```powershell
python -m unittest discover -s SD/backend -p "test_*.py"
python -m compileall -q SD/backend
node SD/frontend/test_v2_alert_frontend.js
node SD/frontend/test_v2_future_risk_frontend.js
node SD/frontend/test_v2_lifecycle_views.js
node SD/frontend/test_v2_shipment_form.js
```

Confirm the monitoring panel has no `undefined`, `NaN`, blank signal, or 0%
substitute for an unavailable prediction before presenting.
