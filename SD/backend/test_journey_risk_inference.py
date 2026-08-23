import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    from .journey_risk_examples import JOURNEY_RISK_FEATURE_VERSION, JOURNEY_RISK_LABEL_VERSION
    from .journey_risk_inference import (
        JourneyRiskArtifactError,
        JourneyRiskInferenceArtifact,
        JourneyRiskInferenceService,
        JourneyRiskNotPredictedReason,
        JourneyRiskPrediction,
        journey_risk_document,
        load_journey_risk_artifact,
        persist_journey_risk_artifact,
    )
    from .journey_risk_training import JOURNEY_RISK_MODEL_VERSION, JOURNEY_RISK_VALIDATION_STATUS, train_and_compare_journey_risk_models
    from .repository_contract_suite import contract_assignment, contract_decision_record, contract_sample, contract_shipment_access, contract_state, contract_trip
    from .decision_outbox import InMemoryProcessingBundleRepository
    from .simulated_training_corpus import SimulatedCorpusConfig, build_approved_simulator_corpus
    from .state_repository import telemetry_record_from_sample
    from .temporal_risk_baseline import TrainingSourceKind
    from .trip_identity import TripStatus
except ImportError:
    from journey_risk_examples import JOURNEY_RISK_FEATURE_VERSION, JOURNEY_RISK_LABEL_VERSION
    from journey_risk_inference import JourneyRiskArtifactError, JourneyRiskInferenceArtifact, JourneyRiskInferenceService, JourneyRiskNotPredictedReason, JourneyRiskPrediction, journey_risk_document, load_journey_risk_artifact, persist_journey_risk_artifact
    from journey_risk_training import JOURNEY_RISK_MODEL_VERSION, JOURNEY_RISK_VALIDATION_STATUS, train_and_compare_journey_risk_models
    from repository_contract_suite import contract_assignment, contract_decision_record, contract_sample, contract_shipment_access, contract_state, contract_trip
    from decision_outbox import InMemoryProcessingBundleRepository
    from simulated_training_corpus import SimulatedCorpusConfig, build_approved_simulator_corpus
    from state_repository import telemetry_record_from_sample
    from temporal_risk_baseline import TrainingSourceKind
    from trip_identity import TripStatus


class _FixedEstimator:
    def __init__(self, probability=0.72):
        self.probability = probability

    def predict_probabilities(self, rows):
        return (self.probability,) * len(rows)


def _artifact(probability=0.72):
    return JourneyRiskInferenceArtifact(
        _FixedEstimator(probability), "a" * 64, JOURNEY_RISK_MODEL_VERSION, "LOGISTIC",
        JOURNEY_RISK_FEATURE_VERSION, JOURNEY_RISK_LABEL_VERSION, "b" * 64,
        TrainingSourceKind.APPROVED_SIMULATOR, JOURNEY_RISK_VALIDATION_STATUS,
        ("Simulator-trained test artifact",),
    )


def _repositories():
    identity = InMemoryProcessingBundleRepository()
    history = identity
    trip = contract_trip(status=TripStatus.ACTIVE)
    identity.register_trip_and_assignment(trip, contract_assignment(active=True))
    sample = contract_sample(minutes=5)
    state = contract_state(sample)
    history.commit_processing_bundle(
        telemetry_record_from_sample(trip.trip_id, trip.lot_trip_id, sample), state,
        contract_decision_record(sample, state), None, None,
    )
    return identity, history, trip


class JourneyRiskInferenceTests(unittest.TestCase):
    def test_valid_route_horizon_produces_bounded_probability(self):
        identity, history, trip = _repositories()
        value = JourneyRiskInferenceService(identity, history, _artifact()).predict(trip.lot_trip_id, 97)
        self.assertIsInstance(value, JourneyRiskPrediction)
        self.assertEqual(value.remaining_journey_minutes, 97)
        self.assertAlmostEqual(value.deterioration_probability, 0.72)
        document = journey_risk_document(value)
        self.assertNotIn("plannedArrivalAt", document)
        self.assertNotIn("firstAdverseAt", document)
        self.assertNotIn("label", document)

    def test_missing_route_horizon_is_explicitly_unavailable(self):
        identity, history, trip = _repositories()
        value = JourneyRiskInferenceService(identity, history, _artifact()).predict(trip.lot_trip_id, None)
        self.assertEqual(value.reason_code, JourneyRiskNotPredictedReason.REMAINING_JOURNEY_DURATION_UNAVAILABLE)

    def test_invalid_probability_fails_safe(self):
        identity, history, trip = _repositories()
        value = JourneyRiskInferenceService(identity, history, _artifact(1.2)).predict(trip.lot_trip_id, 50)
        self.assertEqual(value.reason_code, JourneyRiskNotPredictedReason.HISTORY_NOT_COHERENT)

    def test_artifact_manifest_rejects_fixed_horizon_metadata(self):
        corpus = build_approved_simulator_corpus(SimulatedCorpusConfig(trip_count=30, master_seed=7654))
        result = train_and_compare_journey_risk_models(corpus.journey_training_dataset)
        with tempfile.TemporaryDirectory() as directory:
            path, digest = persist_journey_risk_artifact(result, directory, created_at=datetime(2026, 8, 23, tzinfo=timezone.utc))
            manifest = json.loads(Path(path).read_text("utf-8"))
            manifest["horizonSemantics"] = "FIXED_30_MINUTES"
            Path(path).write_text(json.dumps(manifest), encoding="utf-8")
            from hashlib import sha256
            changed = sha256(Path(path).read_bytes()).hexdigest()
            with self.assertRaises(JourneyRiskArtifactError):
                load_journey_risk_artifact(directory, expected_manifest_sha256=changed)

    def test_trusted_artifact_round_trip_keeps_journey_metadata(self):
        corpus = build_approved_simulator_corpus(SimulatedCorpusConfig(trip_count=30, master_seed=9876))
        result = train_and_compare_journey_risk_models(corpus.journey_training_dataset)
        with tempfile.TemporaryDirectory() as directory:
            _, digest = persist_journey_risk_artifact(
                result, directory, created_at=datetime(2026, 8, 23, tzinfo=timezone.utc)
            )
            loaded = load_journey_risk_artifact(directory, expected_manifest_sha256=digest)
            self.assertEqual(loaded.model_version, JOURNEY_RISK_MODEL_VERSION)
            self.assertEqual(loaded.feature_version, JOURNEY_RISK_FEATURE_VERSION)
            self.assertEqual(loaded.label_version, JOURNEY_RISK_LABEL_VERSION)
            self.assertEqual(loaded.selected_strategy, result.selected_strategy)


if __name__ == "__main__":
    unittest.main()
