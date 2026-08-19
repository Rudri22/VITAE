import unittest
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch

try:
    from .alerting import InMemoryAlertRepository
    from .monitoring_service import MonitoringService
    from .product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
    )
    from .shipment_registration import V2ShipmentRegistrationService
    from .shipment_access import InMemoryIdentityAccessRepository
    from .storage import (
        DRIVERS,
        SENSORS,
        SHIPMENTS,
        assign_organization_driver,
        create_organization_shipment,
    )
    from . import storage as storage_module
    from .trip_identity import DeviceAssignment, TripIdentity, TripStatus
except ImportError:
    from alerting import InMemoryAlertRepository
    from monitoring_service import MonitoringService
    from product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
    )
    from shipment_registration import V2ShipmentRegistrationService
    from shipment_access import InMemoryIdentityAccessRepository
    from storage import (
        DRIVERS,
        SENSORS,
        SHIPMENTS,
        assign_organization_driver,
        create_organization_shipment,
    )
    import storage as storage_module
    from trip_identity import DeviceAssignment, TripIdentity, TripStatus


class V2ShipmentRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.shipments = deepcopy(SHIPMENTS)
        self.drivers = deepcopy(DRIVERS)
        self.sensors = deepcopy(SENSORS)
        self.repository = InMemoryIdentityAccessRepository()
        self.service = V2ShipmentRegistrationService(self.repository)
        self.user = {
            "organizationId": "hospital-a",
            "name": "Organization Operator",
        }

    def tearDown(self):
        SHIPMENTS.clear()
        SHIPMENTS.update(self.shipments)
        DRIVERS.clear()
        DRIVERS.update(self.drivers)
        SENSORS.clear()
        SENSORS.update(self.sensors)

    def test_success_registers_identity_before_linking_shipment(self):
        shipment, created = create_organization_shipment(
            self.payload(),
            self.user,
            self.service,
        )
        self.assertTrue(created)
        self.assertEqual(shipment["lotTripId"], "lot-trip-ship-dynamic-v2-001")

        trip = self.repository.get_trip_by_lot_trip_id(shipment["lotTripId"])
        self.assertIsNotNone(trip)
        self.assertEqual(trip.trip_id, "trip-ship-dynamic-v2-001")
        self.assertEqual(trip.lot_id, "lot-dynamic-001")
        self.assertEqual(trip.status, TripStatus.PLANNED)
        self.assertEqual(trip.product_rule_version, GARDASIL_9_SOURCE_VERSION)
        self.assertEqual(shipment["tripId"], trip.trip_id)
        self.assertEqual(
            shipment["productRuleVersion"], GARDASIL_9_SOURCE_VERSION
        )
        self.assertEqual(shipment["tripStatus"], "PLANNED")
        assignments = self.repository.get_device_assignments("sensor-cold-12")
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0].lot_trip_id, shipment["lotTripId"])
        self.assertFalse(assignments[0].active)
        self.assertEqual(
            SHIPMENTS[shipment["shipmentId"]]["v2DeviceAssignmentId"],
            assignments[0].assignment_id,
        )
        access = self.repository.get_shipment_access(shipment["lotTripId"])
        self.assertEqual(access.shipment_id, shipment["shipmentId"])
        self.assertEqual(access.organization_id, self.user["organizationId"])
        self.assertEqual(access.driver_id, shipment["driverId"])

    def test_created_identity_is_available_through_monitoring_readback(self):
        shipment, _ = create_organization_shipment(
            self.payload(shipment_id="ship-monitor-readback"),
            self.user,
            self.service,
        )
        monitoring = MonitoringService(
            self.repository,
            self.repository,
            InMemoryAlertRepository(),
        )
        snapshot = monitoring.get_live_snapshot(shipment["lotTripId"])
        self.assertEqual(snapshot.trip_identity.product_id, GARDASIL_9_PRODUCT_ID)
        self.assertEqual(snapshot.trip_identity.status, TripStatus.PLANNED)
        self.assertIsNone(snapshot.live_state)
        self.assertEqual(snapshot.open_alert_count, 0)

    def test_duplicate_submission_is_idempotent(self):
        payload = self.payload(shipment_id="ship-idempotent")
        first, first_created = create_organization_shipment(
            payload,
            self.user,
            self.service,
        )
        second, second_created = create_organization_shipment(
            payload,
            self.user,
            self.service,
        )
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first, second)
        self.assertEqual(
            len(self.repository.get_device_assignments("sensor-cold-12")),
            1,
        )

    def test_invalid_rule_context_leaves_no_shipment_or_identity(self):
        cases = (
            ("productId", "unknown-product"),
            ("presentation", "wrong-presentation"),
            ("state", "opened"),
        )
        for index, (field, value) in enumerate(cases):
            with self.subTest(field=field):
                payload = self.payload(
                    shipment_id=f"ship-invalid-context-{index}",
                    submission_id=f"submission-invalid-context-{index}",
                )
                payload["v2Monitoring"][field] = value
                with self.assertRaises(ValueError):
                    create_organization_shipment(payload, self.user, self.service)
                self.assertNotIn(payload["shipmentId"], SHIPMENTS)
                self.assertIsNone(
                    self.repository.get_trip_by_id(f"trip-{payload['shipmentId']}")
                )
                self.assertEqual(
                    self.repository.get_device_assignments("sensor-cold-12"),
                    (),
                )

    def test_missing_v2_fields_leave_no_shipment(self):
        for index, field in enumerate(
            (
                "productId",
                "presentation",
                "state",
                "lotId",
                "deviceId",
            )
        ):
            with self.subTest(field=field):
                payload = self.payload(
                    shipment_id=f"ship-missing-v2-{index}",
                    submission_id=f"submission-missing-v2-{index}",
                )
                del payload["v2Monitoring"][field]
                with self.assertRaises(ValueError):
                    create_organization_shipment(payload, self.user, self.service)
                self.assertNotIn(payload["shipmentId"], SHIPMENTS)

    def test_client_cannot_supply_product_rule_version(self):
        payload = self.payload(shipment_id="ship-client-version")
        payload["v2Monitoring"]["productRuleVersion"] = "client-version"
        with self.assertRaisesRegex(ValueError, "backend-managed"):
            create_organization_shipment(payload, self.user, self.service)
        self.assertNotIn(payload["shipmentId"], SHIPMENTS)
        self.assertIsNone(
            self.repository.get_trip_by_id("trip-ship-client-version")
        )

    def test_device_mismatch_leaves_legacy_resources_unchanged(self):
        payload = self.payload(shipment_id="ship-device-mismatch")
        payload["v2Monitoring"]["deviceId"] = "different-device"
        driver_before = deepcopy(DRIVERS["driver-aya"])
        sensor_before = deepcopy(SENSORS["sensor-cold-12"])
        with self.assertRaises(ValueError):
            create_organization_shipment(payload, self.user, self.service)
        self.assertNotIn(payload["shipmentId"], SHIPMENTS)
        self.assertEqual(DRIVERS["driver-aya"], driver_before)
        self.assertEqual(SENSORS["sensor-cold-12"], sensor_before)

    def test_product_context_must_match_legacy_product(self):
        payload = self.payload(shipment_id="ship-product-mismatch")
        payload["productName"] = "Unrelated vaccine"
        with self.assertRaisesRegex(ValueError, "must match"):
            create_organization_shipment(payload, self.user, self.service)
        self.assertNotIn(payload["shipmentId"], SHIPMENTS)
        self.assertIsNone(
            self.repository.get_trip_by_id("trip-ship-product-mismatch")
        )

    def test_planned_device_reservation_rejects_without_partial_trip(self):
        existing_trip = TripIdentity(
            trip_id="trip-existing",
            lot_trip_id="lot-trip-existing",
            lot_id="lot-existing",
            device_id="sensor-cold-12",
            product_id=GARDASIL_9_PRODUCT_ID,
            presentation=GARDASIL_9_PRESENTATION,
            state=GARDASIL_9_STATE,
            product_rule_version=GARDASIL_9_SOURCE_VERSION,
            origin="Origin",
            destination="Destination",
            start_time=datetime(2026, 8, 19, tzinfo=timezone.utc),
            status=TripStatus.PLANNED,
        )
        existing_assignment = DeviceAssignment(
            assignment_id="assignment-existing",
            device_id="sensor-cold-12",
            trip_id=existing_trip.trip_id,
            lot_trip_id=existing_trip.lot_trip_id,
            assigned_at=existing_trip.start_time,
            active=False,
        )
        self.repository.register_trip_and_assignment(
            existing_trip,
            existing_assignment,
        )

        payload = self.payload(shipment_id="ship-device-conflict")
        with self.assertRaises(ValueError):
            create_organization_shipment(payload, self.user, self.service)
        self.assertNotIn(payload["shipmentId"], SHIPMENTS)
        self.assertIsNone(
            self.repository.get_trip_by_id("trip-ship-device-conflict")
        )

    def test_legacy_write_failure_compensates_v2_and_legacy_mutations(self):
        payload = self.payload(shipment_id="ship-write-failure")
        driver_before = deepcopy(DRIVERS["driver-aya"])
        sensor_before = deepcopy(SENSORS["sensor-cold-12"])

        def fail_after_partial_write(shipment, driver, sensor):
            SHIPMENTS[shipment["shipmentId"]] = shipment
            driver["status"] = "assigned"
            sensor["shipmentId"] = shipment["shipmentId"]
            raise RuntimeError("simulated legacy persistence failure")

        with patch.object(
            storage_module,
            "_commit_organization_shipment",
            side_effect=fail_after_partial_write,
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated legacy"):
                create_organization_shipment(payload, self.user, self.service)

        self.assertNotIn(payload["shipmentId"], SHIPMENTS)
        self.assertEqual(DRIVERS["driver-aya"], driver_before)
        self.assertEqual(SENSORS["sensor-cold-12"], sensor_before)
        self.assertIsNone(
            self.repository.get_trip_by_id("trip-ship-write-failure")
        )
        self.assertEqual(
            self.repository.get_device_assignments("sensor-cold-12"),
            (),
        )
        self.assertIsNone(
            self.repository.get_shipment_access("lot-trip-ship-write-failure")
        )

    def test_driver_reassignment_updates_authoritative_shipment_access(self):
        shipment, _ = create_organization_shipment(
            self.payload(shipment_id="ship-driver-transition"),
            self.user,
            self.service,
        )
        DRIVERS["driver-replacement"] = {
            **deepcopy(DRIVERS["driver-aya"]),
            "driverId": "driver-replacement",
            "name": "Replacement Driver",
            "status": "available",
        }
        updated = assign_organization_driver(
            self.user["organizationId"],
            shipment["shipmentId"],
            {"driverId": "driver-replacement"},
            self.repository,
        )
        access = self.repository.get_shipment_access(shipment["lotTripId"])
        self.assertEqual(updated["driverId"], "driver-replacement")
        self.assertEqual(access.driver_id, "driver-replacement")

    def test_failed_driver_reassignment_compensates_access_and_legacy(self):
        shipment, _ = create_organization_shipment(
            self.payload(shipment_id="ship-driver-rollback"),
            self.user,
            self.service,
        )
        DRIVERS["driver-replacement"] = {
            **deepcopy(DRIVERS["driver-aya"]),
            "driverId": "driver-replacement",
            "name": "Replacement Driver",
            "status": "available",
        }
        with patch.object(
            storage_module,
            "now_iso",
            side_effect=RuntimeError("simulated legacy driver write failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated legacy"):
                assign_organization_driver(
                    self.user["organizationId"],
                    shipment["shipmentId"],
                    {"driverId": "driver-replacement"},
                    self.repository,
                )
        stored = SHIPMENTS[shipment["shipmentId"]]
        access = self.repository.get_shipment_access(shipment["lotTripId"])
        self.assertEqual(stored["driverId"], "driver-aya")
        self.assertEqual(access.driver_id, "driver-aya")

    def test_legacy_creation_does_not_register_v2_identity(self):
        payload = self.payload(shipment_id="ship-legacy-only")
        del payload["v2Monitoring"]
        shipment, created = create_organization_shipment(payload, self.user)
        self.assertTrue(created)
        self.assertIsNone(shipment["lotTripId"])
        self.assertIsNone(
            self.repository.get_trip_by_id("trip-ship-legacy-only")
        )

    def test_explicitly_disabled_v2_creation_remains_legacy_only(self):
        payload = self.payload(shipment_id="ship-v2-disabled")
        payload["v2Monitoring"] = {"enabled": False}
        shipment, _ = create_organization_shipment(
            payload,
            self.user,
            self.service,
        )
        self.assertIsNone(shipment["lotTripId"])

    def test_requested_v2_requires_registration_service(self):
        payload = self.payload(shipment_id="ship-no-registration-service")
        with self.assertRaisesRegex(ValueError, "registration is unavailable"):
            create_organization_shipment(payload, self.user)
        self.assertNotIn(payload["shipmentId"], SHIPMENTS)

    def test_atomic_repository_registration_rejects_mismatch_without_trip(self):
        trip = self.trip("trip-atomic", "lot-trip-atomic")
        assignment = DeviceAssignment(
            assignment_id="assignment-atomic",
            device_id=trip.device_id,
            trip_id="wrong-trip",
            lot_trip_id=trip.lot_trip_id,
            assigned_at=trip.start_time,
            active=True,
        )
        with self.assertRaises(ValueError):
            self.repository.register_trip_and_assignment(trip, assignment)
        self.assertIsNone(self.repository.get_trip_by_id(trip.trip_id))
        self.assertEqual(
            self.repository.get_device_assignments(trip.device_id),
            (),
        )

    def payload(
        self,
        *,
        shipment_id="ship-dynamic-v2-001",
        submission_id="submission-dynamic-v2-001",
    ):
        return {
            "shipmentId": shipment_id,
            "submissionId": submission_id,
            "productCategory": "Vaccines",
            "productName": "GARDASIL 9",
            "quantity": 24,
            "unit": "doses",
            "originFacilityId": "facility-a-central",
            "destinationFacilityId": "facility-a-receiving",
            "driverId": "driver-aya",
            "vehicleId": "van-12",
            "sensorId": "sensor-cold-12",
            "departureAt": "2026-08-20T08:00:00Z",
            "expectedArrival": "2026-08-20T10:00:00Z",
            "v2Monitoring": {
                "enabled": True,
                "productId": GARDASIL_9_PRODUCT_ID,
                "presentation": GARDASIL_9_PRESENTATION,
                "state": GARDASIL_9_STATE,
                "lotId": "lot-dynamic-001",
                "deviceId": "sensor-cold-12",
            },
        }

    def trip(self, trip_id, lot_trip_id):
        return TripIdentity(
            trip_id=trip_id,
            lot_trip_id=lot_trip_id,
            lot_id="lot-atomic",
            device_id="sensor-atomic",
            product_id=GARDASIL_9_PRODUCT_ID,
            presentation=GARDASIL_9_PRESENTATION,
            state=GARDASIL_9_STATE,
            product_rule_version=GARDASIL_9_SOURCE_VERSION,
            origin="Origin",
            destination="Destination",
            start_time=datetime(2026, 8, 19, tzinfo=timezone.utc),
            status=TripStatus.PLANNED,
        )


if __name__ == "__main__":
    unittest.main()
