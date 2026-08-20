import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier

try:
    from .completed_trip_outcome import (
        CompletedTripOutcomeConflictError,
        CompletedTripOutcomeRepository,
        completed_trip_outcome_from_state,
    )
    from .repository_contract_suite import (
        CONTRACT_TIME,
        CompletedTripOutcomeRepositoryContractMixin,
        contract_completed_trip_outcome,
        contract_trip,
    )
    from .repository_serialization import serialize_completed_trip_outcome
    from .sqlite_completed_trip_outcome_repository import (
        SQLiteCompletedTripOutcomeRepository,
        SQLiteCompletedTripOutcomeStorageError,
    )
    from .trip_identity import TripStatus
except ImportError:
    from completed_trip_outcome import (
        CompletedTripOutcomeConflictError,
        CompletedTripOutcomeRepository,
        completed_trip_outcome_from_state,
    )
    from repository_contract_suite import (
        CONTRACT_TIME,
        CompletedTripOutcomeRepositoryContractMixin,
        contract_completed_trip_outcome,
        contract_trip,
    )
    from repository_serialization import serialize_completed_trip_outcome
    from sqlite_completed_trip_outcome_repository import (
        SQLiteCompletedTripOutcomeRepository,
        SQLiteCompletedTripOutcomeStorageError,
    )
    from trip_identity import TripStatus


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class SQLiteCompletedOutcomeContractTests(
    CompletedTripOutcomeRepositoryContractMixin,
    unittest.TestCase,
):
    def make_completed_trip_outcome_repository(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "outcomes.sqlite3"
        return SQLiteCompletedTripOutcomeRepository(path)


class SQLiteCompletedOutcomePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "outcomes.sqlite3"
        self.repository = SQLiteCompletedTripOutcomeRepository(self.path)
        self.outcome = contract_completed_trip_outcome()

    def test_adapter_satisfies_runtime_protocol(self):
        self.assertIsInstance(
            self.repository,
            CompletedTripOutcomeRepository,
        )

    def test_save_then_read(self):
        saved = self.repository.save_outcome(self.outcome)
        self.assertEqual(saved, self.outcome)
        self.assertEqual(
            self.repository.get_outcome(self.outcome.lot_trip_id),
            self.outcome,
        )

    def test_restart_preserves_outcome(self):
        self.repository.save_outcome(self.outcome)

        restarted = SQLiteCompletedTripOutcomeRepository(self.path)

        self.assertEqual(
            restarted.get_outcome(self.outcome.lot_trip_id),
            self.outcome,
        )

    def test_identical_retry_before_and_after_restart(self):
        first = self.repository.save_outcome(self.outcome)
        self.assertEqual(self.repository.save_outcome(self.outcome), first)

        restarted = SQLiteCompletedTripOutcomeRepository(self.path)

        self.assertEqual(restarted.save_outcome(self.outcome), first)
        self.assertEqual(
            restarted.get_outcome(self.outcome.lot_trip_id),
            first,
        )

    def test_conflicting_rewrite_before_and_after_restart(self):
        self.repository.save_outcome(self.outcome)
        conflict = replace(
            self.outcome,
            completed_at=self.outcome.completed_at + timedelta(seconds=1),
        )
        with self.assertRaises(CompletedTripOutcomeConflictError):
            self.repository.save_outcome(conflict)

        restarted = SQLiteCompletedTripOutcomeRepository(self.path)
        with self.assertRaises(CompletedTripOutcomeConflictError):
            restarted.save_outcome(conflict)
        self.assertEqual(
            restarted.get_outcome(self.outcome.lot_trip_id),
            self.outcome,
        )

    def test_no_telemetry_outcome_preserves_null_final_state(self):
        no_telemetry = completed_trip_outcome_from_state(
            contract_trip(status=TripStatus.COMPLETED),
            CONTRACT_TIME + timedelta(minutes=30),
            None,
        )
        self.repository.save_outcome(no_telemetry)

        restored = SQLiteCompletedTripOutcomeRepository(
            self.path
        ).get_outcome(no_telemetry.lot_trip_id)

        self.assertEqual(restored, no_telemetry)
        self.assertIsNone(restored.final_status)
        self.assertIsNone(restored.final_live_state_revision)

    def test_stored_document_is_canonical_versioned_serialization(self):
        self.repository.save_outcome(self.outcome)
        with closing(sqlite3.connect(self.path)) as connection:
            stored = connection.execute(
                "SELECT document FROM completed_trip_outcomes "
                "WHERE lot_trip_id = ?",
                (self.outcome.lot_trip_id,),
            ).fetchone()[0]
        self.assertEqual(
            stored,
            _canonical_json(serialize_completed_trip_outcome(self.outcome)),
        )

    def test_multiple_lot_trips_remain_isolated(self):
        other = replace(
            self.outcome,
            lot_trip_id="other-lot-trip",
            trip_id="other-trip",
            lot_id="other-lot",
        )
        self.repository.save_outcome(self.outcome)
        self.repository.save_outcome(other)

        restarted = SQLiteCompletedTripOutcomeRepository(self.path)

        self.assertEqual(
            restarted.get_outcome(self.outcome.lot_trip_id),
            self.outcome,
        )
        self.assertEqual(restarted.get_outcome(other.lot_trip_id), other)

    def test_concurrent_conflicting_finalization_has_one_winner(self):
        first_repository = SQLiteCompletedTripOutcomeRepository(self.path)
        second_repository = SQLiteCompletedTripOutcomeRepository(self.path)
        conflict = replace(
            self.outcome,
            completed_at=self.outcome.completed_at + timedelta(seconds=1),
        )
        barrier = Barrier(2)

        def save(repository, outcome):
            barrier.wait()
            try:
                repository.save_outcome(outcome)
                return "saved", outcome
            except CompletedTripOutcomeConflictError:
                return "conflict", outcome

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = (
                executor.submit(save, first_repository, self.outcome),
                executor.submit(save, second_repository, conflict),
            )
            outcomes = tuple(result.result() for result in results)

        self.assertCountEqual(
            (result for result, _ in outcomes),
            ("saved", "conflict"),
        )
        winner = next(outcome for result, outcome in outcomes if result == "saved")
        self.assertEqual(
            SQLiteCompletedTripOutcomeRepository(self.path).get_outcome(
                self.outcome.lot_trip_id
            ),
            winner,
        )

    def test_persistence_is_visible_to_a_new_sqlite_connection(self):
        self.repository.save_outcome(self.outcome)
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT lot_trip_id FROM completed_trip_outcomes"
            ).fetchone()
        self.assertEqual(row, (self.outcome.lot_trip_id,))

    def test_corrupt_document_fails_closed_without_rewriting_it(self):
        self.repository.save_outcome(self.outcome)
        corrupt = serialize_completed_trip_outcome(self.outcome)
        corrupt["schema_version"] = 999
        corrupt_document = _canonical_json(corrupt)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "UPDATE completed_trip_outcomes SET document = ? "
                "WHERE lot_trip_id = ?",
                (corrupt_document, self.outcome.lot_trip_id),
            )
            connection.commit()

        with self.assertRaises(SQLiteCompletedTripOutcomeStorageError):
            self.repository.get_outcome(self.outcome.lot_trip_id)
        with self.assertRaises(SQLiteCompletedTripOutcomeStorageError):
            self.repository.save_outcome(self.outcome)

        with closing(sqlite3.connect(self.path)) as connection:
            stored = connection.execute(
                "SELECT document FROM completed_trip_outcomes "
                "WHERE lot_trip_id = ?",
                (self.outcome.lot_trip_id,),
            ).fetchone()[0]
        self.assertEqual(stored, corrupt_document)

    def test_unknown_schema_version_is_rejected(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("PRAGMA user_version = 2")
        with self.assertRaisesRegex(
            SQLiteCompletedTripOutcomeStorageError,
            "Unsupported SQLite completed-outcome schema version",
        ):
            SQLiteCompletedTripOutcomeRepository(self.path)


if __name__ == "__main__":
    unittest.main()
