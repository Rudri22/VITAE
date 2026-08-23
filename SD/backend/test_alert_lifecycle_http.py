import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

try:
    from . import app
    from .alert_lifecycle_service import AlertLifecycleService
    from .alerting import InMemoryAlertRepository
    from .monitoring_service import MonitoringService
    from .operational_service import OperationalTelemetryService
    from .decision_outbox import InMemoryProcessingBundleRepository
    from .telemetry_http import TelemetryHttpAdapter
    from .telemetry_processor import TelemetryProcessor
except ImportError:
    import app
    from alert_lifecycle_service import AlertLifecycleService
    from alerting import InMemoryAlertRepository
    from monitoring_service import MonitoringService
    from operational_service import OperationalTelemetryService
    from decision_outbox import InMemoryProcessingBundleRepository
    from telemetry_http import TelemetryHttpAdapter
    from telemetry_processor import TelemetryProcessor


class V2AlertLifecycleHttpTests(unittest.TestCase):
    def setUp(self):
        state_repository = InMemoryProcessingBundleRepository()
        state_repository.register_trip(app.V2_PROTOTYPE_TRIP)
        state_repository.register_device_assignment(app.V2_PROTOTYPE_ASSIGNMENT)
        self.alert_repository = InMemoryAlertRepository()
        processor = TelemetryProcessor(state_repository, state_repository)
        operational = OperationalTelemetryService(processor, self.alert_repository)
        access_resolver = lambda lot_trip_id: {
            "shipmentId": "ship-a-v2-001",
            "lotTripId": lot_trip_id,
            "organizationId": "hospital-a",
            "driverId": "driver-aya",
        }
        self.patches = (
            patch.object(app, "V2_DEVICE_INGEST_TOKEN", "test-device-token"),
            patch.object(app, "V2_TELEMETRY_HTTP_ADAPTER", TelemetryHttpAdapter(operational)),
            patch.object(
                app,
                "V2_MONITORING_SERVICE",
                MonitoringService(
                    state_repository,
                    state_repository,
                    self.alert_repository,
                ),
            ),
            patch.object(
                app,
                "V2_ALERT_LIFECYCLE_SERVICE",
                AlertLifecycleService(self.alert_repository, access_resolver),
            ),
        )
        for active_patch in self.patches:
            active_patch.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.ApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def test_organization_acknowledgement_uses_authenticated_actor(self):
        alert_id = self.create_monitor_alert()
        status, body = self.command(
            alert_id,
            "acknowledge",
            {},
            "organization-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["alert"]["status"], "ACKNOWLEDGED")
        self.assertEqual(
            body["alert"]["acknowledgedBy"],
            "organization-operations-user",
        )
        self.assertTrue(body["alert"]["acknowledgedAt"].endswith("Z"))

    def test_assigned_driver_can_acknowledge_and_record_action(self):
        alert_id = self.create_monitor_alert()
        ack_status, _ = self.command(
            alert_id,
            "acknowledge",
            {},
            "driver-token",
        )
        action_status, action = self.command(
            alert_id,
            "actions",
            {"description": "Cooling unit inspected"},
            "driver-token",
        )
        self.assertEqual(ack_status, 200)
        self.assertEqual(action_status, 200)
        self.assertEqual(
            action["alert"]["actions"][0]["actorId"],
            "driver-aya-user",
        )
        self.assertEqual(
            action["alert"]["actions"][0]["description"],
            "Cooling unit inspected",
        )

    def test_organization_can_record_action_and_resolve(self):
        alert_id = self.create_monitor_alert()
        action_status, _ = self.command(
            alert_id,
            "actions",
            {"description": "Pharmacist reviewed the lot"},
            "organization-token",
        )
        resolve_status, resolved = self.command(
            alert_id,
            "resolve",
            {"resolutionNote": "Cooling restored and product disposition recorded"},
            "organization-token",
        )
        self.assertEqual(action_status, 200)
        self.assertEqual(resolve_status, 200)
        self.assertEqual(resolved["alert"]["status"], "RESOLVED")
        self.assertEqual(
            resolved["alert"]["resolvedBy"],
            "organization-operations-user",
        )

    def test_monitoring_reflects_resolution_and_retains_latest_alert(self):
        alert_id = self.create_monitor_alert()
        _, before = self.request(
            "GET",
            "/api/v2/monitor/live/lot-trip-sim-001",
            headers={"Authorization": "Bearer organization-token"},
        )
        self.command(
            alert_id,
            "resolve",
            {"resolutionNote": "Reviewed and closed"},
            "organization-token",
        )
        status, after = self.request(
            "GET",
            "/api/v2/monitor/live/lot-trip-sim-001",
            headers={"Authorization": "Bearer organization-token"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(before["openAlertCount"], 1)
        self.assertEqual(after["openAlertCount"], 0)
        self.assertIsNone(after["latestAlert"])

        history_status, history = self.request(
            "GET",
            "/api/v2/alerts/lot-trip-sim-001",
            headers={"Authorization": "Bearer organization-token"},
        )
        self.assertEqual(history_status, 200)
        self.assertEqual(history["count"], 1)
        self.assertEqual(history["alerts"][0]["alertId"], alert_id)
        self.assertEqual(history["alerts"][0]["status"], "RESOLVED")

    def test_acknowledged_alert_remains_active_in_monitoring(self):
        alert_id = self.create_monitor_alert()
        self.command(alert_id, "acknowledge", {}, "driver-token")
        status, snapshot = self.request(
            "GET",
            "/api/v2/monitor/live/lot-trip-sim-001",
            headers={"Authorization": "Bearer organization-token"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(snapshot["openAlertCount"], 1)
        self.assertEqual(snapshot["latestAlert"]["alertId"], alert_id)
        self.assertEqual(snapshot["latestAlert"]["status"], "ACKNOWLEDGED")

    def test_authenticated_list_and_detail_are_available_to_both_roles(self):
        alert_id = self.create_monitor_alert()
        for token in ("organization-token", "driver-token"):
            with self.subTest(token=token):
                list_status, listing = self.request(
                    "GET",
                    "/api/v2/alerts/lot-trip-sim-001",
                    headers={"Authorization": f"Bearer {token}"},
                )
                detail_status, detail = self.request(
                    "GET",
                    f"/api/v2/alerts/lot-trip-sim-001/{alert_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                self.assertEqual(list_status, 200)
                self.assertEqual(listing["alerts"][0]["alertId"], alert_id)
                self.assertEqual(detail_status, 200)
                self.assertEqual(detail["alert"]["alertId"], alert_id)

    def test_alert_reads_require_authentication(self):
        alert_id = self.create_monitor_alert()
        for path in (
            "/api/v2/alerts/lot-trip-sim-001",
            f"/api/v2/alerts/lot-trip-sim-001/{alert_id}",
        ):
            with self.subTest(path=path):
                status, body = self.request("GET", path)
                self.assertEqual(status, 401)
                self.assertEqual(body["error"], "Authentication required")

    def test_route_lot_trip_scope_must_match_alert(self):
        alert_id = self.create_monitor_alert()
        status, body = self.command(
            alert_id,
            "acknowledge",
            {},
            "organization-token",
            lot_trip_id="different-lot-trip",
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "ALERT_NOT_FOUND")
        self.assertEqual(self.alert_repository.get_alert(alert_id).status.value, "OPEN")

    def test_authentication_is_required(self):
        alert_id = self.create_monitor_alert()
        status, body = self.command(alert_id, "acknowledge", {}, None)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "Authentication required")
        self.assertEqual(self.alert_repository.get_alert(alert_id).status.value, "OPEN")

    def test_non_operational_role_is_forbidden(self):
        alert_id = self.create_monitor_alert()
        status, body = self.command(
            alert_id,
            "acknowledge",
            {},
            "support-token",
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "ALERT_ROLE_FORBIDDEN")

    def test_wrong_organization_is_forbidden(self):
        alert_id = self.create_monitor_alert()
        status, body = self.command(
            alert_id,
            "acknowledge",
            {},
            "hospital-b-token",
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "ALERT_ACCESS_DENIED")

    def test_driver_assigned_to_a_different_shipment_is_forbidden(self):
        alert_id = self.create_monitor_alert()
        resolver = lambda lot_trip_id: {
            "shipmentId": "ship-a-v2-001",
            "lotTripId": lot_trip_id,
            "organizationId": "hospital-a",
            "driverId": "driver-rami",
        }
        with patch.object(
            app,
            "V2_ALERT_LIFECYCLE_SERVICE",
            AlertLifecycleService(self.alert_repository, resolver),
        ):
            status, body = self.command(
                alert_id,
                "acknowledge",
                {},
                "driver-token",
            )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "ALERT_ACCESS_DENIED")

    def test_driver_cannot_resolve(self):
        alert_id = self.create_monitor_alert()
        status, body = self.command(
            alert_id,
            "resolve",
            {"resolutionNote": "Driver attempted closure"},
            "driver-token",
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "ALERT_ACCESS_DENIED")
        self.assertEqual(self.alert_repository.get_alert(alert_id).status.value, "OPEN")

    def test_unknown_alert_returns_404(self):
        status, body = self.command(
            "missing-alert",
            "acknowledge",
            {},
            "organization-token",
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "ALERT_NOT_FOUND")

    def test_transition_conflict_returns_409(self):
        alert_id = self.create_monitor_alert()
        self.command(alert_id, "acknowledge", {}, "organization-token")
        status, body = self.command(
            alert_id,
            "acknowledge",
            {},
            "organization-token",
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["code"], "ALERT_TRANSITION_CONFLICT")

    def test_client_cannot_supply_actor_or_lifecycle_timestamp(self):
        alert_id = self.create_monitor_alert()
        status, body = self.command(
            alert_id,
            "acknowledge",
            {"actorId": "spoofed", "acknowledgedAt": "2020-01-01T00:00:00Z"},
            "organization-token",
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "INVALID_ALERT_COMMAND")
        self.assertEqual(self.alert_repository.get_alert(alert_id).status.value, "OPEN")

    def create_monitor_alert(self):
        self.request(
            "POST",
            "/api/v2/sensor-data",
            {
                "sample_id": "alert-http-safe",
                "device_id": "device-sim-001",
                "timestamp": "2026-08-19T18:00:00Z",
                "temperature": 6.0,
            },
            {"Authorization": "Bearer test-device-token"},
        )
        status, monitor = self.request(
            "POST",
            "/api/v2/sensor-data",
            {
                "sample_id": "alert-http-monitor",
                "device_id": "device-sim-001",
                "timestamp": "2026-08-19T18:10:00Z",
                "temperature": 9.0,
            },
            {"Authorization": "Bearer test-device-token"},
        )
        self.assertEqual(status, 200)
        return monitor["alert"]["alertId"]

    def command(
        self,
        alert_id,
        command,
        body,
        token,
        lot_trip_id="lot-trip-sim-001",
    ):
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        return self.request(
            "POST",
            f"/api/v2/alerts/{lot_trip_id}/{alert_id}/{command}",
            body,
            headers,
        )

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_port,
            timeout=2,
        )
        request_headers = dict(headers or {})
        encoded = None
        if body is not None:
            encoded = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded, headers=request_headers)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, payload


if __name__ == "__main__":
    unittest.main()
