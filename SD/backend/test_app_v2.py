import http.client
import json
import threading
import unittest
from datetime import timedelta
from http.server import ThreadingHTTPServer
from unittest.mock import patch

try:
    from . import app
    from .alerting import InMemoryAlertRepository
    from .monitoring_service import MonitoringService
    from .operational_service import OperationalTelemetryService
    from .product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_PRODUCT_NAME,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
    )
    from .decision_outbox import InMemoryProcessingBundleRepository
    from .telemetry_http import TelemetryHttpAdapter
    from .telemetry_processor import TelemetryProcessor
    from .temporal_risk_baseline import BASELINE_MODEL_VERSION, TrainingSourceKind
    from .temporal_risk_calibration import CALIBRATION_METHOD
    from .temporal_risk_examples import TEMPORAL_RISK_FEATURE_VERSION
    from .temporal_risk_inference import (
        SIMULATOR_PERFORMANCE_SCOPE,
        TemporalRiskPrediction,
    )
except ImportError:
    import app
    from alerting import InMemoryAlertRepository
    from monitoring_service import MonitoringService
    from operational_service import OperationalTelemetryService
    from product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_PRODUCT_NAME,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
    )
    from decision_outbox import InMemoryProcessingBundleRepository
    from telemetry_http import TelemetryHttpAdapter
    from telemetry_processor import TelemetryProcessor
    from temporal_risk_baseline import BASELINE_MODEL_VERSION, TrainingSourceKind
    from temporal_risk_calibration import CALIBRATION_METHOD
    from temporal_risk_examples import TEMPORAL_RISK_FEATURE_VERSION
    from temporal_risk_inference import (
        SIMULATOR_PERFORMANCE_SCOPE,
        TemporalRiskPrediction,
    )


class _Predictor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def predict(self, lot_trip_id):
        self.calls.append(lot_trip_id)
        return self.result


class V2SensorDataRouteTests(unittest.TestCase):
    def setUp(self):
        state_repository = InMemoryProcessingBundleRepository()
        state_repository.register_trip(app.V2_PROTOTYPE_TRIP)
        state_repository.register_device_assignment(app.V2_PROTOTYPE_ASSIGNMENT)
        processor = TelemetryProcessor(state_repository, state_repository)
        alert_repository = InMemoryAlertRepository()
        self.state_repository = state_repository
        self.alert_repository = alert_repository
        service = OperationalTelemetryService(
            processor,
            alert_repository,
        )
        self.adapter = TelemetryHttpAdapter(service)
        self.patches = (
            patch.object(app, "V2_TELEMETRY_HTTP_ADAPTER", self.adapter),
            patch.object(
                app,
                "V2_MONITORING_SERVICE",
                MonitoringService(
                    state_repository,
                    state_repository,
                    alert_repository,
                ),
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

    def test_application_scope_has_explicit_prototype_identity(self):
        trip = app.V2_PROTOTYPE_TRIP
        assignment = app.V2_PROTOTYPE_ASSIGNMENT
        self.assertEqual(trip.device_id, "device-sim-001")
        self.assertEqual(trip.product_id, "gardasil-9")
        self.assertEqual(assignment.trip_id, trip.trip_id)
        self.assertEqual(assignment.lot_trip_id, trip.lot_trip_id)
        self.assertIsNotNone(
            app.V2_IDENTITY_REPOSITORY.get_trip_by_id(trip.trip_id)
        )
        self.assertIs(
            app.V2_TELEMETRY_PROCESSOR._identity_repository,
            app.V2_IDENTITY_REPOSITORY,
        )
        self.assertIs(
            app.V2_TELEMETRY_PROCESSOR._state_repository,
            app.V2_STATE_REPOSITORY,
        )

    def test_product_context_catalog_is_json_safe_and_unauthenticated(self):
        status, body = self.get_path(
            "/api/v2/catalog/product-contexts",
            token=None,
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(body["productContexts"]), 1)
        context = body["productContexts"][0]
        self.assertEqual(context["productId"], GARDASIL_9_PRODUCT_ID)
        self.assertEqual(context["productName"], GARDASIL_9_PRODUCT_NAME)
        self.assertEqual(context["presentation"], GARDASIL_9_PRESENTATION)
        self.assertEqual(context["state"], GARDASIL_9_STATE)
        self.assertEqual(
            context["productRuleVersion"],
            GARDASIL_9_SOURCE_VERSION,
        )
        self.assertTrue(context["source"])
        self.assertTrue(context["ruleIds"])

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

    def test_monitoring_routes_share_ingestion_state_and_alerts(self):
        live_path = "/api/v2/monitor/live/lot-trip-sim-001"
        alerts_path = "/api/v2/monitor/alerts/lot-trip-sim-001"
        before_status, before = self.get_path(live_path)
        alerts_status, no_alerts = self.get_path(alerts_path)
        self.assertEqual(before_status, 200)
        self.assertIsNone(before["liveState"])
        self.assertEqual(
            before["tripIdentity"]["lotTripId"],
            "lot-trip-sim-001",
        )
        self.assertEqual(before["tripIdentity"]["productId"], "gardasil-9")
        self.assertEqual(before["tripIdentity"]["status"], "ACTIVE")
        self.assertEqual(before["openAlertCount"], 0)
        self.assertIsNone(before["latestAlert"])
        self.assertEqual(before["futureRisk"], {"state": "NOT_CONFIGURED"})
        self.assertEqual(alerts_status, 200)
        self.assertEqual(no_alerts["alerts"], [])

        self.post(
            {
                "sample_id": "route-shared-safe",
                "device_id": "device-sim-001",
                "timestamp": "2026-08-19T18:00:00Z",
                "temperature": 6.0,
            }
        )
        _, safe = self.get_path(live_path)
        self.assertEqual(safe["liveState"]["status"], "SAFE")
        self.assertEqual(safe["liveState"]["latestTemperature"], 6.0)

        self.post(
            {
                "sample_id": "route-shared-monitor",
                "device_id": "device-sim-001",
                "timestamp": "2026-08-19T18:10:00Z",
                "temperature": 9.0,
            }
        )
        _, monitor = self.get_path(live_path)
        _, alerts = self.get_path(alerts_path)
        self.assertEqual(monitor["liveState"]["status"], "MONITOR")
        self.assertEqual(monitor["liveState"]["revision"], 2)
        self.assertEqual(monitor["openAlertCount"], 1)
        self.assertEqual(
            monitor["latestAlert"]["alertType"],
            "EXCURSION_MONITOR",
        )
        self.assertEqual(alerts["count"], 1)
        self.assertEqual(alerts["alerts"][0]["alertType"], "EXCURSION_MONITOR")

    def test_health_check_is_public_and_does_not_mutate_v2_state(self):
        assignments_before = self.state_repository.get_device_assignments(
            "device-sim-001"
        )
        status, body = self.get_path("/healthz", token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})
        self.assertEqual(
            self.state_repository.get_device_assignments("device-sim-001"),
            assignments_before,
        )

    def test_monitoring_requires_authentication_before_inference(self):
        predictor = _Predictor(None)
        monitoring = MonitoringService(
            self.state_repository,
            self.state_repository,
            self.alert_repository,
            predictor,
        )
        trip_before = self.state_repository.get_trip_by_lot_trip_id(
            "lot-trip-sim-001"
        )
        assignments_before = self.state_repository.get_device_assignments(
            "device-sim-001"
        )
        with patch.object(app, "V2_MONITORING_SERVICE", monitoring):
            responses = [
                self.get_path(path, token=None)
                for path in (
                    "/api/v2/monitor/live/lot-trip-sim-001",
                    "/api/v2/monitor/alerts/lot-trip-sim-001",
                )
            ]
        for status, body in responses:
            self.assertEqual(status, 401)
            self.assertEqual(body, {"error": "Authentication required"})
            self.assertNotIn("futureRisk", body)
        self.assertEqual(predictor.calls, [])
        self.assertEqual(
            self.state_repository.get_trip_by_lot_trip_id("lot-trip-sim-001"),
            trip_before,
        )
        self.assertEqual(
            self.state_repository.get_device_assignments("device-sim-001"),
            assignments_before,
        )

    def test_monitoring_allows_owning_organization_and_assigned_driver(self):
        for token in ("organization-token", "driver-token"):
            with self.subTest(token=token):
                live_status, body = self.get_path(
                    "/api/v2/monitor/live/lot-trip-sim-001",
                    token=token,
                )
                alerts_status, alerts = self.get_path(
                    "/api/v2/monitor/alerts/lot-trip-sim-001",
                    token=token,
                )
                self.assertEqual(live_status, 200)
                self.assertEqual(body["tripIdentity"]["status"], "ACTIVE")
                self.assertEqual(body["futureRisk"], {"state": "NOT_CONFIGURED"})
                self.assertEqual(alerts_status, 200)
                self.assertEqual(alerts["alerts"], [])

    def test_monitoring_rejects_wrong_organization_and_unassigned_driver(self):
        organization_status, organization_body = self.get_path(
            "/api/v2/monitor/live/lot-trip-sim-001",
            token="hospital-b-token",
        )
        unrelated_driver = {
            "userId": "other-driver-user",
            "role": "driver",
            "organizationId": "hospital-a",
            "driverId": "driver-other",
        }
        with patch.object(
            app,
            "get_user_by_token",
            return_value=unrelated_driver,
        ), patch.object(app, "is_driver_user", return_value=True):
            driver_status, driver_body = self.get_path(
                "/api/v2/monitor/live/lot-trip-sim-001",
                token="other-driver-token",
            )
        for status, body in (
            (organization_status, organization_body),
            (driver_status, driver_body),
        ):
            self.assertEqual(status, 403)
            self.assertEqual(body["error"]["code"], "MONITOR_ACCESS_DENIED")
            self.assertNotIn("futureRisk", body)

    def test_admin_and_support_dashboards_remain_available_but_monitor_is_forbidden(self):
        for token, dashboard in (
            ("admin-token", "/api/admin/dashboard"),
            ("support-token", "/api/support/dashboard"),
        ):
            with self.subTest(token=token):
                monitor_status, monitor_body = self.get_path(
                    "/api/v2/monitor/live/lot-trip-sim-001",
                    token=token,
                )
                dashboard_status, _ = self.get_path(dashboard, token=token)
                self.assertEqual(monitor_status, 403)
                self.assertEqual(
                    monitor_body["error"]["code"],
                    "MONITOR_ROLE_FORBIDDEN",
                )
                self.assertNotIn("futureRisk", monitor_body)
                self.assertEqual(dashboard_status, 200)

    def test_monitoring_routes_return_404_for_unknown_lot_trip(self):
        for path in (
            "/api/v2/monitor/live/not-a-real-lot-trip",
            "/api/v2/monitor/alerts/not-a-real-lot-trip",
        ):
            with self.subTest(path=path):
                status, body = self.get_path(path)
                self.assertEqual(status, 404)
                self.assertFalse(body["success"])
                self.assertEqual(body["error"]["code"], "LOT_TRIP_NOT_FOUND")
                self.assertEqual(
                    body["error"]["lotTripId"],
                    "not-a-real-lot-trip",
                )

    def test_monitoring_route_serializes_optional_probability_without_status_band(self):
        self.post(
            {
                "sample_id": "route-future-risk",
                "device_id": "device-sim-001",
                "timestamp": "2026-08-19T18:00:00Z",
                "temperature": 6.0,
            }
        )
        state = self.state_repository.get_live_state("lot-trip-sim-001")
        prediction = TemporalRiskPrediction(
            prediction_version="temporal-risk-prediction-v1",
            lot_trip_id="lot-trip-sim-001",
            trip_id="trip-sim-001",
            cutoff_sample_id=state.last_sample_id,
            cutoff_at=state.last_sample_timestamp,
            horizon_ends_at=state.last_sample_timestamp + timedelta(minutes=30),
            prediction_horizon_minutes=30,
            adverse_event_probability=0.17,
            model_version=BASELINE_MODEL_VERSION,
            calibration_method=CALIBRATION_METHOD,
            feature_version=TEMPORAL_RISK_FEATURE_VERSION,
            artifact_manifest_sha256="a" * 64,
            training_source_kind=TrainingSourceKind.APPROVED_SIMULATOR,
            performance_scope=SIMULATOR_PERFORMANCE_SCOPE,
            limitations=("Simulated-only",),
        )
        monitoring = MonitoringService(
            self.state_repository,
            self.state_repository,
            self.alert_repository,
            _Predictor(prediction),
        )
        with patch.object(app, "V2_MONITORING_SERVICE", monitoring):
            status, body = self.get_path(
                "/api/v2/monitor/live/lot-trip-sim-001"
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["liveState"]["status"], "SAFE")
        self.assertEqual(body["futureRisk"]["state"], "PREDICTED")
        self.assertEqual(body["futureRisk"]["adverseEventProbability"], 0.17)
        self.assertNotIn("riskPolicy", body["futureRisk"])
        self.assertNotIn("riskBand", body["futureRisk"])

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

    def get_path(self, path, token="organization-token"):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_port,
            timeout=2,
        )
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, payload


if __name__ == "__main__":
    unittest.main()
