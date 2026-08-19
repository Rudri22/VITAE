from dataclasses import dataclass
from threading import RLock
from typing import Dict, Optional, Protocol, Tuple, runtime_checkable

try:
    from .state_repository import IdentityRepository
    from .trip_identity import DeviceAssignment, TripIdentity
except ImportError:
    from state_repository import IdentityRepository
    from trip_identity import DeviceAssignment, TripIdentity


@dataclass(frozen=True)
class ShipmentAccess:
    shipment_id: str
    lot_trip_id: str
    organization_id: str
    driver_id: str


class ShipmentAccessError(ValueError):
    pass


class ShipmentAccessConflictError(ShipmentAccessError):
    pass


class ShipmentAccessNotFoundError(ShipmentAccessError):
    pass


@runtime_checkable
class ShipmentAccessRepository(Protocol):
    def register_shipment_access(self, access: ShipmentAccess) -> ShipmentAccess:
        ...

    def get_shipment_access(self, lot_trip_id: str) -> Optional[ShipmentAccess]:
        ...

    def list_shipment_accesses(
        self,
        *,
        organization_id: Optional[str] = None,
        driver_id: Optional[str] = None,
    ) -> Tuple[ShipmentAccess, ...]:
        ...

    def unregister_shipment_access(
        self,
        lot_trip_id: str,
        shipment_id: str,
    ) -> None:
        ...

    def transition_shipment_access_driver(
        self,
        lot_trip_id: str,
        expected_driver_id: str,
        next_driver_id: str,
    ) -> ShipmentAccess:
        ...


@runtime_checkable
class IdentityAccessRepository(IdentityRepository, ShipmentAccessRepository, Protocol):
    def register_trip_assignment_and_access(
        self,
        trip: TripIdentity,
        assignment: DeviceAssignment,
        access: ShipmentAccess,
    ) -> None:
        ...

    def unregister_planned_trip_assignment_and_access(
        self,
        trip_id: str,
        assignment_id: str,
        lot_trip_id: str,
        shipment_id: str,
    ) -> None:
        ...


class InMemoryShipmentAccessRepository(ShipmentAccessRepository):
    def __init__(self):
        self._by_lot_trip_id: Dict[str, ShipmentAccess] = {}
        self._lot_trip_by_shipment_id: Dict[str, str] = {}
        self._lock = RLock()

    def register_shipment_access(self, access: ShipmentAccess) -> ShipmentAccess:
        _validate_shipment_access(access)
        with self._lock:
            existing = self._by_lot_trip_id.get(access.lot_trip_id)
            shipment_lot_trip = self._lot_trip_by_shipment_id.get(
                access.shipment_id
            )
            if existing is not None:
                if existing != access:
                    raise ShipmentAccessConflictError(
                        "lot_trip_id is already linked to different access"
                    )
                return existing
            if shipment_lot_trip is not None:
                raise ShipmentAccessConflictError(
                    "shipment_id is already linked to a different lot trip"
                )
            self._by_lot_trip_id[access.lot_trip_id] = access
            self._lot_trip_by_shipment_id[access.shipment_id] = access.lot_trip_id
            return access

    def get_shipment_access(self, lot_trip_id: str) -> Optional[ShipmentAccess]:
        normalized = _required_text(lot_trip_id, "lot_trip_id")
        with self._lock:
            return self._by_lot_trip_id.get(normalized)

    def list_shipment_accesses(
        self,
        *,
        organization_id: Optional[str] = None,
        driver_id: Optional[str] = None,
    ) -> Tuple[ShipmentAccess, ...]:
        organization = _optional_filter(organization_id, "organization_id")
        driver = _optional_filter(driver_id, "driver_id")
        with self._lock:
            values = tuple(self._by_lot_trip_id.values())
        if organization is not None:
            values = tuple(
                value for value in values if value.organization_id == organization
            )
        if driver is not None:
            values = tuple(value for value in values if value.driver_id == driver)
        return tuple(sorted(values, key=lambda value: value.lot_trip_id))

    def unregister_shipment_access(
        self,
        lot_trip_id: str,
        shipment_id: str,
    ) -> None:
        lot_trip = _required_text(lot_trip_id, "lot_trip_id")
        shipment = _required_text(shipment_id, "shipment_id")
        with self._lock:
            existing = self._by_lot_trip_id.get(lot_trip)
            if existing is None or existing.shipment_id != shipment:
                raise ShipmentAccessNotFoundError(
                    "Shipment access does not exist or identity does not match"
                )
            del self._by_lot_trip_id[lot_trip]
            del self._lot_trip_by_shipment_id[shipment]

    def transition_shipment_access_driver(
        self,
        lot_trip_id: str,
        expected_driver_id: str,
        next_driver_id: str,
    ) -> ShipmentAccess:
        lot_trip = _required_text(lot_trip_id, "lot_trip_id")
        expected = _required_text(expected_driver_id, "expected_driver_id")
        next_driver = _required_text(next_driver_id, "next_driver_id")
        with self._lock:
            existing = self._by_lot_trip_id.get(lot_trip)
            if existing is None:
                raise ShipmentAccessNotFoundError("Shipment access does not exist")
            if existing.driver_id != expected:
                raise ShipmentAccessConflictError(
                    "Shipment access driver changed before transition"
                )
            updated = ShipmentAccess(
                shipment_id=existing.shipment_id,
                lot_trip_id=existing.lot_trip_id,
                organization_id=existing.organization_id,
                driver_id=next_driver,
            )
            self._by_lot_trip_id[lot_trip] = updated
            return updated


def validate_shipment_access(access: ShipmentAccess) -> ShipmentAccess:
    return _validate_shipment_access(access)


def _validate_shipment_access(access: ShipmentAccess) -> ShipmentAccess:
    if not isinstance(access, ShipmentAccess):
        raise ShipmentAccessError("access must be a ShipmentAccess")
    for field in (
        "shipment_id",
        "lot_trip_id",
        "organization_id",
        "driver_id",
    ):
        _required_text(getattr(access, field), field)
    return access


def _required_text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShipmentAccessError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_filter(value, field: str) -> Optional[str]:
    return None if value is None else _required_text(value, field)
