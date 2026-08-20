import inspect
import json
import unittest
from dataclasses import replace
from datetime import timedelta

try:
    from .completed_trip_dataset import CompletedTripDatasetRecord
    from .completed_trip_outcome import completed_trip_outcome_from_state
    from .repository_contract_suite import (
        CONTRACT_TIME,
        contract_decision_record,
        contract_sample,
        contract_state,
        contract_trip,
    )
    from .repository_serialization import (
        RepositorySerializationError,
        deserialize_temporal_risk_example,
        serialize_temporal_risk_example,
    )
    from .risk_rules import ApplicationStatus
    from .state_repository import telemetry_record_from_sample
    from .temporal_risk_examples import (
        TEMPORAL_RISK_ADVERSE_STATUSES,
        TEMPORAL_RISK_CATEGORICAL_FEATURES,
        TEMPORAL_RISK_EXAMPLE_VERSION,
        TEMPORAL_RISK_FEATURE_VERSION,
        TEMPORAL_RISK_LABEL_VERSION,
        TEMPORAL_RISK_NUMERIC_FEATURES,
        TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES,
        TEMPORAL_RISK_TARGET_NAME,
        TemporalRiskExampleError,
        build_temporal_risk_dataset,
        build_temporal_risk_examples,
        temporal_risk_examples_jsonl,
        validate_temporal_risk_example,
    )
    from .trip_identity import TripStatus
except ImportError:
    from completed_trip_dataset import CompletedTripDatasetRecord
    from completed_trip_outcome import completed_trip_outcome_from_state
    from repository_contract_suite import (
        CONTRACT_TIME,
        contract_decision_record,
        contract_sample,
        contract_state,
        contract_trip,
    )
    from repository_serialization import (
        RepositorySerializationError,
        deserialize_temporal_risk_example,
        serialize_temporal_risk_example,
    )
    from risk_rules import ApplicationStatus
    from state_repository import telemetry_record_from_sample
    from temporal_risk_examples import (
        TEMPORAL_RISK_ADVERSE_STATUSES,
        TEMPORAL_RISK_CATEGORICAL_FEATURES,
        TEMPORAL_RISK_EXAMPLE_VERSION,
        TEMPORAL_RISK_FEATURE_VERSION,
        TEMPORAL_RISK_LABEL_VERSION,
        TEMPORAL_RISK_NUMERIC_FEATURES,
        TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES,
        TEMPORAL_RISK_TARGET_NAME,
        TemporalRiskExampleError,
        build_temporal_risk_dataset,
        build_temporal_risk_examples,
        temporal_risk_examples_jsonl,
        validate_temporal_risk_example,
    )
    from trip_identity import TripStatus


TIMES = (0, 10, 20, 30, 40, 50)
STATUSES = (
    ApplicationStatus.SAFE,
    ApplicationStatus.MONITOR,
    ApplicationStatus.AT_RISK,
    ApplicationStatus.SAFE,
    ApplicationStatus.MONITOR,
    ApplicationStatus.SAFE,
)
TEMPERATURES = (6.0, 9.0, 10.0, 7.0, 8.5, 6.0)


def completed_source_record(
    *,
    statuses=STATUSES,
    temperatures=TEMPERATURES,
    times=TIMES,
    completed_minutes=60,
    battery_levels=None,
):
    completed_at = CONTRACT_TIME + timedelta(minutes=completed_minutes)
    trip = contract_trip(
        status=TripStatus.COMPLETED,
        completed_at=completed_at,
    )
    records = []
    decisions = []
    previous_state = None
    batteries = battery_levels or tuple(90.0 for _ in times)
    for index, (minutes, status, temperature, battery) in enumerate(
        zip(times, statuses, temperatures, batteries),
        start=1,
    ):
        sample = replace(
            contract_sample(sample_id=f"sample-{index}", minutes=minutes),
            temperature=temperature,
            battery_level=battery,
        )
        state = contract_state(sample, previous_state)
        active = None if status == ApplicationStatus.SAFE else "rule-1"
        utilization = None if status == ApplicationStatus.SAFE else index / 10.0
        state = replace(
            state,
            status=status,
            reason_code=f"STATUS_{status.value}",
            active_rule_id=active,
            excursion_episode_duration_minutes=float(index - 1),
            cumulative_excursion_duration_minutes=float(index - 1),
            excursion_utilization=utilization,
        )
        records.append(
            telemetry_record_from_sample(trip.trip_id, trip.lot_trip_id, sample)
        )
        decisions.append(contract_decision_record(sample, state))
        previous_state = state
    outcome = completed_trip_outcome_from_state(
        trip,
        completed_at,
        previous_state,
    )
    return CompletedTripDatasetRecord(
        lot_trip_id=trip.lot_trip_id,
        outcome=outcome,
        telemetry_records=tuple(records),
        decision_records=tuple(decisions),
    )


class TemporalRiskExampleTests(unittest.TestCase):
    def setUp(self):
        self.source = completed_source_record()
        self.examples = build_temporal_risk_examples(self.source)

    def _example_at_first_cutoff(self, **source_changes):
        examples = build_temporal_risk_examples(
            completed_source_record(**source_changes)
        )
        return next(
            example
            for example in examples
            if example.cutoff_sample_id == "sample-1"
        )

    def test_prediction_problem_and_horizon_are_frozen(self):
        self.assertEqual(
            TEMPORAL_RISK_TARGET_NAME,
            "adverse_deterministic_status_within_horizon",
        )
        self.assertEqual(TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES, 30)
        self.assertEqual(
            TEMPORAL_RISK_ADVERSE_STATUSES,
            {
                ApplicationStatus.AT_RISK,
                ApplicationStatus.CRITICAL,
                ApplicationStatus.RULE_VIOLATION,
            },
        )
        self.assertEqual(
            [example.cutoff_sample_id for example in self.examples],
            ["sample-1", "sample-2", "sample-4"],
        )

    def test_positive_label_uses_first_future_adverse_decision(self):
        first = self.examples[0]
        self.assertTrue(first.label.adverse_event_within_horizon)
        self.assertEqual(first.label.first_adverse_status, ApplicationStatus.AT_RISK)
        self.assertEqual(
            first.label.first_adverse_at,
            CONTRACT_TIME + timedelta(minutes=20),
        )

    def test_non_adverse_decisions_before_at_risk_still_produce_positive(self):
        example = self._example_at_first_cutoff(
            statuses=(
                ApplicationStatus.SAFE,
                ApplicationStatus.SAFE,
                ApplicationStatus.AT_RISK,
                ApplicationStatus.SAFE,
            ),
            temperatures=(6.0, 6.0, 10.0, 6.0),
            times=(0, 5, 20, 30),
            completed_minutes=30,
        )
        self.assertTrue(example.label.adverse_event_within_horizon)
        self.assertEqual(example.label.first_adverse_status, ApplicationStatus.AT_RISK)
        self.assertEqual(
            example.label.first_adverse_at,
            CONTRACT_TIME + timedelta(minutes=20),
        )

    def test_monitor_before_critical_at_minute_29_is_positive(self):
        example = self._example_at_first_cutoff(
            statuses=(
                ApplicationStatus.SAFE,
                ApplicationStatus.MONITOR,
                ApplicationStatus.CRITICAL,
                ApplicationStatus.SAFE,
            ),
            temperatures=(6.0, 9.0, 20.0, 6.0),
            times=(0, 5, 29, 30),
            completed_minutes=30,
        )
        self.assertTrue(example.label.adverse_event_within_horizon)
        self.assertEqual(example.label.first_adverse_status, ApplicationStatus.CRITICAL)
        self.assertEqual(
            example.label.first_adverse_at,
            CONTRACT_TIME + timedelta(minutes=29),
        )

    def test_safe_throughout_observed_horizon_is_negative(self):
        example = self._example_at_first_cutoff(
            statuses=(ApplicationStatus.SAFE,) * 4,
            temperatures=(6.0,) * 4,
            times=(0, 5, 20, 30),
            completed_minutes=30,
        )
        self.assertFalse(example.label.adverse_event_within_horizon)

    def test_adverse_exactly_at_horizon_is_included(self):
        example = self._example_at_first_cutoff(
            statuses=(
                ApplicationStatus.SAFE,
                ApplicationStatus.SAFE,
                ApplicationStatus.RULE_VIOLATION,
            ),
            temperatures=(6.0, 6.0, 30.0),
            times=(0, 5, 30),
            completed_minutes=30,
        )
        self.assertTrue(example.label.adverse_event_within_horizon)
        self.assertEqual(
            example.label.first_adverse_status,
            ApplicationStatus.RULE_VIOLATION,
        )
        self.assertEqual(
            example.label.first_adverse_at,
            CONTRACT_TIME + timedelta(minutes=30),
        )

    def test_adverse_just_after_horizon_is_not_included(self):
        example = self._example_at_first_cutoff(
            statuses=(
                ApplicationStatus.SAFE,
                ApplicationStatus.SAFE,
                ApplicationStatus.SAFE,
                ApplicationStatus.AT_RISK,
                ApplicationStatus.SAFE,
            ),
            temperatures=(6.0, 6.0, 6.0, 10.0, 6.0),
            times=(0, 5, 30, 31, 60),
            completed_minutes=60,
        )
        self.assertFalse(example.label.adverse_event_within_horizon)

    def test_multiple_adverse_events_preserve_earliest_provenance(self):
        example = self._example_at_first_cutoff(
            statuses=(
                ApplicationStatus.SAFE,
                ApplicationStatus.SAFE,
                ApplicationStatus.CRITICAL,
                ApplicationStatus.AT_RISK,
                ApplicationStatus.RULE_VIOLATION,
                ApplicationStatus.SAFE,
            ),
            temperatures=(6.0, 6.0, 20.0, 10.0, 30.0, 6.0),
            times=(0, 5, 10, 20, 25, 30),
            completed_minutes=30,
        )
        self.assertEqual(example.label.first_adverse_status, ApplicationStatus.CRITICAL)
        self.assertEqual(
            example.label.first_adverse_at,
            CONTRACT_TIME + timedelta(minutes=10),
        )

    def test_negative_label_requires_observed_non_error_future(self):
        negative = self.examples[-1]
        self.assertEqual(negative.cutoff_at, CONTRACT_TIME + timedelta(minutes=30))
        self.assertFalse(negative.label.adverse_event_within_horizon)
        self.assertIsNone(negative.label.first_adverse_status)
        self.assertIsNone(negative.label.first_adverse_at)

    def test_already_adverse_and_incomplete_horizon_cutoffs_are_excluded(self):
        cutoff_ids = {example.cutoff_sample_id for example in self.examples}
        self.assertNotIn("sample-3", cutoff_ids)
        self.assertNotIn("sample-5", cutoff_ids)
        self.assertNotIn("sample-6", cutoff_ids)

    def test_future_data_error_censors_the_example(self):
        source = completed_source_record(
            statuses=(
                ApplicationStatus.SAFE,
                ApplicationStatus.DATA_ERROR,
                ApplicationStatus.SAFE,
                ApplicationStatus.SAFE,
            ),
            temperatures=(6.0, 6.0, 6.0, 6.0),
            times=(0, 10, 20, 30),
            completed_minutes=30,
        )
        self.assertEqual(build_temporal_risk_examples(source), ())

    def test_incomplete_observation_horizon_is_censored(self):
        source = completed_source_record(
            statuses=(
                ApplicationStatus.SAFE,
                ApplicationStatus.SAFE,
                ApplicationStatus.SAFE,
            ),
            temperatures=(6.0, 6.0, 6.0),
            times=(0, 5, 20),
            completed_minutes=60,
        )
        self.assertEqual(build_temporal_risk_examples(source), ())

    def test_no_telemetry_completed_trip_produces_no_examples(self):
        completed_at = CONTRACT_TIME + timedelta(minutes=60)
        trip = contract_trip(
            status=TripStatus.COMPLETED,
            completed_at=completed_at,
        )
        source = CompletedTripDatasetRecord(
            lot_trip_id=trip.lot_trip_id,
            outcome=completed_trip_outcome_from_state(trip, completed_at, None),
            telemetry_records=(),
            decision_records=(),
        )
        self.assertEqual(build_temporal_risk_examples(source), ())

    def test_v1_feature_formulas_are_exact_at_monitor_cutoff(self):
        example = self.examples[1]
        features = example.features
        self.assertEqual(features.sample_count, 2)
        self.assertEqual(features.trip_elapsed_minutes, 10.0)
        self.assertEqual(features.observation_span_minutes, 10.0)
        self.assertEqual(features.minutes_since_previous_sample, 10.0)
        self.assertFalse(features.minutes_since_previous_sample_missing)
        self.assertEqual(features.latest_temperature_c, 9.0)
        self.assertEqual(features.mean_temperature_c, 7.5)
        self.assertEqual(features.minimum_temperature_c, 6.0)
        self.assertEqual(features.maximum_temperature_c, 9.0)
        self.assertEqual(features.temperature_range_c, 3.0)
        self.assertEqual(features.temperature_change_from_first_c, 3.0)
        self.assertEqual(features.temperature_slope_c_per_hour, 18.0)
        self.assertEqual(features.current_status, ApplicationStatus.MONITOR)
        self.assertEqual(features.safe_count_through_cutoff, 1)
        self.assertEqual(features.monitor_count_through_cutoff, 1)

    def test_missing_values_have_explicit_indicators(self):
        source = completed_source_record(
            battery_levels=(None, 90.0, 90.0, 90.0, 90.0, 90.0)
        )
        first = build_temporal_risk_examples(source)[0]
        self.assertIsNone(first.features.minutes_since_previous_sample)
        self.assertTrue(first.features.minutes_since_previous_sample_missing)
        self.assertIsNone(first.features.latest_battery_level_percent)
        self.assertTrue(first.features.latest_battery_level_missing)
        self.assertIsNone(first.features.current_excursion_utilization)
        self.assertTrue(first.features.current_excursion_utilization_missing)

    def test_future_values_cannot_change_cutoff_features(self):
        altered = completed_source_record(
            temperatures=(6.0, 9.0, -20.0, 40.0, 35.0, 30.0)
        )
        original_by_cutoff = {
            item.cutoff_sample_id: item for item in self.examples
        }
        altered_by_cutoff = {
            item.cutoff_sample_id: item
            for item in build_temporal_risk_examples(altered)
        }
        self.assertEqual(
            original_by_cutoff["sample-1"].features,
            altered_by_cutoff["sample-1"].features,
        )
        self.assertEqual(
            original_by_cutoff["sample-2"].features,
            altered_by_cutoff["sample-2"].features,
        )

    def test_example_ids_and_dataset_order_are_deterministic(self):
        repeated = build_temporal_risk_examples(self.source)
        self.assertEqual(self.examples, repeated)
        self.assertEqual(build_temporal_risk_dataset((self.source,)), self.examples)
        self.assertEqual(len({item.example_id for item in self.examples}), 3)
        self.assertTrue(all(item.lot_trip_id == self.source.lot_trip_id for item in self.examples))

    def test_versioned_serialization_round_trip_is_strict(self):
        example = self.examples[0]
        document = serialize_temporal_risk_example(example)
        self.assertEqual(document["schema"], "vitae.temporal_risk_example")
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(deserialize_temporal_risk_example(document), example)
        document["features"]["future_temperature"] = 99.0
        with self.assertRaises(RepositorySerializationError):
            deserialize_temporal_risk_example(document)

    def test_jsonl_export_is_canonical_and_keeps_label_separate(self):
        exported = temporal_risk_examples_jsonl(reversed(self.examples))
        self.assertEqual(
            exported,
            temporal_risk_examples_jsonl(self.examples),
        )
        documents = tuple(json.loads(line) for line in exported.splitlines())
        self.assertEqual(len(documents), len(self.examples))
        self.assertTrue(all("features" in item and "label" in item for item in documents))
        self.assertTrue(
            all(
                "adverse_event_within_horizon" not in item["features"]
                for item in documents
            )
        )

    def test_versions_and_feature_manifests_are_explicit(self):
        example = self.examples[0]
        self.assertEqual(example.example_version, TEMPORAL_RISK_EXAMPLE_VERSION)
        self.assertEqual(example.feature_version, TEMPORAL_RISK_FEATURE_VERSION)
        self.assertEqual(example.label_version, TEMPORAL_RISK_LABEL_VERSION)
        self.assertEqual(
            set(TEMPORAL_RISK_CATEGORICAL_FEATURES),
            {
                "product_id",
                "presentation",
                "state",
                "product_rule_version",
                "current_status",
                "current_active_rule_id",
                "latest_device_health",
            },
        )
        self.assertEqual(len(TEMPORAL_RISK_NUMERIC_FEATURES), 24)

    def test_validation_rejects_label_or_missingness_inconsistency(self):
        example = self.examples[0]
        invalid_missing = replace(
            example,
            features=replace(
                example.features,
                latest_battery_level_missing=True,
            ),
        )
        invalid_label = replace(
            example,
            label=replace(
                example.label,
                adverse_event_within_horizon=False,
            ),
        )
        for invalid in (invalid_missing, invalid_label):
            with self.assertRaises(TemporalRiskExampleError):
                validate_temporal_risk_example(invalid)

    def test_builder_does_not_recalculate_status_or_import_product_rules(self):
        source = inspect.getsource(build_temporal_risk_examples)
        self.assertNotIn("evaluate_status", source)
        self.assertNotIn("ProductRules", source)


if __name__ == "__main__":
    unittest.main()
