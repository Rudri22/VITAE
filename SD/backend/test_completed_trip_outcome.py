import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

try:
    from .completed_trip_outcome import (
        CompletedTripOutcomeValidationError,
        completed_trip_outcome_from_state,
        validate_completed_trip_outcome,
    )
    from .repository_contract_suite import (
        CONTRACT_TIME,
        contract_completed_trip_outcome,
        contract_sample,
        contract_state,
        contract_trip,
    )
    from .repository_serialization import (
        deserialize_completed_trip_outcome,
        serialize_completed_trip_outcome,
    )
    from .risk_rules import ApplicationStatus
    from .trip_identity import TripStatus
except ImportError:
    from completed_trip_outcome import (
        CompletedTripOutcomeValidationError,
        completed_trip_outcome_from_state,
        validate_completed_trip_outcome,
    )
    from repository_contract_suite import (
        CONTRACT_TIME,
        contract_completed_trip_outcome,
        contract_sample,
        contract_state,
        contract_trip,
    )
    from repository_serialization import (
        deserialize_completed_trip_outcome,
        serialize_completed_trip_outcome,
    )
    from risk_rules import ApplicationStatus
    from trip_identity import TripStatus


class CompletedTripOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.trip = contract_trip(status=TripStatus.COMPLETED)
        self.state = contract_state(contract_sample())
        self.completed_at = CONTRACT_TIME + timedelta(minutes=30)

    def test_factory_preserves_identity_provenance_and_final_state(self):
        outcome = completed_trip_outcome_from_state(
            self.trip,
            self.completed_at,
            self.state,
        )
        self.assertEqual(outcome.lot_trip_id, self.trip.lot_trip_id)
        self.assertEqual(outcome.product_id, self.trip.product_id)
        self.assertEqual(
            outcome.product_rule_version,
            self.trip.product_rule_version,
        )
        self.assertEqual(outcome.final_status, ApplicationStatus.SAFE)
        self.assertEqual(outcome.final_sample_id, self.state.last_sample_id)
        self.assertEqual(
            outcome.final_live_state_revision,
            self.state.revision,
        )

    def test_factory_requires_explicit_completed_trip(self):
        with self.assertRaises(CompletedTripOutcomeValidationError):
            completed_trip_outcome_from_state(
                contract_trip(status=TripStatus.ACTIVE),
                self.completed_at,
                self.state,
            )

    def test_completed_trip_without_telemetry_has_no_invented_status(self):
        outcome = completed_trip_outcome_from_state(
            self.trip,
            self.completed_at,
            None,
        )
        self.assertIsNone(outcome.final_status)
        self.assertIsNone(outcome.final_reason_code)
        self.assertIsNone(outcome.final_live_state_revision)
        self.assertIsNone(outcome.final_sample_id)

    def test_final_state_identity_or_provenance_mismatch_is_rejected(self):
        mismatches = (
            replace(self.state, lot_trip_id="different-lot-trip"),
            replace(self.state, trip_id="different-trip"),
            replace(self.state, device_id="different-device"),
            replace(self.state, product_id="different-product"),
            replace(self.state, product_rule_version="different-version"),
        )
        for state in mismatches:
            with self.subTest(state=state):
                with self.assertRaises(CompletedTripOutcomeValidationError):
                    completed_trip_outcome_from_state(
                        self.trip,
                        self.completed_at,
                        state,
                    )

    def test_completion_time_is_aware_and_not_before_trip_or_sample(self):
        invalid_times = (
            datetime(2026, 8, 19, 12, 30),
            CONTRACT_TIME - timedelta(seconds=1),
        )
        for completed_at in invalid_times:
            with self.subTest(completed_at=completed_at):
                with self.assertRaises(CompletedTripOutcomeValidationError):
                    completed_trip_outcome_from_state(
                        self.trip,
                        completed_at,
                        self.state,
                    )

        later_state = replace(
            self.state,
            last_sample_timestamp=self.completed_at + timedelta(seconds=1),
        )
        with self.assertRaises(CompletedTripOutcomeValidationError):
            completed_trip_outcome_from_state(
                self.trip,
                self.completed_at,
                later_state,
            )

    def test_model_is_immutable(self):
        outcome = contract_completed_trip_outcome()
        with self.assertRaises(FrozenInstanceError):
            outcome.final_status = ApplicationStatus.CRITICAL

    def test_partial_final_state_is_rejected(self):
        no_telemetry = completed_trip_outcome_from_state(
            self.trip,
            self.completed_at,
            None,
        )
        with self.assertRaises(CompletedTripOutcomeValidationError):
            validate_completed_trip_outcome(
                replace(no_telemetry, final_status=ApplicationStatus.SAFE)
            )

    def test_invalid_final_numeric_values_are_rejected(self):
        outcome = contract_completed_trip_outcome()
        invalid = (
            replace(outcome, final_temperature=float("nan")),
            replace(outcome, final_excursion_episode_duration_minutes=-1.0),
            replace(outcome, final_cumulative_excursion_duration_minutes=-1.0),
            replace(outcome, final_excursion_utilization=-0.1),
            replace(outcome, final_live_state_revision=0),
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(CompletedTripOutcomeValidationError):
                    validate_completed_trip_outcome(value)

    def test_serialization_round_trip(self):
        outcome = contract_completed_trip_outcome()
        self.assertEqual(
            deserialize_completed_trip_outcome(
                serialize_completed_trip_outcome(outcome)
            ),
            outcome,
        )

    def test_offset_timestamps_normalize_to_utc(self):
        offset = timezone(timedelta(hours=3))
        outcome = replace(
            contract_completed_trip_outcome(),
            completed_at=self.completed_at.astimezone(offset),
        )
        restored = deserialize_completed_trip_outcome(
            serialize_completed_trip_outcome(outcome)
        )
        self.assertEqual(restored.completed_at, self.completed_at)


if __name__ == "__main__":
    unittest.main()
