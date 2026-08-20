import json
import inspect
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

try:
    from .completed_trip_dataset import (
        CompletedTripDatasetIntegrityError,
        CompletedTripDatasetNotFoundError,
        CompletedTripDatasetRecord,
        CompletedTripDatasetService,
        CompletedTripHistoryReader,
        CompletedTripOutcomeReader,
        completed_trip_dataset_jsonl,
        validate_completed_trip_dataset_record,
    )
    from .repository_contract_suite import (
        CONTRACT_TIME,
        contract_assignment,
        contract_decision_record,
        contract_sample,
        contract_state,
        contract_trip,
    )
    from .repository_serialization import (
        RepositorySerializationError,
        deserialize_completed_trip_dataset_record,
        serialize_completed_trip_dataset_record,
    )
    from .shipment_access import InMemoryIdentityAccessRepository
    from .sqlite_trip_completion_repository import SQLiteTripCompletionRepository
    from .state_repository import telemetry_record_from_sample
    from .trip_identity import TripStatus
except ImportError:
    from completed_trip_dataset import (
        CompletedTripDatasetIntegrityError,
        CompletedTripDatasetNotFoundError,
        CompletedTripDatasetRecord,
        CompletedTripDatasetService,
        CompletedTripHistoryReader,
        CompletedTripOutcomeReader,
        completed_trip_dataset_jsonl,
        validate_completed_trip_dataset_record,
    )
    from repository_contract_suite import (
        CONTRACT_TIME,
        contract_assignment,
        contract_decision_record,
        contract_sample,
        contract_state,
        contract_trip,
    )
    from repository_serialization import (
        RepositorySerializationError,
        deserialize_completed_trip_dataset_record,
        serialize_completed_trip_dataset_record,
    )
    from shipment_access import InMemoryIdentityAccessRepository
    from sqlite_trip_completion_repository import SQLiteTripCompletionRepository
    from state_repository import telemetry_record_from_sample
    from trip_identity import TripStatus


class CompletedTripDatasetTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryIdentityAccessRepository()
        self.trip = contract_trip()
        self.assignment = contract_assignment()
        self.repository.register_trip_and_assignment(self.trip, self.assignment)
        self.repository.transition_trip_and_assignment(
            self.trip.trip_id,
            self.assignment.assignment_id,
            TripStatus.PLANNED,
            TripStatus.ACTIVE,
            False,
            True,
        )
        self.service = CompletedTripDatasetService(
            self.repository,
            self.repository,
        )

    def test_repository_adapters_conform_to_read_only_source_protocols(self):
        self.assertIsInstance(self.repository, CompletedTripOutcomeReader)
        self.assertIsInstance(self.repository, CompletedTripHistoryReader)

    def _commit_sample(self, sample, previous_state=None):
        state = contract_state(sample, previous_state)
        record = telemetry_record_from_sample(
            self.trip.trip_id,
            self.trip.lot_trip_id,
            sample,
        )
        decision = contract_decision_record(sample, state)
        self.repository.commit_processing_bundle(
            record,
            state,
            decision,
            None,
            None if previous_state is None else previous_state.revision,
        )
        return record, state, decision

    def _complete(self):
        return self.repository.complete_trip(
            self.trip.trip_id,
            self.assignment.assignment_id,
            completed_at=CONTRACT_TIME + timedelta(minutes=30),
        )

    def test_assembles_authoritative_completed_history_in_chronological_order(self):
        first_record, first_state, first_decision = self._commit_sample(
            contract_sample(minutes=1)
        )
        second_record, _, second_decision = self._commit_sample(
            contract_sample(sample_id="contract-sample-2", minutes=2),
            first_state,
        )
        completion = self._complete()

        value = self.service.get_record(self.trip.lot_trip_id)

        self.assertEqual(value.outcome, completion.outcome)
        self.assertEqual(value.telemetry_records, (first_record, second_record))
        self.assertEqual(
            value.decision_records,
            (first_decision, second_decision),
        )

    def test_requires_an_explicit_finalized_outcome(self):
        with self.assertRaises(CompletedTripDatasetNotFoundError):
            self.service.get_record(self.trip.lot_trip_id)

    def test_completed_trip_without_telemetry_has_empty_histories(self):
        completion = self._complete()
        value = self.service.get_record(self.trip.lot_trip_id)
        self.assertIsNone(completion.outcome.final_status)
        self.assertEqual(value.telemetry_records, ())
        self.assertEqual(value.decision_records, ())

    def test_missing_decision_fails_closed(self):
        sample = contract_sample(minutes=1)
        state = contract_state(sample)
        self.repository.commit_sample_and_state(
            telemetry_record_from_sample(
                self.trip.trip_id,
                self.trip.lot_trip_id,
                sample,
            ),
            state,
            None,
        )
        self._complete()
        with self.assertRaisesRegex(
            CompletedTripDatasetIntegrityError,
            "missing its deterministic decision",
        ):
            self.service.get_record(self.trip.lot_trip_id)

    def test_outcome_must_match_final_persisted_decision(self):
        self._commit_sample(contract_sample(minutes=1))
        self._complete()
        value = self.service.get_record(self.trip.lot_trip_id)
        invalid = replace(
            value,
            outcome=replace(
                value.outcome,
                final_temperature=value.outcome.final_temperature + 1,
            ),
        )
        with self.assertRaisesRegex(
            CompletedTripDatasetIntegrityError,
            "final persisted telemetry decision",
        ):
            validate_completed_trip_dataset_record(invalid)

    def test_non_chronological_or_noncontiguous_history_is_rejected(self):
        first_record, first_state, first_decision = self._commit_sample(
            contract_sample(minutes=1)
        )
        second_record, _, second_decision = self._commit_sample(
            contract_sample(sample_id="contract-sample-2", minutes=2),
            first_state,
        )
        self._complete()
        value = self.service.get_record(self.trip.lot_trip_id)
        for invalid in (
            replace(
                value,
                telemetry_records=(second_record, first_record),
                decision_records=(second_decision, first_decision),
            ),
            replace(
                value,
                decision_records=(
                    first_decision,
                    replace(second_decision, resulting_live_state_revision=3),
                ),
            ),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(CompletedTripDatasetIntegrityError):
                    validate_completed_trip_dataset_record(invalid)

    def test_post_completion_telemetry_is_rejected_as_leakage(self):
        self._commit_sample(contract_sample(minutes=1))
        self._complete()
        value = self.service.get_record(self.trip.lot_trip_id)
        leaked_timestamp = value.outcome.completed_at + timedelta(seconds=1)
        invalid = replace(
            value,
            telemetry_records=(
                replace(value.telemetry_records[0], timestamp=leaked_timestamp),
            ),
            decision_records=(
                replace(
                    value.decision_records[0],
                    sample_timestamp=leaked_timestamp,
                ),
            ),
        )
        with self.assertRaisesRegex(
            CompletedTripDatasetIntegrityError,
            "outside the completed trip",
        ):
            validate_completed_trip_dataset_record(invalid)

    def test_versioned_serialization_round_trip_is_canonical(self):
        self._commit_sample(contract_sample(minutes=1))
        self._complete()
        value = self.service.get_record(self.trip.lot_trip_id)
        document = serialize_completed_trip_dataset_record(value)
        self.assertEqual(document["schema"], "vitae.completed_trip_dataset_record")
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(
            deserialize_completed_trip_dataset_record(document),
            value,
        )
        self.assertEqual(
            serialize_completed_trip_dataset_record(
                deserialize_completed_trip_dataset_record(document)
            ),
            document,
        )

    def test_serialization_rejects_unversioned_extra_fields(self):
        self._complete()
        document = serialize_completed_trip_dataset_record(
            self.service.get_record(self.trip.lot_trip_id)
        )
        document["label"] = "must-not-exist"
        with self.assertRaises(RepositorySerializationError):
            deserialize_completed_trip_dataset_record(document)

    def test_jsonl_is_repeatable_sorted_and_contains_no_features_or_labels(self):
        self._complete()
        first = self.service.get_record(self.trip.lot_trip_id)
        second_outcome = replace(
            first.outcome,
            lot_trip_id="a-lot-trip",
            trip_id="a-trip",
            lot_id="a-lot",
            device_id="a-device",
        )
        second = CompletedTripDatasetRecord(
            lot_trip_id=second_outcome.lot_trip_id,
            outcome=second_outcome,
            telemetry_records=(),
            decision_records=(),
        )
        exported = completed_trip_dataset_jsonl((first, second))
        self.assertEqual(exported, completed_trip_dataset_jsonl((second, first)))
        documents = [json.loads(line) for line in exported.splitlines()]
        self.assertEqual(
            [item["lot_trip_id"] for item in documents],
            ["a-lot-trip", self.trip.lot_trip_id],
        )
        self.assertFalse(
            {"features", "feature", "labels", "label"}
            & set().union(*(document.keys() for document in documents))
        )

    def test_batch_rejects_duplicate_lot_trip_ids(self):
        self._complete()
        with self.assertRaisesRegex(
            CompletedTripDatasetIntegrityError,
            "must not contain duplicates",
        ):
            self.service.get_records(
                (self.trip.lot_trip_id, self.trip.lot_trip_id)
            )

    def test_dataset_module_does_not_recalculate_status_or_alert_policy(self):
        source = inspect.getsource(CompletedTripDatasetService)
        self.assertNotIn("evaluate_status", source)
        self.assertNotIn("evaluate_alert_policy", source)
        self.assertNotIn("ProductRules", source)


class SQLiteCompletedTripDatasetTests(unittest.TestCase):
    def test_completed_history_is_available_after_repository_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "dataset.sqlite3"
            repository = SQLiteTripCompletionRepository(database_path)
            trip = contract_trip()
            assignment = contract_assignment()
            repository.register_trip_and_assignment(trip, assignment)
            repository.transition_trip_and_assignment(
                trip.trip_id,
                assignment.assignment_id,
                TripStatus.PLANNED,
                TripStatus.ACTIVE,
                False,
                True,
            )
            sample = contract_sample(minutes=1)
            state = contract_state(sample)
            record = telemetry_record_from_sample(
                trip.trip_id,
                trip.lot_trip_id,
                sample,
            )
            decision = contract_decision_record(sample, state)
            repository.commit_processing_bundle(
                record,
                state,
                decision,
                None,
                None,
            )
            completion = repository.complete_trip(
                trip.trip_id,
                assignment.assignment_id,
                completed_at=CONTRACT_TIME + timedelta(minutes=30),
            )

            reopened = SQLiteTripCompletionRepository(database_path)
            value = CompletedTripDatasetService(reopened, reopened).get_record(
                trip.lot_trip_id
            )

            self.assertEqual(value.outcome, completion.outcome)
            self.assertEqual(value.telemetry_records, (record,))
            self.assertEqual(value.decision_records, (decision,))


if __name__ == "__main__":
    unittest.main()
