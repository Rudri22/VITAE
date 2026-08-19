import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from threading import Barrier

try:
    from .repository_contract_suite import (
        IdentityRepositoryContractMixin,
        ShipmentAccessRepositoryContractMixin,
        contract_assignment,
        contract_shipment_access,
        contract_trip,
    )
    from .shipment_access import IdentityAccessRepository, ShipmentAccessConflictError
    from .sqlite_identity_repository import SQLiteIdentityAccessRepository
    from .state_repository import StateIntegrityError
    from .trip_identity import TripStatus
except ImportError:
    from repository_contract_suite import (
        IdentityRepositoryContractMixin,
        ShipmentAccessRepositoryContractMixin,
        contract_assignment,
        contract_shipment_access,
        contract_trip,
    )
    from shipment_access import IdentityAccessRepository, ShipmentAccessConflictError
    from sqlite_identity_repository import SQLiteIdentityAccessRepository
    from state_repository import StateIntegrityError
    from trip_identity import TripStatus


class SQLiteContractDatabaseMixin:
    def repository_path(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name) / "identity.sqlite3"


class SQLiteIdentityRepositoryContractTests(
    SQLiteContractDatabaseMixin,
    IdentityRepositoryContractMixin,
    unittest.TestCase,
):
    def make_identity_repository(self):
        return SQLiteIdentityAccessRepository(self.repository_path())


class SQLiteShipmentAccessRepositoryContractTests(
    SQLiteContractDatabaseMixin,
    ShipmentAccessRepositoryContractMixin,
    unittest.TestCase,
):
    def make_shipment_access_repository(self):
        return SQLiteIdentityAccessRepository(self.repository_path())


class SQLiteIdentityPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "identity.sqlite3"
        self.repository = SQLiteIdentityAccessRepository(self.path)

    def test_adapter_satisfies_combined_identity_access_protocol(self):
        self.assertIsInstance(self.repository, IdentityAccessRepository)

    def test_registration_persists_identity_reservation_and_access_atomically(self):
        trip = contract_trip()
        assignment = contract_assignment()
        access = contract_shipment_access()
        self.repository.register_trip_assignment_and_access(
            trip, assignment, access
        )
        self.assertEqual(self.repository.get_trip_by_id(trip.trip_id), trip)
        self.assertEqual(
            self.repository.get_device_assignments(assignment.device_id),
            (assignment,),
        )
        self.assertEqual(
            self.repository.get_shipment_access(access.lot_trip_id), access
        )
        with closing(sqlite3.connect(self.path)) as connection:
            reservation = connection.execute(
                "SELECT assignment_id, reservation_state "
                "FROM device_reservations WHERE device_id = ?",
                (assignment.device_id,),
            ).fetchone()
        self.assertEqual(
            reservation,
            (assignment.assignment_id, TripStatus.PLANNED.value),
        )

    def test_access_conflict_rolls_back_identity_and_reservation(self):
        existing = replace(
            contract_shipment_access(), lot_trip_id="existing-lot-trip"
        )
        self.repository.register_shipment_access(existing)
        trip = contract_trip()
        assignment = contract_assignment()
        with self.assertRaises(ShipmentAccessConflictError):
            self.repository.register_trip_assignment_and_access(
                trip,
                assignment,
                contract_shipment_access(),
            )
        self.assertIsNone(self.repository.get_trip_by_id(trip.trip_id))
        self.assertEqual(
            self.repository.get_device_assignments(assignment.device_id), ()
        )
        with closing(sqlite3.connect(self.path)) as connection:
            reservation_count = connection.execute(
                "SELECT COUNT(*) FROM device_reservations"
            ).fetchone()[0]
        self.assertEqual(reservation_count, 0)
        self.assertEqual(
            self.repository.get_shipment_access(existing.lot_trip_id), existing
        )

    def test_restart_preserves_complete_planned_registration(self):
        trip = contract_trip()
        assignment = contract_assignment()
        access = contract_shipment_access()
        self.repository.register_trip_assignment_and_access(
            trip, assignment, access
        )

        restarted = SQLiteIdentityAccessRepository(self.path)

        self.assertEqual(restarted.get_trip_by_id(trip.trip_id), trip)
        self.assertEqual(
            restarted.get_trip_by_lot_trip_id(trip.lot_trip_id), trip
        )
        self.assertEqual(
            restarted.get_device_assignments(assignment.device_id),
            (assignment,),
        )
        self.assertEqual(
            restarted.get_shipment_access(access.lot_trip_id), access
        )

    def test_two_repository_instances_cannot_reserve_same_device(self):
        first = SQLiteIdentityAccessRepository(self.path)
        second = SQLiteIdentityAccessRepository(self.path)
        barrier = Barrier(2)

        def register(repository, suffix):
            trip = replace(
                contract_trip(),
                trip_id=f"trip-{suffix}",
                lot_trip_id=f"lot-trip-{suffix}",
                lot_id=f"lot-{suffix}",
            )
            assignment = replace(
                contract_assignment(),
                assignment_id=f"assignment-{suffix}",
                trip_id=trip.trip_id,
                lot_trip_id=trip.lot_trip_id,
            )
            barrier.wait()
            try:
                repository.register_trip_and_assignment(trip, assignment)
                return "registered", trip
            except StateIntegrityError:
                return "conflict", trip

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(register, first, "one"),
                executor.submit(register, second, "two"),
            )
            outcomes = tuple(future.result() for future in futures)

        self.assertCountEqual(
            (outcome for outcome, _ in outcomes),
            ("registered", "conflict"),
        )
        winner = next(trip for outcome, trip in outcomes if outcome == "registered")
        loser = next(trip for outcome, trip in outcomes if outcome == "conflict")
        restarted = SQLiteIdentityAccessRepository(self.path)
        self.assertEqual(restarted.get_trip_by_id(winner.trip_id), winner)
        self.assertIsNone(restarted.get_trip_by_id(loser.trip_id))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM device_reservations"
                ).fetchone()[0],
                1,
            )

    def test_concurrent_stale_lifecycle_transition_has_one_winner(self):
        trip = contract_trip()
        assignment = contract_assignment()
        self.repository.register_trip_and_assignment(trip, assignment)
        first = SQLiteIdentityAccessRepository(self.path)
        second = SQLiteIdentityAccessRepository(self.path)
        barrier = Barrier(2)

        def activate(repository):
            barrier.wait()
            try:
                repository.transition_trip_and_assignment(
                    trip.trip_id,
                    assignment.assignment_id,
                    TripStatus.PLANNED,
                    TripStatus.ACTIVE,
                    False,
                    True,
                )
                return "activated"
            except StateIntegrityError:
                return "stale"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(
                executor.map(activate, (first, second))
            )
        self.assertCountEqual(outcomes, ("activated", "stale"))
        restarted = SQLiteIdentityAccessRepository(self.path)
        self.assertEqual(
            restarted.get_trip_by_id(trip.trip_id).status,
            TripStatus.ACTIVE,
        )
        self.assertTrue(
            restarted.get_device_assignments(assignment.device_id)[0].active
        )

    def test_completion_releases_device_for_a_new_planned_trip(self):
        trip = contract_trip()
        assignment = contract_assignment()
        self.repository.register_trip_and_assignment(trip, assignment)
        self.repository.transition_trip_and_assignment(
            trip.trip_id,
            assignment.assignment_id,
            TripStatus.PLANNED,
            TripStatus.ACTIVE,
            False,
            True,
        )
        completed_trip, completed_assignment = (
            self.repository.transition_trip_and_assignment(
                trip.trip_id,
                assignment.assignment_id,
                TripStatus.ACTIVE,
                TripStatus.COMPLETED,
                True,
                False,
            )
        )
        self.assertEqual(completed_trip.status, TripStatus.COMPLETED)
        self.assertFalse(completed_assignment.active)

        next_trip = replace(
            contract_trip(),
            trip_id="replacement-trip",
            lot_trip_id="replacement-lot-trip",
            lot_id="replacement-lot",
        )
        next_assignment = replace(
            contract_assignment(),
            assignment_id="replacement-assignment",
            trip_id=next_trip.trip_id,
            lot_trip_id=next_trip.lot_trip_id,
        )
        self.repository.register_trip_and_assignment(
            next_trip,
            next_assignment,
        )
        self.assertEqual(
            self.repository.get_trip_by_id(next_trip.trip_id), next_trip
        )

    def test_compensation_removes_access_and_identity_together(self):
        trip = contract_trip()
        assignment = contract_assignment()
        access = contract_shipment_access()
        self.repository.register_trip_assignment_and_access(
            trip, assignment, access
        )
        self.repository.unregister_planned_trip_assignment_and_access(
            trip.trip_id,
            assignment.assignment_id,
            access.lot_trip_id,
            access.shipment_id,
        )
        self.assertIsNone(self.repository.get_trip_by_id(trip.trip_id))
        self.assertIsNone(
            self.repository.get_shipment_access(access.lot_trip_id)
        )

    def test_schema_contains_only_identity_and_access_tables(self):
        with closing(sqlite3.connect(self.path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        self.assertEqual(
            tables,
            {
                "trips",
                "assignments",
                "device_reservations",
                "shipment_access",
            },
        )

    def test_unknown_database_schema_version_is_rejected(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("PRAGMA user_version = 2")
        with self.assertRaisesRegex(
            StateIntegrityError,
            "Unsupported SQLite identity schema version",
        ):
            SQLiteIdentityAccessRepository(self.path)


if __name__ == "__main__":
    unittest.main()
