import unittest
from datetime import datetime, timedelta, timezone

try:
    from .telemetry import (
        TelemetryValidationError,
        TelemetrySource,
        ValidatedTelemetrySample,
        is_newer_sample,
        sample_identity,
        validate_and_normalize_telemetry,
    )
except ImportError:
    from telemetry import (
        TelemetryValidationError,
        TelemetrySource,
        ValidatedTelemetrySample,
        is_newer_sample,
        sample_identity,
        validate_and_normalize_telemetry,
    )


def valid_payload(**changes):
    payload = {
        "sample_id": "sample-001",
        "device_id": "device-001",
        "timestamp": "2026-01-01T10:00:00Z",
        "temperature": 5.0,
    }
    payload.update(changes)
    return payload


def validated_sample(**changes):
    values = {
        "sample_id": "sample-001",
        "device_id": "device-001",
        "timestamp": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        "temperature": 5.0,
    }
    values.update(changes)
    return ValidatedTelemetrySample(**values)


class TelemetryValidationTests(unittest.TestCase):
    def test_valid_minimal_payload_succeeds(self):
        sample = validate_and_normalize_telemetry(valid_payload())
        self.assertEqual(sample.sample_id, "sample-001")
        self.assertEqual(sample.temperature, 5.0)
        self.assertIsNone(sample.battery_level)
        self.assertIsNone(sample.latitude)
        self.assertEqual(sample.source, TelemetrySource.MANUAL_TEST)

    def test_real_device_and_simulator_provenance_are_validated(self):
        self.assertEqual(
            validate_and_normalize_telemetry(valid_payload(source="REAL_DEVICE")).source,
            TelemetrySource.REAL_DEVICE,
        )
        self.assertEqual(
            validate_and_normalize_telemetry(valid_payload(source="simulator")).source,
            TelemetrySource.SIMULATOR,
        )
        self._assert_error(valid_payload(source="UNTRUSTED_SOURCE"), "INVALID_TELEMETRY_SOURCE")

    def test_identifiers_are_trimmed(self):
        sample = validate_and_normalize_telemetry(
            valid_payload(sample_id="  sample-001  ", device_id="  device-001  ")
        )
        self.assertEqual(sample_identity(sample), ("device-001", "sample-001"))

    def test_missing_sample_id_fails(self):
        payload = valid_payload()
        del payload["sample_id"]
        self._assert_error(payload, "MISSING_SAMPLE_ID")

    def test_empty_sample_id_fails(self):
        self._assert_error(valid_payload(sample_id="  "), "INVALID_SAMPLE_ID")

    def test_missing_device_id_fails(self):
        payload = valid_payload()
        del payload["device_id"]
        self._assert_error(payload, "MISSING_DEVICE_ID")

    def test_empty_device_id_fails(self):
        self._assert_error(valid_payload(device_id=""), "INVALID_DEVICE_ID")

    def test_missing_timestamp_fails(self):
        payload = valid_payload()
        del payload["timestamp"]
        self._assert_error(payload, "MISSING_TIMESTAMP")

    def test_invalid_timestamp_fails(self):
        self._assert_error(valid_payload(timestamp="not-a-time"), "INVALID_TIMESTAMP")

    def test_naive_timestamp_fails(self):
        self._assert_error(valid_payload(timestamp="2026-01-01T10:00:00"), "NAIVE_TIMESTAMP")

    def test_aware_timestamp_normalizes_to_utc(self):
        sample = validate_and_normalize_telemetry(
            valid_payload(timestamp="2026-01-01T12:00:00+02:00")
        )
        self.assertEqual(sample.timestamp, datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc))

    def test_timestamp_too_far_in_future_fails(self):
        received_at = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        self._assert_error(
            valid_payload(timestamp="2026-01-01T10:05:01Z"),
            "TIMESTAMP_TOO_FAR_IN_FUTURE",
            received_at=received_at,
        )

    def test_timestamp_within_clock_skew_is_accepted(self):
        received_at = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        sample = validate_and_normalize_telemetry(
            valid_payload(timestamp="2026-01-01T10:05:00Z"),
            received_at=received_at,
        )
        self.assertEqual(sample.timestamp, received_at + timedelta(minutes=5))

    def test_missing_temperature_fails(self):
        payload = valid_payload()
        del payload["temperature"]
        self._assert_error(payload, "MISSING_TEMPERATURE")

    def test_non_numeric_temperature_fails(self):
        self._assert_error(valid_payload(temperature="cold"), "INVALID_TEMPERATURE")

    def test_bool_temperature_fails(self):
        self._assert_error(valid_payload(temperature=True), "INVALID_TEMPERATURE")

    def test_nan_temperature_fails(self):
        self._assert_error(valid_payload(temperature=float("nan")), "INVALID_TEMPERATURE")

    def test_infinite_temperature_fails(self):
        self._assert_error(valid_payload(temperature=float("inf")), "INVALID_TEMPERATURE")

    def test_optional_battery_absent_succeeds(self):
        self.assertIsNone(validate_and_normalize_telemetry(valid_payload()).battery_level)

    def test_battery_below_zero_fails(self):
        self._assert_error(valid_payload(battery_level=-0.1), "INVALID_BATTERY_LEVEL")

    def test_battery_above_100_fails(self):
        self._assert_error(valid_payload(battery_level=100.1), "INVALID_BATTERY_LEVEL")

    def test_latitude_without_longitude_fails(self):
        self._assert_error(valid_payload(latitude=33.9), "INCOMPLETE_GPS")

    def test_longitude_without_latitude_fails(self):
        self._assert_error(valid_payload(longitude=35.5), "INCOMPLETE_GPS")

    def test_invalid_latitude_fails(self):
        self._assert_error(valid_payload(latitude=90.1, longitude=35.5), "INVALID_LATITUDE")

    def test_invalid_longitude_fails(self):
        self._assert_error(valid_payload(latitude=33.9, longitude=180.1), "INVALID_LONGITUDE")

    def test_valid_gps_succeeds(self):
        sample = validate_and_normalize_telemetry(valid_payload(latitude=33.9, longitude=35.5))
        self.assertEqual((sample.latitude, sample.longitude), (33.9, 35.5))

    def test_empty_device_health_fails_when_supplied(self):
        self._assert_error(valid_payload(device_health="  "), "INVALID_DEVICE_HEALTH")

    def test_duplicate_identity_uses_device_and_sample_id(self):
        first = validate_and_normalize_telemetry(valid_payload())
        second = validate_and_normalize_telemetry(valid_payload(timestamp="2026-01-01T10:01:00Z"))
        self.assertEqual(sample_identity(first), sample_identity(second))

    def test_same_sample_id_on_different_devices_is_not_duplicate_identity(self):
        first = validate_and_normalize_telemetry(valid_payload())
        second = validate_and_normalize_telemetry(valid_payload(device_id="device-002"))
        self.assertNotEqual(sample_identity(first), sample_identity(second))

    def test_validator_does_not_add_product_or_trip_identity(self):
        sample = validate_and_normalize_telemetry(
            valid_payload(product_id="untrusted-product", trip_id="untrusted-trip", lot_trip_id="untrusted-lot-trip")
        )
        self.assertFalse(hasattr(sample, "product_id"))
        self.assertFalse(hasattr(sample, "trip_id"))
        self.assertFalse(hasattr(sample, "lot_trip_id"))

    def test_validator_does_not_generate_sample_id(self):
        payload = valid_payload()
        del payload["sample_id"]
        with self.assertRaises(TelemetryValidationError):
            validate_and_normalize_telemetry(payload)
        self.assertNotIn("sample_id", payload)

    def test_timestamp_ordering_identifies_older_and_newer_samples(self):
        previous = validated_sample()
        newer = validated_sample(
            sample_id="sample-002",
            timestamp=previous.timestamp + timedelta(seconds=1),
        )
        older = validated_sample(
            sample_id="sample-003",
            timestamp=previous.timestamp - timedelta(seconds=1),
        )
        self.assertTrue(is_newer_sample(newer, previous))
        self.assertFalse(is_newer_sample(older, previous))
        self.assertFalse(is_newer_sample(previous, previous))

    def _assert_error(self, payload, reason_code, **validation_options):
        with self.assertRaises(TelemetryValidationError) as context:
            validate_and_normalize_telemetry(payload, **validation_options)
        self.assertEqual(context.exception.reason_code, reason_code)


if __name__ == "__main__":
    unittest.main()
