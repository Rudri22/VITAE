import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Optional, Tuple

try:
    from .repository_serialization import (
        deserialize_device_assignment,
        deserialize_shipment_access,
        deserialize_trip_identity,
        serialize_device_assignment,
        serialize_shipment_access,
        serialize_trip_identity,
    )
    from .shipment_access import (
        ShipmentAccess,
        ShipmentAccessConflictError,
        IdentityAccessRepository,
        ShipmentAccessNotFoundError,
        validate_shipment_access,
    )
    from .state_repository import StateIntegrityError
    from .trip_identity import (
        DeviceAssignment,
        TripIdentity,
        TripStatus,
        validate_device_assignment,
        validate_trip_identity,
    )
except ImportError:
    from repository_serialization import (
        deserialize_device_assignment,
        deserialize_shipment_access,
        deserialize_trip_identity,
        serialize_device_assignment,
        serialize_shipment_access,
        serialize_trip_identity,
    )
    from shipment_access import (
        ShipmentAccess,
        ShipmentAccessConflictError,
        IdentityAccessRepository,
        ShipmentAccessNotFoundError,
        validate_shipment_access,
    )
    from state_repository import StateIntegrityError
    from trip_identity import (
        DeviceAssignment,
        TripIdentity,
        TripStatus,
        validate_device_assignment,
        validate_trip_identity,
    )


class SQLiteIdentityAccessRepository(IdentityAccessRepository):
    """SQLite reference adapter for durable identity and shipment access only."""

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

    def register_trip(self, trip: TripIdentity) -> None:
        validate_trip_identity(trip)
        with self._write_transaction() as connection:
            self._insert_trip(connection, trip)

    def register_trip_and_assignment(
        self,
        trip: TripIdentity,
        assignment: DeviceAssignment,
    ) -> None:
        validate_trip_identity(trip)
        validate_device_assignment(assignment, trip, assignment.device_id)
        with self._write_transaction() as connection:
            self._insert_trip(connection, trip)
            self._insert_assignment(connection, trip, assignment)

    def register_trip_assignment_and_access(
        self,
        trip: TripIdentity,
        assignment: DeviceAssignment,
        access: ShipmentAccess,
    ) -> None:
        """Atomically register identity, reservation, and authorization scope."""
        validate_trip_identity(trip)
        validate_device_assignment(assignment, trip, assignment.device_id)
        validate_shipment_access(access)
        if access.lot_trip_id != trip.lot_trip_id:
            raise StateIntegrityError(
                "ShipmentAccess and TripIdentity lot_trip_id must match"
            )
        with self._write_transaction() as connection:
            self._insert_trip(connection, trip)
            self._insert_assignment(connection, trip, assignment)
            self._insert_access(connection, access)

    def unregister_planned_trip_and_assignment(
        self,
        trip_id: str,
        assignment_id: str,
    ) -> None:
        with self._write_transaction() as connection:
            self._remove_planned_identity(connection, trip_id, assignment_id)

    def unregister_planned_trip_assignment_and_access(
        self,
        trip_id: str,
        assignment_id: str,
        lot_trip_id: str,
        shipment_id: str,
    ) -> None:
        with self._write_transaction() as connection:
            self._remove_access(connection, lot_trip_id, shipment_id)
            self._remove_planned_identity(connection, trip_id, assignment_id)

    def transition_trip_and_assignment(
        self,
        trip_id: str,
        assignment_id: str,
        expected_trip_status: TripStatus,
        next_trip_status: TripStatus,
        expected_assignment_active: bool,
        next_assignment_active: bool,
    ) -> Tuple[TripIdentity, DeviceAssignment]:
        with self._write_transaction() as connection:
            trip = self._read_trip_by_id(connection, trip_id)
            assignment = self._read_assignment_by_id(connection, assignment_id)
            if trip is None or assignment is None:
                raise StateIntegrityError("Trip lifecycle identity does not exist")
            validate_device_assignment(assignment, trip, assignment.device_id)
            if (
                trip.status != expected_trip_status
                or assignment.active is not expected_assignment_active
            ):
                raise StateIntegrityError(
                    "Trip lifecycle does not match the expected prior state"
                )
            if next_assignment_active != (next_trip_status == TripStatus.ACTIVE):
                raise StateIntegrityError(
                    "Only an ACTIVE trip may have an active device assignment"
                )

            next_trip = replace(trip, status=next_trip_status)
            next_assignment = replace(assignment, active=next_assignment_active)
            validate_trip_identity(next_trip)
            validate_device_assignment(
                next_assignment, next_trip, next_assignment.device_id
            )
            self._update_device_reservation(
                connection,
                next_trip,
                next_assignment,
            )
            connection.execute(
                "UPDATE trips SET status = ?, document = ? WHERE trip_id = ?",
                (
                    next_trip.status.value,
                    _json(serialize_trip_identity(next_trip)),
                    next_trip.trip_id,
                ),
            )
            connection.execute(
                "UPDATE assignments SET active = ?, document = ? WHERE assignment_id = ?",
                (
                    int(next_assignment.active),
                    _json(serialize_device_assignment(next_assignment)),
                    next_assignment.assignment_id,
                ),
            )
            return next_trip, next_assignment

    def get_trip_by_id(self, trip_id: str) -> Optional[TripIdentity]:
        with self._read_connection() as connection:
            return self._read_trip_by_id(connection, trip_id)

    def get_trip_by_lot_trip_id(
        self, lot_trip_id: str
    ) -> Optional[TripIdentity]:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT document FROM trips WHERE lot_trip_id = ?",
                (_required_text(lot_trip_id, "lot_trip_id"),),
            ).fetchone()
            return None if row is None else deserialize_trip_identity(_load(row[0]))

    def register_device_assignment(self, assignment: DeviceAssignment) -> None:
        with self._write_transaction() as connection:
            trip = self._read_trip_by_id(connection, assignment.trip_id)
            if trip is None:
                raise StateIntegrityError(
                    "DeviceAssignment must reference a registered TripIdentity"
                )
            validate_device_assignment(assignment, trip, assignment.device_id)
            self._insert_assignment(connection, trip, assignment)

    def get_device_assignments(
        self, device_id: str
    ) -> Tuple[DeviceAssignment, ...]:
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT document FROM assignments WHERE device_id = ? "
                "ORDER BY assigned_at, assignment_id",
                (_required_text(device_id, "device_id"),),
            ).fetchall()
            return tuple(
                deserialize_device_assignment(_load(row[0])) for row in rows
            )

    def register_shipment_access(self, access: ShipmentAccess) -> ShipmentAccess:
        validate_shipment_access(access)
        with self._write_transaction() as connection:
            return self._insert_access(connection, access)

    def get_shipment_access(self, lot_trip_id: str) -> Optional[ShipmentAccess]:
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT document FROM shipment_access WHERE lot_trip_id = ?",
                (_required_text(lot_trip_id, "lot_trip_id"),),
            ).fetchone()
            return None if row is None else deserialize_shipment_access(_load(row[0]))

    def list_shipment_accesses(
        self,
        *,
        organization_id: Optional[str] = None,
        driver_id: Optional[str] = None,
    ) -> Tuple[ShipmentAccess, ...]:
        conditions = []
        parameters = []
        if organization_id is not None:
            conditions.append("organization_id = ?")
            parameters.append(_required_text(organization_id, "organization_id"))
        if driver_id is not None:
            conditions.append("driver_id = ?")
            parameters.append(_required_text(driver_id, "driver_id"))
        query = "SELECT document FROM shipment_access"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY lot_trip_id"
        with self._read_connection() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
            return tuple(deserialize_shipment_access(_load(row[0])) for row in rows)

    def unregister_shipment_access(
        self,
        lot_trip_id: str,
        shipment_id: str,
    ) -> None:
        with self._write_transaction() as connection:
            self._remove_access(connection, lot_trip_id, shipment_id)

    def transition_shipment_access_driver(
        self,
        lot_trip_id: str,
        expected_driver_id: str,
        next_driver_id: str,
    ) -> ShipmentAccess:
        lot_trip = _required_text(lot_trip_id, "lot_trip_id")
        expected = _required_text(expected_driver_id, "expected_driver_id")
        next_driver = _required_text(next_driver_id, "next_driver_id")
        with self._write_transaction() as connection:
            row = connection.execute(
                "SELECT document FROM shipment_access WHERE lot_trip_id = ?",
                (lot_trip,),
            ).fetchone()
            if row is None:
                raise ShipmentAccessNotFoundError("Shipment access does not exist")
            current = deserialize_shipment_access(_load(row[0]))
            if current.driver_id != expected:
                raise ShipmentAccessConflictError(
                    "Shipment access driver changed before transition"
                )
            updated = replace(current, driver_id=next_driver)
            connection.execute(
                "UPDATE shipment_access SET driver_id = ?, document = ? "
                "WHERE lot_trip_id = ?",
                (
                    updated.driver_id,
                    _json(serialize_shipment_access(updated)),
                    updated.lot_trip_id,
                ),
            )
            return updated

    def _initialize_schema(self) -> None:
        connection = self._connect()
        try:
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if schema_version not in (0, 1):
                raise StateIntegrityError(
                    f"Unsupported SQLite identity schema version: {schema_version}"
                )
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS trips (
                    trip_id TEXT PRIMARY KEY,
                    lot_trip_id TEXT NOT NULL UNIQUE,
                    device_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    document TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS assignments (
                    assignment_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    trip_id TEXT NOT NULL,
                    lot_trip_id TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    active INTEGER NOT NULL CHECK (active IN (0, 1)),
                    document TEXT NOT NULL,
                    FOREIGN KEY (trip_id) REFERENCES trips(trip_id)
                );

                CREATE INDEX IF NOT EXISTS assignments_by_device
                    ON assignments(device_id, assigned_at, assignment_id);

                CREATE TABLE IF NOT EXISTS device_reservations (
                    device_id TEXT PRIMARY KEY,
                    assignment_id TEXT NOT NULL UNIQUE,
                    trip_id TEXT NOT NULL,
                    lot_trip_id TEXT NOT NULL,
                    reservation_state TEXT NOT NULL,
                    FOREIGN KEY (assignment_id) REFERENCES assignments(assignment_id),
                    FOREIGN KEY (trip_id) REFERENCES trips(trip_id)
                );

                CREATE TABLE IF NOT EXISTS shipment_access (
                    lot_trip_id TEXT PRIMARY KEY,
                    shipment_id TEXT NOT NULL UNIQUE,
                    organization_id TEXT NOT NULL,
                    driver_id TEXT NOT NULL,
                    document TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS shipment_access_by_organization
                    ON shipment_access(organization_id, lot_trip_id);
                CREATE INDEX IF NOT EXISTS shipment_access_by_driver
                    ON shipment_access(driver_id, lot_trip_id);
                """
            )
            if schema_version == 0:
                connection.execute("PRAGMA user_version = 1")
        finally:
            connection.close()

    def _insert_trip(self, connection, trip: TripIdentity) -> None:
        rows = connection.execute(
            "SELECT document FROM trips WHERE trip_id = ? OR lot_trip_id = ?",
            (trip.trip_id, trip.lot_trip_id),
        ).fetchall()
        if rows:
            existing = tuple(deserialize_trip_identity(_load(row[0])) for row in rows)
            if len(existing) == 1 and existing[0] == trip:
                return
            raise StateIntegrityError("Trip identity is already registered")
        try:
            connection.execute(
                "INSERT INTO trips (trip_id, lot_trip_id, device_id, status, document) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    trip.trip_id,
                    trip.lot_trip_id,
                    trip.device_id,
                    trip.status.value,
                    _json(serialize_trip_identity(trip)),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise StateIntegrityError("Trip identity is already registered") from error

    def _insert_assignment(
        self,
        connection,
        trip: TripIdentity,
        assignment: DeviceAssignment,
    ) -> None:
        row = connection.execute(
            "SELECT document FROM assignments WHERE assignment_id = ?",
            (assignment.assignment_id,),
        ).fetchone()
        if row is not None:
            existing = deserialize_device_assignment(_load(row[0]))
            if existing != assignment:
                raise StateIntegrityError(
                    "DeviceAssignment is already registered with different content"
                )
            return
        try:
            connection.execute(
                "INSERT INTO assignments "
                "(assignment_id, device_id, trip_id, lot_trip_id, assigned_at, active, document) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    assignment.assignment_id,
                    assignment.device_id,
                    assignment.trip_id,
                    assignment.lot_trip_id,
                    assignment.assigned_at.isoformat(),
                    int(assignment.active),
                    _json(serialize_device_assignment(assignment)),
                ),
            )
            if assignment.active or trip.status == TripStatus.PLANNED:
                connection.execute(
                    "INSERT INTO device_reservations "
                    "(device_id, assignment_id, trip_id, lot_trip_id, reservation_state) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        assignment.device_id,
                        assignment.assignment_id,
                        assignment.trip_id,
                        assignment.lot_trip_id,
                        trip.status.value,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise StateIntegrityError(
                "Device already has an active assignment or PLANNED reservation"
            ) from error

    def _insert_access(self, connection, access: ShipmentAccess) -> ShipmentAccess:
        rows = connection.execute(
            "SELECT document FROM shipment_access "
            "WHERE lot_trip_id = ? OR shipment_id = ?",
            (access.lot_trip_id, access.shipment_id),
        ).fetchall()
        if rows:
            existing = tuple(deserialize_shipment_access(_load(row[0])) for row in rows)
            if len(existing) == 1 and existing[0] == access:
                return existing[0]
            raise ShipmentAccessConflictError(
                "Shipment access identity is already registered"
            )
        try:
            connection.execute(
                "INSERT INTO shipment_access "
                "(lot_trip_id, shipment_id, organization_id, driver_id, document) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    access.lot_trip_id,
                    access.shipment_id,
                    access.organization_id,
                    access.driver_id,
                    _json(serialize_shipment_access(access)),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise ShipmentAccessConflictError(
                "Shipment access identity is already registered"
            ) from error
        return access

    def _remove_planned_identity(
        self,
        connection,
        trip_id: str,
        assignment_id: str,
    ) -> None:
        trip = self._read_trip_by_id(connection, trip_id)
        assignment = self._read_assignment_by_id(connection, assignment_id)
        if trip is None or assignment is None:
            raise StateIntegrityError(
                "Planned registration compensation target does not exist"
            )
        if (
            trip.status != TripStatus.PLANNED
            or assignment.active
            or assignment.trip_id != trip.trip_id
            or assignment.lot_trip_id != trip.lot_trip_id
        ):
            raise StateIntegrityError(
                "Only an inactive untouched PLANNED registration can be removed"
            )
        connection.execute(
            "DELETE FROM device_reservations WHERE assignment_id = ?",
            (assignment.assignment_id,),
        )
        connection.execute(
            "DELETE FROM assignments WHERE assignment_id = ?",
            (assignment.assignment_id,),
        )
        connection.execute("DELETE FROM trips WHERE trip_id = ?", (trip.trip_id,))

    def _remove_access(self, connection, lot_trip_id: str, shipment_id: str) -> None:
        lot_trip = _required_text(lot_trip_id, "lot_trip_id")
        shipment = _required_text(shipment_id, "shipment_id")
        cursor = connection.execute(
            "DELETE FROM shipment_access WHERE lot_trip_id = ? AND shipment_id = ?",
            (lot_trip, shipment),
        )
        if cursor.rowcount != 1:
            raise ShipmentAccessNotFoundError(
                "Shipment access does not exist or identity does not match"
            )

    def _update_device_reservation(
        self,
        connection,
        trip: TripIdentity,
        assignment: DeviceAssignment,
    ) -> None:
        should_reserve = assignment.active or trip.status == TripStatus.PLANNED
        current = connection.execute(
            "SELECT assignment_id FROM device_reservations WHERE device_id = ?",
            (assignment.device_id,),
        ).fetchone()
        if should_reserve:
            if current is not None and current[0] != assignment.assignment_id:
                raise StateIntegrityError(
                    "Device already has another active assignment or reservation"
                )
            if current is None:
                try:
                    connection.execute(
                        "INSERT INTO device_reservations "
                        "(device_id, assignment_id, trip_id, lot_trip_id, reservation_state) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            assignment.device_id,
                            assignment.assignment_id,
                            assignment.trip_id,
                            assignment.lot_trip_id,
                            trip.status.value,
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise StateIntegrityError(
                        "Device already has another active assignment or reservation"
                    ) from error
            else:
                connection.execute(
                    "UPDATE device_reservations SET reservation_state = ? "
                    "WHERE device_id = ? AND assignment_id = ?",
                    (trip.status.value, assignment.device_id, assignment.assignment_id),
                )
        elif current is not None and current[0] == assignment.assignment_id:
            connection.execute(
                "DELETE FROM device_reservations WHERE device_id = ?",
                (assignment.device_id,),
            )

    def _read_trip_by_id(self, connection, trip_id: str) -> Optional[TripIdentity]:
        row = connection.execute(
            "SELECT document FROM trips WHERE trip_id = ?",
            (_required_text(trip_id, "trip_id"),),
        ).fetchone()
        return None if row is None else deserialize_trip_identity(_load(row[0]))

    def _read_assignment_by_id(
        self, connection, assignment_id: str
    ) -> Optional[DeviceAssignment]:
        row = connection.execute(
            "SELECT document FROM assignments WHERE assignment_id = ?",
            (_required_text(assignment_id, "assignment_id"),),
        ).fetchone()
        return None if row is None else deserialize_device_assignment(_load(row[0]))

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


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _load(value: str):
    return json.loads(value)


def _required_text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()
