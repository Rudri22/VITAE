import unittest
from datetime import datetime, timezone

try:
    from .alert_lifecycle_service import (
        AlertActor,
        AlertActorRole,
        AlertLifecycleAccessDeniedError,
        AlertLifecycleService,
    )
    from .alerting import (
        Alert,
        AlertNotFoundError,
        AlertSeverity,
        AlertStatus,
        AlertTransitionError,
        AlertType,
        InMemoryAlertRepository,
    )
    from .risk_rules import ApplicationStatus
except ImportError:
    from alert_lifecycle_service import (
        AlertActor,
        AlertActorRole,
        AlertLifecycleAccessDeniedError,
        AlertLifecycleService,
    )
    from alerting import (
        Alert,
        AlertNotFoundError,
        AlertSeverity,
        AlertStatus,
        AlertTransitionError,
        AlertType,
        InMemoryAlertRepository,
    )
    from risk_rules import ApplicationStatus


DETECTED_AT = datetime(2026, 8, 19, 18, 10, tzinfo=timezone.utc)
COMMAND_AT = datetime(2026, 8, 19, 18, 15, tzinfo=timezone.utc)


class AlertLifecycleServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryAlertRepository()
        self.repository.save_alert(_alert())
        self.service = AlertLifecycleService(
            self.repository,
            lambda lot_trip_id: _access(lot_trip_id),
            clock=lambda: COMMAND_AT,
        )
        self.organization = AlertActor(
            actor_id="organization-user",
            role=AlertActorRole.ORGANIZATION,
            organization_id="organization-a",
        )
        self.driver = AlertActor(
            actor_id="driver-user",
            role=AlertActorRole.DRIVER,
            organization_id="organization-a",
            driver_id="driver-a",
        )

    def test_organization_can_acknowledge_owned_alert(self):
        updated = self.service.acknowledge(
            "lot-trip-v2", "alert-v2", self.organization
        )
        self.assertEqual(updated.status, AlertStatus.ACKNOWLEDGED)
        self.assertEqual(updated.acknowledged_by, "organization-user")
        self.assertEqual(updated.acknowledged_at, COMMAND_AT)

    def test_assigned_driver_can_acknowledge_alert(self):
        updated = self.service.acknowledge(
            "lot-trip-v2", "alert-v2", self.driver
        )
        self.assertEqual(updated.status, AlertStatus.ACKNOWLEDGED)
        self.assertEqual(updated.acknowledged_by, "driver-user")

    def test_organization_and_driver_can_record_actions(self):
        first = self.service.record_action(
            "lot-trip-v2",
            "alert-v2",
            "Organization contacted the driver",
            self.organization,
        )
        second = self.service.record_action(
            "lot-trip-v2",
            "alert-v2",
            "Driver inspected the cooling unit",
            self.driver,
        )
        self.assertEqual(len(first.actions), 1)
        self.assertEqual(len(second.actions), 2)
        self.assertEqual(second.actions[-1].actor_id, "driver-user")

    def test_only_organization_can_resolve(self):
        with self.assertRaises(AlertLifecycleAccessDeniedError):
            self.service.resolve(
                "lot-trip-v2", "alert-v2", "Conditions restored", self.driver
            )
        updated = self.service.resolve(
            "lot-trip-v2",
            "alert-v2",
            "Conditions restored and product reviewed",
            self.organization,
        )
        self.assertEqual(updated.status, AlertStatus.RESOLVED)
        self.assertEqual(updated.resolved_by, "organization-user")
        self.assertEqual(updated.resolved_at, COMMAND_AT)

    def test_wrong_organization_is_denied(self):
        actor = AlertActor(
            actor_id="other-organization-user",
            role=AlertActorRole.ORGANIZATION,
            organization_id="organization-b",
        )
        with self.assertRaises(AlertLifecycleAccessDeniedError):
            self.service.acknowledge("lot-trip-v2", "alert-v2", actor)

    def test_unassigned_driver_is_denied(self):
        actor = AlertActor(
            actor_id="other-driver-user",
            role=AlertActorRole.DRIVER,
            organization_id="organization-a",
            driver_id="driver-b",
        )
        with self.assertRaises(AlertLifecycleAccessDeniedError):
            self.service.record_action(
                "lot-trip-v2", "alert-v2", "Attempted action", actor
            )

    def test_alert_without_unique_legacy_link_fails_closed(self):
        service = AlertLifecycleService(
            self.repository,
            lambda _lot_trip_id: None,
            clock=lambda: COMMAND_AT,
        )
        with self.assertRaises(AlertLifecycleAccessDeniedError):
            service.acknowledge("lot-trip-v2", "alert-v2", self.organization)

    def test_unknown_alert_fails(self):
        with self.assertRaises(AlertNotFoundError):
            self.service.acknowledge(
                "lot-trip-v2", "missing-alert", self.organization
            )

    def test_lot_trip_scope_mismatch_fails_as_not_found(self):
        with self.assertRaises(AlertNotFoundError):
            self.service.get_alert(
                "different-lot-trip",
                "alert-v2",
                self.organization,
            )

    def test_authorized_list_and_detail_preserve_history(self):
        alerts = self.service.list_alerts("lot-trip-v2", self.organization)
        detail = self.service.get_alert(
            "lot-trip-v2", "alert-v2", self.driver
        )
        self.assertEqual(alerts, (detail,))

    def test_repository_transition_rules_remain_authoritative(self):
        self.service.acknowledge(
            "lot-trip-v2", "alert-v2", self.organization
        )
        with self.assertRaises(AlertTransitionError):
            self.service.acknowledge(
                "lot-trip-v2", "alert-v2", self.organization
            )

    def test_server_clock_must_be_timezone_aware(self):
        service = AlertLifecycleService(
            self.repository,
            lambda lot_trip_id: _access(lot_trip_id),
            clock=lambda: datetime(2026, 8, 19, 18, 15),
        )
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            service.acknowledge("lot-trip-v2", "alert-v2", self.organization)


def _access(lot_trip_id):
    return {
        "shipmentId": "shipment-v2",
        "lotTripId": lot_trip_id,
        "organizationId": "organization-a",
        "driverId": "driver-a",
    }


def _alert():
    return Alert(
        alert_id="alert-v2",
        alert_type=AlertType.EXCURSION_MONITOR,
        severity=AlertSeverity.INFO,
        status=AlertStatus.OPEN,
        trip_id="trip-v2",
        lot_trip_id="lot-trip-v2",
        device_id="device-v2",
        sample_id="sample-v2",
        source_status=ApplicationStatus.MONITOR,
        reason_code="PERMITTED_EXCURSION",
        active_rule_id="rule-v2",
        message="Monitor the excursion",
        recommended_action="Continue monitoring",
        detected_at=DETECTED_AT,
        updated_at=DETECTED_AT,
    )


if __name__ == "__main__":
    unittest.main()
