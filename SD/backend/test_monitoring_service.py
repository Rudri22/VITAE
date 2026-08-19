import unittest
from datetime import timedelta
from pathlib import Path

try:
    from .alerting import InMemoryAlertRepository
    from .monitoring_service import LotTripNotFoundError, MonitoringService
    from .operational_service import OperationalTelemetryService
    from .simulator import SIMULATION_LOT_TRIP_ID, build_local_environment
except ImportError:
    from alerting import InMemoryAlertRepository
    from monitoring_service import LotTripNotFoundError, MonitoringService
    from operational_service import OperationalTelemetryService
    from simulator import SIMULATION_LOT_TRIP_ID, build_local_environment


class MonitoringServiceTests(unittest.TestCase):
    def setUp(self):
        self.environment = build_local_environment()
        self.alert_repository = InMemoryAlertRepository()
        self.operational_service = OperationalTelemetryService(
            self.environment.processor,
            self.alert_repository,
        )
        self.monitoring_service = MonitoringService(
            self.environment.repository,
            self.environment.repository,
            self.alert_repository,
        )

    def test_before_telemetry_snapshot_has_trip_and_no_state_or_alerts(self):
        snapshot = self.monitoring_service.get_live_snapshot(
            SIMULATION_LOT_TRIP_ID
        )
        self.assertEqual(snapshot.trip_identity.lot_trip_id, SIMULATION_LOT_TRIP_ID)
        self.assertIsNone(snapshot.live_state)
        self.assertEqual(snapshot.open_alert_count, 0)
        self.assertIsNone(snapshot.latest_alert)
        self.assertEqual(
            self.monitoring_service.list_alerts(SIMULATION_LOT_TRIP_ID),
            (),
        )

    def test_reads_same_live_state_committed_by_operational_service(self):
        result = self.operational_service.process(self.raw_sample("safe", 0, 6.0))
        self.assertIs(
            self.monitoring_service.get_live_snapshot(
                SIMULATION_LOT_TRIP_ID
            ).live_state,
            result.processing_result.live_state,
        )

    def test_reads_persisted_alerts_newest_first(self):
        self.operational_service.process(self.raw_sample("safe", 0, 6.0))
        monitor = self.operational_service.process(
            self.raw_sample("monitor", 10, 9.0)
        )
        at_risk = self.operational_service.process(
            self.raw_sample("at-risk", 2170, 9.0)
        )
        alerts = self.monitoring_service.list_alerts(SIMULATION_LOT_TRIP_ID)
        self.assertEqual(alerts, (at_risk.alert, monitor.alert))
        snapshot = self.monitoring_service.get_live_snapshot(
            SIMULATION_LOT_TRIP_ID
        )
        self.assertEqual(snapshot.open_alert_count, 2)
        self.assertIs(snapshot.latest_alert, at_risk.alert)

    def test_acknowledged_alert_is_active_but_resolved_alert_is_history_only(self):
        self.operational_service.process(self.raw_sample("safe", 0, 6.0))
        result = self.operational_service.process(
            self.raw_sample("monitor", 10, 9.0)
        )
        alert = result.alert
        acknowledged = self.alert_repository.acknowledge_alert(
            alert.alert_id,
            actor_id="driver-user",
            acknowledged_at=alert.detected_at + timedelta(minutes=1),
        )
        acknowledged_snapshot = self.monitoring_service.get_live_snapshot(
            SIMULATION_LOT_TRIP_ID
        )
        self.assertEqual(acknowledged_snapshot.open_alert_count, 1)
        self.assertIs(acknowledged_snapshot.latest_alert, acknowledged)

        resolved = self.alert_repository.resolve_alert(
            alert.alert_id,
            actor_id="organization-user",
            resolved_at=alert.detected_at + timedelta(minutes=2),
            resolution_note="Reviewed and closed",
        )
        resolved_snapshot = self.monitoring_service.get_live_snapshot(
            SIMULATION_LOT_TRIP_ID
        )
        self.assertEqual(resolved_snapshot.open_alert_count, 0)
        self.assertIsNone(resolved_snapshot.latest_alert)
        self.assertEqual(
            self.monitoring_service.list_alerts(SIMULATION_LOT_TRIP_ID),
            (resolved,),
        )

    def test_reads_are_side_effect_free(self):
        self.monitoring_service.get_live_snapshot(SIMULATION_LOT_TRIP_ID)
        self.monitoring_service.list_alerts(SIMULATION_LOT_TRIP_ID)
        self.assertEqual(
            self.environment.repository.get_telemetry_history(
                SIMULATION_LOT_TRIP_ID
            ),
            (),
        )

    def test_lot_trip_id_is_required(self):
        with self.assertRaises(ValueError):
            self.monitoring_service.get_live_snapshot(" ")
        with self.assertRaises(ValueError):
            self.monitoring_service.list_alerts("")

    def test_unknown_lot_trip_fails_for_both_read_paths(self):
        with self.assertRaises(LotTripNotFoundError):
            self.monitoring_service.get_live_snapshot("unknown-lot-trip")
        with self.assertRaises(LotTripNotFoundError):
            self.monitoring_service.list_alerts("unknown-lot-trip")

    def test_monitoring_has_no_rules_ml_or_legacy_storage_dependencies(self):
        source = Path(__file__).with_name("monitoring_service.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("product_rules", source)
        self.assertNotIn("ProductRule", source)
        self.assertNotIn("evaluate_status", source)
        self.assertNotIn("ml_client", source)
        self.assertNotIn("storage", source)
        self.assertNotIn("shipment", source.lower())

    def raw_sample(self, sample_id, elapsed_minutes, temperature):
        timestamp = self.environment.start_time.isoformat()
        if elapsed_minutes:
            timestamp = (
                self.environment.start_time + timedelta(minutes=elapsed_minutes)
            ).isoformat()
        return {
            "sample_id": sample_id,
            "device_id": self.environment.device_id,
            "timestamp": timestamp,
            "temperature": temperature,
        }


if __name__ == "__main__":
    unittest.main()
