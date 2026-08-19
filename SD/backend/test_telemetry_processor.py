import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from threading import Barrier

try:
    from .product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
        ProductRulesNotFoundError,
    )
    from .risk_rules import ApplicationStatus, StatusDecision
    from .state_repository import (
        DuplicateTelemetrySampleError,
        InMemoryTelemetryStateRepository,
        LiveState,
        OutOfOrderTelemetryError,
        TelemetryRecord,
    )
    from .telemetry import TelemetryValidationError
    from .telemetry_processor import TelemetryProcessor
    from .trip_identity import (
        DeviceAssignment,
        NoActiveAssignmentError,
        TripIdentity,
        TripNotActiveError,
        TripRuleVersionMismatchError,
        TripStatus,
        UnknownDeviceError,
    )
except ImportError:
    from product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
        ProductRulesNotFoundError,
    )
    from risk_rules import ApplicationStatus, StatusDecision
    from state_repository import (
        DuplicateTelemetrySampleError,
        InMemoryTelemetryStateRepository,
        LiveState,
        OutOfOrderTelemetryError,
        TelemetryRecord,
    )
    from telemetry import TelemetryValidationError
    from telemetry_processor import TelemetryProcessor
    from trip_identity import (
        DeviceAssignment,
        NoActiveAssignmentError,
        TripIdentity,
        TripNotActiveError,
        TripRuleVersionMismatchError,
        TripStatus,
        UnknownDeviceError,
    )


DEVICE_ID = "device-temperature-001"
TRIP_ID = "trip-001"
LOT_TRIP_ID = "lot-trip-001"
BASE_TIME = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)


def gardasil_trip(**changes):
    value = TripIdentity(
        trip_id=TRIP_ID,
        lot_trip_id=LOT_TRIP_ID,
        lot_id="lot-g9-001",
        device_id=DEVICE_ID,
        product_id=GARDASIL_9_PRODUCT_ID,
        presentation=GARDASIL_9_PRESENTATION,
        state=GARDASIL_9_STATE,
        product_rule_version=GARDASIL_9_SOURCE_VERSION,
        origin="Central Cold Storage",
        destination="Hospital A Receiving",
        start_time=BASE_TIME - timedelta(hours=1),
        status=TripStatus.ACTIVE,
    )
    return replace(value, **changes)


def assignment(**changes):
    value = DeviceAssignment(
        assignment_id="assignment-001",
        device_id=DEVICE_ID,
        trip_id=TRIP_ID,
        lot_trip_id=LOT_TRIP_ID,
        assigned_at=BASE_TIME - timedelta(hours=1),
        active=True,
    )
    return replace(value, **changes)


def raw_sample(
    *, sample_id="sample-001", timestamp=BASE_TIME, temperature=6.0, **extra
):
    payload = {
        "sample_id": sample_id,
        "device_id": DEVICE_ID,
        "timestamp": timestamp.isoformat(),
        "temperature": temperature,
    }
    payload.update(extra)
    return payload


class TelemetryProcessorTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryTelemetryStateRepository()
        self.repository.register_trip(gardasil_trip())
        self.repository.register_device_assignment(assignment())
        self.processor = TelemetryProcessor(self.repository, self.repository)

    def test_safe_sample_runs_complete_pipeline(self):
        result = self.processor.process(raw_sample())

        self.assertEqual(result.decision.status, ApplicationStatus.SAFE)
        self.assertEqual(result.telemetry_record.trip_id, TRIP_ID)
        self.assertEqual(result.telemetry_record.lot_trip_id, LOT_TRIP_ID)
        self.assertEqual(result.live_state.revision, 1)

    def test_success_persists_immutable_telemetry_history(self):
        self.processor.process(raw_sample())
        history = self.repository.get_telemetry_history(LOT_TRIP_ID)

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].trip_id, TRIP_ID)
        self.assertEqual(history[0].temperature, 6.0)

    def test_success_persists_current_live_state(self):
        self.processor.process(raw_sample(temperature=7.0))
        state = self.repository.get_live_state(LOT_TRIP_ID)

        self.assertEqual(state.status, ApplicationStatus.SAFE)
        self.assertEqual(state.latest_temperature, 7.0)
        self.assertEqual(state.last_sample_id, "sample-001")

    def test_beginning_excursion_is_monitor(self):
        result = self.processor.process(raw_sample(temperature=9.0))
        self.assertEqual(result.decision.status, ApplicationStatus.MONITOR)

    def test_previous_live_state_drives_excursion_utilization(self):
        self.processor.process(raw_sample(temperature=9.0))
        result = self.processor.process(
            raw_sample(
                sample_id="sample-002",
                timestamp=BASE_TIME + timedelta(minutes=2160),
                temperature=9.0,
            )
        )

        self.assertEqual(result.decision.status, ApplicationStatus.AT_RISK)
        self.assertEqual(result.decision.excursion_utilization, 0.5)
        self.assertEqual(
            result.decision.cumulative_excursion_duration_minutes, 2160.0
        )
        self.assertEqual(result.live_state.revision, 2)

    def test_recovery_preserves_cumulative_excursion_state(self):
        self.processor.process(raw_sample(temperature=9.0))
        self.processor.process(
            raw_sample(
                sample_id="sample-002",
                timestamp=BASE_TIME + timedelta(minutes=30),
                temperature=9.0,
            )
        )
        result = self.processor.process(
            raw_sample(
                sample_id="sample-003",
                timestamp=BASE_TIME + timedelta(minutes=40),
                temperature=6.0,
            )
        )

        self.assertEqual(result.decision.status, ApplicationStatus.SAFE)
        self.assertEqual(
            result.decision.cumulative_excursion_duration_minutes, 40.0
        )

    def test_product_and_trip_fields_in_payload_are_not_authoritative(self):
        result = self.processor.process(
            raw_sample(
                product_id="spoofed-product",
                trip_id="spoofed-trip",
                lot_trip_id="spoofed-lot-trip",
                presentation="spoofed-presentation",
                state="spoofed-state",
            )
        )

        self.assertEqual(result.decision.status, ApplicationStatus.SAFE)
        self.assertEqual(result.telemetry_record.trip_id, TRIP_ID)
        self.assertEqual(result.telemetry_record.lot_trip_id, LOT_TRIP_ID)

    def test_invalid_telemetry_propagates_and_writes_nothing(self):
        with self.assertRaises(TelemetryValidationError):
            self.processor.process(raw_sample(temperature="not-a-number"))
        self.assert_no_telemetry_written()

    def test_unknown_device_propagates_and_writes_nothing(self):
        payload = raw_sample()
        payload["device_id"] = "unknown-device"
        with self.assertRaises(UnknownDeviceError):
            self.processor.process(payload)
        self.assert_no_telemetry_written()

    def test_no_active_assignment_propagates_and_writes_nothing(self):
        repository = InMemoryTelemetryStateRepository()
        repository.register_trip(gardasil_trip())
        repository.register_device_assignment(assignment(active=False))

        with self.assertRaises(NoActiveAssignmentError):
            TelemetryProcessor(repository, repository).process(raw_sample())
        self.assertEqual(repository.get_telemetry_history(LOT_TRIP_ID), ())

    def test_completed_trip_propagates_and_writes_nothing(self):
        repository = InMemoryTelemetryStateRepository()
        repository.register_trip(gardasil_trip(status=TripStatus.COMPLETED))
        repository.register_device_assignment(assignment())

        with self.assertRaises(TripNotActiveError):
            TelemetryProcessor(repository, repository).process(raw_sample())
        self.assertEqual(repository.get_telemetry_history(LOT_TRIP_ID), ())

    def test_unknown_product_rules_propagate_and_write_nothing(self):
        repository = InMemoryTelemetryStateRepository()
        repository.register_trip(gardasil_trip(product_id="unknown-product"))
        repository.register_device_assignment(assignment())

        with self.assertRaises(ProductRulesNotFoundError):
            TelemetryProcessor(repository, repository).process(raw_sample())
        self.assertEqual(repository.get_telemetry_history(LOT_TRIP_ID), ())

    def test_rule_version_mismatch_propagates_and_writes_nothing(self):
        repository = InMemoryTelemetryStateRepository()
        repository.register_trip(gardasil_trip(product_rule_version="wrong-version"))
        repository.register_device_assignment(assignment())

        with self.assertRaises(TripRuleVersionMismatchError):
            TelemetryProcessor(repository, repository).process(raw_sample())
        self.assertEqual(repository.get_telemetry_history(LOT_TRIP_ID), ())

    def test_duplicate_sample_is_rejected_without_advancing_state(self):
        self.processor.process(raw_sample())
        with self.assertRaises(DuplicateTelemetrySampleError):
            self.processor.process(raw_sample())

        self.assertEqual(len(self.repository.get_telemetry_history(LOT_TRIP_ID)), 1)
        self.assertEqual(self.repository.get_live_state(LOT_TRIP_ID).revision, 1)

    def test_older_sample_is_rejected_without_advancing_state(self):
        self.processor.process(raw_sample())
        with self.assertRaises(OutOfOrderTelemetryError):
            self.processor.process(
                raw_sample(
                    sample_id="sample-old",
                    timestamp=BASE_TIME - timedelta(minutes=1),
                )
            )

        self.assertEqual(len(self.repository.get_telemetry_history(LOT_TRIP_ID)), 1)
        self.assertEqual(self.repository.get_live_state(LOT_TRIP_ID).revision, 1)

    def test_equal_timestamp_is_rejected_without_advancing_state(self):
        self.processor.process(raw_sample())
        with self.assertRaises(OutOfOrderTelemetryError):
            self.processor.process(raw_sample(sample_id="sample-equal"))
        self.assertEqual(len(self.repository.get_telemetry_history(LOT_TRIP_ID)), 1)

    def test_successive_samples_preserve_complete_history(self):
        for index, temperature in enumerate((6.0, 9.0, 10.0), start=1):
            self.processor.process(
                raw_sample(
                    sample_id=f"sample-{index}",
                    timestamp=BASE_TIME + timedelta(minutes=index),
                    temperature=temperature,
                )
            )
        self.assertEqual(len(self.repository.get_telemetry_history(LOT_TRIP_ID)), 3)

    def test_simultaneous_duplicate_processing_has_one_success(self):
        barrier = Barrier(2)

        def process_once():
            barrier.wait()
            try:
                self.processor.process(raw_sample())
                return "processed"
            except DuplicateTelemetrySampleError:
                return "duplicate"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: process_once(), range(2)))

        self.assertCountEqual(outcomes, ["processed", "duplicate"])
        self.assertEqual(len(self.repository.get_telemetry_history(LOT_TRIP_ID)), 1)

    def test_processing_result_has_no_alert_side_effect(self):
        result = self.processor.process(raw_sample(temperature=30.0))
        self.assertEqual(result.decision.status, ApplicationStatus.RULE_VIOLATION)
        self.assertFalse(hasattr(result, "alert"))

    def test_processing_result_wraps_authoritative_committed_objects(self):
        result = self.processor.process(raw_sample(temperature=9.0))
        committed_record = self.repository.get_telemetry_history(LOT_TRIP_ID)[0]
        committed_state = self.repository.get_live_state(LOT_TRIP_ID)

        self.assertEqual(
            tuple(field.name for field in fields(result)),
            (
                "previous_live_state",
                "telemetry_record",
                "decision",
                "live_state",
            ),
        )
        self.assertIsInstance(result.telemetry_record, TelemetryRecord)
        self.assertIsInstance(result.decision, StatusDecision)
        self.assertIsInstance(result.live_state, LiveState)
        self.assertIs(result.telemetry_record, committed_record)
        self.assertIs(result.live_state, committed_state)
        self.assertEqual(result.decision.status, result.live_state.status)
        self.assertEqual(result.decision.reason_code, result.live_state.reason_code)
        self.assertEqual(
            result.decision.active_rule_id, result.live_state.active_rule_id
        )
        self.assertEqual(
            (result.telemetry_record.device_id, result.telemetry_record.sample_id),
            (result.live_state.device_id, result.live_state.last_sample_id),
        )
        self.assertEqual(
            result.telemetry_record.temperature,
            result.live_state.latest_temperature,
        )
        self.assertEqual(
            result.telemetry_record.timestamp,
            result.live_state.last_sample_timestamp,
        )

    def test_first_successful_sample_has_no_previous_live_state(self):
        result = self.processor.process(raw_sample())
        self.assertIsNone(result.previous_live_state)

    def test_second_sample_returns_exact_prior_live_state(self):
        first = self.processor.process(raw_sample())
        second = self.processor.process(
            raw_sample(
                sample_id="sample-002",
                timestamp=BASE_TIME + timedelta(minutes=5),
            )
        )

        self.assertIs(second.previous_live_state, first.live_state)
        self.assertEqual(
            second.previous_live_state.revision,
            second.live_state.revision - 1,
        )
        self.assertEqual(
            second.previous_live_state.last_sample_id,
            first.telemetry_record.sample_id,
        )

    def test_committing_next_state_does_not_mutate_previous_state(self):
        first = self.processor.process(raw_sample(temperature=9.0))
        prior_snapshot = first.live_state
        self.processor.process(
            raw_sample(
                sample_id="sample-002",
                timestamp=BASE_TIME + timedelta(minutes=30),
                temperature=9.0,
            )
        )

        self.assertEqual(prior_snapshot.revision, 1)
        self.assertEqual(prior_snapshot.last_sample_id, "sample-001")
        self.assertEqual(prior_snapshot.cumulative_excursion_duration_minutes, 0.0)

    def test_recovery_result_exposes_prior_excursion_state(self):
        self.processor.process(raw_sample(temperature=9.0))
        excursion = self.processor.process(
            raw_sample(
                sample_id="sample-002",
                timestamp=BASE_TIME + timedelta(minutes=30),
                temperature=9.0,
            )
        )
        recovery = self.processor.process(
            raw_sample(
                sample_id="sample-003",
                timestamp=BASE_TIME + timedelta(minutes=40),
                temperature=6.0,
            )
        )

        self.assertIs(recovery.previous_live_state, excursion.live_state)
        self.assertIsNotNone(recovery.previous_live_state.active_rule_id)
        self.assertEqual(
            recovery.previous_live_state.cumulative_excursion_duration_minutes,
            30.0,
        )
        self.assertEqual(recovery.decision.status, ApplicationStatus.SAFE)

    def test_duplicate_failure_does_not_replace_stored_previous_state(self):
        first = self.processor.process(raw_sample())
        with self.assertRaises(DuplicateTelemetrySampleError):
            self.processor.process(raw_sample())
        self.assertIs(
            self.repository.get_live_state(LOT_TRIP_ID),
            first.live_state,
        )

    def test_out_of_order_failure_does_not_replace_stored_previous_state(self):
        first = self.processor.process(raw_sample())
        with self.assertRaises(OutOfOrderTelemetryError):
            self.processor.process(
                raw_sample(
                    sample_id="sample-old",
                    timestamp=BASE_TIME - timedelta(minutes=1),
                )
            )
        self.assertIs(
            self.repository.get_live_state(LOT_TRIP_ID),
            first.live_state,
        )

    def assert_no_telemetry_written(self):
        self.assertEqual(self.repository.get_telemetry_history(LOT_TRIP_ID), ())
        self.assertIsNone(self.repository.get_live_state(LOT_TRIP_ID))


if __name__ == "__main__":
    unittest.main()
