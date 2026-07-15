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

- `GET /api/me`
- `GET /api/admin/dashboard`
- `GET /api/hospital/dashboard`
- `POST /api/inventory`
- `POST /api/requests`
- `GET /api/dashboard` (admin/support only)
- `POST /api/shipments` (admin/support only)
- `POST /api/sensor-data` (admin/support only)
- `POST /api/organizations` (admin/support only)
- `POST /api/vans` (admin/support only)
- `POST /api/aws-events` (admin/support only)
- `GET /api/shipments` (admin/support only)
- `GET /api/shipments/<shipmentId>` (admin/support or owning hospital)

Demo bearer tokens:

- `admin-token`
- `support-token`
- `hospital-a-token`
- `hospital-b-token`

Notes:

- The backend starts with no fake shipment data.
- Shipment, hospital, NGO, van, sensor, and AWS-event data must be provided through API requests.
- The frontend reads role-scoped dashboard values from `GET /api/admin/dashboard` or `GET /api/hospital/dashboard`.
- Hospital inventory and requests are private by default. Other hospitals only see records explicitly shared with the network, and exact quantities are hidden unless explicitly enabled.
- Nearest hospital lookup uses the OpenStreetMap Overpass API.
- ML prediction calls the URL in `ML_API_URL` when that environment variable is set.
