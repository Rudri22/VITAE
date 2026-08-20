from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Optional, Tuple

try:
    from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
    from botocore.exceptions import ClientError
except ImportError as error:  # pragma: no cover - exercised only without AWS extras
    raise RuntimeError(
        "boto3 is required to use DynamoIdentityAccessRepository"
    ) from error

try:
    from .repository_serialization import (
        RepositorySerializationError,
        deserialize_device_assignment,
        deserialize_shipment_access,
        deserialize_trip_identity,
        serialize_device_assignment,
        serialize_shipment_access,
        serialize_trip_identity,
    )
    from .shipment_access import (
        IdentityAccessRepository,
        ShipmentAccess,
        ShipmentAccessConflictError,
        ShipmentAccessNotFoundError,
        validate_shipment_access,
    )
    from .state_repository import StateIntegrityError
    from .trip_identity import (
        DeviceAssignment,
        TripIdentity,
        TripStatus,
        trip_identity_with_status,
        validate_device_assignment,
        validate_trip_identity,
    )
except ImportError:
    from repository_serialization import (
        RepositorySerializationError,
        deserialize_device_assignment,
        deserialize_shipment_access,
        deserialize_trip_identity,
        serialize_device_assignment,
        serialize_shipment_access,
        serialize_trip_identity,
    )
    from shipment_access import (
        IdentityAccessRepository,
        ShipmentAccess,
        ShipmentAccessConflictError,
        ShipmentAccessNotFoundError,
        validate_shipment_access,
    )
    from state_repository import StateIntegrityError
    from trip_identity import (
        DeviceAssignment,
        TripIdentity,
        TripStatus,
        trip_identity_with_status,
        validate_device_assignment,
        validate_trip_identity,
    )


_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()
_ABSENT = "attribute_not_exists(PK) AND attribute_not_exists(SK)"


class DynamoIdentityAccessRepository(IdentityAccessRepository):
    """DynamoDB adapter for durable trip identity and shipment access only."""

    def __init__(self, dynamodb_client, table_name: str, *, key_namespace=""):
        if dynamodb_client is None:
            raise ValueError("dynamodb_client is required")
        self.table_name = _required_text(table_name, "table_name")
        namespace = str(key_namespace or "").strip()
        self._key_prefix = f"{namespace}#" if namespace else ""
        self._client = dynamodb_client

    def register_trip(self, trip: TripIdentity) -> None:
        validate_trip_identity(trip)
        actions = self._trip_registration_actions(trip)
        try:
            self._transact_write(actions)
        except ClientError as error:
            if self._is_transaction_cancel(error) and self._trip_is_identical(trip):
                return
            self._raise_state_conflict(error, "Trip identity is already registered")

    def register_trip_and_assignment(
        self,
        trip: TripIdentity,
        assignment: DeviceAssignment,
    ) -> None:
        validate_trip_identity(trip)
        validate_device_assignment(assignment, trip, assignment.device_id)
        actions = self._trip_registration_actions(
            trip
        ) + self._assignment_registration_actions(trip, assignment)
        try:
            self._transact_write(actions)
        except ClientError as error:
            if self._is_transaction_cancel(error) and self._identity_is_identical(
                trip, assignment
            ):
                return
            self._raise_state_conflict(
                error,
                "Trip, assignment, or device reservation is already registered",
            )

    def register_trip_assignment_and_access(
        self,
        trip: TripIdentity,
        assignment: DeviceAssignment,
        access: ShipmentAccess,
    ) -> None:
        validate_trip_identity(trip)
        validate_device_assignment(assignment, trip, assignment.device_id)
        validate_shipment_access(access)
        if access.lot_trip_id != trip.lot_trip_id:
            raise StateIntegrityError(
                "ShipmentAccess and TripIdentity lot_trip_id must match"
            )
        actions = (
            self._trip_registration_actions(trip)
            + self._assignment_registration_actions(trip, assignment)
            + self._access_registration_actions(access)
        )
        try:
            self._transact_write(actions)
        except ClientError as error:
            if self._is_transaction_cancel(error):
                if self._combined_is_identical(trip, assignment, access):
                    return
                if self._access_identity_conflicts(access):
                    raise ShipmentAccessConflictError(
                        "Shipment access identity is already registered"
                    ) from error
            self._raise_state_conflict(
                error,
                "Trip, assignment, or device reservation is already registered",
            )

    def unregister_planned_trip_and_assignment(
        self,
        trip_id: str,
        assignment_id: str,
    ) -> None:
        trip, assignment = self._load_compensation_identity(trip_id, assignment_id)
        try:
            self._transact_write(self._identity_delete_actions(trip, assignment))
        except ClientError as error:
            self._raise_state_conflict(
                error, "Planned registration changed before compensation"
            )

    def unregister_planned_trip_assignment_and_access(
        self,
        trip_id: str,
        assignment_id: str,
        lot_trip_id: str,
        shipment_id: str,
    ) -> None:
        trip, assignment = self._load_compensation_identity(trip_id, assignment_id)
        access = self.get_shipment_access(lot_trip_id)
        if access is None or access.shipment_id != _required_text(
            shipment_id, "shipment_id"
        ):
            raise ShipmentAccessNotFoundError(
                "Shipment access does not exist or identity does not match"
            )
        if access.lot_trip_id != trip.lot_trip_id:
            raise StateIntegrityError("ShipmentAccess and TripIdentity identity mismatch")
        actions = self._identity_delete_actions(trip, assignment)
        actions.extend(self._access_delete_actions(access))
        try:
            self._transact_write(actions)
        except ClientError as error:
            if self._is_transaction_cancel(error):
                current = self.get_shipment_access(access.lot_trip_id)
                if current is None or current.shipment_id != access.shipment_id:
                    raise ShipmentAccessNotFoundError(
                        "Shipment access changed before compensation"
                    ) from error
            self._raise_state_conflict(
                error, "Planned registration changed before compensation"
            )

    def transition_trip_and_assignment(
        self,
        trip_id: str,
        assignment_id: str,
        expected_trip_status: TripStatus,
        next_trip_status: TripStatus,
        expected_assignment_active: bool,
        next_assignment_active: bool,
        completed_at: Optional[datetime] = None,
    ) -> Tuple[TripIdentity, DeviceAssignment]:
        if not isinstance(expected_trip_status, TripStatus) or not isinstance(
            next_trip_status, TripStatus
        ):
            raise StateIntegrityError("Trip lifecycle status is invalid")
        if not isinstance(expected_assignment_active, bool) or not isinstance(
            next_assignment_active, bool
        ):
            raise StateIntegrityError("Assignment lifecycle state is invalid")
        if next_assignment_active != (next_trip_status == TripStatus.ACTIVE):
            raise StateIntegrityError(
                "Only an ACTIVE trip may have an active device assignment"
            )

        trip, assignment = self._get_trip_and_assignment(trip_id, assignment_id)
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

        next_trip = trip_identity_with_status(
            trip,
            next_trip_status,
            completed_at=completed_at,
        )
        next_assignment = replace(assignment, active=next_assignment_active)
        validate_trip_identity(next_trip)
        validate_device_assignment(next_assignment, next_trip, assignment.device_id)
        actions = self._lifecycle_actions(
            trip,
            assignment,
            next_trip,
            next_assignment,
        )
        try:
            self._transact_write(actions)
        except ClientError as error:
            self._raise_state_conflict(
                error, "Trip lifecycle does not match the expected prior state"
            )
        return next_trip, next_assignment

    def get_trip_by_id(self, trip_id: str) -> Optional[TripIdentity]:
        item = self._get_item(self._trip_key(_required_text(trip_id, "trip_id")))
        return None if item is None else self._trip_from_item(item)

    def get_trip_by_lot_trip_id(
        self, lot_trip_id: str
    ) -> Optional[TripIdentity]:
        lot_trip = _required_text(lot_trip_id, "lot_trip_id")
        lookup = self._get_item(self._lot_trip_key(lot_trip))
        if lookup is None:
            return None
        self._require_item_type(lookup, "LOT_TRIP_LOOKUP")
        trip_id = self._item_text(lookup, "tripId")
        trip = self.get_trip_by_id(trip_id)
        if trip is None or trip.lot_trip_id != lot_trip:
            raise StateIntegrityError("Lot-trip lookup references invalid trip identity")
        return trip

    def register_device_assignment(self, assignment: DeviceAssignment) -> None:
        trip = self.get_trip_by_id(assignment.trip_id)
        if trip is None:
            raise StateIntegrityError(
                "DeviceAssignment must reference a registered TripIdentity"
            )
        validate_device_assignment(assignment, trip, assignment.device_id)
        actions = self._assignment_registration_actions(trip, assignment)
        actions.append(
            self._condition(
                self._trip_key(trip.trip_id),
                "#entityType = :trip AND lotTripId = :lotTripId",
                {"#entityType": "entityType"},
                {":trip": "TRIP", ":lotTripId": trip.lot_trip_id},
            )
        )
        try:
            self._transact_write(actions)
        except ClientError as error:
            if self._is_transaction_cancel(error) and self._assignment_is_identical(
                assignment, trip
            ):
                return
            self._raise_state_conflict(
                error,
                "DeviceAssignment or device reservation is already registered",
            )

    def get_device_assignments(
        self, device_id: str
    ) -> Tuple[DeviceAssignment, ...]:
        device = _required_text(device_id, "device_id")
        items = self._query(
            self._device_partition(device),
            "ASSIGNMENT#",
        )
        assignments = tuple(self._assignment_from_item(item) for item in items)
        return tuple(
            sorted(
                assignments,
                key=lambda value: (value.assigned_at, value.assignment_id),
            )
        )

    def register_shipment_access(self, access: ShipmentAccess) -> ShipmentAccess:
        validate_shipment_access(access)
        try:
            self._transact_write(self._access_registration_actions(access))
        except ClientError as error:
            if self._is_transaction_cancel(error) and self._access_is_identical(access):
                return access
            if self._is_transaction_cancel(error):
                raise ShipmentAccessConflictError(
                    "Shipment access identity is already registered"
                ) from error
            raise
        return access

    def get_shipment_access(self, lot_trip_id: str) -> Optional[ShipmentAccess]:
        item = self._get_item(
            self._access_key(_required_text(lot_trip_id, "lot_trip_id"))
        )
        return None if item is None else self._access_from_item(item)

    def list_shipment_accesses(
        self,
        *,
        organization_id: Optional[str] = None,
        driver_id: Optional[str] = None,
    ) -> Tuple[ShipmentAccess, ...]:
        organization = (
            None
            if organization_id is None
            else _required_text(organization_id, "organization_id")
        )
        driver = (
            None if driver_id is None else _required_text(driver_id, "driver_id")
        )
        if organization is not None:
            partition = self._organization_partition(organization)
        elif driver is not None:
            partition = self._driver_partition(driver)
        else:
            partition = self._all_access_partition()
        values = tuple(
            self._access_from_item(item)
            for item in self._query(partition, "ACCESS#")
        )
        if driver is not None:
            values = tuple(value for value in values if value.driver_id == driver)
        if organization is not None:
            values = tuple(
                value for value in values if value.organization_id == organization
            )
        return tuple(sorted(values, key=lambda value: value.lot_trip_id))

    def unregister_shipment_access(
        self,
        lot_trip_id: str,
        shipment_id: str,
    ) -> None:
        lot_trip = _required_text(lot_trip_id, "lot_trip_id")
        shipment = _required_text(shipment_id, "shipment_id")
        access = self.get_shipment_access(lot_trip)
        if access is None or access.shipment_id != shipment:
            raise ShipmentAccessNotFoundError(
                "Shipment access does not exist or identity does not match"
            )
        try:
            self._transact_write(self._access_delete_actions(access))
        except ClientError as error:
            if self._is_transaction_cancel(error):
                raise ShipmentAccessNotFoundError(
                    "Shipment access does not exist or identity does not match"
                ) from error
            raise

    def transition_shipment_access_driver(
        self,
        lot_trip_id: str,
        expected_driver_id: str,
        next_driver_id: str,
    ) -> ShipmentAccess:
        lot_trip = _required_text(lot_trip_id, "lot_trip_id")
        expected = _required_text(expected_driver_id, "expected_driver_id")
        next_driver = _required_text(next_driver_id, "next_driver_id")
        current = self.get_shipment_access(lot_trip)
        if current is None:
            raise ShipmentAccessNotFoundError("Shipment access does not exist")
        if current.driver_id != expected:
            raise ShipmentAccessConflictError(
                "Shipment access driver changed before transition"
            )
        if expected == next_driver:
            return current

        updated = replace(current, driver_id=next_driver)
        actions = [
            self._access_update(
                self._access_key(current.lot_trip_id), current, updated
            ),
            self._access_update(
                self._all_access_key(current.lot_trip_id), current, updated
            ),
            self._access_update(
                self._organization_access_key(
                    current.organization_id, current.lot_trip_id
                ),
                current,
                updated,
            ),
            self._delete(
                self._driver_access_key(current.driver_id, current.lot_trip_id),
                "shipmentId = :shipmentId AND driverId = :driverId",
                values={
                    ":shipmentId": current.shipment_id,
                    ":driverId": current.driver_id,
                },
            ),
            self._put(
                self._access_item(
                    self._driver_access_key(updated.driver_id, updated.lot_trip_id),
                    "DRIVER_ACCESS_MEMBERSHIP",
                    updated,
                )
            ),
        ]
        try:
            self._transact_write(actions)
        except ClientError as error:
            if self._is_transaction_cancel(error):
                raise ShipmentAccessConflictError(
                    "Shipment access driver changed before transition"
                ) from error
            raise
        return updated

    def _trip_puts(self, trip):
        return [
            self._put(self._trip_item(trip)),
            self._put(
                {
                    **self._lot_trip_key(trip.lot_trip_id),
                    "entityType": "LOT_TRIP_LOOKUP",
                    "tripId": trip.trip_id,
                    "lotTripId": trip.lot_trip_id,
                }
            ),
        ]

    def _trip_registration_actions(self, trip):
        trip_item = self._get_item(self._trip_key(trip.trip_id))
        lookup_item = self._get_item(self._lot_trip_key(trip.lot_trip_id))
        if trip_item is None and lookup_item is None:
            return self._trip_puts(trip)
        if trip_item is None or lookup_item is None:
            raise StateIntegrityError("Trip identity is partially registered")
        if (
            self._trip_from_item(trip_item) != trip
            or self._item_text(lookup_item, "tripId") != trip.trip_id
            or self._item_text(lookup_item, "lotTripId") != trip.lot_trip_id
        ):
            raise StateIntegrityError("Trip identity is already registered")
        return [
            self._condition(
                self._trip_key(trip.trip_id),
                "document = :document AND lotTripId = :lotTripId",
                values={
                    ":document": serialize_trip_identity(trip),
                    ":lotTripId": trip.lot_trip_id,
                },
            ),
            self._condition(
                self._lot_trip_key(trip.lot_trip_id),
                "tripId = :tripId AND lotTripId = :lotTripId",
                values={
                    ":tripId": trip.trip_id,
                    ":lotTripId": trip.lot_trip_id,
                },
            ),
        ]

    def _assignment_puts(self, trip, assignment):
        actions = [
            self._put(self._assignment_item(self._assignment_key(assignment.assignment_id), assignment)),
            self._put(
                self._assignment_item(
                    self._device_assignment_key(assignment), assignment
                )
            ),
        ]
        if assignment.active or trip.status == TripStatus.PLANNED:
            actions.append(self._put(self._reservation_item(trip, assignment)))
        return actions

    def _assignment_registration_actions(self, trip, assignment):
        canonical_key = self._assignment_key(assignment.assignment_id)
        membership_key = self._device_assignment_key(assignment)
        canonical = self._get_item(canonical_key)
        membership = self._get_item(membership_key)
        reservation = self._get_item(self._reservation_key(assignment.device_id))
        should_reserve = assignment.active or trip.status == TripStatus.PLANNED
        if canonical is None and membership is None and reservation is None:
            return self._assignment_puts(trip, assignment)
        if canonical is None or membership is None:
            raise StateIntegrityError("DeviceAssignment is partially registered")
        if (
            self._assignment_from_item(canonical) != assignment
            or self._assignment_from_item(membership) != assignment
        ):
            raise StateIntegrityError(
                "DeviceAssignment is already registered with different content"
            )
        if should_reserve:
            if reservation is None or not self._reservation_matches(
                reservation, trip, assignment
            ):
                raise StateIntegrityError(
                    "Device already has an active assignment or PLANNED reservation"
                )
        elif reservation is not None:
            raise StateIntegrityError(
                "Inactive non-PLANNED assignment has an unexpected reservation"
            )

        document = serialize_device_assignment(assignment)
        actions = [
            self._condition(
                canonical_key,
                "document = :document",
                values={":document": document},
            ),
            self._condition(
                membership_key,
                "document = :document",
                values={":document": document},
            ),
        ]
        if should_reserve:
            actions.append(
                self._condition(
                    self._reservation_key(assignment.device_id),
                    "assignmentId = :assignmentId AND tripId = :tripId AND lotTripId = :lotTripId AND reservationState = :reservationState",
                    values={
                        ":assignmentId": assignment.assignment_id,
                        ":tripId": trip.trip_id,
                        ":lotTripId": trip.lot_trip_id,
                        ":reservationState": trip.status.value,
                    },
                )
            )
        return actions

    def _access_puts(self, access):
        return [
            self._put(self._access_item(self._access_key(access.lot_trip_id), "SHIPMENT_ACCESS", access)),
            self._put(
                {
                    **self._shipment_guard_key(access.shipment_id),
                    "entityType": "SHIPMENT_ACCESS_GUARD",
                    "shipmentId": access.shipment_id,
                    "lotTripId": access.lot_trip_id,
                }
            ),
            self._put(self._access_item(self._all_access_key(access.lot_trip_id), "ALL_ACCESS_MEMBERSHIP", access)),
            self._put(self._access_item(self._organization_access_key(access.organization_id, access.lot_trip_id), "ORGANIZATION_ACCESS_MEMBERSHIP", access)),
            self._put(self._access_item(self._driver_access_key(access.driver_id, access.lot_trip_id), "DRIVER_ACCESS_MEMBERSHIP", access)),
        ]

    def _access_registration_actions(self, access):
        keys = (
            self._access_key(access.lot_trip_id),
            self._shipment_guard_key(access.shipment_id),
            self._all_access_key(access.lot_trip_id),
            self._organization_access_key(
                access.organization_id, access.lot_trip_id
            ),
            self._driver_access_key(access.driver_id, access.lot_trip_id),
        )
        items = tuple(self._get_item(key) for key in keys)
        if all(item is None for item in items):
            return self._access_puts(access)
        if any(item is None for item in items):
            raise ShipmentAccessConflictError(
                "Shipment access identity is partially registered"
            )
        if (
            self._item_text(items[1], "shipmentId") != access.shipment_id
            or self._item_text(items[1], "lotTripId") != access.lot_trip_id
            or any(
                self._access_from_item(item) != access
                for item in (items[0], items[2], items[3], items[4])
            )
        ):
            raise ShipmentAccessConflictError(
                "Shipment access identity is already registered"
            )
        values = {
            ":shipmentId": access.shipment_id,
            ":lotTripId": access.lot_trip_id,
        }
        actions = [
            self._condition(
                keys[0],
                "document = :document",
                values={":document": serialize_shipment_access(access)},
            ),
            self._condition(
                keys[1],
                "shipmentId = :shipmentId AND lotTripId = :lotTripId",
                values=values,
            ),
        ]
        actions.extend(
            self._condition(
                key,
                "document = :document",
                values={":document": serialize_shipment_access(access)},
            )
            for key in keys[2:]
        )
        return actions

    def _identity_delete_actions(self, trip, assignment):
        identity_values = {
            ":tripId": trip.trip_id,
            ":lotTripId": trip.lot_trip_id,
            ":assignmentId": assignment.assignment_id,
        }
        return [
            self._delete(
                self._trip_key(trip.trip_id),
                "lotTripId = :lotTripId AND #status = :planned",
                names={"#status": "status"},
                values={":lotTripId": trip.lot_trip_id, ":planned": TripStatus.PLANNED.value},
            ),
            self._delete(
                self._lot_trip_key(trip.lot_trip_id),
                "tripId = :tripId",
                values={":tripId": trip.trip_id},
            ),
            self._delete(
                self._assignment_key(assignment.assignment_id),
                "tripId = :tripId AND lotTripId = :lotTripId AND active = :inactive",
                values={":tripId": trip.trip_id, ":lotTripId": trip.lot_trip_id, ":inactive": False},
            ),
            self._delete(
                self._device_assignment_key(assignment),
                "assignmentId = :assignmentId AND active = :inactive",
                values={":assignmentId": assignment.assignment_id, ":inactive": False},
            ),
            self._delete(
                self._reservation_key(assignment.device_id),
                "assignmentId = :assignmentId AND tripId = :tripId AND lotTripId = :lotTripId AND reservationState = :planned",
                values={**identity_values, ":planned": TripStatus.PLANNED.value},
            ),
        ]

    def _access_delete_actions(self, access):
        condition = "shipmentId = :shipmentId AND lotTripId = :lotTripId"
        values = {":shipmentId": access.shipment_id, ":lotTripId": access.lot_trip_id}
        return [
            self._delete(self._access_key(access.lot_trip_id), condition, values=values),
            self._delete(self._shipment_guard_key(access.shipment_id), condition, values=values),
            self._delete(self._all_access_key(access.lot_trip_id), condition, values=values),
            self._delete(self._organization_access_key(access.organization_id, access.lot_trip_id), condition, values=values),
            self._delete(self._driver_access_key(access.driver_id, access.lot_trip_id), condition, values=values),
        ]

    def _lifecycle_actions(self, trip, assignment, next_trip, next_assignment):
        actions = [
            self._update_document(
                self._trip_key(trip.trip_id),
                serialize_trip_identity(next_trip),
                "#status = :expectedStatus AND lotTripId = :lotTripId",
                names={"#status": "status"},
                values={":expectedStatus": trip.status.value, ":lotTripId": trip.lot_trip_id},
                extra_updates={"status": next_trip.status.value},
            ),
            self._update_document(
                self._assignment_key(assignment.assignment_id),
                serialize_device_assignment(next_assignment),
                "active = :expectedActive AND tripId = :tripId AND lotTripId = :lotTripId",
                values={":expectedActive": assignment.active, ":tripId": trip.trip_id, ":lotTripId": trip.lot_trip_id},
                extra_updates={"active": next_assignment.active},
            ),
            self._update_document(
                self._device_assignment_key(assignment),
                serialize_device_assignment(next_assignment),
                "active = :expectedActive AND assignmentId = :assignmentId",
                values={":expectedActive": assignment.active, ":assignmentId": assignment.assignment_id},
                extra_updates={"active": next_assignment.active},
            ),
        ]
        currently_reserved = assignment.active or trip.status == TripStatus.PLANNED
        next_reserved = next_assignment.active or next_trip.status == TripStatus.PLANNED
        reservation_key = self._reservation_key(assignment.device_id)
        reservation_values = {
            ":assignmentId": assignment.assignment_id,
            ":tripId": trip.trip_id,
            ":lotTripId": trip.lot_trip_id,
        }
        if currently_reserved and next_reserved:
            actions.append(
                self._update_fields(
                    reservation_key,
                    {"reservationState": next_trip.status.value},
                    "assignmentId = :assignmentId AND tripId = :tripId AND lotTripId = :lotTripId",
                    values=reservation_values,
                )
            )
        elif currently_reserved:
            actions.append(
                self._delete(
                    reservation_key,
                    "assignmentId = :assignmentId AND tripId = :tripId AND lotTripId = :lotTripId",
                    values=reservation_values,
                )
            )
        elif next_reserved:
            actions.append(self._put(self._reservation_item(next_trip, next_assignment)))
        return actions

    def _load_compensation_identity(self, trip_id, assignment_id):
        trip, assignment = self._get_trip_and_assignment(trip_id, assignment_id)
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
        return trip, assignment

    def _get_trip_and_assignment(self, trip_id, assignment_id):
        keys = [
            self._trip_key(_required_text(trip_id, "trip_id")),
            self._assignment_key(_required_text(assignment_id, "assignment_id")),
        ]
        response = self._client.transact_get_items(
            TransactItems=[
                {"Get": {"TableName": self.table_name, "Key": _marshal(key)}}
                for key in keys
            ]
        )
        items = [
            None if not entry.get("Item") else _unmarshal(entry["Item"])
            for entry in response["Responses"]
        ]
        trip = None if items[0] is None else self._trip_from_item(items[0])
        assignment = (
            None if items[1] is None else self._assignment_from_item(items[1])
        )
        return trip, assignment

    def _trip_is_identical(self, trip):
        try:
            by_id = self.get_trip_by_id(trip.trip_id)
            by_lot = self.get_trip_by_lot_trip_id(trip.lot_trip_id)
            return by_id == trip and by_lot == trip
        except StateIntegrityError:
            return False

    def _assignment_is_identical(self, assignment, trip):
        item = self._get_item(self._assignment_key(assignment.assignment_id))
        if item is None or self._assignment_from_item(item) != assignment:
            return False
        memberships = self.get_device_assignments(assignment.device_id)
        if assignment not in memberships:
            return False
        reservation = self._get_item(self._reservation_key(assignment.device_id))
        should_reserve = assignment.active or trip.status == TripStatus.PLANNED
        if not should_reserve:
            return reservation is None
        return reservation is not None and self._reservation_matches(
            reservation, trip, assignment
        )

    def _identity_is_identical(self, trip, assignment):
        return self._trip_is_identical(trip) and self._assignment_is_identical(
            assignment, trip
        )

    def _access_is_identical(self, access):
        current = self.get_shipment_access(access.lot_trip_id)
        guard = self._get_item(self._shipment_guard_key(access.shipment_id))
        if current != access or guard is None:
            return False
        if self._item_text(guard, "lotTripId") != access.lot_trip_id:
            return False
        copies = (
            self._get_item(self._all_access_key(access.lot_trip_id)),
            self._get_item(self._organization_access_key(access.organization_id, access.lot_trip_id)),
            self._get_item(self._driver_access_key(access.driver_id, access.lot_trip_id)),
        )
        return all(item is not None and self._access_from_item(item) == access for item in copies)

    def _combined_is_identical(self, trip, assignment, access):
        return self._identity_is_identical(trip, assignment) and self._access_is_identical(access)

    def _access_identity_conflicts(self, access):
        current = self.get_shipment_access(access.lot_trip_id)
        guard = self._get_item(self._shipment_guard_key(access.shipment_id))
        return (
            current is not None and current != access
            or guard is not None and self._item_text(guard, "lotTripId") != access.lot_trip_id
        )

    def _reservation_matches(self, item, trip, assignment):
        self._require_item_type(item, "DEVICE_RESERVATION")
        return (
            self._item_text(item, "assignmentId") == assignment.assignment_id
            and self._item_text(item, "tripId") == trip.trip_id
            and self._item_text(item, "lotTripId") == trip.lot_trip_id
            and self._item_text(item, "reservationState") == trip.status.value
        )

    def _trip_from_item(self, item):
        self._require_item_type(item, "TRIP")
        try:
            value = deserialize_trip_identity(item["document"])
        except (KeyError, RepositorySerializationError) as error:
            raise StateIntegrityError("Persisted TripIdentity document is invalid") from error
        expected = (value.trip_id, value.lot_trip_id, value.device_id, value.status.value)
        actual = tuple(self._item_text(item, name) for name in ("tripId", "lotTripId", "deviceId", "status"))
        if actual != expected:
            raise StateIntegrityError("Persisted TripIdentity index attributes disagree with document")
        return value

    def _assignment_from_item(self, item):
        if item.get("entityType") not in ("ASSIGNMENT", "DEVICE_ASSIGNMENT_MEMBERSHIP"):
            raise StateIntegrityError("Unexpected DynamoDB assignment item type")
        try:
            value = deserialize_device_assignment(item["document"])
        except (KeyError, RepositorySerializationError) as error:
            raise StateIntegrityError("Persisted DeviceAssignment document is invalid") from error
        expected = (value.assignment_id, value.device_id, value.trip_id, value.lot_trip_id, value.active)
        actual = (
            self._item_text(item, "assignmentId"),
            self._item_text(item, "deviceId"),
            self._item_text(item, "tripId"),
            self._item_text(item, "lotTripId"),
            item.get("active"),
        )
        if actual != expected:
            raise StateIntegrityError("Persisted DeviceAssignment index attributes disagree with document")
        return value

    def _access_from_item(self, item):
        if item.get("entityType") not in (
            "SHIPMENT_ACCESS",
            "ALL_ACCESS_MEMBERSHIP",
            "ORGANIZATION_ACCESS_MEMBERSHIP",
            "DRIVER_ACCESS_MEMBERSHIP",
        ):
            raise StateIntegrityError("Unexpected DynamoDB ShipmentAccess item type")
        try:
            value = deserialize_shipment_access(item["document"])
        except (KeyError, RepositorySerializationError) as error:
            raise StateIntegrityError("Persisted ShipmentAccess document is invalid") from error
        expected = (value.shipment_id, value.lot_trip_id, value.organization_id, value.driver_id)
        actual = tuple(self._item_text(item, name) for name in ("shipmentId", "lotTripId", "organizationId", "driverId"))
        if actual != expected:
            raise StateIntegrityError("Persisted ShipmentAccess index attributes disagree with document")
        return value

    def _trip_item(self, trip):
        return {
            **self._trip_key(trip.trip_id),
            "entityType": "TRIP",
            "tripId": trip.trip_id,
            "lotTripId": trip.lot_trip_id,
            "deviceId": trip.device_id,
            "status": trip.status.value,
            "document": serialize_trip_identity(trip),
        }

    def _assignment_item(self, key, assignment):
        entity_type = "ASSIGNMENT" if key["PK"] == self._assignment_partition(assignment.assignment_id) else "DEVICE_ASSIGNMENT_MEMBERSHIP"
        return {
            **key,
            "entityType": entity_type,
            "assignmentId": assignment.assignment_id,
            "deviceId": assignment.device_id,
            "tripId": assignment.trip_id,
            "lotTripId": assignment.lot_trip_id,
            "active": assignment.active,
            "document": serialize_device_assignment(assignment),
        }

    def _reservation_item(self, trip, assignment):
        return {
            **self._reservation_key(assignment.device_id),
            "entityType": "DEVICE_RESERVATION",
            "deviceId": assignment.device_id,
            "assignmentId": assignment.assignment_id,
            "tripId": assignment.trip_id,
            "lotTripId": assignment.lot_trip_id,
            "reservationState": trip.status.value,
        }

    def _access_item(self, key, entity_type, access):
        return {
            **key,
            "entityType": entity_type,
            "shipmentId": access.shipment_id,
            "lotTripId": access.lot_trip_id,
            "organizationId": access.organization_id,
            "driverId": access.driver_id,
            "document": serialize_shipment_access(access),
        }

    def _access_update(self, key, current, updated):
        return self._update_document(
            key,
            serialize_shipment_access(updated),
            "shipmentId = :shipmentId AND lotTripId = :lotTripId AND driverId = :expectedDriver",
            values={
                ":shipmentId": current.shipment_id,
                ":lotTripId": current.lot_trip_id,
                ":expectedDriver": current.driver_id,
            },
            extra_updates={"driverId": updated.driver_id},
        )

    def _put(self, item):
        return {"Put": {"TableName": self.table_name, "Item": _marshal(item), "ConditionExpression": _ABSENT}}

    def _delete(self, key, condition, *, names=None, values=None):
        operation = {"TableName": self.table_name, "Key": _marshal(key), "ConditionExpression": condition}
        if names:
            operation["ExpressionAttributeNames"] = names
        if values:
            operation["ExpressionAttributeValues"] = _marshal(values)
        return {"Delete": operation}

    def _condition(self, key, condition, names=None, values=None):
        operation = {"TableName": self.table_name, "Key": _marshal(key), "ConditionExpression": condition}
        if names:
            operation["ExpressionAttributeNames"] = names
        if values:
            operation["ExpressionAttributeValues"] = _marshal(values)
        return {"ConditionCheck": operation}

    def _update_document(self, key, document, condition, *, names=None, values=None, extra_updates=None):
        updates = {"document": document, **(extra_updates or {})}
        return self._update_fields(key, updates, condition, names=names, values=values)

    def _update_fields(self, key, updates, condition, *, names=None, values=None):
        expression_names = dict(names or {})
        expression_values = dict(values or {})
        clauses = []
        for index, (field, value) in enumerate(updates.items()):
            name_token = f"#set{index}"
            value_token = f":set{index}"
            expression_names[name_token] = field
            expression_values[value_token] = value
            clauses.append(f"{name_token} = {value_token}")
        operation = {
            "TableName": self.table_name,
            "Key": _marshal(key),
            "UpdateExpression": "SET " + ", ".join(clauses),
            "ConditionExpression": condition,
            "ExpressionAttributeNames": expression_names,
            "ExpressionAttributeValues": _marshal(expression_values),
        }
        return {"Update": operation}

    def _transact_write(self, actions):
        self._client.transact_write_items(TransactItems=actions)

    def _get_item(self, key):
        response = self._client.get_item(
            TableName=self.table_name,
            Key=_marshal(key),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return None if item is None else _unmarshal(item)

    def _query(self, partition_key, sort_prefix):
        request = {
            "TableName": self.table_name,
            "KeyConditionExpression": "PK = :pk AND begins_with(SK, :prefix)",
            "ExpressionAttributeValues": _marshal(
                {":pk": partition_key, ":prefix": sort_prefix}
            ),
            "ConsistentRead": True,
        }
        items = []
        while True:
            response = self._client.query(**request)
            items.extend(_unmarshal(item) for item in response.get("Items", ()))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return tuple(items)
            request["ExclusiveStartKey"] = last_key

    def _raise_state_conflict(self, error, message):
        if self._is_transaction_cancel(error):
            raise StateIntegrityError(message) from error
        raise error

    @staticmethod
    def _is_transaction_cancel(error):
        return error.response.get("Error", {}).get("Code") == "TransactionCanceledException"

    @staticmethod
    def _require_item_type(item, expected):
        if item.get("entityType") != expected:
            raise StateIntegrityError(f"Expected DynamoDB item type {expected}")

    @staticmethod
    def _item_text(item, field):
        value = item.get(field)
        if not isinstance(value, str) or not value:
            raise StateIntegrityError(f"Persisted DynamoDB attribute {field} is invalid")
        return value

    def _pk(self, value):
        return f"{self._key_prefix}{value}"

    def _trip_partition(self, trip_id):
        return self._pk(f"TRIP#{trip_id}")

    def _trip_key(self, trip_id):
        return {"PK": self._trip_partition(trip_id), "SK": "META"}

    def _lot_trip_key(self, lot_trip_id):
        return {"PK": self._pk(f"LOTTRIP#{lot_trip_id}"), "SK": "TRIP"}

    def _assignment_partition(self, assignment_id):
        return self._pk(f"ASSIGNMENT#{assignment_id}")

    def _assignment_key(self, assignment_id):
        return {"PK": self._assignment_partition(assignment_id), "SK": "META"}

    def _device_partition(self, device_id):
        return self._pk(f"DEVICE#{device_id}")

    def _device_assignment_key(self, assignment):
        timestamp = serialize_device_assignment(assignment)["assigned_at"]
        return {"PK": self._device_partition(assignment.device_id), "SK": f"ASSIGNMENT#{timestamp}#{assignment.assignment_id}"}

    def _reservation_key(self, device_id):
        return {"PK": self._device_partition(device_id), "SK": "RESERVATION"}

    def _access_key(self, lot_trip_id):
        return {"PK": self._pk(f"LOTTRIP#{lot_trip_id}"), "SK": "ACCESS"}

    def _shipment_guard_key(self, shipment_id):
        return {"PK": self._pk(f"SHIPMENT#{shipment_id}"), "SK": "ACCESS"}

    def _all_access_partition(self):
        return self._pk("ACCESS")

    def _all_access_key(self, lot_trip_id):
        return {"PK": self._all_access_partition(), "SK": f"ACCESS#{lot_trip_id}"}

    def _organization_partition(self, organization_id):
        return self._pk(f"ORG#{organization_id}")

    def _organization_access_key(self, organization_id, lot_trip_id):
        return {"PK": self._organization_partition(organization_id), "SK": f"ACCESS#{lot_trip_id}"}

    def _driver_partition(self, driver_id):
        return self._pk(f"DRIVER#{driver_id}")

    def _driver_access_key(self, driver_id, lot_trip_id):
        return {"PK": self._driver_partition(driver_id), "SK": f"ACCESS#{lot_trip_id}"}


def _marshal(values):
    return {key: _SERIALIZER.serialize(value) for key, value in values.items()}


def _unmarshal(values):
    return {
        key: _normalize_numbers(_DESERIALIZER.deserialize(value))
        for key, value in values.items()
    }


def _normalize_numbers(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _normalize_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_numbers(item) for item in value]
    if isinstance(value, set):
        return {_normalize_numbers(item) for item in value}
    return value


def _required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()
