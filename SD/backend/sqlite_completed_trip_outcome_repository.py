import json
import sqlite3
from contextlib import contextmanager
from json import JSONDecodeError
from pathlib import Path
from typing import Optional

try:
    from .completed_trip_outcome import (
        CompletedTripOutcome,
        CompletedTripOutcomeConflictError,
        CompletedTripOutcomeError,
        CompletedTripOutcomeRepository,
        validate_completed_trip_outcome,
    )
    from .repository_serialization import (
        RepositorySerializationError,
        deserialize_completed_trip_outcome,
        serialize_completed_trip_outcome,
    )
except ImportError:
    from completed_trip_outcome import (
        CompletedTripOutcome,
        CompletedTripOutcomeConflictError,
        CompletedTripOutcomeError,
        CompletedTripOutcomeRepository,
        validate_completed_trip_outcome,
    )
    from repository_serialization import (
        RepositorySerializationError,
        deserialize_completed_trip_outcome,
        serialize_completed_trip_outcome,
    )


class SQLiteCompletedTripOutcomeStorageError(CompletedTripOutcomeError):
    pass


class SQLiteCompletedTripOutcomeRepository(CompletedTripOutcomeRepository):
    """File-backed reference adapter for immutable completed-trip outcomes."""

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

    def save_outcome(self, outcome: CompletedTripOutcome) -> CompletedTripOutcome:
        validate_completed_trip_outcome(outcome)
        document = _json(serialize_completed_trip_outcome(outcome))
        with self._write_transaction() as connection:
            row = connection.execute(
                "SELECT document FROM completed_trip_outcomes "
                "WHERE lot_trip_id = ?",
                (outcome.lot_trip_id,),
            ).fetchone()
            if row is not None:
                existing = _deserialize_document(row[0])
                if existing == outcome:
                    return existing
                raise CompletedTripOutcomeConflictError(
                    "A different outcome is already finalized for this lot_trip_id"
                )
            try:
                connection.execute(
                    "INSERT INTO completed_trip_outcomes (lot_trip_id, document) "
                    "VALUES (?, ?)",
                    (outcome.lot_trip_id, document),
                )
            except sqlite3.IntegrityError as error:
                raise CompletedTripOutcomeConflictError(
                    "An outcome is already finalized for this lot_trip_id"
                ) from error
            return outcome

    def get_outcome(
        self, lot_trip_id: str
    ) -> Optional[CompletedTripOutcome]:
        normalized_lot_trip_id = _required_text(lot_trip_id, "lot_trip_id")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT document FROM completed_trip_outcomes "
                "WHERE lot_trip_id = ?",
                (normalized_lot_trip_id,),
            ).fetchone()
            return None if row is None else _deserialize_document(row[0])

    def _initialize_schema(self) -> None:
        connection = self._connect()
        try:
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if schema_version not in (0, 1):
                raise SQLiteCompletedTripOutcomeStorageError(
                    "Unsupported SQLite completed-outcome schema version: "
                    f"{schema_version}"
                )
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS completed_trip_outcomes (
                    lot_trip_id TEXT PRIMARY KEY,
                    document TEXT NOT NULL
                );
                """
            )
            if schema_version == 0:
                connection.execute("PRAGMA user_version = 1")
        finally:
            connection.close()

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
        connection.execute(
            f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}"
        )
        return connection


def _deserialize_document(value: str) -> CompletedTripOutcome:
    try:
        return deserialize_completed_trip_outcome(json.loads(value))
    except (
        JSONDecodeError,
        RepositorySerializationError,
        CompletedTripOutcomeError,
        TypeError,
    ) as error:
        raise SQLiteCompletedTripOutcomeStorageError(
            "Stored completed-trip outcome is corrupt"
        ) from error


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _required_text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()
