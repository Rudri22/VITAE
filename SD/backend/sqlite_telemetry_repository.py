import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from json import JSONDecodeError
from pathlib import Path
from typing import Optional, Tuple

try:
    from .decision_outbox import (
        AlertOutboxEvent,
        DecisionOutboxError,
        OutboxClaimError,
        OutboxDeliveryStatus,
        OutboxDiscoveryBatch,
        OutboxTransitionError,
        ProcessingBundleRepository,
        StatusDecisionRecord,
        validate_alert_outbox_event,
        validate_processing_bundle_commit,
    )
    from .repository_serialization import (
        RepositorySerializationError,
        deserialize_alert_outbox_event,
        deserialize_live_state,
        deserialize_status_decision_record,
        deserialize_telemetry_record,
        deserialize_trip_identity,
        serialize_alert_outbox_event,
        serialize_live_state,
        serialize_status_decision_record,
        serialize_telemetry_record,
    )
    from .state_repository import (
        DuplicateTelemetrySampleError,
        LiveState,
        StateIntegrityError,
        TelemetryRecord,
        TripNotActiveAtCommitError,
        validate_telemetry_state_commit,
    )
    from .trip_identity import TripStatus
except ImportError:
    from decision_outbox import (
        AlertOutboxEvent,
        DecisionOutboxError,
        OutboxClaimError,
        OutboxDeliveryStatus,
        OutboxDiscoveryBatch,
        OutboxTransitionError,
        ProcessingBundleRepository,
        StatusDecisionRecord,
        validate_alert_outbox_event,
        validate_processing_bundle_commit,
    )
    from repository_serialization import (
        RepositorySerializationError,
        deserialize_alert_outbox_event,
        deserialize_live_state,
        deserialize_status_decision_record,
        deserialize_telemetry_record,
        deserialize_trip_identity,
        serialize_alert_outbox_event,
        serialize_live_state,
        serialize_status_decision_record,
        serialize_telemetry_record,
    )
    from state_repository import (
        DuplicateTelemetrySampleError,
        LiveState,
        StateIntegrityError,
        TelemetryRecord,
        TripNotActiveAtCommitError,
        validate_telemetry_state_commit,
    )
    from trip_identity import TripStatus


class SQLiteTelemetryStorageError(StateIntegrityError):
    pass


class SQLiteTelemetryStateRepository(ProcessingBundleRepository):
    """File-backed atomic telemetry, state, decision, and outbox repository."""

    _DISCOVERY_OVERREAD = 4

    def __init__(self, database_path, *, timeout_seconds: float = 5.0):
        path = Path(database_path)
        if str(path) == ":memory:":
            raise ValueError("Use a file-backed SQLite database for persistence")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = str(path)
        self.timeout_seconds = float(timeout_seconds)
        self._initialize_schema()

    def get_live_state(self, lot_trip_id: str) -> Optional[LiveState]:
        with self._read_connection() as connection:
            return self._read_live_state(connection, lot_trip_id)

    def has_sample(self, device_id: str, sample_id: str) -> bool:
        with self._read_connection() as connection:
            return self._sample_exists(connection, device_id, sample_id)

    def get_telemetry_history(
        self, lot_trip_id: str
    ) -> Tuple[TelemetryRecord, ...]:
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT lot_trip_id, sample_timestamp, device_id, sample_id, "
                "trip_id, document FROM telemetry_records WHERE lot_trip_id = ? "
                "ORDER BY sample_timestamp, device_id, sample_id",
                (_required_text(lot_trip_id, "lot_trip_id"),),
            ).fetchall()
            records = []
            for row in rows:
                record = _deserialize_record(row[5])
                if (
                    record.lot_trip_id != row[0]
                    or _iso(record.timestamp) != row[1]
                    or record.device_id != row[2]
                    or record.sample_id != row[3]
                    or record.trip_id != row[4]
                ):
                    raise SQLiteTelemetryStorageError(
                        "Stored TelemetryRecord index attributes disagree with document"
                    )
                records.append(record)
            return tuple(records)

    def commit_sample_and_state(
        self,
        record: TelemetryRecord,
        new_state: LiveState,
        expected_revision: Optional[int],
    ) -> None:
        with self._write_transaction() as connection:
            current_state = self._read_live_state(connection, record.lot_trip_id)
            sample_exists = self._sample_exists(
                connection, record.device_id, record.sample_id
            )
            validate_telemetry_state_commit(
                record=record,
                new_state=new_state,
                current_state=current_state,
                expected_revision=expected_revision,
                sample_exists=sample_exists,
            )
            self._require_active_trip(connection, record)
            self._write_telemetry_transition(connection, record, new_state)

    def commit_processing_bundle(
        self,
        record: TelemetryRecord,
        new_state: LiveState,
        decision_record: StatusDecisionRecord,
        alert_outbox_event: Optional[AlertOutboxEvent],
        expected_revision: Optional[int],
    ) -> None:
        with self._write_transaction() as connection:
            current_state = self._read_live_state(connection, record.lot_trip_id)
            sample_exists = self._sample_exists(
                connection, record.device_id, record.sample_id
            )
            validate_processing_bundle_commit(
                record=record,
                new_state=new_state,
                decision_record=decision_record,
                alert_outbox_event=alert_outbox_event,
                current_state=current_state,
                expected_revision=expected_revision,
                sample_exists=sample_exists,
            )
            self._require_active_trip(connection, record)
            if self._read_decision(connection, decision_record.decision_id) is not None:
                raise DecisionOutboxError("Decision ID is already committed")
            if (
                alert_outbox_event is not None
                and self._read_outbox(connection, alert_outbox_event.event_id)
                is not None
            ):
                raise DecisionOutboxError("Outbox event ID is already committed")

            self._write_telemetry_transition(connection, record, new_state)
            connection.execute(
                "INSERT INTO status_decisions "
                "(decision_id, lot_trip_id, sample_timestamp, document) "
                "VALUES (?, ?, ?, ?)",
                (
                    decision_record.decision_id,
                    decision_record.lot_trip_id,
                    _iso(decision_record.sample_timestamp),
                    _json(serialize_status_decision_record(decision_record)),
                ),
            )
            if alert_outbox_event is not None:
                self._insert_outbox(connection, alert_outbox_event)

    def get_decision(self, decision_id: str) -> Optional[StatusDecisionRecord]:
        with self._read_connection() as connection:
            return self._read_decision(connection, decision_id)

    def get_decision_history(
        self, lot_trip_id: str
    ) -> Tuple[StatusDecisionRecord, ...]:
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT decision_id, lot_trip_id, sample_timestamp, document "
                "FROM status_decisions WHERE lot_trip_id = ? "
                "ORDER BY sample_timestamp, decision_id",
                (_required_text(lot_trip_id, "lot_trip_id"),),
            ).fetchall()
            return tuple(self._decision_from_row(row) for row in rows)

    def get_outbox_event(self, event_id: str) -> Optional[AlertOutboxEvent]:
        with self._read_connection() as connection:
            return self._read_outbox(connection, event_id)

    def list_dispatchable_outbox_events(
        self, as_of: datetime
    ) -> Tuple[AlertOutboxEvent, ...]:
        return self.discover_dispatchable_outbox_events(
            as_of,
            limit=1000,
        ).events

    def discover_dispatchable_outbox_events(
        self,
        as_of: datetime,
        *,
        limit: int,
    ) -> OutboxDiscoveryBatch:
        timestamp = _aware_timestamp(as_of, "as_of")
        bounded_limit = _positive_integer(limit, "limit")
        inspection_limit = bounded_limit * self._DISCOVERY_OVERREAD
        with self._write_transaction() as connection:
            rows = connection.execute(
                "SELECT event_id, lot_trip_id, delivery_status, attempt_count, "
                "due_at, document, persistence_state FROM alert_outbox_events "
                "WHERE persistence_state = 'ACTIVE' AND due_at IS NOT NULL "
                "AND due_at <= ? ORDER BY due_at, event_id LIMIT ?",
                (_iso(timestamp), inspection_limit),
            ).fetchall()
            events = []
            corrupt_count = 0
            for row in rows:
                event_id = row[0]
                try:
                    event = self._outbox_from_row(row)
                except SQLiteTelemetryStorageError:
                    connection.execute(
                        "UPDATE alert_outbox_events SET persistence_state = 'QUARANTINED', "
                        "due_at = NULL, quarantined_at = ?, "
                        "quarantine_error_code = 'OUTBOX_DESERIALIZATION_FAILED', "
                        "record_version = record_version + 1 WHERE event_id = ?",
                        (_iso(timestamp), event_id),
                    )
                    corrupt_count += 1
                    continue
                if _is_dispatchable(event, timestamp):
                    events.append(event)
                    if len(events) == bounded_limit:
                        break
            return OutboxDiscoveryBatch(
                events=tuple(events),
                corrupt_quarantined_count=corrupt_count,
            )

    def claim_outbox_event(
        self,
        event_id: str,
        *,
        worker_id: str,
        claimed_at: datetime,
        lease_duration: timedelta,
    ) -> AlertOutboxEvent:
        worker = _required_text(worker_id, "worker_id")
        timestamp = _aware_timestamp(claimed_at, "claimed_at")
        if not isinstance(lease_duration, timedelta) or lease_duration <= timedelta(0):
            raise OutboxClaimError("lease_duration must be positive")
        with self._write_transaction() as connection:
            event, version = self._require_outbox_with_version(connection, event_id)
            if not _is_dispatchable(event, timestamp):
                raise OutboxClaimError("Outbox event is not available for claim")
            claimed = replace(
                event,
                delivery_status=OutboxDeliveryStatus.IN_FLIGHT,
                attempt_count=event.attempt_count + 1,
                lease_owner=worker,
                lease_expires_at=timestamp + lease_duration,
            )
            return self._update_outbox(connection, claimed, version)

    def release_outbox_event(
        self,
        event_id: str,
        *,
        worker_id: str,
        released_at: datetime,
        retry_at: datetime,
        error_code: str,
    ) -> AlertOutboxEvent:
        worker = _required_text(worker_id, "worker_id")
        released = _aware_timestamp(released_at, "released_at")
        retry = _aware_timestamp(retry_at, "retry_at")
        error = _required_text(error_code, "error_code")
        if retry < released:
            raise OutboxTransitionError("retry_at cannot predate released_at")
        with self._write_transaction() as connection:
            event, version = self._require_owned_lease(
                connection, event_id, worker, released
            )
            pending = replace(
                event,
                delivery_status=OutboxDeliveryStatus.PENDING,
                available_at=retry,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=error,
            )
            return self._update_outbox(connection, pending, version)

    def mark_outbox_delivered(
        self,
        event_id: str,
        *,
        worker_id: str,
        delivered_at: datetime,
    ) -> AlertOutboxEvent:
        worker = _required_text(worker_id, "worker_id")
        timestamp = _aware_timestamp(delivered_at, "delivered_at")
        with self._write_transaction() as connection:
            event, version = self._require_owned_lease(
                connection, event_id, worker, timestamp
            )
            delivered = replace(
                event,
                delivery_status=OutboxDeliveryStatus.DELIVERED,
                lease_owner=None,
                lease_expires_at=None,
                delivered_at=timestamp,
                last_error_code=None,
            )
            return self._update_outbox(connection, delivered, version)

    def mark_outbox_dead_letter(
        self,
        event_id: str,
        *,
        worker_id: str,
        failed_at: datetime,
        error_code: str,
    ) -> AlertOutboxEvent:
        worker = _required_text(worker_id, "worker_id")
        timestamp = _aware_timestamp(failed_at, "failed_at")
        error = _required_text(error_code, "error_code")
        with self._write_transaction() as connection:
            event, version = self._require_owned_lease(
                connection, event_id, worker, timestamp
            )
            dead = replace(
                event,
                delivery_status=OutboxDeliveryStatus.DEAD_LETTER,
                lease_owner=None,
                lease_expires_at=None,
                delivered_at=None,
                last_error_code=error,
                dead_lettered_at=timestamp,
                dead_lettered_by=worker,
            )
            return self._update_outbox(connection, dead, version)

    def _initialize_schema(self) -> None:
        connection = self._connect()
        try:
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if schema_version not in (0, 1):
                raise SQLiteTelemetryStorageError(
                    f"Unsupported SQLite telemetry schema version: {schema_version}"
                )
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS telemetry_sample_guards (
                    device_id TEXT NOT NULL,
                    sample_id TEXT NOT NULL,
                    lot_trip_id TEXT NOT NULL,
                    PRIMARY KEY (device_id, sample_id)
                );

                CREATE TABLE IF NOT EXISTS telemetry_records (
                    lot_trip_id TEXT NOT NULL,
                    sample_timestamp TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    sample_id TEXT NOT NULL,
                    trip_id TEXT NOT NULL,
                    document TEXT NOT NULL,
                    PRIMARY KEY (lot_trip_id, sample_timestamp, device_id, sample_id),
                    UNIQUE (device_id, sample_id)
                );
                CREATE INDEX IF NOT EXISTS telemetry_records_by_lot_trip
                    ON telemetry_records(lot_trip_id, sample_timestamp, device_id, sample_id);

                CREATE TABLE IF NOT EXISTS live_states (
                    lot_trip_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL CHECK (revision > 0),
                    last_sample_timestamp TEXT NOT NULL,
                    document TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS status_decisions (
                    decision_id TEXT PRIMARY KEY,
                    lot_trip_id TEXT NOT NULL,
                    sample_timestamp TEXT NOT NULL,
                    document TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS status_decisions_by_lot_trip
                    ON status_decisions(lot_trip_id, sample_timestamp, decision_id);

                CREATE TABLE IF NOT EXISTS alert_outbox_events (
                    event_id TEXT PRIMARY KEY,
                    lot_trip_id TEXT NOT NULL,
                    delivery_status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
                    due_at TEXT,
                    persistence_state TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (persistence_state IN ('ACTIVE', 'QUARANTINED')),
                    record_version INTEGER NOT NULL CHECK (record_version > 0),
                    quarantined_at TEXT,
                    quarantine_error_code TEXT,
                    document TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS alert_outbox_due
                    ON alert_outbox_events(persistence_state, due_at, event_id);
                """
            )
            if schema_version == 0:
                connection.execute("PRAGMA user_version = 1")
        finally:
            connection.close()

    def _require_active_trip(self, connection, record: TelemetryRecord) -> None:
        try:
            row = connection.execute(
                "SELECT status, device_id, lot_trip_id, document FROM trips "
                "WHERE trip_id = ?",
                (record.trip_id,),
            ).fetchone()
        except sqlite3.OperationalError as error:
            raise SQLiteTelemetryStorageError(
                "SQLite telemetry commits require identity tables in the same database"
            ) from error
        if row is None:
            raise TripNotActiveAtCommitError(
                "Trip must still be ACTIVE when telemetry is committed"
            )
        try:
            trip = deserialize_trip_identity(json.loads(row[3]))
        except (JSONDecodeError, RepositorySerializationError, TypeError) as error:
            raise SQLiteTelemetryStorageError(
                "Stored TripIdentity is corrupt"
            ) from error
        if (
            row[0] != TripStatus.ACTIVE.value
            or row[1] != record.device_id
            or row[2] != record.lot_trip_id
            or trip.trip_id != record.trip_id
            or trip.lot_trip_id != record.lot_trip_id
            or trip.device_id != record.device_id
            or trip.status != TripStatus.ACTIVE
            or trip.completed_at is not None
        ):
            raise TripNotActiveAtCommitError(
                "Trip must still be ACTIVE when telemetry is committed"
            )

    def _write_telemetry_transition(self, connection, record, state):
        try:
            connection.execute(
                "INSERT INTO telemetry_sample_guards "
                "(device_id, sample_id, lot_trip_id) VALUES (?, ?, ?)",
                (record.device_id, record.sample_id, record.lot_trip_id),
            )
            connection.execute(
                "INSERT INTO telemetry_records "
                "(lot_trip_id, sample_timestamp, device_id, sample_id, trip_id, document) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.lot_trip_id,
                    _iso(record.timestamp),
                    record.device_id,
                    record.sample_id,
                    record.trip_id,
                    _json(serialize_telemetry_record(record)),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise DuplicateTelemetrySampleError(
                "Telemetry identity has already been committed"
            ) from error
        document = _json(serialize_live_state(state))
        existing = connection.execute(
            "SELECT 1 FROM live_states WHERE lot_trip_id = ?",
            (state.lot_trip_id,),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO live_states "
                "(lot_trip_id, revision, last_sample_timestamp, document) "
                "VALUES (?, ?, ?, ?)",
                (
                    state.lot_trip_id,
                    state.revision,
                    _iso(state.last_sample_timestamp),
                    document,
                ),
            )
        else:
            connection.execute(
                "UPDATE live_states SET revision = ?, last_sample_timestamp = ?, "
                "document = ? WHERE lot_trip_id = ?",
                (
                    state.revision,
                    _iso(state.last_sample_timestamp),
                    document,
                    state.lot_trip_id,
                ),
            )

    def _insert_outbox(self, connection, event):
        validate_alert_outbox_event(event)
        connection.execute(
            "INSERT INTO alert_outbox_events "
            "(event_id, lot_trip_id, delivery_status, attempt_count, due_at, "
            "persistence_state, record_version, document) "
            "VALUES (?, ?, ?, ?, ?, 'ACTIVE', 1, ?)",
            (
                event.event_id,
                event.lot_trip_id,
                event.delivery_status.value,
                event.attempt_count,
                _due_at(event),
                _json(serialize_alert_outbox_event(event)),
            ),
        )

    def _update_outbox(self, connection, event, expected_version):
        validate_alert_outbox_event(event)
        cursor = connection.execute(
            "UPDATE alert_outbox_events SET delivery_status = ?, attempt_count = ?, "
            "due_at = ?, record_version = record_version + 1, document = ? "
            "WHERE event_id = ? AND persistence_state = 'ACTIVE' "
            "AND record_version = ?",
            (
                event.delivery_status.value,
                event.attempt_count,
                _due_at(event),
                _json(serialize_alert_outbox_event(event)),
                event.event_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise OutboxTransitionError("Outbox event changed before transition")
        return event

    def _read_live_state(self, connection, lot_trip_id):
        row = connection.execute(
            "SELECT revision, last_sample_timestamp, document FROM live_states "
            "WHERE lot_trip_id = ?",
            (_required_text(lot_trip_id, "lot_trip_id"),),
        ).fetchone()
        if row is None:
            return None
        state = _deserialize_state(row[2])
        if (
            state.lot_trip_id != _required_text(lot_trip_id, "lot_trip_id")
            or state.revision != row[0]
            or _iso(state.last_sample_timestamp) != row[1]
        ):
            raise SQLiteTelemetryStorageError(
                "Stored LiveState index attributes disagree with document"
            )
        return state

    def _read_decision(self, connection, decision_id):
        row = connection.execute(
            "SELECT decision_id, lot_trip_id, sample_timestamp, document "
            "FROM status_decisions "
            "WHERE decision_id = ?",
            (_required_text(decision_id, "decision_id"),),
        ).fetchone()
        if row is None:
            return None
        return self._decision_from_row(row)

    def _decision_from_row(self, row):
        decision = _deserialize_decision(row[3])
        if (
            decision.decision_id != row[0]
            or decision.lot_trip_id != row[1]
            or _iso(decision.sample_timestamp) != row[2]
        ):
            raise SQLiteTelemetryStorageError(
                "Stored StatusDecisionRecord index attributes disagree with document"
            )
        return decision

    def _read_outbox(self, connection, event_id):
        row = connection.execute(
            "SELECT event_id, lot_trip_id, delivery_status, attempt_count, "
            "due_at, document, persistence_state FROM alert_outbox_events "
            "WHERE event_id = ?",
            (_required_text(event_id, "event_id"),),
        ).fetchone()
        if row is None:
            return None
        return self._outbox_from_row(row)

    def _outbox_from_row(self, row):
        if row[6] != "ACTIVE":
            raise SQLiteTelemetryStorageError(
                "Stored AlertOutboxEvent is quarantined"
            )
        event = _deserialize_outbox(row[5])
        if (
            event.event_id != row[0]
            or event.lot_trip_id != row[1]
            or event.delivery_status.value != row[2]
            or event.attempt_count != row[3]
            or _due_at(event) != row[4]
        ):
            raise SQLiteTelemetryStorageError(
                "Stored AlertOutboxEvent index attributes disagree with document"
            )
        return event

    def _require_outbox_with_version(self, connection, event_id):
        event = self._read_outbox(connection, event_id)
        if event is None:
            raise OutboxTransitionError("Outbox event does not exist")
        row = connection.execute(
            "SELECT record_version FROM alert_outbox_events WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()
        return event, row[0]

    def _require_owned_lease(self, connection, event_id, worker, timestamp):
        event, version = self._require_outbox_with_version(connection, event_id)
        if (
            event.delivery_status != OutboxDeliveryStatus.IN_FLIGHT
            or event.lease_owner != worker
            or event.lease_expires_at is None
            or timestamp >= event.lease_expires_at
        ):
            raise OutboxTransitionError("Outbox event lease is not owned and active")
        return event, version

    def _sample_exists(self, connection, device_id, sample_id):
        return connection.execute(
            "SELECT 1 FROM telemetry_sample_guards "
            "WHERE device_id = ? AND sample_id = ?",
            (
                _required_text(device_id, "device_id"),
                _required_text(sample_id, "sample_id"),
            ),
        ).fetchone() is not None

    @contextmanager
    def _write_transaction(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _read_connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _connect(self):
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.timeout_seconds,
            isolation_level=None,
        )
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}"
        )
        return connection


def _deserialize_record(document):
    return _deserialize(document, deserialize_telemetry_record, "TelemetryRecord")


def _deserialize_state(document):
    return _deserialize(document, deserialize_live_state, "LiveState")


def _deserialize_decision(document):
    return _deserialize(
        document, deserialize_status_decision_record, "StatusDecisionRecord"
    )


def _deserialize_outbox(document):
    return _deserialize(document, deserialize_alert_outbox_event, "AlertOutboxEvent")


def _deserialize(document, loader, model_name):
    try:
        return loader(json.loads(document))
    except (JSONDecodeError, RepositorySerializationError, TypeError) as error:
        raise SQLiteTelemetryStorageError(
            f"Stored {model_name} is corrupt"
        ) from error


def _is_dispatchable(event, timestamp):
    return (
        event.delivery_status == OutboxDeliveryStatus.PENDING
        and event.available_at <= timestamp
    ) or (
        event.delivery_status == OutboxDeliveryStatus.IN_FLIGHT
        and event.lease_expires_at is not None
        and event.lease_expires_at <= timestamp
    )


def _due_at(event):
    if event.delivery_status == OutboxDeliveryStatus.PENDING:
        return _iso(event.available_at)
    if event.delivery_status == OutboxDeliveryStatus.IN_FLIGHT:
        return _iso(event.lease_expires_at)
    return None


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _iso(value: datetime) -> str:
    return (
        _aware_timestamp(value, "timestamp")
        .astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _aware_timestamp(value, field):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    if value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _positive_integer(value, field):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value
