import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from threading import Barrier

try:
    from .alerting import InMemoryAlertRepository
    from .completed_trip_outcome import completed_trip_outcome_from_state
    from .decision_outbox import OutboxDeliveryStatus
    from .repository_contract_suite import (
        CONTRACT_TIME,
        ProcessingBundleRepositoryContractMixin,
        TelemetryStateRepositoryContractMixin,
        contract_alert_outbox_event,
        contract_assignment,
        contract_completed_trip_outcome,
        contract_decision_record,
        contract_sample,
        contract_state,
        contract_trip,
    )
    from .repository_serialization import (
        serialize_alert_outbox_event,
        serialize_live_state,
        serialize_status_decision_record,
        serialize_telemetry_record,
    )
    from .sqlite_completed_trip_outcome_repository import (
        SQLiteCompletedTripOutcomeRepository,
    )
    from .sqlite_identity_repository import SQLiteIdentityAccessRepository
    from .sqlite_telemetry_repository import (
        SQLiteTelemetryStateRepository,
        SQLiteTelemetryStorageError,
    )
    from .state_repository import (
        StateIntegrityError,
        TripNotActiveAtCommitError,
        telemetry_record_from_sample,
    )
    from .operational_service import OperationalTelemetryService
    from .telemetry_processor import TelemetryProcessor
    from .trip_identity import TripStatus
except ImportError:
    from alerting import InMemoryAlertRepository
    from completed_trip_outcome import completed_trip_outcome_from_state
    from decision_outbox import OutboxDeliveryStatus
    from repository_contract_suite import (
        CONTRACT_TIME,
        ProcessingBundleRepositoryContractMixin,
        TelemetryStateRepositoryContractMixin,
        contract_alert_outbox_event,
        contract_assignment,
        contract_completed_trip_outcome,
        contract_decision_record,
        contract_sample,
        contract_state,
        contract_trip,
    )
    from repository_serialization import (
        serialize_alert_outbox_event,
        serialize_live_state,
        serialize_status_decision_record,
        serialize_telemetry_record,
    )
    from sqlite_completed_trip_outcome_repository import (
        SQLiteCompletedTripOutcomeRepository,
    )
    from sqlite_identity_repository import SQLiteIdentityAccessRepository
    from sqlite_telemetry_repository import (
        SQLiteTelemetryStateRepository,
        SQLiteTelemetryStorageError,
    )
    from state_repository import (
        StateIntegrityError,
        TripNotActiveAtCommitError,
        telemetry_record_from_sample,
    )
    from operational_service import OperationalTelemetryService
    from telemetry_processor import TelemetryProcessor
    from trip_identity import TripStatus


class SQLiteTelemetryContractBase:
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = str(
            Path(self._temporary_directory.name) / "vitae-contract.sqlite3"
        )
        self.identity_repository = SQLiteIdentityAccessRepository(
            self.database_path
        )
        super().setUp()

    def tearDown(self):
        try:
            super().tearDown()
        finally:
            self._temporary_directory.cleanup()

    def prepare_active_contract_trip(self, repository):
        self.identity_repository.register_trip_and_assignment(
            contract_trip(status=TripStatus.ACTIVE),
            contract_assignment(active=True),
        )

    def complete_contract_trip(self, repository):
        self.identity_repository.transition_trip_and_assignment(
            "contract-trip",
            "contract-assignment",
            TripStatus.ACTIVE,
            TripStatus.COMPLETED,
            True,
            False,
            completed_at=CONTRACT_TIME + timedelta(minutes=30),
        )


class SQLiteTelemetryStateContractTests(
    SQLiteTelemetryContractBase,
    TelemetryStateRepositoryContractMixin,
    unittest.TestCase,
):
    def make_telemetry_state_repository(self):
        return SQLiteTelemetryStateRepository(self.database_path)


class SQLiteProcessingBundleContractTests(
    SQLiteTelemetryContractBase,
    ProcessingBundleRepositoryContractMixin,
    unittest.TestCase,
):
    def make_processing_bundle_repository(self):
        return SQLiteTelemetryStateRepository(self.database_path)


class SQLiteTelemetryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = str(
            Path(self.temporary_directory.name) / "vitae.sqlite3"
        )
        self.identity = SQLiteIdentityAccessRepository(self.database_path)
        self.identity.register_trip_and_assignment(
            contract_trip(status=TripStatus.ACTIVE),
            contract_assignment(active=True),
        )
        self.repository = SQLiteTelemetryStateRepository(self.database_path)
        self.sample = contract_sample()
        self.record = telemetry_record_from_sample(
            "contract-trip", "contract-lot-trip", self.sample
        )
        self.state = contract_state(self.sample)
        self.decision = contract_decision_record(self.sample, self.state)
        self.event = contract_alert_outbox_event(self.decision)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def commit_bundle(self, event=None):
        self.repository.commit_processing_bundle(
            self.record,
            self.state,
            self.decision,
            event,
            expected_revision=None,
        )

    def test_bundle_survives_repository_restart_with_canonical_documents(self):
        self.commit_bundle(self.event)
        restarted = SQLiteTelemetryStateRepository(self.database_path)
        self.assertEqual(
            serialize_telemetry_record(
                restarted.get_telemetry_history("contract-lot-trip")[0]
            ),
            serialize_telemetry_record(self.record),
        )
        self.assertEqual(
            serialize_live_state(restarted.get_live_state("contract-lot-trip")),
            serialize_live_state(self.state),
        )
        self.assertEqual(
            serialize_status_decision_record(
                restarted.get_decision(self.decision.decision_id)
            ),
            serialize_status_decision_record(self.decision),
        )
        self.assertEqual(
            serialize_alert_outbox_event(
                restarted.get_outbox_event(self.event.event_id)
            ),
            serialize_alert_outbox_event(self.event),
        )

    def test_operational_service_uses_durable_bundle_without_status_duplication(self):
        alerts = InMemoryAlertRepository()
        service = OperationalTelemetryService(
            TelemetryProcessor(self.identity, self.repository),
            alerts,
        )
        safe = service.process(
            {
                "sample_id": "sqlite-safe",
                "device_id": "contract-device",
                "timestamp": CONTRACT_TIME.isoformat(),
                "temperature": 6.0,
            }
        )
        monitor = service.process(
            {
                "sample_id": "sqlite-monitor",
                "device_id": "contract-device",
                "timestamp": (CONTRACT_TIME + timedelta(minutes=5)).isoformat(),
                "temperature": 9.0,
            }
        )
        restarted = SQLiteTelemetryStateRepository(self.database_path)
        self.assertEqual(safe.processing_result.live_state.revision, 1)
        self.assertEqual(monitor.processing_result.live_state.revision, 2)
        self.assertIsNotNone(monitor.alert)
        self.assertEqual(
            len(restarted.get_decision_history("contract-lot-trip")), 2
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            event_id, status = connection.execute(
                "SELECT event_id, delivery_status FROM alert_outbox_events"
            ).fetchone()
        persisted_event = restarted.get_outbox_event(event_id)
        self.assertEqual(status, OutboxDeliveryStatus.DELIVERED.value)
        self.assertEqual(
            persisted_event.delivery_status,
            OutboxDeliveryStatus.DELIVERED,
        )
        self.assertEqual(persisted_event.alert_candidate, monitor.alert)

    def test_bundle_failure_rolls_back_every_document(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "CREATE TRIGGER reject_test_outbox BEFORE INSERT ON alert_outbox_events "
                "BEGIN SELECT RAISE(ABORT, 'test outbox failure'); END"
            )
            connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.commit_bundle(self.event)
        self.assertFalse(
            self.repository.has_sample(self.record.device_id, self.record.sample_id)
        )
        self.assertEqual(
            self.repository.get_telemetry_history("contract-lot-trip"), ()
        )
        self.assertIsNone(self.repository.get_live_state("contract-lot-trip"))
        self.assertIsNone(self.repository.get_decision(self.decision.decision_id))
        self.assertIsNone(self.repository.get_outbox_event(self.event.event_id))

    def test_outbox_lease_and_retry_state_survive_restarts(self):
        self.commit_bundle(self.event)
        claimed = self.repository.claim_outbox_event(
            self.event.event_id,
            worker_id="worker-a",
            claimed_at=CONTRACT_TIME,
            lease_duration=timedelta(minutes=5),
        )
        restarted = SQLiteTelemetryStateRepository(self.database_path)
        self.assertEqual(restarted.get_outbox_event(self.event.event_id), claimed)
        pending = restarted.release_outbox_event(
            self.event.event_id,
            worker_id="worker-a",
            released_at=CONTRACT_TIME + timedelta(minutes=1),
            retry_at=CONTRACT_TIME + timedelta(minutes=2),
            error_code="ALERT_STORE_UNAVAILABLE",
        )
        restarted_again = SQLiteTelemetryStateRepository(self.database_path)
        self.assertEqual(
            restarted_again.get_outbox_event(self.event.event_id), pending
        )
        self.assertEqual(
            restarted_again.list_dispatchable_outbox_events(
                CONTRACT_TIME + timedelta(minutes=2)
            ),
            (pending,),
        )

    def test_identity_telemetry_and_outcome_tables_coexist(self):
        self.commit_bundle()
        completed_at = CONTRACT_TIME + timedelta(minutes=30)
        completed_trip, _ = self.identity.transition_trip_and_assignment(
            "contract-trip",
            "contract-assignment",
            TripStatus.ACTIVE,
            TripStatus.COMPLETED,
            True,
            False,
            completed_at=completed_at,
        )
        outcome = completed_trip_outcome_from_state(
            completed_trip,
            completed_at,
            self.state,
        )
        outcomes = SQLiteCompletedTripOutcomeRepository(self.database_path)
        outcomes.save_outcome(outcome)

        self.assertEqual(
            self.identity.get_trip_by_id("contract-trip"), completed_trip
        )
        self.assertEqual(
            self.repository.get_live_state("contract-lot-trip"), self.state
        )
        self.assertEqual(outcomes.get_outcome("contract-lot-trip"), outcome)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_outcome_first_then_identity_and_telemetry_are_additive(self):
        other_path = str(Path(self.temporary_directory.name) / "reverse.sqlite3")
        outcomes = SQLiteCompletedTripOutcomeRepository(other_path)
        expected_outcome = contract_completed_trip_outcome()
        outcomes.save_outcome(expected_outcome)
        identity = SQLiteIdentityAccessRepository(other_path)
        identity.register_trip_and_assignment(
            contract_trip(status=TripStatus.ACTIVE),
            contract_assignment(active=True),
        )
        telemetry = SQLiteTelemetryStateRepository(other_path)
        telemetry.commit_processing_bundle(
            self.record,
            self.state,
            self.decision,
            None,
            expected_revision=None,
        )
        self.assertEqual(
            SQLiteCompletedTripOutcomeRepository(other_path).get_outcome(
                expected_outcome.lot_trip_id
            ),
            expected_outcome,
        )
        self.assertEqual(
            telemetry.get_live_state("contract-lot-trip"), self.state
        )

    def test_completion_and_bundle_commit_are_serialized_by_database_lock(self):
        barrier = Barrier(2)

        def commit_bundle():
            barrier.wait()
            try:
                self.commit_bundle(self.event)
                return "telemetry"
            except TripNotActiveAtCommitError:
                return "fenced"

        def complete_trip():
            barrier.wait()
            self.identity.transition_trip_and_assignment(
                "contract-trip",
                "contract-assignment",
                TripStatus.ACTIVE,
                TripStatus.COMPLETED,
                True,
                False,
                completed_at=CONTRACT_TIME + timedelta(minutes=30),
            )
            return "completed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            telemetry_future = executor.submit(commit_bundle)
            completion_future = executor.submit(complete_trip)
            telemetry_result = telemetry_future.result()
            self.assertEqual(completion_future.result(), "completed")

        self.assertIn(telemetry_result, ("telemetry", "fenced"))
        expected_count = 1 if telemetry_result == "telemetry" else 0
        self.assertEqual(
            len(self.repository.get_telemetry_history("contract-lot-trip")),
            expected_count,
        )
        with self.assertRaises(TripNotActiveAtCommitError):
            if expected_count:
                next_sample = contract_sample(sample_id="after-completion", minutes=31)
                next_state = contract_state(next_sample, self.state)
                self.repository.commit_processing_bundle(
                    telemetry_record_from_sample(
                        "contract-trip", "contract-lot-trip", next_sample
                    ),
                    next_state,
                    contract_decision_record(next_sample, next_state),
                    None,
                    expected_revision=1,
                )
            else:
                self.commit_bundle()

    def test_corrupt_live_state_fails_closed(self):
        self.commit_bundle()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE live_states SET document = '{not-json}' "
                "WHERE lot_trip_id = 'contract-lot-trip'"
            )
            connection.commit()
        with self.assertRaises(SQLiteTelemetryStorageError):
            self.repository.get_live_state("contract-lot-trip")

    def test_corrupt_outbox_is_quarantined_without_rewriting_raw_payload(self):
        self.commit_bundle(self.event)
        corrupt = "{not-json}"
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE alert_outbox_events SET document = ? WHERE event_id = ?",
                (corrupt, self.event.event_id),
            )
            connection.commit()
        discovery = self.repository.discover_dispatchable_outbox_events(
            CONTRACT_TIME,
            limit=10,
        )
        self.assertEqual(discovery.events, ())
        self.assertEqual(discovery.corrupt_quarantined_count, 1)
        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute(
                "SELECT persistence_state, document, record_version "
                "FROM alert_outbox_events WHERE event_id = ?",
                (self.event.event_id,),
            ).fetchone()
        self.assertEqual(row, ("QUARANTINED", corrupt, 2))
        with self.assertRaises(SQLiteTelemetryStorageError):
            self.repository.get_outbox_event(self.event.event_id)

    def test_repository_requires_identity_tables_in_same_database(self):
        isolated_path = str(Path(self.temporary_directory.name) / "isolated.sqlite3")
        repository = SQLiteTelemetryStateRepository(isolated_path)
        with self.assertRaisesRegex(StateIntegrityError, "identity tables"):
            repository.commit_sample_and_state(self.record, self.state, None)
        self.assertEqual(
            repository.get_telemetry_history("contract-lot-trip"), ()
        )

    def test_no_telemetry_reads_as_empty_without_manufacturing_state(self):
        other_path = str(Path(self.temporary_directory.name) / "empty.sqlite3")
        SQLiteIdentityAccessRepository(other_path)
        repository = SQLiteTelemetryStateRepository(other_path)
        self.assertEqual(repository.get_telemetry_history("missing"), ())
        self.assertIsNone(repository.get_live_state("missing"))
        self.assertEqual(repository.get_decision_history("missing"), ())


if __name__ == "__main__":
    unittest.main()
