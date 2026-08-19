import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from threading import Barrier

try:
    from .risk_rules import ApplicationStatus, StatusDecision
    from .state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        InMemoryTelemetryStateRepository,
        OutOfOrderTelemetryError,
        StateIntegrityError,
        live_state_from_decision,
        live_state_to_previous_state,
        telemetry_record_from_sample,
    )
    from .telemetry import ValidatedTelemetrySample
    from .trip_identity import DeviceAssignment, TripIdentity, TripStatus
except ImportError:
    from risk_rules import ApplicationStatus, StatusDecision
    from state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        InMemoryTelemetryStateRepository,
        OutOfOrderTelemetryError,
        StateIntegrityError,
        live_state_from_decision,
        live_state_to_previous_state,
        telemetry_record_from_sample,
    )
    from telemetry import ValidatedTelemetrySample
    from trip_identity import DeviceAssignment, TripIdentity, TripStatus


BASE_TIME = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)


def sample(*, sample_id="sample-1", timestamp=BASE_TIME, device_id="device-1"):
    return ValidatedTelemetrySample(
        sample_id=sample_id,
        device_id=device_id,
        timestamp=timestamp,
        temperature=6.0,
        battery_level=80.0,
        latitude=33.8938,
        longitude=35.5018,
        device_health="OK",
    )


def decision(
    *,
    status=ApplicationStatus.SAFE,
    active_rule_id=None,
    started_at=None,
    episode=0.0,
    cumulative=0.0,
    utilization=None,
):
    return StatusDecision(
        status=status,
        reason_code="TEST_REASON",
        active_rule_id=active_rule_id,
        excursion_episode_duration_minutes=episode,
        cumulative_excursion_duration_minutes=cumulative,
        excursion_utilization=utilization,
        excursion_started_at=started_at,
    )


def state_for(current_sample, *, previous=None, current_decision=None):
    return live_state_from_decision(
        lot_trip_id="lot-trip-1",
        trip_id="trip-1",
        product_id="GARDASIL_9",
        product_rule_version="1.0",
        sample=current_sample,
        decision=current_decision or decision(),
        previous_live_state=previous,
    )


def trip():
    return TripIdentity(
        trip_id="trip-1",
        lot_trip_id="lot-trip-1",
        lot_id="lot-1",
        device_id="device-1",
        product_id="GARDASIL_9",
        presentation="PREFILLED_SYRINGE",
        state="UNOPENED",
        product_rule_version="1.0",
        origin="Beirut",
        destination="Byblos",
        start_time=BASE_TIME,
        status=TripStatus.ACTIVE,
    )


def assignment():
    return DeviceAssignment(
        assignment_id="assignment-1",
        device_id="device-1",
        trip_id="trip-1",
        lot_trip_id="lot-trip-1",
        assigned_at=BASE_TIME,
        active=True,
    )


class StateRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryTelemetryStateRepository()
        self.first_sample = sample()
        self.first_record = telemetry_record_from_sample(
            "trip-1", "lot-trip-1", self.first_sample
        )
        self.first_state = state_for(self.first_sample)

    def commit_first(self):
        self.repository.commit_sample_and_state(
            self.first_record, self.first_state, expected_revision=None
        )

    def test_telemetry_record_preserves_validated_sensor_facts(self):
        self.assertEqual(self.first_record.trip_id, "trip-1")
        self.assertEqual(self.first_record.lot_trip_id, "lot-trip-1")
        self.assertEqual(self.first_record.sample_id, self.first_sample.sample_id)
        self.assertEqual(self.first_record.timestamp, self.first_sample.timestamp)
        self.assertEqual(self.first_record.temperature, self.first_sample.temperature)
        self.assertEqual(self.first_record.latitude, self.first_sample.latitude)

    def test_models_are_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            self.first_record.temperature = 7.0
        with self.assertRaises(FrozenInstanceError):
            self.first_state.status = ApplicationStatus.CRITICAL

    def test_initial_commit_atomically_saves_record_and_live_state(self):
        self.commit_first()
        self.assertEqual(
            self.repository.get_telemetry_history("lot-trip-1"),
            (self.first_record,),
        )
        self.assertEqual(
            self.repository.get_live_state("lot-trip-1"), self.first_state
        )

    def test_initial_state_has_revision_one(self):
        self.assertEqual(self.first_state.revision, 1)

    def test_duplicate_sample_is_rejected_without_partial_write(self):
        self.commit_first()
        duplicate_state = replace(self.first_state, revision=2)

        with self.assertRaises(DuplicateTelemetrySampleError):
            self.repository.commit_sample_and_state(
                self.first_record, duplicate_state, expected_revision=1
            )

        self.assertEqual(len(self.repository.get_telemetry_history("lot-trip-1")), 1)
        self.assertEqual(self.repository.get_live_state("lot-trip-1"), self.first_state)

    def test_sample_identity_is_device_scoped(self):
        self.commit_first()
        self.assertTrue(self.repository.has_sample("device-1", "sample-1"))
        self.assertFalse(self.repository.has_sample("device-2", "sample-1"))

    def test_older_sample_is_rejected_without_partial_write(self):
        self.commit_first()
        older = sample(sample_id="sample-old", timestamp=BASE_TIME - timedelta(minutes=1))
        older_record = telemetry_record_from_sample("trip-1", "lot-trip-1", older)
        older_state = state_for(older, previous=self.first_state)

        with self.assertRaises(OutOfOrderTelemetryError):
            self.repository.commit_sample_and_state(
                older_record, older_state, expected_revision=1
            )

        self.assertEqual(len(self.repository.get_telemetry_history("lot-trip-1")), 1)
        self.assertEqual(self.repository.get_live_state("lot-trip-1"), self.first_state)

    def test_equal_timestamp_is_rejected(self):
        self.commit_first()
        equal = sample(sample_id="sample-equal")
        with self.assertRaises(OutOfOrderTelemetryError):
            self.repository.commit_sample_and_state(
                telemetry_record_from_sample("trip-1", "lot-trip-1", equal),
                state_for(equal, previous=self.first_state),
                expected_revision=1,
            )

    def test_newer_sample_advances_history_state_and_revision(self):
        self.commit_first()
        newer = sample(sample_id="sample-2", timestamp=BASE_TIME + timedelta(minutes=5))
        newer_state = state_for(newer, previous=self.first_state)
        self.repository.commit_sample_and_state(
            telemetry_record_from_sample("trip-1", "lot-trip-1", newer),
            newer_state,
            expected_revision=1,
        )

        self.assertEqual(len(self.repository.get_telemetry_history("lot-trip-1")), 2)
        self.assertEqual(self.repository.get_live_state("lot-trip-1"), newer_state)
        self.assertEqual(newer_state.revision, 2)

    def test_stale_expected_revision_is_rejected_without_partial_write(self):
        self.commit_first()
        newer = sample(sample_id="sample-2", timestamp=BASE_TIME + timedelta(minutes=5))

        with self.assertRaises(ConcurrentStateUpdateError):
            self.repository.commit_sample_and_state(
                telemetry_record_from_sample("trip-1", "lot-trip-1", newer),
                state_for(newer, previous=self.first_state),
                expected_revision=0,
            )

        self.assertEqual(len(self.repository.get_telemetry_history("lot-trip-1")), 1)

    def test_initial_commit_rejects_expected_existing_revision(self):
        with self.assertRaises(ConcurrentStateUpdateError):
            self.repository.commit_sample_and_state(
                self.first_record, self.first_state, expected_revision=0
            )
        self.assertEqual(self.repository.get_telemetry_history("lot-trip-1"), ())

    def test_revision_must_increment_exactly_once(self):
        self.commit_first()
        newer = sample(sample_id="sample-2", timestamp=BASE_TIME + timedelta(minutes=5))
        invalid_state = replace(state_for(newer, previous=self.first_state), revision=3)

        with self.assertRaises(StateIntegrityError):
            self.repository.commit_sample_and_state(
                telemetry_record_from_sample("trip-1", "lot-trip-1", newer),
                invalid_state,
                expected_revision=1,
            )

    def test_record_and_state_identity_must_match(self):
        mismatched_state = replace(self.first_state, last_sample_id="other-sample")
        with self.assertRaises(StateIntegrityError):
            self.repository.commit_sample_and_state(
                self.first_record, mismatched_state, expected_revision=None
            )

    def test_record_trip_id_must_match_live_state(self):
        mismatched_record = replace(self.first_record, trip_id="different-trip")
        with self.assertRaises(StateIntegrityError):
            self.repository.commit_sample_and_state(
                mismatched_record, self.first_state, expected_revision=None
            )

    def test_live_state_identity_cannot_change(self):
        newer = sample(sample_id="sample-2", timestamp=BASE_TIME + timedelta(minutes=5))
        with self.assertRaises(StateIntegrityError):
            live_state_from_decision(
                lot_trip_id="lot-trip-1",
                trip_id="different-trip",
                product_id="GARDASIL_9",
                product_rule_version="1.0",
                sample=newer,
                decision=decision(),
                previous_live_state=self.first_state,
            )

    def test_live_state_projects_to_engine_previous_state(self):
        excursion_start = BASE_TIME - timedelta(minutes=20)
        excursion_decision = decision(
            status=ApplicationStatus.AT_RISK,
            active_rule_id="high-rule",
            started_at=excursion_start,
            episode=20.0,
            cumulative=30.0,
            utilization=0.5,
        )
        live_state = state_for(
            self.first_sample, current_decision=excursion_decision
        )

        previous = live_state_to_previous_state(live_state)

        self.assertEqual(previous.last_sample_timestamp, BASE_TIME)
        self.assertEqual(previous.active_rule_id, "high-rule")
        self.assertEqual(previous.excursion_started_at, excursion_start)
        self.assertEqual(previous.cumulative_excursion_duration_minutes, 30.0)

    def test_decision_fields_map_back_to_live_state(self):
        excursion_decision = decision(
            status=ApplicationStatus.CRITICAL,
            active_rule_id="high-rule",
            started_at=BASE_TIME,
            episode=50.0,
            cumulative=60.0,
            utilization=0.95,
        )
        state = state_for(self.first_sample, current_decision=excursion_decision)

        self.assertEqual(state.status, ApplicationStatus.CRITICAL)
        self.assertEqual(state.reason_code, "TEST_REASON")
        self.assertEqual(state.active_rule_id, "high-rule")
        self.assertEqual(state.excursion_episode_duration_minutes, 50.0)
        self.assertEqual(state.cumulative_excursion_duration_minutes, 60.0)
        self.assertEqual(state.excursion_utilization, 0.95)

    def test_live_state_stores_latest_temperature_from_sample(self):
        warm_sample = replace(self.first_sample, temperature=14.5)
        state = state_for(warm_sample)
        self.assertEqual(state.latest_temperature, 14.5)

    def test_live_state_last_updated_is_accepted_sample_timestamp(self):
        state = state_for(self.first_sample)
        self.assertEqual(state.last_updated, self.first_sample.timestamp)

    def test_commit_rejects_each_immutable_identity_change(self):
        self.commit_first()
        newer = sample(sample_id="sample-2", timestamp=BASE_TIME + timedelta(minutes=5))
        record = telemetry_record_from_sample("trip-1", "lot-trip-1", newer)
        valid_state = state_for(newer, previous=self.first_state)
        changes = {
            "lot_trip_id": "other-lot-trip",
            "trip_id": "other-trip",
            "device_id": "other-device",
            "product_id": "OTHER_PRODUCT",
            "product_rule_version": "2.0",
        }

        for field, value in changes.items():
            with self.subTest(field=field):
                with self.assertRaises(StateIntegrityError):
                    self.repository.commit_sample_and_state(
                        record,
                        replace(valid_state, **{field: value}),
                        expected_revision=1,
                    )

    def test_missing_live_state_returns_empty_engine_previous_state(self):
        previous = live_state_to_previous_state(None)
        self.assertIsNone(previous.last_sample_timestamp)
        self.assertEqual(previous.cumulative_excursion_duration_minutes, 0.0)

    def test_history_is_partitioned_by_lot_trip_id(self):
        self.commit_first()
        self.assertEqual(self.repository.get_telemetry_history("other-lot-trip"), ())

    def test_repository_does_not_create_state_on_read(self):
        self.assertIsNone(self.repository.get_live_state("missing-lot-trip"))
        self.assertEqual(self.repository.get_telemetry_history("missing-lot-trip"), ())

    def test_trip_can_be_registered_and_read_by_trip_id(self):
        expected = trip()
        self.repository.register_trip(expected)
        self.assertEqual(self.repository.get_trip_by_id("trip-1"), expected)

    def test_trip_can_be_read_by_lot_trip_id(self):
        expected = trip()
        self.repository.register_trip(expected)
        self.assertEqual(
            self.repository.get_trip_by_lot_trip_id("lot-trip-1"), expected
        )

    def test_device_assignment_can_be_registered_and_read_by_device(self):
        expected = assignment()
        self.repository.register_trip(trip())
        self.repository.register_device_assignment(expected)
        self.assertEqual(
            self.repository.get_device_assignments("device-1"), (expected,)
        )

    def test_telemetry_commit_does_not_create_trip_identity(self):
        self.commit_first()
        self.assertIsNone(self.repository.get_trip_by_id("trip-1"))
        self.assertIsNone(
            self.repository.get_trip_by_lot_trip_id("lot-trip-1")
        )

    def test_telemetry_commit_does_not_create_device_assignment(self):
        self.commit_first()
        self.assertEqual(self.repository.get_device_assignments("device-1"), ())

    def test_registered_trip_identity_cannot_be_silently_changed(self):
        self.repository.register_trip(trip())
        with self.assertRaises(StateIntegrityError):
            self.repository.register_trip(replace(trip(), destination="Sidon"))

    def test_registered_assignment_cannot_be_silently_changed(self):
        self.repository.register_trip(trip())
        self.repository.register_device_assignment(assignment())
        with self.assertRaises(StateIntegrityError):
            self.repository.register_device_assignment(
                replace(assignment(), active=False)
            )

    def test_assignment_must_reference_registered_trip(self):
        with self.assertRaises(StateIntegrityError):
            self.repository.register_device_assignment(assignment())

    def test_same_sample_cannot_be_committed_twice_concurrently(self):
        barrier = Barrier(2)

        def commit_once():
            barrier.wait()
            try:
                self.repository.commit_sample_and_state(
                    self.first_record, self.first_state, expected_revision=None
                )
                return "committed"
            except DuplicateTelemetrySampleError:
                return "duplicate"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: commit_once(), range(2)))

        self.assertCountEqual(outcomes, ["committed", "duplicate"])
        self.assertEqual(len(self.repository.get_telemetry_history("lot-trip-1")), 1)

    def test_naive_record_timestamp_is_rejected(self):
        naive_record = replace(self.first_record, timestamp=BASE_TIME.replace(tzinfo=None))
        with self.assertRaises(StateIntegrityError):
            self.repository.commit_sample_and_state(
                naive_record, self.first_state, expected_revision=None
            )


if __name__ == "__main__":
    unittest.main()
