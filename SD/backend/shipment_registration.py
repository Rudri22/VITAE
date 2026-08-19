from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

try:
    from .product_rules import resolve_applicable_rules
    from .shipment_access import (
        IdentityAccessRepository,
        ShipmentAccess,
    )
    from .trip_identity import (
        DeviceAssignment,
        TripIdentity,
        TripStatus,
        validate_trip_rule_context,
    )
except ImportError:
    from product_rules import resolve_applicable_rules
    from shipment_access import IdentityAccessRepository, ShipmentAccess
    from trip_identity import (
        DeviceAssignment,
        TripIdentity,
        TripStatus,
        validate_trip_rule_context,
    )


@dataclass(frozen=True)
class V2ShipmentRegistration:
    trip_identity: TripIdentity
    device_assignment: DeviceAssignment
    shipment_access: ShipmentAccess


class V2ShipmentRegistrationError(ValueError):
    pass


class V2ShipmentRegistrationService:
    def __init__(self, identity_repository: IdentityAccessRepository):
        self._identity_repository = identity_repository

    def register_for_shipment(
        self,
        request: Mapping[str, Any],
        shipment: Mapping[str, Any],
    ) -> V2ShipmentRegistration:
        """Validate and atomically register v2 identity for a prepared shipment."""
        if not isinstance(request, Mapping) or request.get("enabled") is not True:
            raise V2ShipmentRegistrationError(
                "v2Monitoring must be an object with enabled=true"
            )
        if "productRuleVersion" in request:
            raise V2ShipmentRegistrationError(
                "productRuleVersion is backend-managed and must not be supplied"
            )

        required = {
            "productId": request.get("productId"),
            "presentation": request.get("presentation"),
            "state": request.get("state"),
            "lotId": request.get("lotId"),
            "deviceId": request.get("deviceId"),
        }
        missing = [name for name, value in required.items() if not _text(value)]
        if missing:
            raise V2ShipmentRegistrationError(
                "v2Monitoring is missing required fields: " + ", ".join(missing)
            )

        shipment_id = _required_shipment_value(shipment, "shipmentId")
        shipment_product = _required_shipment_value(shipment, "productName")
        product_id = _text(required["productId"])
        if _product_key(shipment_product) != _product_key(product_id):
            raise V2ShipmentRegistrationError(
                "v2Monitoring productId must match the shipment productName"
            )
        device_id = _text(required["deviceId"])
        shipment_sensor_id = _required_shipment_value(shipment, "sensorId")
        if device_id != shipment_sensor_id:
            raise V2ShipmentRegistrationError(
                "v2Monitoring deviceId must match the shipment sensorId"
            )

        presentation = _text(required["presentation"])
        state = _text(required["state"])
        rules = resolve_applicable_rules(product_id, presentation, state)
        versions = {rule.version for rule in rules}
        if len(versions) != 1:
            raise V2ShipmentRegistrationError(
                "Verified ProductRules must resolve to exactly one version"
            )
        product_rule_version = next(iter(versions))

        trip = TripIdentity(
            trip_id=f"trip-{shipment_id}",
            lot_trip_id=f"lot-trip-{shipment_id}",
            lot_id=_text(required["lotId"]),
            device_id=device_id,
            product_id=product_id,
            presentation=presentation,
            state=state,
            product_rule_version=product_rule_version,
            origin=_required_shipment_value(shipment, "origin"),
            destination=_required_shipment_value(shipment, "destination"),
            start_time=_aware_datetime(shipment.get("departureAt"), "departureAt"),
            status=TripStatus.PLANNED,
        )
        assignment = DeviceAssignment(
            assignment_id=f"assignment-{shipment_id}",
            device_id=device_id,
            trip_id=trip.trip_id,
            lot_trip_id=trip.lot_trip_id,
            assigned_at=_aware_datetime(shipment.get("lastUpdated"), "lastUpdated"),
            active=False,
        )
        access = ShipmentAccess(
            shipment_id=shipment_id,
            lot_trip_id=trip.lot_trip_id,
            organization_id=_required_shipment_value(shipment, "organizationId"),
            driver_id=_required_shipment_value(shipment, "driverId"),
        )

        validate_trip_rule_context(trip)
        self._identity_repository.register_trip_assignment_and_access(
            trip,
            assignment,
            access,
        )
        return V2ShipmentRegistration(
            trip_identity=trip,
            device_assignment=assignment,
            shipment_access=access,
        )

    def rollback_registration(
        self,
        registration: V2ShipmentRegistration,
    ) -> None:
        """Remove an untouched PLANNED registration after legacy write failure."""
        self._identity_repository.unregister_planned_trip_assignment_and_access(
            registration.trip_identity.trip_id,
            registration.device_assignment.assignment_id,
            registration.shipment_access.lot_trip_id,
            registration.shipment_access.shipment_id,
        )


def _required_shipment_value(shipment, name):
    value = _text(shipment.get(name))
    if not value:
        raise V2ShipmentRegistrationError(
            f"Prepared shipment is missing required {name}"
        )
    return value


def _aware_datetime(value, name):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise V2ShipmentRegistrationError(
            f"Prepared shipment {name} must be a valid timestamp"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _text(value):
    return str(value or "").strip()


def _product_key(value):
    return "".join(character for character in _text(value).lower() if character.isalnum())
