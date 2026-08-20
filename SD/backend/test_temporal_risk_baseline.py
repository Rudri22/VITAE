import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from math import isfinite
from pathlib import Path

try:
    from .repository_contract_suite import CONTRACT_TIME
    from .repository_serialization import serialize_temporal_risk_example
    from .risk_rules import ApplicationStatus
    from .temporal_risk_baseline import (
        BASELINE_MODEL_VERSION,
        BASELINE_RANDOM_SEED,
        MISSING_CATEGORY_TOKEN,
        TemporalRiskTrainingDataset,
        TrainingReadinessError,
        TrainingReadinessPolicy,
        TrainingSourceKind,
        assess_training_readiness,
        diagnose_temporal_risk_dataset,
        discover_local_temporal_risk_sources,
        equal_trip_sample_weights,
        grouped_train_validation_test_split,
        load_temporal_risk_jsonl,
        persist_logistic_baseline_artifact,
        temporal_risk_dataset_fingerprint,
        train_logistic_regression_baseline,
    )
    from .temporal_risk_examples import (
        TEMPORAL_RISK_CATEGORICAL_FEATURES,
        TEMPORAL_RISK_EXAMPLE_VERSION,
        TEMPORAL_RISK_FEATURE_VERSION,
        TEMPORAL_RISK_LABEL_VERSION,
        TEMPORAL_RISK_NUMERIC_FEATURES,
        TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES,
        TemporalRiskExample,
        TemporalRiskFeatures,
        TemporalRiskLabel,
        temporal_risk_example_id,
    )
except ImportError:
    from repository_contract_suite import CONTRACT_TIME
    from repository_serialization import serialize_temporal_risk_example
    from risk_rules import ApplicationStatus
    from temporal_risk_baseline import (
        BASELINE_MODEL_VERSION,
        BASELINE_RANDOM_SEED,
        MISSING_CATEGORY_TOKEN,
        TemporalRiskTrainingDataset,
        TrainingReadinessError,
        TrainingReadinessPolicy,
        TrainingSourceKind,
        assess_training_readiness,
        diagnose_temporal_risk_dataset,
        discover_local_temporal_risk_sources,
        equal_trip_sample_weights,
        grouped_train_validation_test_split,
        load_temporal_risk_jsonl,
        persist_logistic_baseline_artifact,
        temporal_risk_dataset_fingerprint,
        train_logistic_regression_baseline,
    )
    from temporal_risk_examples import (
        TEMPORAL_RISK_CATEGORICAL_FEATURES,
        TEMPORAL_RISK_EXAMPLE_VERSION,
        TEMPORAL_RISK_FEATURE_VERSION,
        TEMPORAL_RISK_LABEL_VERSION,
        TEMPORAL_RISK_NUMERIC_FEATURES,
        TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES,
        TemporalRiskExample,
        TemporalRiskFeatures,
        TemporalRiskLabel,
        temporal_risk_example_id,
    )


def fixture_features(index=0, *, device_health=None):
    sample_count = index + 1
    first_sample = index == 0
    return TemporalRiskFeatures(
        product_id="gardasil-9",
        presentation="single-dose-prefilled-syringe-0.5-ml",
        state="unopened",
        product_rule_version="uspi-v503-i-2503r017",
        current_status=ApplicationStatus.SAFE,
        current_active_rule_id=None,
        latest_device_health=device_health,
        sample_count=sample_count,
        trip_elapsed_minutes=float(index * 5),
        observation_span_minutes=float(index * 5),
        minutes_since_previous_sample=None if first_sample else 5.0,
        minutes_since_previous_sample_missing=first_sample,
        latest_temperature_c=6.0 + index,
        mean_temperature_c=6.0 + index / 2,
        minimum_temperature_c=6.0,
        maximum_temperature_c=6.0 + index,
        temperature_range_c=float(index),
        temperature_change_from_first_c=float(index),
        temperature_slope_c_per_hour=float(index),
        latest_battery_level_percent=None if first_sample else 90.0,
        latest_battery_level_missing=first_sample,
        current_excursion_episode_duration_minutes=0.0,
        current_cumulative_excursion_duration_minutes=0.0,
        current_excursion_utilization=None,
        current_excursion_utilization_missing=True,
        safe_count_through_cutoff=sample_count,
        monitor_count_through_cutoff=0,
        at_risk_count_through_cutoff=0,
        critical_count_through_cutoff=0,
        rule_violation_count_through_cutoff=0,
        data_error_count_through_cutoff=0,
    )


def fixture_example(trip_index, example_index, *, positive, device_health=None):
    lot_trip_id = f"fixture-lot-trip-{trip_index:03d}"
    sample_id = f"fixture-sample-{example_index:03d}"
    cutoff_at = CONTRACT_TIME + timedelta(minutes=example_index * 5)
    label = TemporalRiskLabel(
        adverse_event_within_horizon=positive,
        first_adverse_status=(ApplicationStatus.AT_RISK if positive else None),
        first_adverse_at=(cutoff_at + timedelta(minutes=10) if positive else None),
    )
    return TemporalRiskExample(
        example_id=temporal_risk_example_id(lot_trip_id, sample_id),
        lot_trip_id=lot_trip_id,
        trip_id=f"fixture-trip-{trip_index:03d}",
        cutoff_sample_id=sample_id,
        cutoff_at=cutoff_at,
        horizon_ends_at=cutoff_at + timedelta(minutes=30),
        prediction_horizon_minutes=TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES,
        example_version=TEMPORAL_RISK_EXAMPLE_VERSION,
        feature_version=TEMPORAL_RISK_FEATURE_VERSION,
        label_version=TEMPORAL_RISK_LABEL_VERSION,
        features=fixture_features(example_index, device_health=device_health),
        label=label,
    )


def sufficient_fixture_dataset(
    source_kind=TrainingSourceKind.APPROVED_SIMULATOR,
):
    examples = tuple(
        fixture_example(trip, index, positive=index % 2 == 1)
        for trip in range(30)
        for index in range(4)
    )
    return TemporalRiskTrainingDataset(
        source_id="unit-test-fixture-only",
        source_kind=source_kind,
        examples=examples,
    )


class TemporalRiskBaselineDiagnosticsTests(unittest.TestCase):
    def test_workspace_discovery_is_limited_to_known_generated_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ignored = root / "random.jsonl"
            ignored.write_text("{}\n", encoding="utf-8")
            expected = root / "SD" / "backend" / "generated_datasets" / "data.jsonl"
            expected.parent.mkdir(parents=True)
            expected.write_text("{}\n", encoding="utf-8")
            self.assertEqual(discover_local_temporal_risk_sources(root), (expected,))

    def test_empty_unknown_dataset_reports_exact_hard_failures(self):
        dataset = TemporalRiskTrainingDataset(
            source_id="no-local-data",
            source_kind=TrainingSourceKind.UNKNOWN,
            examples=(),
        )
        assessment = assess_training_readiness(dataset)
        self.assertFalse(assessment.ready)
        self.assertEqual(assessment.diagnostics.unique_trip_count, 0)
        self.assertEqual(assessment.diagnostics.example_count, 0)
        self.assertIsNone(assessment.diagnostics.positive_prevalence)
        self.assertIn("SOURCE_NOT_APPROVED_FOR_TRAINING", assessment.hard_failures)
        self.assertIn("NO_TEMPORAL_EXAMPLES", assessment.hard_failures)
        self.assertIn("SINGLE_LABEL_CLASS", assessment.hard_failures)

    def test_test_fixtures_are_never_approved_for_claimed_training(self):
        dataset = sufficient_fixture_dataset(TrainingSourceKind.TEST_FIXTURE)
        with self.assertRaises(TrainingReadinessError) as raised:
            train_logistic_regression_baseline(dataset)
        self.assertIn(
            "SOURCE_NOT_APPROVED_FOR_TRAINING",
            raised.exception.assessment.hard_failures,
        )

    def test_single_class_is_a_hard_failure(self):
        dataset = sufficient_fixture_dataset()
        negatives = tuple(
            replace(
                example,
                label=TemporalRiskLabel(False, None, None),
            )
            for example in dataset.examples
        )
        assessment = assess_training_readiness(replace(dataset, examples=negatives))
        self.assertIn("SINGLE_LABEL_CLASS", assessment.hard_failures)

    def test_configurable_sample_adequacy_thresholds_are_warnings(self):
        dataset = sufficient_fixture_dataset()
        policy = TrainingReadinessPolicy(
            minimum_unique_trips=31,
            minimum_examples=121,
            minimum_positive_trips=31,
            minimum_negative_trips=31,
        )
        assessment = assess_training_readiness(dataset, policy)
        self.assertTrue(assessment.ready)
        self.assertEqual(assessment.hard_failures, ())
        self.assertTrue(
            {
                "BELOW_POLICY_MINIMUM_EXAMPLES",
                "BELOW_POLICY_MINIMUM_INDEPENDENT_TRIPS",
                "BELOW_POLICY_MINIMUM_POSITIVE_TRIPS",
                "BELOW_POLICY_MINIMUM_NEGATIVE_TRIPS",
            }.issubset(assessment.statistical_warnings)
        )

    def test_twenty_nine_trips_alone_is_not_a_hard_failure(self):
        dataset = sufficient_fixture_dataset()
        examples = tuple(
            example
            for example in dataset.examples
            if example.lot_trip_id != "fixture-lot-trip-029"
        )
        assessment = assess_training_readiness(replace(dataset, examples=examples))
        self.assertTrue(assessment.ready)
        self.assertNotIn(
            "INSUFFICIENT_INDEPENDENT_TRIPS", assessment.hard_failures
        )
        self.assertIn(
            "BELOW_POLICY_MINIMUM_INDEPENDENT_TRIPS",
            assessment.statistical_warnings,
        )

    def test_ninety_nine_examples_alone_is_not_a_hard_failure(self):
        dataset = sufficient_fixture_dataset()
        examples = tuple(
            fixture_example(trip, index, positive=index % 2 == 1)
            for trip in range(30)
            for index in range(3 + (trip < 9))
        )
        self.assertEqual(len(examples), 99)
        assessment = assess_training_readiness(replace(dataset, examples=examples))
        self.assertTrue(assessment.ready)
        self.assertNotIn("INSUFFICIENT_EXAMPLES", assessment.hard_failures)
        self.assertIn(
            "BELOW_POLICY_MINIMUM_EXAMPLES", assessment.statistical_warnings
        )

    def test_impossible_grouped_partition_is_a_hard_failure(self):
        examples = (
            fixture_example(0, 0, positive=False),
            fixture_example(0, 1, positive=True),
            fixture_example(1, 0, positive=False),
            fixture_example(1, 1, positive=True),
        )
        dataset = TemporalRiskTrainingDataset(
            source_id="too-few-groups",
            source_kind=TrainingSourceKind.APPROVED_SIMULATOR,
            examples=examples,
        )
        assessment = assess_training_readiness(dataset)
        self.assertFalse(assessment.ready)
        self.assertIn("GROUPED_SPLIT_NOT_POSSIBLE", assessment.hard_failures)

    def test_low_positive_trip_count_is_an_adequacy_warning(self):
        examples = tuple(
            fixture_example(trip, 0, positive=False) for trip in range(30)
        )
        split = grouped_train_validation_test_split(examples)
        positive_indices = {
            split.train_indices[0],
            split.validation_indices[0],
            split.test_indices[0],
        }
        examples = tuple(
            fixture_example(trip, 0, positive=index in positive_indices)
            for index, trip in enumerate(range(30))
        )
        dataset = TemporalRiskTrainingDataset(
            source_id="low-positive-trip-coverage",
            source_kind=TrainingSourceKind.APPROVED_SIMULATOR,
            examples=examples,
        )
        assessment = assess_training_readiness(dataset)
        self.assertTrue(assessment.ready)
        self.assertEqual(assessment.diagnostics.positive_trip_count, 3)
        self.assertIn(
            "BELOW_POLICY_MINIMUM_POSITIVE_TRIPS",
            assessment.statistical_warnings,
        )

    def test_split_missing_a_label_class_is_a_hard_failure(self):
        examples = tuple(
            fixture_example(trip, 0, positive=False) for trip in range(30)
        )
        split = grouped_train_validation_test_split(examples)
        positive_indices = {split.train_indices[0], split.test_indices[0]}
        examples = tuple(
            fixture_example(trip, 0, positive=index in positive_indices)
            for index, trip in enumerate(range(30))
        )
        dataset = TemporalRiskTrainingDataset(
            source_id="invalid-validation-label-coverage",
            source_kind=TrainingSourceKind.APPROVED_SIMULATOR,
            examples=examples,
        )
        assessment = assess_training_readiness(dataset)
        self.assertFalse(assessment.ready)
        self.assertIn("SPLIT_LABEL_COVERAGE_FAILURE", assessment.hard_failures)

    def test_diagnostics_report_prevalence_density_and_missingness(self):
        diagnostics = diagnose_temporal_risk_dataset(sufficient_fixture_dataset())
        self.assertEqual(diagnostics.unique_trip_count, 30)
        self.assertEqual(diagnostics.example_count, 120)
        self.assertEqual(diagnostics.positive_count, 60)
        self.assertEqual(diagnostics.negative_count, 60)
        self.assertEqual(diagnostics.positive_prevalence, 0.5)
        self.assertEqual(diagnostics.examples_per_trip_minimum, 4)
        self.assertEqual(diagnostics.examples_per_trip_maximum, 4)
        self.assertEqual(diagnostics.examples_per_trip_mean, 4.0)
        missing = {
            item.feature_name: item for item in diagnostics.feature_missingness
        }
        self.assertEqual(missing["current_active_rule_id"].missing_fraction, 1.0)
        self.assertEqual(
            missing["minutes_since_previous_sample"].missing_fraction,
            0.25,
        )


class TemporalRiskBaselineTrainingTests(unittest.TestCase):
    def setUp(self):
        self.dataset = sufficient_fixture_dataset()

    def test_grouped_split_is_deterministic_and_has_no_trip_overlap(self):
        first = grouped_train_validation_test_split(self.dataset.examples)
        second = grouped_train_validation_test_split(self.dataset.examples)
        self.assertEqual(first, second)
        train = set(first.train_lot_trip_ids)
        validation = set(first.validation_lot_trip_ids)
        test = set(first.test_lot_trip_ids)
        self.assertFalse(train & validation)
        self.assertFalse(train & test)
        self.assertFalse(validation & test)
        self.assertEqual((len(train), len(validation), len(test)), (18, 6, 6))
        self.assertEqual(first.random_seed, BASELINE_RANDOM_SEED)

    def test_dense_trip_weights_give_each_trip_equal_total_weight(self):
        split = grouped_train_validation_test_split(self.dataset.examples)
        weights = equal_trip_sample_weights(
            self.dataset.examples,
            split.train_indices,
        )
        totals = {}
        for index, weight in zip(split.train_indices, weights):
            lot_trip_id = self.dataset.examples[index].lot_trip_id
            totals[lot_trip_id] = totals.get(lot_trip_id, 0.0) + weight
        self.assertEqual(len(set(round(value, 12) for value in totals.values())), 1)
        self.assertAlmostEqual(sum(weights) / len(weights), 1.0)

    def test_preprocessing_is_fit_on_train_only_and_handles_unknown_categories(self):
        split = grouped_train_validation_test_split(self.dataset.examples)
        held_out_indices = set(split.validation_indices) | set(split.test_indices)
        examples = tuple(
            replace(
                example,
                features=replace(
                    example.features,
                    latest_device_health=(
                        "HELD_OUT_ONLY"
                        if index in held_out_indices
                        else "TRAIN_ONLY"
                    ),
                ),
            )
            for index, example in enumerate(self.dataset.examples)
        )
        result = train_logistic_regression_baseline(
            replace(self.dataset, examples=examples)
        )
        encoder = result.model.named_steps["preprocessor"].named_transformers_[
            "categorical"
        ]
        categories = {str(item) for values in encoder.categories_ for item in values}
        self.assertIn("TRAIN_ONLY", categories)
        self.assertNotIn("HELD_OUT_ONLY", categories)

    def test_logistic_baseline_configuration_and_metrics_are_finite(self):
        result = train_logistic_regression_baseline(self.dataset)
        classifier = result.model.named_steps["classifier"]
        self.assertEqual(classifier.C, 1.0)
        self.assertEqual(classifier.l1_ratio, 0.0)
        self.assertEqual(classifier.solver, "liblinear")
        self.assertIsNone(classifier.class_weight)
        self.assertEqual(classifier.max_iter, 1000)
        self.assertTrue(result.coefficients_by_absolute_magnitude)
        for metrics in (result.validation_metrics, result.test_metrics):
            self.assertTrue(all(isfinite(value) for value in vars(metrics).values()))

    def test_explicit_artifact_contains_model_hash_versions_and_split_metadata(self):
        result = train_logistic_regression_baseline(self.dataset)
        with tempfile.TemporaryDirectory() as directory:
            metadata = persist_logistic_baseline_artifact(
                result,
                directory,
                created_at=CONTRACT_TIME,
            )
            destination = Path(directory)
            self.assertTrue((destination / "model.joblib").is_file())
            self.assertTrue((destination / "metadata.json").is_file())
            self.assertEqual(metadata["model_version"], BASELINE_MODEL_VERSION)
            self.assertEqual(len(metadata["model_sha256"]), 64)
            self.assertEqual(metadata["dataset_sha256"], result.dataset_sha256)
            self.assertIsNone(metadata["classifier"]["class_weight"])
            self.assertEqual(
                metadata["split"]["random_seed"],
                BASELINE_RANDOM_SEED,
            )

    def test_dataset_fingerprint_is_order_independent_and_content_sensitive(self):
        original = temporal_risk_dataset_fingerprint(self.dataset)
        reordered = temporal_risk_dataset_fingerprint(
            replace(self.dataset, examples=tuple(reversed(self.dataset.examples)))
        )
        changed_example = replace(
            self.dataset.examples[0],
            features=replace(
                self.dataset.examples[0].features,
                latest_temperature_c=7.0,
            ),
        )
        changed = temporal_risk_dataset_fingerprint(
            replace(
                self.dataset,
                examples=(changed_example,) + self.dataset.examples[1:],
            )
        )
        self.assertEqual(original, reordered)
        self.assertNotEqual(original, changed)

    def test_jsonl_loader_preserves_explicit_source_classification(self):
        example = self.dataset.examples[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "examples.jsonl"
            path.write_text(
                json.dumps(serialize_temporal_risk_example(example)) + "\n",
                encoding="utf-8",
            )
            loaded = load_temporal_risk_jsonl(
                path,
                source_kind=TrainingSourceKind.TEST_FIXTURE,
            )
        self.assertEqual(loaded.examples, (example,))
        self.assertEqual(loaded.source_kind, TrainingSourceKind.TEST_FIXTURE)

    def test_feature_manifests_exclude_identifiers_labels_and_future_fields(self):
        features = set(
            TEMPORAL_RISK_CATEGORICAL_FEATURES + TEMPORAL_RISK_NUMERIC_FEATURES
        )
        self.assertFalse(
            features
            & {
                "lot_trip_id",
                "trip_id",
                "device_id",
                "cutoff_sample_id",
                "first_adverse_at",
                "first_adverse_status",
                "adverse_event_within_horizon",
                "completed_at",
                "final_status",
            }
        )

    def test_generated_artifact_directories_are_ignored(self):
        root = Path(__file__).parents[2]
        ignore = (root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("SD/backend/generated_datasets/", ignore)
        self.assertIn("SD/backend/ml_artifacts/", ignore)
        self.assertIn("SD/backend/ml_runs/", ignore)
        self.assertEqual(MISSING_CATEGORY_TOKEN, "__MISSING__")


if __name__ == "__main__":
    unittest.main()
