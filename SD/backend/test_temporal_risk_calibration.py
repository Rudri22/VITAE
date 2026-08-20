import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    from .simulated_training_corpus import (
        SimulatedCorpusConfig,
        build_approved_simulator_corpus,
    )
    from .temporal_risk_baseline import train_logistic_regression_baseline
    from .temporal_risk_calibration import (
        CALIBRATION_METHOD,
        TemporalRiskCalibrationError,
        analyze_temporal_risk_calibration,
        persist_calibration_analysis,
        threshold_sweep,
        validation_threshold_candidates,
    )
except ImportError:
    from simulated_training_corpus import (
        SimulatedCorpusConfig,
        build_approved_simulator_corpus,
    )
    from temporal_risk_baseline import train_logistic_regression_baseline
    from temporal_risk_calibration import (
        CALIBRATION_METHOD,
        TemporalRiskCalibrationError,
        analyze_temporal_risk_calibration,
        persist_calibration_analysis,
        threshold_sweep,
        validation_threshold_candidates,
    )


class TemporalRiskCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = build_approved_simulator_corpus(
            SimulatedCorpusConfig(trip_count=30, master_seed=9898)
        )
        cls.training = train_logistic_regression_baseline(
            cls.corpus.training_dataset
        )
        cls.analysis = analyze_temporal_risk_calibration(
            cls.corpus.training_dataset,
            cls.training,
            bootstrap_replicates=50,
        )

    def test_calibration_and_thresholds_use_validation_groups_only(self):
        split = self.training.readiness.split
        self.assertEqual(
            self.analysis.calibration_lot_trip_ids,
            split.validation_lot_trip_ids,
        )
        self.assertEqual(
            self.analysis.threshold_analysis_lot_trip_ids,
            split.validation_lot_trip_ids,
        )
        self.assertFalse(
            set(self.analysis.calibration_lot_trip_ids)
            & set(self.analysis.test_lot_trip_ids)
        )

    def test_test_partition_is_evaluation_only(self):
        split = self.training.readiness.split
        self.assertEqual(self.analysis.test_lot_trip_ids, split.test_lot_trip_ids)
        self.assertTrue(self.analysis.calibrated_test_metrics.roc_auc >= 0.0)

    def test_sigmoid_calibration_outputs_finite_probabilities(self):
        calibrated = self.analysis.calibrator.predict((0.0, 0.1, 0.5, 0.9, 1.0))
        self.assertEqual(self.analysis.calibration_method, CALIBRATION_METHOD)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in calibrated))
        self.assertEqual(tuple(sorted(calibrated)), calibrated)

    def test_threshold_sweep_has_exact_confusion_accounting(self):
        sweep = threshold_sweep((0, 0, 1, 1), (0.1, 0.6, 0.4, 0.9))
        at_half = next(item for item in sweep if item.threshold == 0.5)
        self.assertEqual(
            (
                at_half.true_negative,
                at_half.false_positive,
                at_half.false_negative,
                at_half.true_positive,
            ),
            (1, 1, 1, 1),
        )

    def test_candidate_selection_is_deterministic_and_validation_metric_only(self):
        sweep = threshold_sweep((0, 0, 1, 1), (0.1, 0.6, 0.4, 0.9))
        first = validation_threshold_candidates(sweep)
        second = validation_threshold_candidates(sweep)
        self.assertEqual(first, second)
        self.assertEqual(
            {item.name for item in first},
            {"MAX_F1", "MAX_F2", "MAX_BALANCED_ACCURACY"},
        )

    def test_no_operational_risk_policy_is_invented(self):
        self.assertIsNone(self.analysis.risk_policy)
        self.assertEqual(
            self.analysis.policy_status,
            "NOT_CREATED_OPERATIONAL_COSTS_UNSPECIFIED",
        )

    def test_group_bootstrap_is_deterministic(self):
        repeated = analyze_temporal_risk_calibration(
            self.corpus.training_dataset,
            self.training,
            bootstrap_replicates=50,
        )
        self.assertEqual(
            repeated.validation_bootstrap_intervals,
            self.analysis.validation_bootstrap_intervals,
        )

    def test_constant_feature_audit_includes_single_product_context(self):
        names = {item.feature_name for item in self.analysis.constant_training_features}
        self.assertTrue(
            {"product_id", "presentation", "state", "product_rule_version"}.issubset(
                names
            )
        )

    def test_dataset_fingerprint_mismatch_fails_closed(self):
        other = build_approved_simulator_corpus(
            SimulatedCorpusConfig(trip_count=30, master_seed=9899)
        )
        with self.assertRaisesRegex(
            TemporalRiskCalibrationError, "fingerprints differ"
        ):
            analyze_temporal_risk_calibration(
                other.training_dataset,
                self.training,
                bootstrap_replicates=10,
            )

    def test_artifact_records_policy_absence_and_split_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = persist_calibration_analysis(
                self.analysis,
                directory,
                created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
            )
            metadata = json.loads(Path(paths["metadata"]).read_text("utf-8"))
            self.assertIsNone(metadata["risk_policy"])
            self.assertEqual(
                metadata["calibration_lot_trip_ids"],
                metadata["threshold_analysis_lot_trip_ids"],
            )
            self.assertEqual(len(metadata["calibrator_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
