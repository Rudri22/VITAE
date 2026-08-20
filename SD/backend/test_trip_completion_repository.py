import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

try:
    from .repository_contract_suite import (
        CONTRACT_TIME,
        TripCompletionRepositoryContractMixin,
        contract_assignment,
        contract_sample,
        contract_state,
        contract_trip,
    )
    from .shipment_access import InMemoryIdentityAccessRepository
    from .sqlite_trip_completion_repository import SQLiteTripCompletionRepository
    from .state_repository import TripNotActiveAtCommitError, telemetry_record_from_sample
    from .trip_completion import TripCompletionConflictError
    from .trip_identity import TripStatus
except ImportError:
    from repository_contract_suite import (
        CONTRACT_TIME,
        TripCompletionRepositoryContractMixin,
        contract_assignment,
        contract_sample,
        contract_state,
        contract_trip,
    )
    from shipment_access import InMemoryIdentityAccessRepository
    from sqlite_trip_completion_repository import SQLiteTripCompletionRepository
    from state_repository import TripNotActiveAtCommitError, telemetry_record_from_sample
    from trip_completion import TripCompletionConflictError
    from trip_identity import TripStatus


class InMemoryTripCompletionContractTests(
    TripCompletionRepositoryContractMixin,
    unittest.TestCase,
):
    def make_trip_completion_repository(self):
        return InMemoryIdentityAccessRepository()


class SQLiteTripCompletionContractTests(
    TripCompletionRepositoryContractMixin,
    unittest.TestCase,
):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self._temporary.name) / "completion.sqlite3"
        super().setUp()

    def tearDown(self):
        self._temporary.cleanup()
        super().tearDown()

    def make_trip_completion_repository(self):
        return SQLiteTripCompletionRepository(self.database_path)

    def test_completion_survives_repository_restart(self):
        result = self.repository.complete_trip(
            self.trip.trip_id,
            self.assignment.assignment_id,
            completed_at=self.completed_at,
        )
        reopened = SQLiteTripCompletionRepository(self.database_path)
        self.assertEqual(
            reopened.complete_trip(
                self.trip.trip_id,
                self.assignment.assignment_id,
                completed_at=self.completed_at,
            ),
            result,
        )

    def test_conflicting_retry_after_restart_is_rejected(self):
        self.repository.complete_trip(
            self.trip.trip_id,
            self.assignment.assignment_id,
            completed_at=self.completed_at,
        )
        reopened = SQLiteTripCompletionRepository(self.database_path)
        with self.assertRaises(TripCompletionConflictError):
            reopened.complete_trip(
                self.trip.trip_id,
                self.assignment.assignment_id,
                completed_at=self.completed_at + timedelta(seconds=1),
            )


class _FailingSQLiteCompletionRepository(SQLiteTripCompletionRepository):
    def _before_completion_write(self, connection, result):
        raise RuntimeError("injected completion failure")


class TripCompletionAtomicityTests(unittest.TestCase):
    def _active_repository(self, repository):
        trip = contract_trip()
        assignment = contract_assignment()
        repository.register_trip_and_assignment(trip, assignment)
        active_trip, _ = repository.transition_trip_and_assignment(
            trip.trip_id,
            assignment.assignment_id,
            TripStatus.PLANNED,
            TripStatus.ACTIVE,
            False,
            True,
        )
        return trip, assignment, active_trip

    def test_memory_telemetry_completion_race_has_one_valid_order(self):
        repository = InMemoryIdentityAccessRepository()
        trip, assignment, active_trip = self._active_repository(repository)
        first = contract_sample(minutes=1)
        first_state = contract_state(first)
        repository.commit_sample_and_state(
            telemetry_record_from_sample(
                active_trip.trip_id, active_trip.lot_trip_id, first
            ), first_state, None
        )
        second = contract_sample(sample_id="race-sample", minutes=2)
        second_state = contract_state(second, first_state)
        barrier = Barrier(2)

        def complete():
            barrier.wait()
            return repository.complete_trip(
                trip.trip_id,
                assignment.assignment_id,
                completed_at=CONTRACT_TIME + timedelta(hours=1),
            )

        def commit():
            barrier.wait()
            repository.commit_sample_and_state(
                telemetry_record_from_sample(
                    active_trip.trip_id, active_trip.lot_trip_id, second
                ), second_state, 1
            )
            return "committed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            completion_future = executor.submit(complete)
            telemetry_future = executor.submit(commit)
            result = completion_future.result()
            try:
                telemetry_result = telemetry_future.result()
            except TripNotActiveAtCommitError:
                telemetry_result = "blocked"

        if telemetry_result == "committed":
            self.assertEqual(result.final_live_state, second_state)
        else:
            self.assertEqual(result.final_live_state, first_state)
        self.assertEqual(repository.get_live_state(trip.lot_trip_id), result.final_live_state)

    def test_sqlite_failure_rolls_back_lifecycle_and_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollback.sqlite3"
            repository = _FailingSQLiteCompletionRepository(path)
            trip, assignment, _ = self._active_repository(repository)
            with self.assertRaisesRegex(RuntimeError, "injected completion failure"):
                repository.complete_trip(
                    trip.trip_id,
                    assignment.assignment_id,
                    completed_at=CONTRACT_TIME + timedelta(hours=1),
                )
            reopened = SQLiteTripCompletionRepository(path)
            self.assertEqual(reopened.get_trip_by_id(trip.trip_id).status, TripStatus.ACTIVE)
            self.assertTrue(reopened.get_device_assignments(trip.device_id)[0].active)
            self.assertIsNone(reopened.get_completed_trip_outcome(trip.lot_trip_id))

    def test_sqlite_telemetry_completion_race_has_one_valid_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "race.sqlite3"
            repository = SQLiteTripCompletionRepository(path)
            trip, assignment, active_trip = self._active_repository(repository)
            first = contract_sample(minutes=1)
            first_state = contract_state(first)
            repository.commit_sample_and_state(
                telemetry_record_from_sample(
                    active_trip.trip_id, active_trip.lot_trip_id, first
                ),
                first_state,
                None,
            )
            second = contract_sample(sample_id="sqlite-race-sample", minutes=2)
            second_state = contract_state(second, first_state)
            barrier = Barrier(2)

            def complete():
                barrier.wait()
                return repository.complete_trip(
                    trip.trip_id,
                    assignment.assignment_id,
                    completed_at=CONTRACT_TIME + timedelta(hours=1),
                )

            def commit():
                barrier.wait()
                try:
                    repository.commit_sample_and_state(
                        telemetry_record_from_sample(
                            active_trip.trip_id, active_trip.lot_trip_id, second
                        ),
                        second_state,
                        1,
                    )
                    return "committed"
                except TripNotActiveAtCommitError:
                    return "blocked"

            with ThreadPoolExecutor(max_workers=2) as executor:
                completion_future = executor.submit(complete)
                telemetry_future = executor.submit(commit)
                result = completion_future.result()
                telemetry_result = telemetry_future.result()
            expected = second_state if telemetry_result == "committed" else first_state
            self.assertEqual(result.final_live_state, expected)
            self.assertEqual(repository.get_live_state(trip.lot_trip_id), expected)


if __name__ == "__main__":
    unittest.main()
