import ast
import json
import unittest
from datetime import timedelta
from pathlib import Path

try:
    from .alerting import InMemoryAlertRepository
    from .operational_service import OperationalTelemetryService
    from .product_rules import ProductRulesNotFoundError
    from .simulator import build_local_environment
    from .state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        OutOfOrderTelemetryError,
        StateIntegrityError,
    )
    from .telemetry import TelemetryValidationError
    from .telemetry_http import TelemetryHttpAdapter
    from .trip_identity import (
        NoActiveAssignmentError,
        TripNotActiveError,
        UnknownDeviceError,
    )
except ImportError:
    from alerting import InMemoryAlertRepository
    from operational_service import OperationalTelemetryService
    from product_rules import ProductRulesNotFoundError
    from simulator import build_local_environment
    from state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        OutOfOrderTelemetryError,
        StateIntegrityError,
    )
    from telemetry import TelemetryValidationError
    from telemetry_http import TelemetryHttpAdapter
    from trip_identity import (
        NoActiveAssignmentError,
        TripNotActiveError,
        UnknownDeviceError,
    )


def raw_sample(environment, *, sample_id="sample-001", elapsed=0, temperature=6.0):
    return {
        "sample_id": sample_id,
        "device_id": environment.device_id,
        "timestamp": (
            environment.start_time + timedelta(minutes=elapsed)
        ).isoformat(),
        "temperature": temperature,
    }


class RaisingService:
    def __init__(self, error):
        self.error = error

    def process(self, payload):
        raise self.error


class AlwaysFailAlertRepository(InMemoryAlertRepository):
    def save_alert(self, alert):
        raise RuntimeError("database password and internal host must not leak")


class TelemetryHttpAdapterTests(unittest.TestCase):
    def setUp(self):
        self.environment = build_local_environment()
        self.alert_repository = InMemoryAlertRepository()
        self.service = OperationalTelemetryService(
            self.environment.processor,
            self.alert_repository,
        )
        self.adapter = TelemetryHttpAdapter(self.service)

    def test_safe_success_response_is_json_safe_and_camel_case(self):
        response = self.adapter.handle_post(raw_sample(self.environment))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.body["success"])
        self.assertTrue(response.body["telemetryAccepted"])
        self.assertFalse(response.body["alertRequired"])
        self.assertFalse(response.body["alertPersisted"])
        self.assertIsNone(response.body["alert"])
        self.assertIsNone(
            response.body["processingResult"]["previousLiveState"]
        )
        json.dumps(response.body, allow_nan=False)
        self.assert_no_snake_case_keys(response.body)

    def test_alert_success_serializes_decision_state_record_and_alert(self):
        self.adapter.handle_post(raw_sample(self.environment))
        response = self.adapter.handle_post(
            raw_sample(
                self.environment,
                sample_id="sample-monitor",
                elapsed=10,
                temperature=9.0,
            )
        )
        body = response.body
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["alertRequired"])
        self.assertTrue(body["alertPersisted"])
        self.assertEqual(
            body["processingResult"]["decision"]["status"], "MONITOR"
        )
        self.assertEqual(body["alert"]["alertType"], "EXCURSION_MONITOR")
        self.assertEqual(body["alert"]["sampleId"], "sample-monitor")
        self.assertIsNotNone(
            body["processingResult"]["previousLiveState"]
        )

    def test_timestamps_are_utc_iso_strings_and_optional_values_are_null(self):
        response = self.adapter.handle_post(raw_sample(self.environment))
        record = response.body["processingResult"]["telemetryRecord"]
        decision = response.body["processingResult"]["decision"]
        self.assertTrue(record["timestamp"].endswith("Z"))
        self.assertIsNone(record["batteryLevel"])
        self.assertIsNone(record["latitude"])
        self.assertIsNone(decision["activeRuleId"])

    def test_validation_error_maps_to_400_without_internal_message(self):
        adapter = TelemetryHttpAdapter(
            RaisingService(
                TelemetryValidationError(
                    "INVALID_TEMPERATURE",
                    "secret parser implementation detail",
                    "temperature",
                )
            )
        )
        response = adapter.handle_post({})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.body["error"]["code"], "INVALID_TEMPERATURE")
        self.assertEqual(response.body["error"]["field"], "temperature")
        self.assertNotIn("secret", json.dumps(response.body).lower())

    def test_non_object_json_body_maps_to_400(self):
        response = self.adapter.handle_post([])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.body["error"]["code"], "INVALID_PAYLOAD")
        self.assertFalse(response.body["telemetryAccepted"])

    def test_domain_error_status_code_mapping(self):
        cases = (
            (UnknownDeviceError("detail"), 404, "UNKNOWN_DEVICE"),
            (NoActiveAssignmentError("detail"), 409, "NO_ACTIVE_ASSIGNMENT"),
            (TripNotActiveError("detail"), 409, "TRIP_NOT_ACTIVE"),
            (
                DuplicateTelemetrySampleError("detail"),
                409,
                "DUPLICATE_TELEMETRY_SAMPLE",
            ),
            (
                OutOfOrderTelemetryError("detail"),
                409,
                "OUT_OF_ORDER_TELEMETRY",
            ),
            (
                ConcurrentStateUpdateError("detail"),
                409,
                "CONCURRENT_STATE_UPDATE",
            ),
            (
                ProductRulesNotFoundError("detail"),
                422,
                "PRODUCT_RULES_UNAVAILABLE",
            ),
            (StateIntegrityError("detail"), 500, "STATE_INTEGRITY_ERROR"),
        )
        for error, status_code, code in cases:
            with self.subTest(code=code):
                response = TelemetryHttpAdapter(
                    RaisingService(error)
                ).handle_post({})
                self.assertEqual(response.status_code, status_code)
                self.assertEqual(response.body["error"]["code"], code)
                self.assertFalse(response.body["telemetryAccepted"])

    def test_unexpected_error_maps_to_safe_500(self):
        response = TelemetryHttpAdapter(
            RaisingService(RuntimeError("AWS_SECRET_ACCESS_KEY=do-not-leak"))
        ).handle_post({})
        serialized = json.dumps(response.body)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.body["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("AWS_SECRET", serialized)
        self.assertNotIn("do-not-leak", serialized)

    def test_alert_failure_returns_503_with_committed_identifiers(self):
        service = OperationalTelemetryService(
            self.environment.processor,
            AlwaysFailAlertRepository(),
        )
        response = TelemetryHttpAdapter(service).handle_post(
            raw_sample(self.environment, temperature=9.0)
        )
        body = response.body
        self.assertEqual(response.status_code, 503)
        self.assertTrue(body["telemetryAccepted"])
        self.assertFalse(body["alertPersisted"])
        self.assertEqual(body["sampleId"], "sample-001")
        self.assertEqual(body["deviceId"], self.environment.device_id)
        self.assertEqual(body["tripId"], "sim-vitae-trip-001")
        self.assertEqual(body["lotTripId"], "sim-vitae-lot-trip-001")
        serialized = json.dumps(body)
        self.assertNotIn("password", serialized.lower())
        self.assertNotIn("internal host", serialized.lower())

    def test_duplicate_real_request_maps_to_409(self):
        payload = raw_sample(self.environment)
        self.adapter.handle_post(payload)
        response = self.adapter.handle_post(payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.body["error"]["code"],
            "DUPLICATE_TELEMETRY_SAMPLE",
        )

    def test_adapter_contains_no_business_logic_dependencies(self):
        path = Path(__file__).with_name("telemetry_http.py")
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertNotIn("evaluate_status", source)
        self.assertNotIn("ProductRule", imported_names)
        self.assertNotIn("resolve_applicable_rules", source)
        self.assertNotIn("min_temperature", source)
        self.assertNotIn("max_temperature", source)
        self.assertNotIn("ml_client", source)
        self.assertNotIn("storage", imported_names)
        self.assertNotIn("sensor_processor", source)

    def assert_no_snake_case_keys(self, value):
        if isinstance(value, dict):
            for key, nested in value.items():
                self.assertNotIn("_", key)
                self.assert_no_snake_case_keys(nested)
        elif isinstance(value, list):
            for nested in value:
                self.assert_no_snake_case_keys(nested)


if __name__ == "__main__":
    unittest.main()
