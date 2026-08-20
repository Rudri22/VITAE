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
    from .temporal_risk_examples import (
        TEMPORAL_RISK_CATEGORICAL_FEATURES,
        TEMPORAL_RISK_NUMERIC_FEATURES,
    )
    from .temporal_risk_model_comparison import (
        MODEL_COMPARISON_SCHEMA,
        PairedBootstrapDelta,
        _boosted_advantage_is_material,
        compare_temporal_risk_models,
        paired_group_bootstrap_deltas,
        persist_model_comparison,
    )
except ImportError:
    from simulated_training_corpus import (
        SimulatedCorpusConfig,
        build_approved_simulator_corpus,
    )
    from temporal_risk_baseline import train_logistic_regression_baseline
    from temporal_risk_examples import (
        TEMPORAL_RISK_CATEGORICAL_FEATURES,
        TEMPORAL_RISK_NUMERIC_FEATURES,
    )
    from temporal_risk_model_comparison import (
        MODEL_COMPARISON_SCHEMA,
        PairedBootstrapDelta,
        _boosted_advantage_is_material,
        compare_temporal_risk_models,
        paired_group_bootstrap_deltas,
        persist_model_comparison,
    )


class TemporalRiskModelComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = build_approved_simulator_corpus(
            SimulatedCorpusConfig(trip_count=30, master_seed=7171)
        )
        cls.logistic = train_logistic_regression_baseline(
            cls.corpus.training_dataset
        )
        cls.boosted, cls.comparison = compare_temporal_risk_models(
            cls.corpus.training_dataset,
            cls.logistic,
            bootstrap_replicates=50,
        )

    def test_exact_dataset_and_grouped_split_are_shared(self):
        self.assertEqual(self.boosted.dataset_sha256, self.logistic.dataset_sha256)
        self.assertEqual(self.boosted.split, self.logistic.readiness.split)
        self.assertEqual(
            self.comparison.logistic_calibration.calibration_lot_trip_ids,
            self.comparison.boosted_calibration.calibration_lot_trip_ids,
        )

    def test_boosted_configuration_is_fixed_and_shallow(self):
        classifier = self.boosted.model.named_steps["classifier"]
        self.assertEqual(classifier.n_estimators, 100)
        self.assertEqual(classifier.learning_rate, 0.05)
        self.assertEqual(classifier.max_depth, 2)
        self.assertEqual(classifier.min_samples_leaf, 20)
        self.assertEqual(classifier.subsample, 0.8)

    def test_both_models_use_the_same_feature_schema(self):
        names = self.boosted.model.named_steps["preprocessor"].get_feature_names_out(
            TEMPORAL_RISK_CATEGORICAL_FEATURES
            + TEMPORAL_RISK_NUMERIC_FEATURES
        )
        self.assertTrue(any("current_excursion_utilization" in name for name in names))
        self.assertNotIn("lot_trip_id", " ".join(names))

    def test_paired_bootstrap_is_deterministic(self):
        labels = (0, 1, 0, 1, 0, 1)
        logistic = (0.1, 0.7, 0.2, 0.8, 0.3, 0.6)
        boosted = (0.05, 0.8, 0.1, 0.9, 0.2, 0.7)
        groups = ("a", "a", "b", "b", "c", "c")
        first = paired_group_bootstrap_deltas(
            labels, logistic, boosted, groups, replicates=30
        )
        second = paired_group_bootstrap_deltas(
            labels, logistic, boosted, groups, replicates=30
        )
        self.assertEqual(first, second)

    def test_preference_defaults_to_logistic_when_intervals_overlap(self):
        overlapping = tuple(
            PairedBootstrapDelta(name, -0.1, 0.01, 0.1, 50)
            for name in (
                "roc_auc",
                "average_precision",
                "log_loss",
                "brier_score",
                "expected_calibration_error",
            )
        )
        self.assertFalse(_boosted_advantage_is_material(overlapping))

    def test_test_partition_is_not_used_for_preference(self):
        self.assertIn("validation-trip", self.comparison.preference_basis)
        self.assertNotIn("test", self.comparison.preference_basis.casefold())

    def test_risk_policy_remains_null(self):
        self.assertIsNone(self.comparison.risk_policy)
        self.assertIsNone(self.comparison.logistic_calibration.risk_policy)
        self.assertIsNone(self.comparison.boosted_calibration.risk_policy)

    def test_feature_importance_is_named_and_normalized(self):
        importance = self.comparison.boosted_feature_importance
        self.assertTrue(importance)
        self.assertTrue(all("__x" not in item.feature_name for item in importance))
        self.assertAlmostEqual(sum(item.importance for item in importance), 1.0)

    def test_overfit_audit_has_train_and_validation_metrics(self):
        for audit in (
            self.comparison.logistic_overfit_audit,
            self.comparison.boosted_overfit_audit,
        ):
            self.assertGreaterEqual(audit.raw_train_metrics.roc_auc, 0.0)
            self.assertGreaterEqual(audit.raw_validation_metrics.roc_auc, 0.0)

    def test_artifact_records_simulated_scope_preference_and_null_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = persist_model_comparison(
                self.boosted,
                self.comparison,
                directory,
                created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
            )
            document = json.loads(Path(paths["metadata"]).read_text("utf-8"))
            self.assertEqual(document["schema"], MODEL_COMPARISON_SCHEMA)
            self.assertIsNone(document["risk_policy"])
            self.assertIn(
                document["preferred_engineering_candidate"],
                {"LOGISTIC", "BOOSTED"},
            )
            self.assertTrue(any("SIMULATED ONLY" in value for value in document["warnings"]))
            self.assertEqual(len(document["boosted_model_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
