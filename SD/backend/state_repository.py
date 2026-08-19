from dataclasses import dataclass, replace
from datetime import datetime
from threading import RLock
from typing import Dict, Optional, Protocol, Tuple, runtime_checkable

try:
    from .risk_rules import ApplicationStatus, PreviousState, StatusDecision
    from .telemetry import ValidatedTelemetrySample, sample_identity
    from .trip_identity import (
        DeviceAssignment,
        TripIdentity,
        TripStatus,
        validate_device_assignment,
        validate_trip_identity,
    )
except ImportError:
    from risk_rules import ApplicationStatus, PreviousState, StatusDecision
    from telemetry import ValidatedTelemetrySample, sample_identity
    from trip_identity import (
        DeviceAssignment,
        TripIdentity,
        TripStatus,
        validate_device_assignment,
        validate_trip_identity,
    )


@dataclass(frozen=True)
class TelemetryRecord:
    trip_id: str
    lot_trip_id: str
    sample_id: str
    device_id: str
    timestamp: datetime
    temperature: float
    battery_level: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    device_health: Optional[str] = None


@dataclass(frozen=True)
class LiveState:
    lot_trip_id: str
    trip_id: str
    device_id: str
    product_id: str
    product_rule_version: str
    status: ApplicationStatus
    reason_code: str
    active_rule_id: Optional[str]
    last_sample_id: str
    last_sample_timestamp: datetime
    latest_temperature: float
    last_updated: datetime
    excursion_started_at: Optional[datetime]
    excursion_episode_duration_minutes: float
    cumulative_excursion_duration_minutes: float
    excursion_utilization: Optional[float]
    revision: int


class StateRepositoryError(ValueError):
    pass


class DuplicateTelemetrySampleError(StateRepositoryError):
    pass


class OutOfOrderTelemetryError(StateRepositoryError):
    pass


class ConcurrentStateUpdateError(StateRepositoryError):
    pass


class StateIntegrityError(StateRepositoryError):
    pass


@runtime_checkable
class TelemetryStateRepository(Protocol):
    def get_live_state(self, lot_trip_id: str) -> Optional[LiveState]:
        ...

    def has_sample(self, device_id: str, sample_id: str) -> bool:
        ...

    def get_telemetry_history(self, lot_trip_id: str) -> Tuple[TelemetryRecord, ...]:
        ...

    def commit_sample_and_state(
        self,
        record: TelemetryRecord,
        new_state: LiveState,
        expected_revision: Optional[int],
    ) -> None:
        ...


@runtime_checkable
class IdentityRepository(Protocol):
    def register_trip_and_assignment(
        self,
        trip: TripIdentity,
        assignment: DeviceAssignment,
    ) -> None:
        ...

    def unregister_planned_trip_and_assignment(
        self,
        trip_id: str,
        assignment_id: str,
    ) -> None:
        ...

    def transition_trip_and_assignment(
        self,
        trip_id: str,
        assignment_id: str,
        expected_trip_status: TripStatus,
        next_trip_status: TripStatus,
        expected_assignment_active: bool,
        next_assignment_active: bool,
    ) -> Tuple[TripIdentity, DeviceAssignment]:
        ...

    def register_trip(self, trip: TripIdentity) -> None:
        ...

    def get_trip_by_id(self, trip_id: str) -> Optional[TripIdentity]:
        ...

    def get_trip_by_lot_trip_id(self, lot_trip_id: str) -> Optional[TripIdentity]:
        ...

    def register_device_assignment(self, assignment: DeviceAssignment) -> None:
        ...

    def get_device_assignments(
        self, device_id: str
    ) -> Tuple[DeviceAssignment, ...]:
        ...


def telemetry_record_from_sample(
    trip_id: str,
    lot_trip_id: str,
    sample: ValidatedTelemetrySample,
) -> TelemetryRecord:
    """Attach resolved lot-trip identity without changing sensor facts."""
    normalized_trip_id = _required_text(trip_id, "trip_id")
    normalized_lot_trip_id = _required_text(lot_trip_id, "lot_trip_id")
    device_id, sample_id = sample_identity(sample)
    return TelemetryRecord(
        trip_id=normalized_trip_id,
        lot_trip_id=normalized_lot_trip_id,
        sample_id=sample_id,
        device_id=device_id,
        timestamp=sample.timestamp,
        temperature=sample.temperature,
        battery_level=sample.battery_level,
        latitude=sample.latitude,
        longitude=sample.longitude,
        device_health=sample.device_health,
    )


def live_state_to_previous_state(live_state: Optional[LiveState]) -> PreviousState:
    """Project persisted state into the deterministic engine's input model."""
    if live_state is None:
        return PreviousState()
    return PreviousState(
        last_sample_timestamp=live_state.last_sample_timestamp,
        active_rule_id=live_state.active_rule_id,
        excursion_started_at=live_state.excursion_started_at,
        cumulative_excursion_duration_minutes=(
            live_state.cumulative_excursion_duration_minutes
        ),
    )


def live_state_from_decision(
    *,
    lot_trip_id: str,
    trip_id: str,
    product_id: str,
    product_rule_version: str,
    sample: ValidatedTelemetrySample,
    decision: StatusDecision,
    previous_live_state: Optional[LiveState] = None,
) -> LiveState:
    """Create the next persisted snapshot from one engine decision."""
    identity = {
        "lot_trip_id": _required_text(lot_trip_id, "lot_trip_id"),
        "trip_id": _required_text(trip_id, "trip_id"),
        "device_id": _required_text(sample.device_id, "device_id"),
        "product_id": _required_text(product_id, "product_id"),
        "product_rule_version": _required_text(
            product_rule_version, "product_rule_version"
        ),
    }
    if previous_live_state is not None:
        previous_identity = (
            previous_live_state.lot_trip_id,
            previous_live_state.trip_id,
            previous_live_state.device_id,
            previous_live_state.product_id,
            previous_live_state.product_rule_version,
        )
        if previous_identity != tuple(identity.values()):
            raise StateIntegrityError("LiveState identity cannot change between samples")

    state = LiveState(
        lot_trip_id=identity["lot_trip_id"],
        trip_id=identity["trip_id"],
        device_id=identity["device_id"],
        product_id=identity["product_id"],
        product_rule_version=identity["product_rule_version"],
        status=decision.status,
        reason_code=decision.reason_code,
        active_rule_id=decision.active_rule_id,
        last_sample_id=_required_text(sample.sample_id, "sample_id"),
        last_sample_timestamp=sample.timestamp,
        latest_temperature=sample.temperature,
        last_updated=sample.timestamp,
        excursion_started_at=decision.excursion_started_at,
        excursion_episode_duration_minutes=(
            decision.excursion_episode_duration_minutes
        ),
        cumulative_excursion_duration_minutes=(
            decision.cumulative_excursion_duration_minutes
        ),
        excursion_utilization=decision.excursion_utilization,
        revision=1 if previous_live_state is None else previous_live_state.revision + 1,
    )
    _validate_live_state(state)
    return state


class InMemoryTelemetryStateRepository(IdentityRepository, TelemetryStateRepository):
    """Reference adapter implementing the repository's atomicity contract."""

    def __init__(self):
        self._live_states: Dict[str, LiveState] = {}
        self._history: Dict[str, list] = {}
        self._sample_identities = set()
        self._trips_by_id: Dict[str, TripIdentity] = {}
        self._trips_by_lot_trip_id: Dict[str, TripIdentity] = {}
        self._assignments_by_id: Dict[str, DeviceAssignment] = {}
        self._assignments_by_device: Dict[str, list] = {}
        self._lock = RLock()

    def register_trip(self, trip: TripIdentity) -> None:
        validate_trip_identity(trip)
        with self._lock:
            existing_by_id = self._trips_by_id.get(trip.trip_id)
            existing_by_lot = self._trips_by_lot_trip_id.get(trip.lot_trip_id)
            if (
                existing_by_id is not None
                and existing_by_id != trip
                or existing_by_lot is not None
                and existing_by_lot != trip
            ):
                raise StateIntegrityError("Trip identity is immutable once registered")
            self._trips_by_id[trip.trip_id] = trip
            self._trips_by_lot_trip_id[trip.lot_trip_id] = trip

    def register_trip_and_assignment(
        self,
        trip: TripIdentity,
        assignment: DeviceAssignment,
    ) -> None:
        """Atomically register one immutable trip and its device assignment."""
        validate_trip_identity(trip)
        validate_device_assignment(assignment, trip, assignment.device_id)
        with self._lock:
            existing_by_id = self._trips_by_id.get(trip.trip_id)
            existing_by_lot = self._trips_by_lot_trip_id.get(trip.lot_trip_id)
            existing_assignment = self._assignments_by_id.get(
                assignment.assignment_id
            )
            if (
                existing_by_id is not None
                and existing_by_id != trip
                or existing_by_lot is not None
                and existing_by_lot != trip
            ):
                raise StateIntegrityError("Trip identity is already registered")
            if existing_assignment is not None and existing_assignment != assignment:
                raise StateIntegrityError(
                    "DeviceAssignment is already registered with different content"
                )
            device_assignments = self._assignments_by_device.get(
                assignment.device_id,
                (),
            )
            if any(
                (
                    existing.active
                    or (
                        self._trips_by_id.get(existing.trip_id) is not None
                        and self._trips_by_id[existing.trip_id].status
                        == TripStatus.PLANNED
                    )
                )
                and existing.assignment_id != assignment.assignment_id
                for existing in device_assignments
            ):
                raise StateIntegrityError(
                    "Device already has an active assignment or PLANNED reservation"
                )

            self._trips_by_id[trip.trip_id] = trip
            self._trips_by_lot_trip_id[trip.lot_trip_id] = trip
            if existing_assignment is None:
                self._assignments_by_id[assignment.assignment_id] = assignment
                self._assignments_by_device.setdefault(
                    assignment.device_id,
                    [],
                ).append(assignment)

    def unregister_planned_trip_and_assignment(
        self,
        trip_id: str,
        assignment_id: str,
    ) -> None:
        """Compensate an untouched PLANNED registration after legacy failure."""
        with self._lock:
            trip = self._trips_by_id.get(trip_id)
            assignment = self._assignments_by_id.get(assignment_id)
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
            if (
                trip.lot_trip_id in self._live_states
                or self._history.get(trip.lot_trip_id)
            ):
                raise StateIntegrityError(
                    "Registration with telemetry state cannot be removed"
                )

            del self._assignments_by_id[assignment.assignment_id]
            device_assignments = self._assignments_by_device.get(
                assignment.device_id,
                [],
            )
            self._assignments_by_device[assignment.device_id] = [
                existing
                for existing in device_assignments
                if existing.assignment_id != assignment.assignment_id
            ]
            if not self._assignments_by_device[assignment.device_id]:
                del self._assignments_by_device[assignment.device_id]
            del self._trips_by_id[trip.trip_id]
            del self._trips_by_lot_trip_id[trip.lot_trip_id]

    def transition_trip_and_assignment(
        self,
        trip_id: str,
        assignment_id: str,
        expected_trip_status: TripStatus,
        next_trip_status: TripStatus,
        expected_assignment_active: bool,
        next_assignment_active: bool,
    ) -> Tuple[TripIdentity, DeviceAssignment]:
        """Atomically replace one trip status and assignment active flag."""
        with self._lock:
            trip = self._trips_by_id.get(trip_id)
            assignment = self._assignments_by_id.get(assignment_id)
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
            if next_assignment_active and any(
                existing.active
                and existing.assignment_id != assignment.assignment_id
                for existing in self._assignments_by_device.get(
                    assignment.device_id,
                    (),
                )
            ):
                raise StateIntegrityError(
                    "Device already has another active assignment"
                )

            next_trip = replace(trip, status=next_trip_status)
            next_assignment = replace(
                assignment,
                active=next_assignment_active,
            )
            validate_trip_identity(next_trip)
            validate_device_assignment(
                next_assignment,
                next_trip,
                next_assignment.device_id,
            )
            self._trips_by_id[next_trip.trip_id] = next_trip
            self._trips_by_lot_trip_id[next_trip.lot_trip_id] = next_trip
            self._assignments_by_id[next_assignment.assignment_id] = next_assignment
            device_assignments = self._assignments_by_device[next_assignment.device_id]
            self._assignments_by_device[next_assignment.device_id] = [
                next_assignment
                if existing.assignment_id == next_assignment.assignment_id
                else existing
                for existing in device_assignments
            ]
            return next_trip, next_assignment

    def get_trip_by_id(self, trip_id: str) -> Optional[TripIdentity]:
        with self._lock:
            return self._trips_by_id.get(trip_id)

    def get_trip_by_lot_trip_id(self, lot_trip_id: str) -> Optional[TripIdentity]:
        with self._lock:
            return self._trips_by_lot_trip_id.get(lot_trip_id)

    def register_device_assignment(self, assignment: DeviceAssignment) -> None:
        with self._lock:
            trip = self._trips_by_id.get(assignment.trip_id)
            if trip is None:
                raise StateIntegrityError(
                    "DeviceAssignment must reference a registered TripIdentity"
                )
            validate_device_assignment(assignment, trip, assignment.device_id)
            existing = self._assignments_by_id.get(assignment.assignment_id)
            if existing is not None:
                if existing != assignment:
                    raise StateIntegrityError(
                        "DeviceAssignment is immutable once registered"
                    )
                return
            self._assignments_by_id[assignment.assignment_id] = assignment
            self._assignments_by_device.setdefault(assignment.device_id, []).append(
                assignment
            )

    def get_device_assignments(
        self, device_id: str
    ) -> Tuple[DeviceAssignment, ...]:
        with self._lock:
            return tuple(self._assignments_by_device.get(device_id, ()))

    def get_live_state(self, lot_trip_id: str) -> Optional[LiveState]:
        with self._lock:
            return self._live_states.get(lot_trip_id)

    def has_sample(self, device_id: str, sample_id: str) -> bool:
        with self._lock:
            return (device_id, sample_id) in self._sample_identities

    def get_telemetry_history(self, lot_trip_id: str) -> Tuple[TelemetryRecord, ...]:
        with self._lock:
            return tuple(self._history.get(lot_trip_id, ()))

    def commit_sample_and_state(
        self,
        record: TelemetryRecord,
        new_state: LiveState,
        expected_revision: Optional[int],
    ) -> None:
        _validate_record(record)
        _validate_live_state(new_state)

        with self._lock:
            current_state = self._live_states.get(record.lot_trip_id)
            _validate_commit(
                record=record,
                new_state=new_state,
                current_state=current_state,
                expected_revision=expected_revision,
                sample_identities=self._sample_identities,
            )

            identity = (record.device_id, record.sample_id)
            self._history.setdefault(record.lot_trip_id, []).append(record)
            self._live_states[record.lot_trip_id] = new_state
            self._sample_identities.add(identity)


def _validate_commit(
    *, record, new_state, current_state, expected_revision, sample_identities
):
    if (record.device_id, record.sample_id) in sample_identities:
        raise DuplicateTelemetrySampleError(
            "Telemetry identity has already been committed"
        )
    if (
        record.lot_trip_id != new_state.lot_trip_id
        or record.trip_id != new_state.trip_id
        or record.device_id != new_state.device_id
        or record.sample_id != new_state.last_sample_id
        or record.timestamp != new_state.last_sample_timestamp
        or record.temperature != new_state.latest_temperature
    ):
        raise StateIntegrityError("TelemetryRecord and LiveState identity do not match")

    if current_state is None:
        if expected_revision is not None:
            raise ConcurrentStateUpdateError("Initial state expected no prior revision")
        if new_state.revision != 1:
            raise StateIntegrityError("Initial LiveState revision must be 1")
        return

    if expected_revision != current_state.revision:
        raise ConcurrentStateUpdateError("LiveState revision changed before commit")
    if new_state.revision != current_state.revision + 1:
        raise StateIntegrityError("New LiveState revision must increment by one")
    if (
        new_state.trip_id != current_state.trip_id
        or new_state.device_id != current_state.device_id
        or new_state.product_id != current_state.product_id
        or new_state.product_rule_version != current_state.product_rule_version
    ):
        raise StateIntegrityError("LiveState identity cannot change between commits")
    if record.timestamp <= current_state.last_sample_timestamp:
        raise OutOfOrderTelemetryError(
            "Telemetry timestamp must be newer than the current LiveState"
        )


def validate_telemetry_state_commit(
    *,
    record: TelemetryRecord,
    new_state: LiveState,
    current_state: Optional[LiveState],
    expected_revision: Optional[int],
    sample_exists: bool = False,
) -> None:
    """Validate a repository commit using the shared domain invariants."""
    _validate_record(record)
    _validate_live_state(new_state)
    sample_identities = (
        {(record.device_id, record.sample_id)} if sample_exists else set()
    )
    _validate_commit(
        record=record,
        new_state=new_state,
        current_state=current_state,
        expected_revision=expected_revision,
        sample_identities=sample_identities,
    )


def _validate_record(record):
    _required_text(record.trip_id, "trip_id")
    _required_text(record.lot_trip_id, "lot_trip_id")
    _required_text(record.sample_id, "sample_id")
    _required_text(record.device_id, "device_id")
    _require_aware_timestamp(record.timestamp, "timestamp")


def _validate_live_state(state):
    for field in (
        "lot_trip_id",
        "trip_id",
        "device_id",
        "product_id",
        "product_rule_version",
        "reason_code",
        "last_sample_id",
    ):
        _required_text(getattr(state, field), field)
    _require_aware_timestamp(state.last_sample_timestamp, "last_sample_timestamp")
    _require_aware_timestamp(state.last_updated, "last_updated")
    if state.last_updated != state.last_sample_timestamp:
        raise StateIntegrityError(
            "LiveState last_updated must equal the accepted sample timestamp"
        )
    if state.excursion_started_at is not None:
        _require_aware_timestamp(state.excursion_started_at, "excursion_started_at")
    if not isinstance(state.status, ApplicationStatus):
        raise StateIntegrityError("LiveState status must be an ApplicationStatus")
    if not isinstance(state.revision, int) or isinstance(state.revision, bool) or state.revision < 1:
        raise StateIntegrityError("LiveState revision must be a positive integer")


def _required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise StateIntegrityError(f"{field} must be a non-empty string")
    return value.strip()


def _require_aware_timestamp(value, field):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StateIntegrityError(f"{field} must be timezone-aware")
