from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional

try:
    from .state_repository import IdentityRepository
    from .trip_identity import DeviceAssignment, TripIdentity, TripStatus
except ImportError:
    from state_repository import IdentityRepository
    from trip_identity import DeviceAssignment, TripIdentity, TripStatus


@dataclass(frozen=True)
class V2LifecycleTransition:
    trip_identity: TripIdentity
    device_assignment: DeviceAssignment


class V2ShipmentLifecycleError(ValueError):
    pass


class V2ShipmentLifecycleService:
    def __init__(self, identity_repository: IdentityRepository):
        self._identity_repository = identity_repository

    def activate_for_shipment(
        self,
        shipment: Mapping[str, Any],
    ) -> V2LifecycleTransition:
        return self._transition(
            shipment,
            TripStatus.PLANNED,
            TripStatus.ACTIVE,
            False,
            True,
        )

    def complete_for_shipment(
        self,
        shipment: Mapping[str, Any],
        completed_at: datetime,
    ) -> V2LifecycleTransition:
        return self._transition(
            shipment,
            TripStatus.ACTIVE,
            TripStatus.COMPLETED,
            True,
            False,
            completed_at=completed_at,
        )

    def rollback_activation(
        self,
        shipment: Mapping[str, Any],
    ) -> V2LifecycleTransition:
        return self._transition(
            shipment,
            TripStatus.ACTIVE,
            TripStatus.PLANNED,
            True,
            False,
        )

    def rollback_completion(
        self,
        shipment: Mapping[str, Any],
    ) -> V2LifecycleTransition:
        return self._transition(
            shipment,
            TripStatus.COMPLETED,
            TripStatus.ACTIVE,
            False,
            True,
        )

    def _transition(
        self,
        shipment: Mapping[str, Any],
        expected_status: TripStatus,
        next_status: TripStatus,
        expected_active: bool,
        next_active: bool,
        *,
        completed_at: Optional[datetime] = None,
    ) -> V2LifecycleTransition:
        trip_id = _required(shipment, "tripId")
        lot_trip_id = _required(shipment, "lotTripId")
        assignment_id = _required(shipment, "v2DeviceAssignmentId")
        trip = self._identity_repository.get_trip_by_id(trip_id)
        if trip is None or trip.lot_trip_id != lot_trip_id:
            raise V2ShipmentLifecycleError(
                "Shipment V2 trip identity is missing or inconsistent"
            )
        next_trip, next_assignment = (
            self._identity_repository.transition_trip_and_assignment(
                trip_id,
                assignment_id,
                expected_status,
                next_status,
                expected_active,
                next_active,
                completed_at,
            )
        )
        return V2LifecycleTransition(next_trip, next_assignment)


def _required(shipment, field):
    value = str(shipment.get(field) or "").strip()
    if not value:
        raise V2ShipmentLifecycleError(
            f"V2-linked shipment is missing required {field}"
        )
    return value
