import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

try:
    from . import app
    from .alerting import InMemoryAlertRepository
    from .operational_service import OperationalTelemetryService
    from .state_repository import InMemoryTelemetryStateRepository
    from .telemetry_http import TelemetryHttpAdapter
    from .telemetry_processor import TelemetryProcessor
except ImportError:
    import app
    from alerting import InMemoryAlertRepository
    from operational_service import OperationalTelemetryService
    from state_repository import InMemoryTelemetryStateRepository
    from telemetry_http import TelemetryHttpAdapter
    from telemetry_processor import TelemetryProcessor


class V2SensorDataRouteTests(unittest.TestCase):
    def setUp(self):
        state_repository = InMemoryTelemetryStateRepository()
        state_repository.register_trip(app.V2_PROTOTYPE_TRIP)
        state_repository.register_device_assignment(app.V2_PROTOTYPE_ASSIGNMENT)
        processor = TelemetryProcessor(state_repository, state_repository)
        service = OperationalTelemetryService(
            processor,
            InMemoryAlertRepository(),
        )
        self.adapter = TelemetryHttpAdapter(service)
        self.adapter_patch = patch.object(
            app,
            "V2_TELEMETRY_HTTP_ADAPTER",
            self.adapter,
        )
        self.adapter_patch.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.ApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.adapter_patch.stop()

    def test_application_scope_has_explicit_prototype_identity(self):
        trip = app.V2_PROTOTYPE_TRIP
        assignment = app.V2_PROTOTYPE_ASSIGNMENT
        self.assertEqual(trip.device_id, "device-sim-001")
        self.assertEqual(trip.product_id, "gardasil-9")
        self.assertEqual(assignment.trip_id, trip.trip_id)
        self.assertEqual(assignment.lot_trip_id, trip.lot_trip_id)
        self.assertIsNotNone(app.V2_STATE_REPOSITORY.get_trip_by_id(trip.trip_id))
        self.assertIs(
            app.V2_TELEMETRY_PROCESSOR._state_repository,
            app.V2_STATE_REPOSITORY,
        )

    def test_v2_route_reuses_state_and_returns_safe_then_monitor(self):
        safe_status, safe = self.post(
            {
                "sample_id": "route-safe",
                "device_id": "device-sim-001",
                "timestamp": "2026-08-19T18:00:00Z",
                "temperature": 6.0,
            }
        )
        monitor_status, monitor = self.post(
            {
                "sample_id": "route-monitor",
                "device_id": "device-sim-001",
                "timestamp": "2026-08-19T18:10:00Z",
                "temperature": 9.0,
            }
        )

        self.assertEqual(safe_status, 200)
        self.assertEqual(
            safe["processingResult"]["decision"]["status"],
            "SAFE",
        )
        self.assertEqual(monitor_status, 200)
        self.assertEqual(
            monitor["processingResult"]["decision"]["status"],
            "MONITOR",
        )
        self.assertEqual(monitor["processingResult"]["liveState"]["revision"], 2)
        self.assertEqual(monitor["alert"]["alertType"], "EXCURSION_MONITOR")

    def test_v2_route_does_not_require_legacy_admin_authentication(self):
        status, body = self.post(
            {
                "sample_id": "route-no-auth",
                "device_id": "device-sim-001",
                "timestamp": "2026-08-19T18:00:00Z",
                "temperature": 6.0,
            }
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["telemetryAccepted"])

    def test_invalid_json_gets_safe_400_response(self):
        status, body = self.post_raw(b"not-json")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "INVALID_JSON")
        self.assertFalse(body["telemetryAccepted"])

    def test_legacy_sensor_route_remains_admin_authenticated(self):
        status, body = self.post_path(
            "/api/sensor-data",
            json.dumps({"temperature": 6.0}).encode("utf-8"),
        )
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "Authentication required")

    def post(self, payload):
        return self.post_raw(json.dumps(payload).encode("utf-8"))

    def post_raw(self, body):
        return self.post_path("/api/v2/sensor-data", body)

    def post_path(self, path, body):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_port,
            timeout=2,
        )
        connection.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, payload


if __name__ == "__main__":
    unittest.main()
