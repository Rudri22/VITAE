import unittest
from datetime import datetime, timezone

try:
    from .alert_lifecycle_service import (
        AlertActor,
        AlertActorRole,
        AlertLifecycleService,
    )
    from .alerting import AlertStatus, InMemoryAlertRepository
    from .local_demo_sequence import DEMO_STEPS, LocalDemoSequence
    from .monitoring_service import MonitoringService
    from .operational_service import OperationalTelemetryService
    from .shipment_access import InMemoryIdentityAccessRepository, ShipmentAccess
    from .telemetry_http import TelemetryHttpAdapter
    from .telemetry_processor import TelemetryProcessor
    from .trip_identity import DeviceAssignment, TripIdentity, TripStatus
    from .product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
    )
except ImportError:
    from alert_lifecycle_service import AlertActor, AlertActorRole, AlertLifecycleService
    from alerting import AlertStatus, InMemoryAlertRepository
    from local_demo_sequence import DEMO_STEPS, LocalDemoSequence
    from monitoring_service import MonitoringService
    from operational_service import OperationalTelemetryService
    from shipment_access import InMemoryIdentityAccessRepository, ShipmentAccess
    from telemetry_http import TelemetryHttpAdapter
    from telemetry_processor import TelemetryProcessor
    from trip_identity import DeviceAssignment, TripIdentity, TripStatus
    from product_rules import GARDASIL_9_PRESENTATION, GARDASIL_9_PRODUCT_ID, GARDASIL_9_SOURCE_VERSION, GARDASIL_9_STATE


class LocalDemoSequenceTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 19, tzinfo=timezone.utc)
        self.repository = InMemoryIdentityAccessRepository()
        self.alerts = InMemoryAlertRepository()
        self.trip = TripIdentity(
            trip_id="demo-trip",
            lot_trip_id="demo-lot-trip",
            lot_id="demo-lot",
            device_id="demo-device",
            product_id=GARDASIL_9_PRODUCT_ID,
            presentation=GARDASIL_9_PRESENTATION,
            state=GARDASIL_9_STATE,
            product_rule_version=GARDASIL_9_SOURCE_VERSION,
            origin="Origin",
            destination="Destination",
            start_time=self.start,
            status=TripStatus.ACTIVE,
        )
        self.assignment = DeviceAssignment(
            assignment_id="demo-assignment",
            device_id=self.trip.device_id,
            trip_id=self.trip.trip_id,
            lot_trip_id=self.trip.lot_trip_id,
            assigned_at=self.start,
            active=True,
        )
        self.repository.register_trip_assignment_and_access(
            self.trip,
            self.assignment,
            ShipmentAccess("demo-shipment", self.trip.lot_trip_id, "demo-org", "demo-driver"),
        )
        processor = TelemetryProcessor(self.repository, self.repository)
        telemetry = TelemetryHttpAdapter(OperationalTelemetryService(processor, self.alerts))
        monitoring = MonitoringService(self.repository, self.repository, self.alerts)
        actor = AlertActor("demo-user", AlertActorRole.ORGANIZATION, organization_id="demo-org")
        lifecycle = AlertLifecycleService(
            self.alerts,
            lambda lot_trip_id: {
                "shipmentId": "demo-shipment",
                "lotTripId": lot_trip_id,
                "organizationId": "demo-org",
                "driverId": "demo-driver",
            },
            clock=lambda: datetime(2026, 8, 25, tzinfo=timezone.utc),
        )
        self.sequence = LocalDemoSequence(
            telemetry,
            monitoring,
            lifecycle,
            actor,
            lot_trip_id=self.trip.lot_trip_id,
            device_id=self.trip.device_id,
            trip_started_at=self.trip.start_time,
            complete_shipment=lambda: self.repository.complete_trip(
                self.trip.trip_id,
                self.assignment.assignment_id,
                completed_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
            ),
            clock=lambda: datetime(2026, 8, 25, tzinfo=timezone.utc),
        )
        self.monitoring = monitoring

    def test_sequence_uses_real_status_ladder_intervention_recovery_and_completion(self):
        statuses = []
        results = []
        for _ in range(6):
            result = self.sequence.advance()
            self.assertTrue(result.telemetry_response["telemetryAccepted"])
            results.append(result)
            statuses.append(self.monitoring.get_live_snapshot(self.trip.lot_trip_id).live_state.status.value)
        self.assertEqual(
            statuses,
            ["SAFE", "MONITOR", "MONITOR", "AT_RISK", "CRITICAL", "RULE_VIOLATION"],
        )
        self.assertEqual(results[1].accepted_sample_count, 11)
        self.assertEqual(results[2].accepted_sample_count, 1)
        comparison = self.sequence.status_document()["heroComparison"]
        self.assertEqual(comparison["baseline"]["currentCondition"], "MONITOR")
        self.assertEqual(comparison["baseline"]["revision"], 12)
        self.assertEqual(comparison["intervene"]["currentCondition"], "MONITOR")
        self.assertEqual(comparison["intervene"]["revision"], 13)

        intervention = self.sequence.advance()
        self.assertEqual(intervention.step.step_id, "intervention")
        changed = self.alerts.get_alert(intervention.affected_alert_ids[0])
        self.assertEqual(changed.status, AlertStatus.ACKNOWLEDGED)
        self.assertEqual(len(changed.actions), 1)
        intervention_alert_id = changed.alert_id

        recovery = self.sequence.advance()
        snapshot = self.monitoring.get_live_snapshot(self.trip.lot_trip_id)
        self.assertEqual(snapshot.live_state.status.value, "SAFE")
        self.assertEqual(snapshot.open_alert_count, 0)
        self.assertGreaterEqual(len(recovery.affected_alert_ids), 1)
        self.assertTrue(all(alert.status == AlertStatus.RESOLVED for alert in self.alerts.list_alerts(lot_trip_id=self.trip.lot_trip_id)))
        resolved_intervention = self.alerts.get_alert(intervention_alert_id)
        self.assertEqual(resolved_intervention.status, AlertStatus.RESOLVED)
        self.assertEqual(len(resolved_intervention.actions), 1)

        completion = self.sequence.advance()
        self.assertTrue(completion.complete)
        trip = self.repository.get_trip_by_id(self.trip.trip_id)
        self.assertEqual(trip.status, TripStatus.COMPLETED)
        self.assertIsNotNone(self.repository.get_completed_trip_outcome(self.trip.lot_trip_id))

    def test_status_document_exposes_next_real_step_without_mutating(self):
        before = self.sequence.status_document()
        self.assertEqual(before["nextStep"]["id"], "safe")
        self.assertEqual(before["totalSteps"], len(DEMO_STEPS))
        self.assertIsNone(before["lastResult"])
        self.assertIsNone(self.repository.get_live_state(self.trip.lot_trip_id))

        advanced = self.sequence.advance()
        after = self.sequence.status_document()
        self.assertEqual(after["lastResult"]["step"]["id"], "safe")
        self.assertEqual(after["lastResult"]["stepNumber"], 1)
        self.assertTrue(after["lastResult"]["telemetryResponse"]["telemetryAccepted"])
        self.assertEqual(advanced.step.step_id, "safe")


if __name__ == "__main__":
    unittest.main()
