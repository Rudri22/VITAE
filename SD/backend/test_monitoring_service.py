import unittest
from datetime import timedelta
from pathlib import Path

try:
    from .alerting import InMemoryAlertRepository
    from .monitoring_service import (
        FutureRiskNotConfigured,
        LotTripNotFoundError,
        MonitoringService,
        serialize_future_risk,
    )
    from .operational_service import OperationalTelemetryService
    from .simulator import SIMULATION_LOT_TRIP_ID, build_local_environment
    from .temporal_risk_baseline import BASELINE_MODEL_VERSION, TrainingSourceKind
    from .temporal_risk_calibration import CALIBRATION_METHOD
    from .temporal_risk_examples import TEMPORAL_RISK_FEATURE_VERSION
    from .temporal_risk_inference import (
        SIMULATOR_PERFORMANCE_SCOPE,
        TemporalRiskNotPredicted,
        TemporalRiskNotPredictedReason,
        TemporalRiskPrediction,
    )
except ImportError:
    from alerting import InMemoryAlertRepository
    from monitoring_service import (
        FutureRiskNotConfigured,
        LotTripNotFoundError,
        MonitoringService,
        serialize_future_risk,
    )
    from operational_service import OperationalTelemetryService
    from simulator import SIMULATION_LOT_TRIP_ID, build_local_environment
    from temporal_risk_baseline import BASELINE_MODEL_VERSION, TrainingSourceKind
    from temporal_risk_calibration import CALIBRATION_METHOD
    from temporal_risk_examples import TEMPORAL_RISK_FEATURE_VERSION
    from temporal_risk_inference import (
        SIMULATOR_PERFORMANCE_SCOPE,
        TemporalRiskNotPredicted,
        TemporalRiskNotPredictedReason,
        TemporalRiskPrediction,
    )


class _Predictor:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def predict(self, lot_trip_id):
        self.calls.append(lot_trip_id)
        if self.error is not None:
            raise self.error
        return self.result


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
        self.assertIsInstance(snapshot.future_risk, FutureRiskNotConfigured)
        self.assertEqual(
            serialize_future_risk(snapshot.future_risk),
            {"state": "NOT_CONFIGURED"},
        )
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

    def test_predicted_future_risk_is_additive_and_does_not_change_live_state(self):
        processed = self.operational_service.process(
            self.raw_sample("safe-prediction", 0, 6.0)
        )
        cutoff = processed.processing_result.telemetry_record.timestamp
        prediction = TemporalRiskPrediction(
            prediction_version="temporal-risk-prediction-v1",
            lot_trip_id=SIMULATION_LOT_TRIP_ID,
            trip_id=processed.processing_result.telemetry_record.trip_id,
            cutoff_sample_id="safe-prediction",
            cutoff_at=cutoff,
            horizon_ends_at=cutoff + timedelta(minutes=30),
            prediction_horizon_minutes=30,
            adverse_event_probability=0.23,
            model_version=BASELINE_MODEL_VERSION,
            calibration_method=CALIBRATION_METHOD,
            feature_version=TEMPORAL_RISK_FEATURE_VERSION,
            artifact_manifest_sha256="a" * 64,
            training_source_kind=TrainingSourceKind.APPROVED_SIMULATOR,
            performance_scope=SIMULATOR_PERFORMANCE_SCOPE,
            limitations=("Simulated-only",),
        )
        predictor = _Predictor(prediction)
        monitoring = MonitoringService(
            self.environment.repository,
            self.environment.repository,
            self.alert_repository,
            predictor,
        )
        snapshot = monitoring.get_live_snapshot(SIMULATION_LOT_TRIP_ID)
        document = serialize_future_risk(snapshot.future_risk)
        self.assertIs(
            snapshot.live_state, processed.processing_result.live_state
        )
        self.assertEqual(snapshot.live_state.status.value, "SAFE")
        self.assertEqual(document["state"], "PREDICTED")
        self.assertEqual(document["adverseEventProbability"], 0.23)
        self.assertNotIn("riskPolicy", document)
        self.assertNotIn("riskBand", document)

    def test_not_predicted_reason_is_exposed_without_changing_monitoring(self):
        result = TemporalRiskNotPredicted(
            lot_trip_id=SIMULATION_LOT_TRIP_ID,
            reason_code=TemporalRiskNotPredictedReason.NO_ACCEPTED_TELEMETRY,
            detail="No accepted telemetry prefix exists",
        )
        snapshot = MonitoringService(
            self.environment.repository,
            self.environment.repository,
            self.alert_repository,
            _Predictor(result),
        ).get_live_snapshot(SIMULATION_LOT_TRIP_ID)
        self.assertIsNone(snapshot.live_state)
        self.assertEqual(
            serialize_future_risk(snapshot.future_risk),
            {
                "state": "NOT_PREDICTED",
                "reasonCode": "NO_ACCEPTED_TELEMETRY",
                "detail": "No accepted telemetry prefix exists",
            },
        )

    def test_inference_failure_is_isolated_from_deterministic_snapshot(self):
        processed = self.operational_service.process(
            self.raw_sample("safe-failure", 0, 6.0)
        )
        snapshot = MonitoringService(
            self.environment.repository,
            self.environment.repository,
            self.alert_repository,
            _Predictor(error=RuntimeError("model unavailable")),
        ).get_live_snapshot(SIMULATION_LOT_TRIP_ID)
        self.assertIs(
            snapshot.live_state, processed.processing_result.live_state
        )
        self.assertEqual(snapshot.live_state.status.value, "SAFE")
        self.assertEqual(
            serialize_future_risk(snapshot.future_risk)["reasonCode"],
            "INFERENCE_UNAVAILABLE",
        )

    def test_unknown_trip_is_rejected_before_optional_inference(self):
        predictor = _Predictor(error=AssertionError("must not be called"))
        monitoring = MonitoringService(
            self.environment.repository,
            self.environment.repository,
            self.alert_repository,
            predictor,
        )
        with self.assertRaises(LotTripNotFoundError):
            monitoring.get_live_snapshot("unknown-lot-trip")
        self.assertEqual(predictor.calls, [])

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
