import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

try:
    from .product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
        ProductRulesNotFoundError,
    )
    from .trip_identity import (
        AssignmentTripNotFoundError,
        DeviceAssignment,
        DeviceAssignmentMismatchError,
        DeviceAssignmentValidationError,
        MultipleActiveAssignmentsError,
        NoActiveAssignmentError,
        TripIdentity,
        TripIdentityValidationError,
        TripNotActiveError,
        TripStatus,
        UnknownDeviceError,
        resolve_trip_for_device,
        validate_trip_identity,
        validate_trip_rule_context,
    )
except ImportError:
    from product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
        ProductRulesNotFoundError,
    )
    from trip_identity import (
        AssignmentTripNotFoundError,
        DeviceAssignment,
        DeviceAssignmentMismatchError,
        DeviceAssignmentValidationError,
        MultipleActiveAssignmentsError,
        NoActiveAssignmentError,
        TripIdentity,
        TripIdentityValidationError,
        TripNotActiveError,
        TripStatus,
        UnknownDeviceError,
        resolve_trip_for_device,
        validate_trip_identity,
        validate_trip_rule_context,
    )


DEVICE_ID = "device-temperature-001"
START_TIME = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
ASSIGNED_AT = datetime(2026, 1, 1, 7, 30, tzinfo=timezone.utc)


def gardasil_trip(**changes):
    trip = TripIdentity(
        trip_id="trip-001",
        lot_trip_id="lot-trip-001",
        lot_id="lot-g9-001",
        device_id=DEVICE_ID,
        product_id=GARDASIL_9_PRODUCT_ID,
        presentation=GARDASIL_9_PRESENTATION,
        state=GARDASIL_9_STATE,
        product_rule_version=GARDASIL_9_SOURCE_VERSION,
        origin="Central Cold Storage",
        destination="Hospital A Receiving",
        start_time=START_TIME,
        status=TripStatus.ACTIVE,
    )
    return replace(trip, **changes)


def assignment(**changes):
    value = DeviceAssignment(
        assignment_id="assignment-001",
        device_id=DEVICE_ID,
        trip_id="trip-001",
        lot_trip_id="lot-trip-001",
        assigned_at=ASSIGNED_AT,
        active=True,
    )
    return replace(value, **changes)


class TripIdentityTests(unittest.TestCase):
    def test_valid_active_device_resolves_correct_trip(self):
        trip = gardasil_trip()
        self.assertIs(resolve_trip_for_device(DEVICE_ID, (trip,), (assignment(),)), trip)

    def test_valid_lot_trip_id_is_preserved(self):
        trip = resolve_trip_for_device(DEVICE_ID, (gardasil_trip(),), (assignment(),))
        self.assertEqual(trip.lot_trip_id, "lot-trip-001")

    def test_unknown_device_fails(self):
        with self.assertRaises(UnknownDeviceError):
            resolve_trip_for_device("unknown-device", (gardasil_trip(),), (assignment(),))

    def test_device_with_no_active_assignment_fails(self):
        with self.assertRaises(NoActiveAssignmentError):
            resolve_trip_for_device(DEVICE_ID, (gardasil_trip(),), (assignment(active=False),))

    def test_multiple_active_assignments_for_device_fail(self):
        duplicate = assignment(assignment_id="assignment-002", trip_id="trip-002")
        with self.assertRaises(MultipleActiveAssignmentsError):
            resolve_trip_for_device(DEVICE_ID, (gardasil_trip(),), (assignment(), duplicate))

    def test_assignment_to_missing_trip_fails(self):
        with self.assertRaises(AssignmentTripNotFoundError):
            resolve_trip_for_device(DEVICE_ID, (), (assignment(),))

    def test_assignment_to_non_active_trip_fails(self):
        with self.assertRaises(TripNotActiveError):
            resolve_trip_for_device(
                DEVICE_ID,
                (gardasil_trip(status=TripStatus.PLANNED),),
                (assignment(),),
            )

    def test_missing_product_id_fails_validation(self):
        with self.assertRaises(TripIdentityValidationError):
            resolve_trip_for_device(DEVICE_ID, (gardasil_trip(product_id=""),), (assignment(),))

    def test_missing_lot_trip_id_fails_validation(self):
        broken_trip = gardasil_trip(lot_trip_id="")
        with self.assertRaises(TripIdentityValidationError):
            resolve_trip_for_device(
                DEVICE_ID,
                (broken_trip,),
                (assignment(lot_trip_id=""),),
            )

    def test_missing_device_id_fails_validation(self):
        with self.assertRaises(TripIdentityValidationError):
            resolve_trip_for_device(DEVICE_ID, (gardasil_trip(device_id=""),), (assignment(),))

    def test_missing_origin_fails_validation(self):
        with self.assertRaises(TripIdentityValidationError):
            resolve_trip_for_device(DEVICE_ID, (gardasil_trip(origin=""),), (assignment(),))

    def test_missing_destination_fails_validation(self):
        with self.assertRaises(TripIdentityValidationError):
            resolve_trip_for_device(DEVICE_ID, (gardasil_trip(destination=""),), (assignment(),))

    def test_missing_start_time_fails_validation(self):
        with self.assertRaises(TripIdentityValidationError):
            resolve_trip_for_device(DEVICE_ID, (gardasil_trip(start_time=None),), (assignment(),))

    def test_naive_start_time_fails_validation(self):
        with self.assertRaises(TripIdentityValidationError):
            resolve_trip_for_device(
                DEVICE_ID,
                (gardasil_trip(start_time=datetime(2026, 1, 1, 8, 0)),),
                (assignment(),),
            )

    def test_completed_trip_requires_aware_completed_at(self):
        completed_at = START_TIME + timedelta(hours=1)
        self.assertEqual(
            validate_trip_identity(
                gardasil_trip(
                    status=TripStatus.COMPLETED,
                    completed_at=completed_at,
                )
            ).completed_at,
            completed_at,
        )

    def test_planned_trip_rejects_completed_at(self):
        with self.assertRaises(TripIdentityValidationError):
            validate_trip_identity(
                gardasil_trip(
                    status=TripStatus.PLANNED,
                    completed_at=START_TIME + timedelta(hours=1),
                )
            )

    def test_active_trip_rejects_completed_at(self):
        with self.assertRaises(TripIdentityValidationError):
            validate_trip_identity(
                gardasil_trip(completed_at=START_TIME + timedelta(hours=1))
            )

    def test_cancelled_trip_rejects_completed_at(self):
        with self.assertRaises(TripIdentityValidationError):
            validate_trip_identity(
                gardasil_trip(
                    status=TripStatus.CANCELLED,
                    completed_at=START_TIME + timedelta(hours=1),
                )
            )

    def test_completed_trip_rejects_missing_completed_at(self):
        with self.assertRaises(TripIdentityValidationError):
            validate_trip_identity(gardasil_trip(status=TripStatus.COMPLETED))

    def test_completed_trip_rejects_naive_completed_at(self):
        with self.assertRaises(TripIdentityValidationError):
            validate_trip_identity(
                gardasil_trip(
                    status=TripStatus.COMPLETED,
                    completed_at=datetime(2026, 1, 1, 9, 0),
                )
            )

    def test_completed_trip_rejects_completed_at_before_start(self):
        with self.assertRaises(TripIdentityValidationError):
            validate_trip_identity(
                gardasil_trip(
                    status=TripStatus.COMPLETED,
                    completed_at=START_TIME - timedelta(seconds=1),
                )
            )

    def test_missing_presentation_fails_validation(self):
        with self.assertRaises(TripIdentityValidationError):
            resolve_trip_for_device(DEVICE_ID, (gardasil_trip(presentation=""),), (assignment(),))

    def test_missing_state_fails_validation(self):
        with self.assertRaises(TripIdentityValidationError):
            resolve_trip_for_device(DEVICE_ID, (gardasil_trip(state=""),), (assignment(),))

    def test_assignment_lot_trip_id_mismatch_fails(self):
        with self.assertRaises(DeviceAssignmentMismatchError):
            resolve_trip_for_device(
                DEVICE_ID,
                (gardasil_trip(),),
                (assignment(lot_trip_id="wrong-lot-trip"),),
            )

    def test_assignment_trip_id_mismatch_fails(self):
        with self.assertRaises(DeviceAssignmentMismatchError):
            resolve_trip_for_device(
                DEVICE_ID,
                (gardasil_trip(),),
                (assignment(trip_id="wrong-trip"),),
            )

    def test_requested_device_and_trip_device_mismatch_fails(self):
        with self.assertRaises(DeviceAssignmentMismatchError):
            resolve_trip_for_device(
                DEVICE_ID,
                (gardasil_trip(device_id="different-device"),),
                (assignment(),),
            )

    def test_assigned_at_must_be_timezone_aware(self):
        with self.assertRaises(DeviceAssignmentValidationError):
            resolve_trip_for_device(
                DEVICE_ID,
                (gardasil_trip(),),
                (assignment(assigned_at=datetime(2026, 1, 1, 7, 30)),),
            )

    def test_valid_gardasil_9_trip_resolves_compatible_rules(self):
        rules = validate_trip_rule_context(gardasil_trip())
        self.assertEqual(len(rules), 3)
        self.assertTrue(all(rule.product_id == GARDASIL_9_PRODUCT_ID for rule in rules))
        self.assertTrue(all(rule.version == GARDASIL_9_SOURCE_VERSION for rule in rules))

    def test_unknown_product_causes_rules_resolution_failure(self):
        with self.assertRaises(ProductRulesNotFoundError):
            validate_trip_rule_context(gardasil_trip(product_id="unknown-product"))

    def test_wrong_presentation_causes_rules_resolution_failure(self):
        with self.assertRaises(ProductRulesNotFoundError):
            validate_trip_rule_context(gardasil_trip(presentation="single-dose-vial-0.5-ml"))

    def test_wrong_state_causes_rules_resolution_failure(self):
        with self.assertRaises(ProductRulesNotFoundError):
            validate_trip_rule_context(gardasil_trip(state="opened"))

    def test_completed_trip_cannot_receive_live_telemetry(self):
        with self.assertRaises(TripNotActiveError):
            resolve_trip_for_device(
                DEVICE_ID,
                (
                    gardasil_trip(
                        status=TripStatus.COMPLETED,
                        completed_at=START_TIME + timedelta(hours=1),
                    ),
                ),
                (assignment(),),
            )

    def test_trip_identity_is_immutable_after_creation(self):
        trip = gardasil_trip()
        with self.assertRaises(FrozenInstanceError):
            trip.product_id = "another-product"

    def test_no_resolver_path_creates_a_trip_implicitly(self):
        trips = []
        with self.assertRaises(AssignmentTripNotFoundError):
            resolve_trip_for_device(DEVICE_ID, trips, (assignment(),))
        self.assertEqual(trips, [])


if __name__ == "__main__":
    unittest.main()
