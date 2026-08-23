import unittest
from datetime import datetime, timezone

try:
    from .journey_risk_training import (
        JOURNEY_RISK_HIGH_THRESHOLD,
        JOURNEY_RISK_MEDIUM_THRESHOLD,
        train_and_compare_journey_risk_models,
        journey_risk_evaluation_document,
    )
    from .simulated_training_corpus import SimulatedCorpusConfig, build_approved_simulator_corpus
except ImportError:
    from journey_risk_training import JOURNEY_RISK_HIGH_THRESHOLD, JOURNEY_RISK_MEDIUM_THRESHOLD, train_and_compare_journey_risk_models, journey_risk_evaluation_document
    from simulated_training_corpus import SimulatedCorpusConfig, build_approved_simulator_corpus


class JourneyRiskTrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = build_approved_simulator_corpus(SimulatedCorpusConfig(trip_count=30, master_seed=34567))
        cls.result = train_and_compare_journey_risk_models(cls.corpus.journey_training_dataset)

    def test_grouped_split_never_shares_a_trip(self):
        split = self.result.split
        groups = [set(split.train_lot_trip_ids), set(split.validation_lot_trip_ids), set(split.test_lot_trip_ids)]
        self.assertTrue(groups[0].isdisjoint(groups[1]))
        self.assertTrue(groups[0].isdisjoint(groups[2]))
        self.assertTrue(groups[1].isdisjoint(groups[2]))

    def test_both_models_and_transparent_ensemble_are_evaluated(self):
        self.assertEqual({name for name, _ in self.result.validation_metrics}, {"LOGISTIC", "BOOSTED", "CALIBRATED_AVERAGE"})
        self.assertIn(self.result.selected_strategy, {"LOGISTIC", "BOOSTED", "CALIBRATED_AVERAGE"})

    def test_thresholds_remain_central_engineering_values(self):
        self.assertEqual(JOURNEY_RISK_MEDIUM_THRESHOLD, 0.20)
        self.assertEqual(JOURNEY_RISK_HIGH_THRESHOLD, 0.50)
        self.assertEqual({metric.threshold for _, metric in self.result.selected_threshold_analysis}, {0.20, 0.50})

    def test_heuristic_and_false_negative_metrics_are_reported(self):
        self.assertGreaterEqual(self.result.heuristic_test_metrics.false_negative, 0)
        for _, metrics in self.result.test_metrics:
            self.assertGreaterEqual(metrics.false_negative_rate, 0)
            self.assertLessEqual(metrics.false_negative_rate, 1)

    def test_evaluation_report_is_explicitly_simulator_only(self):
        document = journey_risk_evaluation_document(
            self.result, created_at=datetime(2026, 8, 23, tzinfo=timezone.utc)
        )
        self.assertIn("NOT REAL-WORLD PERFORMANCE", document["disclaimer"])
        self.assertEqual(document["trainingSource"], "SIMULATOR")
        self.assertEqual(document["validationStatus"], "ENGINEERING_POC")
        self.assertEqual(document["selectedStrategy"], self.result.selected_strategy)
        self.assertIn("false_negative_rate", document["testMetrics"]["LOGISTIC"])
        self.assertIn("brier_score", document["rawValidationMetrics"]["LOGISTIC"])
        self.assertIn("brier_score", document["validationMetrics"]["LOGISTIC"])


if __name__ == "__main__":
    unittest.main()
