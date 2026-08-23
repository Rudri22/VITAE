import inspect
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

try:
    from . import simulated_training_corpus
    from .completed_trip_dataset import validate_completed_trip_dataset_record
    from .simulated_training_corpus import (
        SIMULATED_CORPUS_SCHEMA,
        SIMULATED_CORPUS_SCHEMA_VERSION,
        SIMULATED_SCENARIO_FAMILIES,
        SimulatedCorpusConfig,
        SimulatedTrainingCorpusError,
        build_approved_simulator_corpus,
        persist_approved_simulator_corpus,
    )
    from .temporal_risk_baseline import (
        TrainingSourceKind,
        assess_training_readiness,
        load_temporal_risk_jsonl,
    )
except ImportError:
    import simulated_training_corpus
    from completed_trip_dataset import validate_completed_trip_dataset_record
    from simulated_training_corpus import (
        SIMULATED_CORPUS_SCHEMA,
        SIMULATED_CORPUS_SCHEMA_VERSION,
        SIMULATED_SCENARIO_FAMILIES,
        SimulatedCorpusConfig,
        SimulatedTrainingCorpusError,
        build_approved_simulator_corpus,
        persist_approved_simulator_corpus,
    )
    from temporal_risk_baseline import (
        TrainingSourceKind,
        assess_training_readiness,
        load_temporal_risk_jsonl,
    )


class SimulatedTrainingCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = SimulatedCorpusConfig(trip_count=30, master_seed=445566)
        cls.corpus = build_approved_simulator_corpus(cls.config)

    def test_uses_operational_simulator_path_once_per_trip(self):
        original = simulated_training_corpus.run_scenario
        with patch.object(
            simulated_training_corpus,
            "run_scenario",
            wraps=original,
        ) as run:
            corpus = build_approved_simulator_corpus(
                SimulatedCorpusConfig(trip_count=5, master_seed=101)
            )
        self.assertEqual(run.call_count, 5)
        self.assertEqual(len(corpus.records), 5)

    def test_source_is_explicitly_approved_simulator(self):
        self.assertEqual(
            self.corpus.training_dataset.source_kind,
            TrainingSourceKind.APPROVED_SIMULATOR,
        )
        self.assertEqual(
            self.corpus.manifest.source_kind,
            TrainingSourceKind.APPROVED_SIMULATOR.value,
        )
        self.assertTrue(
            all(
                record.source.value == "SIMULATOR"
                for history in self.corpus.records
                for record in history.telemetry_records
            )
        )

    def test_all_scenario_families_are_present_and_balanced(self):
        counts = dict(self.corpus.manifest.scenario_family_counts)
        self.assertEqual(set(counts), set(SIMULATED_SCENARIO_FAMILIES))
        self.assertEqual(set(counts.values()), {6})
        self.assertGreater(
            len(
                {
                    item.nominal_cadence_minutes
                    for item in self.corpus.manifest.trip_summaries
                }
            ),
            1,
        )

    def test_completed_records_are_authoritative_and_have_unique_identity(self):
        lot_trip_ids = tuple(record.lot_trip_id for record in self.corpus.records)
        self.assertEqual(len(lot_trip_ids), len(set(lot_trip_ids)))
        for record in self.corpus.records:
            self.assertIs(validate_completed_trip_dataset_record(record), record)
            self.assertTrue(record.telemetry_records)
            self.assertEqual(
                record.outcome.final_live_state_revision,
                len(record.telemetry_records),
            )

    def test_labels_contain_both_classes_and_are_derived_from_history(self):
        labels = {
            example.label.adverse_event_within_horizon
            for example in self.corpus.training_dataset.examples
        }
        self.assertEqual(labels, {False, True})
        source = inspect.getsource(simulated_training_corpus)
        self.assertNotIn("evaluate_status(", source)
        self.assertNotIn("TemporalRiskLabel(", source)
        self.assertNotIn("adverse_event_within_horizon=", source)

        family_by_trip = {
            item.lot_trip_id: item.scenario_family
            for item in self.corpus.manifest.trip_summaries
        }
        positive_families = {
            family_by_trip[example.lot_trip_id]
            for example in self.corpus.training_dataset.examples
            if example.label.adverse_event_within_horizon
        }
        self.assertTrue(
            {
                "gradual-warming-adverse",
                "sustained-high-excursion",
                "sustained-low-excursion",
            }.issubset(positive_families)
        )

    def test_valid_corpus_scenarios_do_not_create_data_error_decisions(self):
        for record in self.corpus.records:
            statuses = {decision.status.value for decision in record.decision_records}
            self.assertNotIn("DATA_ERROR", statuses)

    def test_same_seed_is_reproducible(self):
        repeated = build_approved_simulator_corpus(self.config)
        self.assertEqual(repeated.manifest, self.corpus.manifest)
        self.assertEqual(
            repeated.training_dataset.examples,
            self.corpus.training_dataset.examples,
        )

    def test_different_seed_changes_corpus_fingerprint(self):
        changed = build_approved_simulator_corpus(
            replace(self.config, master_seed=self.config.master_seed + 1)
        )
        self.assertNotEqual(
            changed.manifest.temporal_examples_sha256,
            self.corpus.manifest.temporal_examples_sha256,
        )

    def test_manifest_has_versioned_provenance_and_content_hashes(self):
        manifest = self.corpus.manifest
        self.assertEqual(manifest.schema, SIMULATED_CORPUS_SCHEMA)
        self.assertEqual(manifest.schema_version, SIMULATED_CORPUS_SCHEMA_VERSION)
        self.assertEqual(len(manifest.completed_history_sha256), 64)
        self.assertEqual(len(manifest.temporal_examples_sha256), 64)
        self.assertEqual(len(manifest.journey_examples_sha256), 64)
        self.assertTrue(
            all(summary.planned_arrival_at for summary in manifest.trip_summaries)
        )

    def test_persisted_examples_round_trip_with_approved_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = persist_approved_simulator_corpus(self.corpus, directory)
            loaded = load_temporal_risk_jsonl(
                paths["temporal_risk_examples"],
                source_kind=TrainingSourceKind.APPROVED_SIMULATOR,
                source_id=self.corpus.training_dataset.source_id,
            )
            self.assertEqual(loaded, self.corpus.training_dataset)
            manifest = json.loads(Path(paths["manifest"]).read_text("utf-8"))
            self.assertEqual(
                manifest["temporal_examples_sha256"],
                self.corpus.manifest.temporal_examples_sha256,
            )
            self.assertEqual(
                manifest["journey_examples_sha256"],
                self.corpus.manifest.journey_examples_sha256,
            )
            self.assertTrue(paths["journey_risk_examples"].is_file())

    def test_corpus_passes_structural_training_readiness(self):
        assessment = assess_training_readiness(self.corpus.training_dataset)
        self.assertTrue(assessment.ready, assessment.hard_failures)
        self.assertIsNotNone(assessment.split)

    def test_invalid_config_fails_before_generation(self):
        with self.assertRaises(SimulatedTrainingCorpusError):
            build_approved_simulator_corpus(SimulatedCorpusConfig(trip_count=4))
        with self.assertRaises(SimulatedTrainingCorpusError):
            build_approved_simulator_corpus(
                SimulatedCorpusConfig(start_at=self.config.start_at.replace(tzinfo=None))
            )


if __name__ == "__main__":
    unittest.main()
