from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Optional, Sequence, Tuple

try:
    from .product_rules import resolve_applicable_rules
    from .risk_rules import ProductRule
except ImportError:
    from product_rules import resolve_applicable_rules
    from risk_rules import ProductRule


class TripStatus(str, Enum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class TripIdentity:
    trip_id: str
    lot_trip_id: str
    lot_id: str
    device_id: str
    product_id: str
    presentation: str
    state: str
    product_rule_version: str
    origin: str
    destination: str
    start_time: datetime
    status: TripStatus
    completed_at: Optional[datetime] = None


@dataclass(frozen=True)
class DeviceAssignment:
    assignment_id: str
    device_id: str
    trip_id: str
    lot_trip_id: str
    assigned_at: datetime
    active: bool


class TripIdentityError(ValueError):
    pass


class UnknownDeviceError(TripIdentityError):
    pass


class NoActiveAssignmentError(TripIdentityError):
    pass


class MultipleActiveAssignmentsError(TripIdentityError):
    pass


class AssignmentTripNotFoundError(TripIdentityError):
    pass


class TripIdentityConflictError(TripIdentityError):
    pass


class TripNotActiveError(TripIdentityError):
    pass


class TripIdentityValidationError(TripIdentityError):
    pass


class DeviceAssignmentValidationError(TripIdentityError):
    pass


class DeviceAssignmentMismatchError(TripIdentityError):
    pass


class TripRuleVersionMismatchError(TripIdentityError):
    pass


def resolve_trip_for_device(
    device_id: str,
    trips: Sequence[TripIdentity],
    assignments: Sequence[DeviceAssignment],
) -> TripIdentity:
    """Resolve one known device to exactly one valid active trip."""
    normalized_device_id = _required_text(device_id)
    device_assignments = [
        assignment
        for assignment in assignments
        if _required_text(assignment.device_id) == normalized_device_id
    ]
    if not normalized_device_id or not device_assignments:
        raise UnknownDeviceError("Device is not known to the assignment catalog")

    active_assignments = [assignment for assignment in device_assignments if assignment.active is True]
    if not active_assignments:
        raise NoActiveAssignmentError("Device has no active trip assignment")
    if len(active_assignments) > 1:
        raise MultipleActiveAssignmentsError("Device has multiple active trip assignments")

    assignment = active_assignments[0]
    matching_trips = [
        trip
        for trip in trips
        if _required_text(trip.trip_id) == _required_text(assignment.trip_id)
        or _required_text(trip.lot_trip_id) == _required_text(assignment.lot_trip_id)
    ]
    if not matching_trips:
        raise AssignmentTripNotFoundError("Active device assignment references a missing trip")
    if len(matching_trips) > 1:
        raise TripIdentityConflictError("Trip identity is not unique")

    trip = matching_trips[0]
    validate_trip_identity(trip)
    validate_device_assignment(assignment, trip, normalized_device_id)
    if trip.status != TripStatus.ACTIVE:
        raise TripNotActiveError("Only an ACTIVE trip may receive live telemetry")
    return trip


def validate_trip_identity(trip: TripIdentity) -> TripIdentity:
    """Validate the immutable identity and ProductRules lookup context."""
    required_fields = {
        "trip_id": trip.trip_id,
        "lot_trip_id": trip.lot_trip_id,
        "lot_id": trip.lot_id,
        "device_id": trip.device_id,
        "product_id": trip.product_id,
        "presentation": trip.presentation,
        "state": trip.state,
        "product_rule_version": trip.product_rule_version,
        "origin": trip.origin,
        "destination": trip.destination,
    }
    missing = [name for name, value in required_fields.items() if not _required_text(value)]
    if missing:
        raise TripIdentityValidationError(
            "Trip identity is missing required fields: " + ", ".join(missing)
        )
    if not isinstance(trip.status, TripStatus):
        raise TripIdentityValidationError("Trip status is invalid")
    if not _is_timezone_aware(trip.start_time):
        raise TripIdentityValidationError("Trip start_time must be timezone-aware")
    if trip.status == TripStatus.COMPLETED:
        if not _is_timezone_aware(trip.completed_at):
            raise TripIdentityValidationError(
                "A COMPLETED trip must have a timezone-aware completed_at"
            )
        if trip.completed_at < trip.start_time:
            raise TripIdentityValidationError(
                "Trip completed_at cannot precede start_time"
            )
    elif trip.completed_at is not None:
        raise TripIdentityValidationError(
            "Only a COMPLETED trip may have completed_at"
        )
    return trip


def trip_identity_with_status(
    trip: TripIdentity,
    status: TripStatus,
    *,
    completed_at: Optional[datetime] = None,
) -> TripIdentity:
    """Create a validated lifecycle snapshot without consulting a clock."""
    if not isinstance(status, TripStatus):
        raise TripIdentityValidationError("Trip status is invalid")
    if status != TripStatus.COMPLETED and completed_at is not None:
        raise TripIdentityValidationError(
            "Only a COMPLETED trip transition may supply completed_at"
        )
    next_trip = replace(
        trip,
        status=status,
        completed_at=completed_at if status == TripStatus.COMPLETED else None,
    )
    return validate_trip_identity(next_trip)


def validate_device_assignment(
    assignment: DeviceAssignment,
    trip: TripIdentity,
    requested_device_id: str,
) -> DeviceAssignment:
    """Require an assignment and trip to agree on every identity key."""
    required_fields = {
        "assignment_id": assignment.assignment_id,
        "device_id": assignment.device_id,
        "trip_id": assignment.trip_id,
        "lot_trip_id": assignment.lot_trip_id,
    }
    missing = [name for name, value in required_fields.items() if not _required_text(value)]
    if missing:
        raise DeviceAssignmentValidationError(
            "Device assignment is missing required fields: " + ", ".join(missing)
        )
    if not _is_timezone_aware(assignment.assigned_at):
        raise DeviceAssignmentValidationError("Device assignment assigned_at must be timezone-aware")
    if not isinstance(assignment.active, bool):
        raise DeviceAssignmentValidationError("Device assignment active must be a boolean")

    expected = (
        _required_text(trip.device_id),
        _required_text(trip.trip_id),
        _required_text(trip.lot_trip_id),
    )
    actual = (
        _required_text(assignment.device_id),
        _required_text(assignment.trip_id),
        _required_text(assignment.lot_trip_id),
    )
    if actual != expected or _required_text(requested_device_id) != expected[0]:
        raise DeviceAssignmentMismatchError(
            "Requested device, assignment, and trip identity do not match"
        )
    return assignment


def validate_trip_rule_context(trip: TripIdentity) -> Tuple[ProductRule, ...]:
    """Resolve verified rules and require the trip's pinned rule version."""
    validate_trip_identity(trip)
    rules = resolve_applicable_rules(trip.product_id, trip.presentation, trip.state)
    if any(rule.version != trip.product_rule_version for rule in rules):
        raise TripRuleVersionMismatchError(
            "Trip ProductRule version does not match the resolved verified catalog"
        )
    return rules


def _required_text(value) -> str:
    return str(value or "").strip()


def _is_timezone_aware(value) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
