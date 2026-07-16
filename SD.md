# Software Development

The SD track is split into backend and frontend code.

Backend code:

- `SD/backend/app.py`
- `SD/backend/sensor_processor.py`
- `SD/backend/storage.py`
- `SD/backend/risk_rules.py`
- `SD/backend/hospital_lookup.py`
- `SD/backend/ml_client.py`
- `SD/backend/alerts.py`

Frontend code:

- `SD/frontend/index.html`
- `SD/frontend/styles.css`
- `SD/frontend/app.js`

Run it with:

```bash
python SD/backend/app.py
```

Available endpoints:

- `POST /api/login`
- `GET /api/me`
- `GET /api/admin/dashboard`
- `POST /api/admin/organizations`
- `PATCH /api/admin/organizations/<organizationId>`
- `POST /api/admin/users`
- `PATCH /api/admin/users/<userId>`
- `POST /api/admin/devices`
- `PATCH /api/admin/devices/<sensorId>`
- `PATCH /api/admin/alerts/<alertId>`
- `PATCH /api/admin/tickets/<ticketId>`
- `POST /api/admin/settings`
- `GET /api/admin/simulations`
- `GET /api/admin/simulations/<simulationId>`
- `POST /api/admin/simulations/start`
- `PATCH /api/admin/simulations/<simulationId>/pause`
- `PATCH /api/admin/simulations/<simulationId>/resume`
- `PATCH /api/admin/simulations/<simulationId>/stop`
- `POST /api/admin/simulations/<simulationId>/reset`
- `GET /api/organization/dashboard`
- `GET /api/driver/dashboard`
- `GET /api/support/dashboard`
- `GET /api/hospital/dashboard`
- `GET /api/shipments/live`
- `POST /api/organization/shipments`
- `GET /api/dashboard` (admin only)
- `POST /api/shipments` (admin only)
- `POST /api/sensor-data` (admin only)
- `POST /api/organizations` (admin only)
- `POST /api/vans` (admin only)
- `POST /api/aws-events` (admin only)
- `GET /api/shipments` (admin only)
- `GET /api/shipments/<shipmentId>` (admin only)

Demo bearer tokens:

- `admin-token`
- `support-token`
- `organization-token`
- `driver-token`
- `driver-rami-token`
- `hospital-a-token`
- `hospital-b-token`

Notes:

- The backend starts with no fake shipment data.
- Shipment, hospital, NGO, van, sensor, and AWS-event data must be provided through API requests.
- The frontend reads role-scoped dashboard values for Admin, Organization, Driver, and Support interfaces.
- The legacy `hospital` role and `/api/hospital/dashboard` remain available for compatibility and map to the Organization interface after login.
- Hospital inventory and requests are private by default. Other hospitals only see records explicitly shared with the network, and exact quantities are hidden unless explicitly enabled.
- Compatible-facility fallback currently uses the existing hospital lookup through the OpenStreetMap Overpass API.
- ML prediction calls the URL in `ML_API_URL` when that environment variable is set.

## Local Shipment Simulation Center

The Simulation Center is an Admin-only local demonstration tool. Open the Admin workspace, then choose **Simulation Center** in the Admin navigation. It is not available in the Organization, Driver, or Support navigation, and every simulator endpoint enforces the backend Admin role check.

The simulator generates **synthetic demonstration data**. It behaves like a local GPS and cold-chain sensor for an existing eligible shipment; it is not a physical sensor feed and should not be treated as production telemetry. A shipment is eligible only while its trip is active and it has an assigned Driver, registered sensor, and one of the predefined routes. Completed, rejected, cancelled, or otherwise ineligible shipments cannot be started.

### Scenarios

- **Normal Delivery:** follows the route normally, keeps temperature near the safe-range midpoint, and reduces battery slowly.
- **Temperature Rising:** raises temperature gradually with small deterministic variation so the existing risk rules decide when it becomes at risk.
- **Cooling Failure:** raises temperature faster and reduces battery moderately; the normal alert builder determines the alerts.
- **Low Battery:** keeps temperature controlled while battery gradually crosses the existing low and critical thresholds.
- **Sensor Offline:** emits three readings, marks the assigned sensor offline, and pauses without producing fake readings. Resume marks the sensor online and continues.
- **Traffic Delay:** advances only every third tick, extends ETA by 15 minutes once, and records the delay.
- **Route Blocked:** stops route progress at a route point while telemetry continues and records the blocked event. It does not invent a reroute or destination.
- **Recovery After Intervention:** is allowed only for an at-risk condition and gradually returns temperature toward the required range through the normal risk engine.

The controlled random variation is seeded by simulation ID. Values change gradually and remain within validated GPS, battery, and temperature bounds. Slow, Normal, and Fast modes generate one reading every 10, 5, and 2 seconds respectively. The 49-point routes make a Normal demonstration last about four minutes when started near the route origin.

### Routes and telemetry flow

Three centralized backend routes represent Central Cold Storage to Hospital A Receiving, Regional Supply Hub to Hospital B Receiving, and NorthLine Main Facility to Beirut Receiving. Each contains ordered latitude/longitude points, progress, total simulated distance, and original duration. Route definitions are in `SD/backend/simulator.py`, never in frontend rendering code.

Every generated reading includes shipment ID, assigned sensor ID, timestamp, GPS coordinates, temperature, battery, route progress, and `source: simulator`. The background worker calls `process_sensor_data`, which is the same function used by `POST /api/sensor-data`:

```text
simulator -> sensor validation -> shared shipment/readings storage
          -> existing risk rules -> existing alert builder -> role-scoped APIs
```

The simulator does not contain a second risk or alert implementation. Organization and Driver views receive their permitted updates from the same centralized shipment state and existing polling. Support does not receive an automatic ticket; diagnostic context remains available only through permitted Support workflows. On route arrival, the simulator records arrival but leaves Driver completion and Organization verification to their existing workflows.

### Running a demonstration

1. Sign in as Admin and open **Simulation Center**.
2. Select an eligible active shipment.
3. Review its product, organization, route, Driver, sensor, range, current condition, and trip status.
4. Select a scenario and speed, then choose **Start simulation**.
5. Watch route progress, GPS, temperature, battery, risk, status, elapsed time, and the last reading update.
6. Use Pause/Resume when needed. Stop prevents further readings. Reset is available only after the run ends and restores the shipment, sensor, and related alerts captured at the start.

Meaningful events—simulation start/completion, risk changes, new alerts, sensor offline/recovery, traffic delay, route blockage, and recovery—are recorded in the shipment timeline. Normal reading ticks are retained in reading history but do not flood the timeline.

### Known limitations and production replacement

- Simulation state and snapshots are in memory and disappear when the Python process restarts.
- Routes are fixed demonstrations; there is no automatic rerouting or paid routing API.
- Reset is intended for controlled local demos, not production audit history.
- One Python process owns its daemon workers; this prototype is not a distributed worker system.
- No new DynamoDB, AWS IoT, machine-learning, or physical-device integration is included in this phase.

Later, a physical sensor or AWS IoT ingestion service can replace the reading generator while retaining the same validated telemetry-processing contract. DynamoDB-compatible live shipment reads and the optional ML client remain in place and were not replaced by the simulator.
