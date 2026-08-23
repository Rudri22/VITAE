import json
import unittest
from dataclasses import replace
from datetime import timedelta

try:
    from .pilot_validation import (
        PilotPredictionObservation,
        PilotSessionRecord,
        build_pilot_validation_rows,
        compare_simulator_and_pilot,
        evaluate_pilot_rows,
        pilot_session_document,
        pilot_session_json,
        pilot_validation_csv,
    )
    from .risk_rules import ApplicationStatus
    from .telemetry import TelemetrySource
    from .test_temporal_risk_examples import CONTRACT_TIME, completed_source_record
except ImportError:
    from pilot_validation import PilotPredictionObservation, PilotSessionRecord, build_pilot_validation_rows, compare_simulator_and_pilot, evaluate_pilot_rows, pilot_session_document, pilot_session_json, pilot_validation_csv
    from risk_rules import ApplicationStatus
    from telemetry import TelemetrySource
    from test_temporal_risk_examples import CONTRACT_TIME, completed_source_record


def _session():
    original = completed_source_record()
    real = replace(
        original,
        telemetry_records=tuple(replace(record, source=TelemetrySource.REAL_DEVICE) for record in original.telemetry_records),
    )
    first = real.telemetry_records[0]
    prediction = PilotPredictionObservation(
        real.lot_trip_id, first.timestamp, first.sample_id, ApplicationStatus.SAFE,
        0.72, "HIGH", "JOURNEY_AWARE_MODEL", 60.0, "INTERVENE",
    )
    return PilotSessionRecord(real, CONTRACT_TIME + timedelta(minutes=60), (prediction,))


class PilotValidationTests(unittest.TestCase):
    def test_prediction_and_later_observed_outcome_are_separate(self):
        rows = build_pilot_validation_rows(_session(), anonymization_salt="local-secret-salt")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].observed_adverse)
        self.assertEqual(rows[0].observed_adverse_at, CONTRACT_TIME + timedelta(minutes=20))
        self.assertEqual(rows[0].telemetry_source, TelemetrySource.REAL_DEVICE)
        self.assertEqual(rows[0].predicted_category, "HIGH")

    def test_outcome_is_bounded_by_declared_prediction_horizon(self):
        session = _session()
        prediction = replace(session.predictions[0], horizon_minutes=10.0)
        rows = build_pilot_validation_rows(
            replace(session, predictions=(prediction,)),
            anonymization_salt="local-secret-salt",
        )
        self.assertFalse(rows[0].observed_adverse)
        self.assertIsNone(rows[0].observed_adverse_at)

    def test_export_is_anonymized_and_contains_no_secret_or_raw_identity(self):
        session = _session()
        document = pilot_session_document(session, anonymization_salt="local-secret-salt")
        encoded = json.dumps(document)
        self.assertNotIn(session.completed_history.lot_trip_id, encoded)
        self.assertNotIn(session.completed_history.outcome.device_id, encoded)
        self.assertNotIn("local-secret-salt", encoded)
        self.assertNotIn("token", encoded.lower())
        self.assertEqual(json.loads(pilot_session_json(session, anonymization_salt="local-secret-salt")), document)
        csv_text = pilot_validation_csv(build_pilot_validation_rows(session, anonymization_salt="local-secret-salt"))
        self.assertIn("predicted_probability", csv_text)
        self.assertIn("observed_adverse", csv_text)

    def test_tiny_pilot_reports_raw_counts_without_performance_rates(self):
        result = evaluate_pilot_rows(build_pilot_validation_rows(_session(), anonymization_salt="salt"))
        self.assertEqual(result.independent_trip_count, 1)
        self.assertEqual(result.positive_count, 1)
        self.assertFalse(result.statistically_interpretable)
        self.assertIsNone(result.recall)
        self.assertIsNone(result.brier_score)

    def test_empty_pilot_reports_zero_counts_without_metrics(self):
        result = evaluate_pilot_rows(())
        self.assertEqual(result.independent_trip_count, 0)
        self.assertEqual(result.example_count, 0)
        self.assertEqual(result.positive_count, 0)
        self.assertEqual(result.negative_count, 0)
        self.assertFalse(result.statistically_interpretable)
        self.assertIsNone(result.precision)

    def test_simulator_history_cannot_be_exported_as_real_pilot(self):
        simulator_session = replace(
            _session(), completed_history=completed_source_record()
        )
        with self.assertRaisesRegex(ValueError, "REAL_DEVICE or REPLAY"):
            pilot_session_document(
                simulator_session, anonymization_salt="local-secret-salt"
            )

    def test_simulator_and_pilot_comparison_remains_descriptive_and_separate(self):
        session = _session()
        comparison = compare_simulator_and_pilot((completed_source_record(),), (session.completed_history,))
        self.assertEqual(comparison["simulator"]["tripCount"], 1)
        self.assertEqual(comparison["pilot"]["tripCount"], 1)
        self.assertIn("do not establish formal drift", comparison["warning"])


if __name__ == "__main__":
    unittest.main()
