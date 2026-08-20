import json
import sqlite3
from dataclasses import replace
from json import JSONDecodeError

try:
    from .completed_trip_outcome import completed_trip_outcome_from_state
    from .repository_serialization import (
        RepositorySerializationError,
        deserialize_completed_trip_outcome,
        deserialize_live_state,
        serialize_completed_trip_outcome,
        serialize_device_assignment,
        serialize_live_state,
        serialize_trip_identity,
    )
    from .sqlite_completed_trip_outcome_repository import (
        SQLiteCompletedTripOutcomeRepository,
    )
    from .sqlite_identity_repository import SQLiteIdentityAccessRepository
    from .sqlite_telemetry_repository import SQLiteTelemetryStateRepository
    from .trip_completion import (
        TripCompletionConflictError,
        TripCompletionIntegrityError,
        TripCompletionRepository,
        TripCompletionResult,
        completed_trip_replay_result,
    )
    from .trip_identity import TripStatus, trip_identity_with_status
except ImportError:
    from completed_trip_outcome import completed_trip_outcome_from_state
    from repository_serialization import (
        RepositorySerializationError,
        deserialize_completed_trip_outcome,
        deserialize_live_state,
        serialize_completed_trip_outcome,
        serialize_device_assignment,
        serialize_live_state,
        serialize_trip_identity,
    )
    from sqlite_completed_trip_outcome_repository import (
        SQLiteCompletedTripOutcomeRepository,
    )
    from sqlite_identity_repository import SQLiteIdentityAccessRepository
    from sqlite_telemetry_repository import SQLiteTelemetryStateRepository
    from trip_completion import (
        TripCompletionConflictError,
        TripCompletionIntegrityError,
        TripCompletionRepository,
        TripCompletionResult,
        completed_trip_replay_result,
    )
    from trip_identity import TripStatus, trip_identity_with_status


class SQLiteTripCompletionRepository(
    SQLiteIdentityAccessRepository,
    SQLiteTelemetryStateRepository,
    TripCompletionRepository,
):
    """One-file SQLite identity, telemetry, and completion unit of work."""

    def __init__(self, database_path, *, timeout_seconds: float = 5.0):
        SQLiteIdentityAccessRepository.__init__(
            self, database_path, timeout_seconds=timeout_seconds
        )
        SQLiteTelemetryStateRepository._initialize_schema(self)
        SQLiteCompletedTripOutcomeRepository(
            database_path, timeout_seconds=timeout_seconds
        )

    def complete_trip(
        self,
        trip_id: str,
        assignment_id: str,
        *,
        completed_at,
    ) -> TripCompletionResult:
        with self._write_transaction() as connection:
            trip = self._read_trip_by_id(connection, trip_id)
            assignment = self._read_assignment_by_id(connection, assignment_id)
            if trip is None or assignment is None:
                raise TripCompletionIntegrityError(
                    "Trip completion identity does not exist"
                )
            if (
                assignment.trip_id != trip.trip_id
                or assignment.lot_trip_id != trip.lot_trip_id
                or assignment.device_id != trip.device_id
            ):
                raise TripCompletionIntegrityError(
                    "Trip completion identity is inconsistent"
                )
            state = self._read_completion_live_state(connection, trip.lot_trip_id)
            existing = self._read_outcome(connection, trip.lot_trip_id)
            if trip.status == TripStatus.COMPLETED:
                return completed_trip_replay_result(
                    trip, assignment, state, existing, completed_at
                )
            if trip.status != TripStatus.ACTIVE or not assignment.active:
                raise TripCompletionConflictError(
                    "Only an ACTIVE trip with an active assignment can complete"
                )
            if existing is not None:
                raise TripCompletionIntegrityError(
                    "An outcome exists before the trip is completed"
                )

            next_trip = trip_identity_with_status(
                trip, TripStatus.COMPLETED, completed_at=completed_at
            )
            next_assignment = replace(assignment, active=False)
            outcome = completed_trip_outcome_from_state(
                next_trip, next_trip.completed_at, state
            )
            result = TripCompletionResult(
                trip=next_trip,
                assignment=next_assignment,
                final_live_state=state,
                outcome=outcome,
            )
            self._before_completion_write(connection, result)
            self._update_device_reservation(connection, next_trip, next_assignment)
            connection.execute(
                "UPDATE trips SET status = ?, document = ? WHERE trip_id = ?",
                (
                    next_trip.status.value,
                    _json(serialize_trip_identity(next_trip)),
                    next_trip.trip_id,
                ),
            )
            connection.execute(
                "UPDATE assignments SET active = 0, document = ? "
                "WHERE assignment_id = ?",
                (
                    _json(serialize_device_assignment(next_assignment)),
                    next_assignment.assignment_id,
                ),
            )
            try:
                connection.execute(
                    "INSERT INTO completed_trip_outcomes (lot_trip_id, document) "
                    "VALUES (?, ?)",
                    (
                        outcome.lot_trip_id,
                        _json(serialize_completed_trip_outcome(outcome)),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise TripCompletionConflictError(
                    "A completed outcome already exists for this lot_trip_id"
                ) from error
            return result

    def get_completed_trip_outcome(self, lot_trip_id):
        with self._read_connection() as connection:
            return self._read_outcome(connection, lot_trip_id)

    def _before_completion_write(self, connection, result):
        """Test seam for proving transaction rollback before any completion write."""

    def _read_completion_live_state(self, connection, lot_trip_id):
        row = connection.execute(
            "SELECT revision, last_sample_timestamp, document FROM live_states "
            "WHERE lot_trip_id = ?",
            (_required_text(lot_trip_id, "lot_trip_id"),),
        ).fetchone()
        if row is None:
            return None
        try:
            state = deserialize_live_state(json.loads(row[2]))
        except (JSONDecodeError, RepositorySerializationError, TypeError) as error:
            raise TripCompletionIntegrityError("Stored LiveState is corrupt") from error
        if (
            state.lot_trip_id != lot_trip_id
            or state.revision != row[0]
            or serialize_live_state(state)["last_sample_timestamp"] != row[1]
        ):
            raise TripCompletionIntegrityError(
                "Stored LiveState index attributes disagree with document"
            )
        return state

    def _read_outcome(self, connection, lot_trip_id):
        row = connection.execute(
            "SELECT document FROM completed_trip_outcomes WHERE lot_trip_id = ?",
            (_required_text(lot_trip_id, "lot_trip_id"),),
        ).fetchone()
        if row is None:
            return None
        try:
            outcome = deserialize_completed_trip_outcome(json.loads(row[0]))
        except (JSONDecodeError, RepositorySerializationError, TypeError) as error:
            raise TripCompletionIntegrityError(
                "Stored completed-trip outcome is corrupt"
            ) from error
        if outcome.lot_trip_id != lot_trip_id:
            raise TripCompletionIntegrityError(
                "Stored completed outcome identity is inconsistent"
            )
        return outcome


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()
