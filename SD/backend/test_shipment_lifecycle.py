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
        GARDASIL_9_STATE,
    )
    from .shipment_lifecycle import V2ShipmentLifecycleService
    from .shipment_registration import V2ShipmentRegistrationService
    from .shipment_access import InMemoryIdentityAccessRepository
    from .storage import (
        DRIVERS,
        SENSORS,
        SHIPMENTS,
        accept_driver_delivery,
        complete_driver_delivery,
        create_organization_shipment,
        start_driver_delivery,
    )
    from . import storage as storage_module
    from .telemetry_processor import TelemetryProcessor
    from .trip_identity import NoActiveAssignmentError, TripStatus
except ImportError:
    from alerting import InMemoryAlertRepository
    from monitoring_service import MonitoringService
    from product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_STATE,
    )
    from shipment_lifecycle import V2ShipmentLifecycleService
    from shipment_registration import V2ShipmentRegistrationService
    from shipment_access import InMemoryIdentityAccessRepository
    from storage import (
        DRIVERS,
        SENSORS,
        SHIPMENTS,
        accept_driver_delivery,
        complete_driver_delivery,
        create_organization_shipment,
        start_driver_delivery,
    )
    import storage as storage_module
    from telemetry_processor import TelemetryProcessor
    from trip_identity import NoActiveAssignmentError, TripStatus


class V2ShipmentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.shipments = deepcopy(SHIPMENTS)
        self.drivers = deepcopy(DRIVERS)
        self.sensors = deepcopy(SENSORS)
        self.repository = InMemoryIdentityAccessRepository()
        self.registration = V2ShipmentRegistrationService(self.repository)
        self.lifecycle = V2ShipmentLifecycleService(self.repository)
        self.processor = TelemetryProcessor(self.repository, self.repository)
        self.monitoring = MonitoringService(
            self.repository,
            self.repository,
            InMemoryAlertRepository(),
        )
        self.organization_user = {
            "organizationId": "hospital-a",
            "name": "Organization Operator",
        }
        self.driver_user = {
            "userId": "driver-user-aya",
            "name": "Aya Mansour",
        }

    def tearDown(self):
        SHIPMENTS.clear()
        SHIPMENTS.update(self.shipments)
        DRIVERS.clear()
        DRIVERS.update(self.drivers)
        SENSORS.clear()
        SENSORS.update(self.sensors)

    def test_complete_real_lifecycle_gates_telemetry_and_preserves_monitoring(self):
        shipment = self.create_and_accept()
        with self.assertRaises(NoActiveAssignmentError):
            self.processor.process(self.sample("before", "2026-08-20T08:10:00Z"))

        started = self.start(shipment["shipmentId"])
        self.assertEqual(started["status"], "in_transit")
        self.assertEqual(started["tripStatus"], "ACTIVE")
        accepted = self.processor.process(
            self.sample("active", "2026-08-20T08:20:00Z")
        )
        self.assertEqual(accepted.decision.status.value, "SAFE")

        completed = self.complete(shipment["shipmentId"])
        self.assertEqual(completed["status"], "awaiting_verification")
        self.assertEqual(completed["tripStatus"], "COMPLETED")
        with self.assertRaises(NoActiveAssignmentError):
            self.processor.process(self.sample("after", "2026-08-20T08:30:00Z"))

        snapshot = self.monitoring.get_live_snapshot(shipment["lotTripId"])
        self.assertEqual(snapshot.trip_identity.status, TripStatus.COMPLETED)
        self.assertIs(snapshot.live_state, accepted.live_state)
        self.assertEqual(len(self.repository.get_telemetry_history(shipment["lotTripId"])), 1)

    def test_activation_atomically_changes_trip_and_assignment(self):
        shipment = self.create_and_accept()
        self.start(shipment["shipmentId"])
        trip = self.repository.get_trip_by_id(shipment["tripId"])
        assignment = self.assignment(shipment["sensorId"])
        self.assertEqual(trip.status, TripStatus.ACTIVE)
        self.assertTrue(assignment.active)

    def test_completion_atomically_changes_trip_and_assignment(self):
        shipment = self.create_and_accept()
        self.start(shipment["shipmentId"])
        self.complete(shipment["shipmentId"])
        trip = self.repository.get_trip_by_id(shipment["tripId"])
        assignment = self.assignment(shipment["sensorId"])
        self.assertEqual(trip.status, TripStatus.COMPLETED)
        self.assertFalse(assignment.active)

    def test_completion_uses_one_authoritative_timestamp(self):
        shipment = self.create_and_accept()
        self.start(shipment["shipmentId"])
        timestamp = "2026-08-20T10:15:00Z"
        with patch.object(storage_module, "now_iso", return_value=timestamp) as clock:
            completed = self.complete(shipment["shipmentId"])
        trip = self.repository.get_trip_by_id(shipment["tripId"])
        self.assertEqual(
            trip.completed_at,
            datetime(2026, 8, 20, 10, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(completed["arrivalTime"], timestamp)
        clock.assert_called_once_with()

    def test_failed_legacy_start_compensates_v2_transition(self):
        shipment = self.create_and_accept()
        shipment_before = deepcopy(SHIPMENTS[shipment["shipmentId"]])
        driver_before = deepcopy(DRIVERS["driver-aya"])
        with patch.object(
            storage_module,
            "_commit_driver_delivery_start",
            side_effect=RuntimeError("simulated start write failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated start"):
                self.start(shipment["shipmentId"])
        self.assertEqual(SHIPMENTS[shipment["shipmentId"]], shipment_before)
        self.assertEqual(DRIVERS["driver-aya"], driver_before)
        self.assertEqual(
            self.repository.get_trip_by_id(shipment["tripId"]).status,
            TripStatus.PLANNED,
        )
        self.assertFalse(self.assignment(shipment["sensorId"]).active)

    def test_failed_legacy_completion_compensates_v2_transition(self):
        shipment = self.create_and_accept()
        self.start(shipment["shipmentId"])
        shipment_before = deepcopy(SHIPMENTS[shipment["shipmentId"]])
        driver_before = deepcopy(DRIVERS["driver-aya"])
        with patch.object(
            storage_module,
            "_commit_driver_delivery_completion",
            side_effect=RuntimeError("simulated completion write failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated completion"):
                self.complete(shipment["shipmentId"])
        self.assertEqual(SHIPMENTS[shipment["shipmentId"]], shipment_before)
        self.assertEqual(DRIVERS["driver-aya"], driver_before)
        self.assertEqual(
            self.repository.get_trip_by_id(shipment["tripId"]).status,
            TripStatus.ACTIVE,
        )
        self.assertIsNone(
            self.repository.get_trip_by_id(shipment["tripId"]).completed_at
        )
        self.assertTrue(self.assignment(shipment["sensorId"]).active)

    def test_invalid_start_does_not_change_v2_lifecycle(self):
        shipment = self.create_and_accept()
        with self.assertRaisesRegex(ValueError, "pre-trip"):
            start_driver_delivery(
                "driver-aya",
                shipment["shipmentId"],
                {"checks": {}},
                self.driver_user,
                self.lifecycle,
            )
        self.assertEqual(
            self.repository.get_trip_by_id(shipment["tripId"]).status,
            TripStatus.PLANNED,
        )

    def test_invalid_completion_does_not_change_v2_lifecycle(self):
        shipment = self.create_and_accept()
        self.start(shipment["shipmentId"])
        with self.assertRaisesRegex(ValueError, "Confirm arrival"):
            complete_driver_delivery(
                "driver-aya",
                shipment["shipmentId"],
                {},
                self.driver_user,
                self.lifecycle,
            )
        self.assertEqual(
            self.repository.get_trip_by_id(shipment["tripId"]).status,
            TripStatus.ACTIVE,
        )
        self.assertTrue(self.assignment(shipment["sensorId"]).active)

    def test_completed_device_can_be_reserved_for_new_planned_trip(self):
        shipment = self.create_and_accept()
        self.start(shipment["shipmentId"])
        self.complete(shipment["shipmentId"])
        DRIVERS["driver-aya"]["status"] = "available"
        second, created = create_organization_shipment(
            self.payload("ship-reuse", "submission-reuse", "lot-reuse"),
            self.organization_user,
            self.registration,
        )
        self.assertTrue(created)
        self.assertEqual(second["tripStatus"], "PLANNED")
        assignments = self.repository.get_device_assignments("sensor-cold-12")
        self.assertEqual(len(assignments), 2)
        self.assertFalse(assignments[0].active)
        self.assertFalse(assignments[1].active)

    def test_legacy_only_start_and_completion_remain_supported(self):
        payload = self.payload("ship-legacy-life", "submission-legacy-life", "lot-unused")
        del payload["v2Monitoring"]
        shipment, _ = create_organization_shipment(payload, self.organization_user)
        accept_driver_delivery("driver-aya", shipment["shipmentId"], self.driver_user)
        started = start_driver_delivery(
            "driver-aya",
            shipment["shipmentId"],
            self.start_payload(),
            self.driver_user,
        )
        self.assertEqual(started["status"], "in_transit")
        completed = self.complete(shipment["shipmentId"], lifecycle=None)
        self.assertEqual(completed["status"], "awaiting_verification")

    def create_and_accept(self):
        shipment, _ = create_organization_shipment(
            self.payload(),
            self.organization_user,
            self.registration,
        )
        accept_driver_delivery("driver-aya", shipment["shipmentId"], self.driver_user)
        return shipment

    def start(self, shipment_id):
        return start_driver_delivery(
            "driver-aya",
            shipment_id,
            self.start_payload(),
            self.driver_user,
            self.lifecycle,
        )

    def complete(self, shipment_id, lifecycle="default"):
        shipment = SHIPMENTS[shipment_id]
        return complete_driver_delivery(
            "driver-aya",
            shipment_id,
            {
                "confirmedArrival": True,
                "receiverName": "Receiving Pharmacist",
                "receiverSignature": "data:image/png;base64,c2lnbmF0dXJl",
                "destinationVerificationCode": shipment[
                    "destinationVerificationCode"
                ],
            },
            self.driver_user,
            self.lifecycle if lifecycle == "default" else lifecycle,
        )

    def assignment(self, device_id):
        assignments = self.repository.get_device_assignments(device_id)
        return assignments[-1]

    @staticmethod
    def start_payload():
        return {
            "checks": {
                "shipmentCollected": True,
                "containerClosed": True,
                "sensorConnected": True,
                "coolingActive": True,
                "vehicleReady": True,
            }
        }

    @staticmethod
    def sample(sample_id, timestamp):
        return {
            "sample_id": sample_id,
            "device_id": "sensor-cold-12",
            "timestamp": timestamp,
            "temperature": 6.0,
        }

    @staticmethod
    def payload(
        shipment_id="ship-lifecycle-v2",
        submission_id="submission-lifecycle-v2",
        lot_id="lot-lifecycle-v2",
    ):
        return {
            "shipmentId": shipment_id,
            "submissionId": submission_id,
            "productCategory": "Vaccines",
            "productName": "GARDASIL 9",
            "quantity": 24,
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
                "lotId": lot_id,
                "deviceId": "sensor-cold-12",
            },
        }


if __name__ == "__main__":
    unittest.main()
