# VITAE Engineering Readiness

This document describes the implemented VITAE software at checkpoint `21fd121`.
VITAE is an engineering prototype for monitored cold-chain shipments. Its V2
path keeps deterministic product-condition decisions, operational alert state,
trip lifecycle, and optional future-risk inference separate.

## Current Capabilities

### Deterministic core

- The backend `ProductRules` catalog is the only authority for **current**
  product condition. The verified catalog currently contains one context:
  GARDASIL 9, single-dose prefilled syringe 0.5 mL, unopened, rule version
  `uspi-v503-i-2503r017`.
- Supported deterministic statuses are `SAFE`, `MONITOR`, `AT_RISK`,
  `CRITICAL`, `RULE_VIOLATION`, and `DATA_ERROR`.
- Accepted telemetry is validated and committed with its `LiveState` and
  immutable `StatusDecisionRecord`. The active-trip condition is checked at
  commit time so telemetry cannot commit after completion.
- Alert policy is evaluated before the processing bundle is committed. An exact
  alert candidate is stored in the durable outbox; delivery does not rerun the
  status engine or alert policy.
- Alert lifecycle is independent of product condition: acknowledgement,
  actions, and resolution do not rewrite the deterministic `LiveState`.
- Trip completion atomically changes an active trip to `COMPLETED`, deactivates
  its device assignment, captures the final `LiveState`, and persists an
  immutable `CompletedTripOutcome`. A trip with no accepted telemetry has
  `final_status = None`; the system does not invent `DATA_ERROR`.

### Monitoring and roles

`GET /api/v2/monitor/live/<lotTripId>` returns the authoritative trip identity,
current `LiveState`, unresolved-alert summary, and `futureRisk`. The associated
alert list/detail/mutation routes use the same `lotTripId` scope.

Monitoring requires authentication and shipment access. The owning Organization
user and assigned Driver may read it. Unauthenticated requests receive `401`;
unsupported roles or authenticated users without access receive `403`; a missing
lot trip receives `404`. Authorization happens before monitoring or ML inference.

The Organization frontend presents **CURRENT STATUS** separately from
**30-MIN FUTURE RISK** and uses the same monitoring response rather than a second
ML poll. Legacy-only shipments continue to use the legacy workflow.

`futureRisk` has three explicit states:

- `{"state": "NOT_CONFIGURED"}` when optional inference is disabled.
- `{"state": "PREDICTED", ...}` with a calibrated adverse-event probability
  for the next 30 minutes.
- `{"state": "NOT_PREDICTED", "reasonCode": "...", "detail": "..."}`
  when inference is configured but the active history is ineligible,
  incoherent, concurrently changing, or temporarily unavailable.

The forecast never changes ProductRules status and exposes no risk band or
operational threshold.

## Architecture

```text
Telemetry
  -> validation and active-trip identity/rule resolution
  -> ProductRules evaluation
  -> atomic TelemetryRecord + LiveState + StatusDecisionRecord
  -> optional exact AlertOutboxEvent
  -> synchronous alert delivery and background recovery
  -> authenticated monitoring

ACTIVE trip
  -> one caller-supplied, timezone-aware completed_at
  -> atomic TripCompletionRepository
  -> COMPLETED trip + inactive assignment + final LiveState snapshot
  -> immutable CompletedTripOutcome

Completed histories
  -> leakage-safe temporal examples at cutoff T
  -> approved simulator training corpus
  -> calibrated logistic-regression artifact
  -> trusted, read-only inference over an ACTIVE prefix
  -> optional 30-minute adverse-event probability in monitoring
```

The backend serves the static frontend, HTTP API, and local role-scoped demo from
one Python process. Repository protocols isolate domain services from memory,
SQLite, and DynamoDB adapters.

## Run Locally

Python 3.12 is the Lambda deployment runtime and the recommended local version.
The repository does not otherwise pin a local interpreter. Node.js is needed
only for the standalone frontend harnesses and JavaScript syntax checks. AWS SAM
CLI and GNU Make are needed only to validate/build the worker package.

From the repository root:

```powershell
python -m pip install -r SD/backend/requirements.txt
python SD/backend/app.py
```

Open `http://127.0.0.1:8000`. The Python server also serves
`SD/frontend/index.html`; there is no separate frontend dev server or npm install.
The default repository mode is memory, so a restart clears V2 state.

The checked-in users, passwords, and bearer tokens are local demo credentials,
not production authentication. Do not reuse them in a deployed environment.

### Render Demo

`render.yaml` defines one free Docker web service, `vitae-demo`, that serves the
same static frontend and backend API. It runs with memory repositories and
disabled temporal-risk artifacts, so restarts intentionally clear demo state and
monitoring returns `futureRisk.state = "NOT_CONFIGURED"`. Render supplies `PORT`;
the application falls back to `8000` locally. Its health check is `GET /healthz`.

Run the small deterministic command-line scenario suite with:

```powershell
python SD/backend/simulator.py
```

The approved training-corpus, calibration, comparison, and artifact code is
exposed as tested Python APIs rather than a supported command-line training job.
Do not treat unit-test fixtures as model-performance evidence.

### Local validation

```powershell
python -m unittest discover -s SD/backend -p "test_*.py"
python -m compileall -q SD/backend
node SD/frontend/test_v2_shipment_form.js
node SD/frontend/test_v2_lifecycle_views.js
node SD/frontend/test_v2_alert_frontend.js
node SD/frontend/test_v2_future_risk_frontend.js
Get-ChildItem SD/frontend -Filter *.js -Recurse | ForEach-Object { node --check $_.FullName }
```

For the Lambda package, from `infrastructure/outbox-worker`:

```powershell
sam validate --template-file template.json
sam validate --lint --template-file template.json
sam build --template-file template.json --no-cached
```

These commands only validate/build locally. Deployment requires separately
approved AWS configuration and least-privilege roles.

## Storage Modes

| Mode | Current use and maturity |
| --- | --- |
| Memory | Default application mode. Implements identity/access, telemetry/state/decision/outbox, alerts, and atomic completion. Fast and contract-tested, but ephemeral. |
| SQLite | Durable local adapters exist for identity/access, full telemetry processing bundles, completed outcomes, and atomic trip completion. They use transactional single-writer semantics and can coexist in one database file. SQLite is **not** currently selectable through `RepositoryConfig`; an embedding application or test must compose these adapters explicitly. |
| DynamoDB | `VITAE_REPOSITORY_MODE=dynamodb` composes durable identity/access, telemetry/state/decision/outbox, alerts, and atomic completion. Configuration fails closed when required tables are missing. This is the cloud-oriented mode. |

The repository contract suites keep idempotency, optimistic revision checks,
active-trip fencing, and atomic completion behavior aligned across adapters.
DynamoDB Local integration tests are conditional on a configured endpoint; this
document does not claim they passed when skipped.

## Environment Variables

### Application repositories

| Variable | Required/default | Purpose |
| --- | --- | --- |
| `VITAE_REPOSITORY_MODE` | Default `memory`; `memory` or `dynamodb` | Selects the application composition. `sqlite` is not a valid value. |
| `VITAE_AWS_REGION` | Required in DynamoDB mode | DynamoDB client region. |
| `VITAE_IDENTITY_TABLE` | Required in DynamoDB mode | Trip identity, device assignment, shipment access, and completion transaction table. |
| `VITAE_TELEMETRY_TABLE` | Required in DynamoDB mode | Telemetry, `LiveState`, decisions, and outbox table. |
| `VITAE_ALERT_TABLE` | Required in DynamoDB mode | Durable alert table. |
| `VITAE_AWS_PROFILE` | Optional | Existing local AWS profile name; no credentials are stored in source. |
| `VITAE_DYNAMODB_ENDPOINT_URL` | Optional | Explicit endpoint for local/test DynamoDB clients. Do not set it in the deployed worker. |
| `VITAE_DYNAMODB_KEY_NAMESPACE` | Optional, default empty | Isolates keys in shared test/development tables. |

The integration suites use the separate testing-only
`VITAE_DYNAMODB_LOCAL_ENDPOINT` variable to decide whether DynamoDB Local is
available. It does not configure the running application.

### Optional temporal-risk inference

| Variable | Required/default | Purpose |
| --- | --- | --- |
| `VITAE_TEMPORAL_RISK_MODE` | Default `disabled`; `disabled` or `artifact` | Enables optional local artifact inference. |
| `VITAE_TEMPORAL_RISK_ARTIFACT_DIR` | Required only in `artifact` mode | Directory containing `model.joblib`, `calibrator.joblib`, and `inference-manifest.json`. |
| `VITAE_TEMPORAL_RISK_MANIFEST_SHA256` | Required only in `artifact` mode | Trusted SHA-256 of the manifest. |

Disabled mode must not include artifact settings. Artifact mode requires both
settings and validates the manifest, model, calibrator, schema, hashes, feature
version, scikit-learn version, training provenance, and `risk_policy = null`.
Partial or invalid configuration fails application startup; runtime prediction
failure is isolated as `NOT_PREDICTED` and deterministic monitoring remains
available. VITAE does not download or train a model at startup.

### Outbox worker

The worker uses `VITAE_AWS_REGION`, `VITAE_TELEMETRY_TABLE`,
`VITAE_ALERT_TABLE`, and optional `VITAE_DYNAMODB_KEY_NAMESPACE`. Its bounded
settings are `VITAE_OUTBOX_BATCH_SIZE` (25), `VITAE_OUTBOX_LEASE_SECONDS` (120),
`VITAE_OUTBOX_BASE_DELAY_SECONDS` (5), `VITAE_OUTBOX_MAX_DELAY_SECONDS` (900),
and `VITAE_OUTBOX_MAX_ATTEMPTS` (96). Parentheses show template defaults.

Legacy integrations also inspect `AWS_REGION` plus `SHIPMENTS_TABLE` for the
legacy shipment adapter and `ML_API_URL` for the legacy external ML client.
They are not part of the V2 ProductRules/temporal-risk path.

## AWS Outbox Hosting

The SAM template at `infrastructure/outbox-worker/template.json` defines a
Python 3.12 Lambda host around one bounded `OutboxDispatcher.run_once()`, an
EventBridge Scheduler trigger (disabled by default, `rate(1 minute)`), separate
Scheduler and Lambda-execution failure queues, least-privilege table access, and
14-day log retention. Lambda reserved concurrency is 1.

Isolated real-AWS Step 70 validation previously proved deployment, manual and
scheduled recovery of pending outbox work, conditional claiming, idempotent
durable alert creation, retry behavior, Lambda `OnFailure` routing, failure-domain
separation, and cleanup. This was an engineering validation, not a statement
that a production stack is currently deployed. The dispatcher provides
at-least-once delivery attempts with effectively-once durable alert creation.

## ML Status and Limitations

The ML target is whether any persisted deterministic decision becomes
`AT_RISK`, `CRITICAL`, or `RULE_VIOLATION` in `(T, T + 30 minutes]`. Features use
only telemetry and decisions available at cutoff `T`; splits are grouped by
`lot_trip_id`, and preprocessing/calibration are learned without test leakage.

The preferred engineering model is calibrated logistic regression. Evidence is
**SIMULATED ONLY**, from the reproducible `APPROVED_SIMULATOR` corpus. There is
no real-device generalization or clinical-validation evidence. The corpus has
severe class imbalance and few positive validation trips. A boosted model had
stronger simulated point estimates, but logistic regression remained preferred
because uncertainty overlapped and nonlinear overfit/simulator-artifact risk was
not justified.

No operational `LOW`/`MEDIUM`/`HIGH` thresholds are approved. Artifact metadata
requires `risk_policy: null`. The returned probability is not a ProductRules
status, disposition, treatment recommendation, or clinical decision. Real
completed-trip/device histories are required before real performance claims or
operational threshold selection.

## Generated Artifacts

The following paths are intentionally ignored by Git:

- `SD/backend/generated_datasets/`
- `SD/backend/ml_artifacts/`
- `SD/backend/ml_runs/`

Simulator corpora, fitted models, calibrators, manifests, and experiment outputs
are generated evidence, not source code. Inference must be configured explicitly
with an artifact directory and trusted manifest hash. Do not commit generated
artifacts or use an unverified artifact in a demo.

## Validation Status

The final engineering regression recorded at checkpoint `21fd121` was:

- Backend discovery: 660 total, 647 passed, 12 skipped, and 1 known
  environment-only error.
- Frontend: all 4 Node harnesses passed; all 16 JavaScript files passed
  `node --check`.
- `python -m compileall -q SD/backend`: passed.
- `git diff --check`: passed.

The 12 skips are DynamoDB Local integrations when no endpoint is configured.
The known error is the isolated worker-package import-closure subprocess under
the pgAdmin-embedded Python 3.14 environment, which omits its subprocess working
directory from `sys.path` before project imports. Existing regression work found
it unrelated to product logic or the worker package itself. Do not summarize
this state as "all tests pass."

## Demo / Engineering Readiness

Safe engineering demonstrations include creating an optional V2-monitored
shipment from the Organization UI, selecting the backend catalog context and an
available sensor, progressing `PLANNED -> ACTIVE -> COMPLETED`, ingesting or
simulating telemetry, observing deterministic condition changes, using alert
acknowledge/action/resolve controls, retaining a completed outcome, and showing
the optional 30-minute probability when a trusted simulator-trained artifact is
configured. The UI keeps current condition and future forecast visually distinct.

Do not present the prototype as proof of real-world ML accuracy, clinical
validation, production security, or approved operational risk bands. The local
authentication data and single-process HTTP server are demo infrastructure.

## Remaining Work

- Collect and validate real-device completed-trip histories before retraining or
  making performance claims.
- Have product/clinical stakeholders define acceptable false-positive and
  false-negative tradeoffs before approving any risk-band policy; revisit the
  30-minute horizon with that evidence.
- Run the conditional DynamoDB Local integration suite when its endpoint is
  available.
- Resolve or formally waive the Python 3.14 embedded-interpreter worker-package
  subprocess issue.
- Harden authentication, secret management, TLS, audit/observability, deployment
  configuration, backups, and operational access controls before production use.
