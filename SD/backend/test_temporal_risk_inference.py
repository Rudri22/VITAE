import json
import inspect
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import numpy as np

try:
    from .decision_outbox import StatusDecisionRecord
    from .repository_contract_suite import (
        CONTRACT_TIME,
        contract_assignment,
        contract_decision_record,
        contract_sample,
        contract_shipment_access,
        contract_state,
        contract_trip,
    )
    from .repository_config import (
        RepositoryConfig,
        RepositoryMode,
        compose_repositories,
    )
    from .risk_rules import ApplicationStatus
    from .shipment_access import InMemoryIdentityAccessRepository
    from .simulated_training_corpus import (
        SimulatedCorpusConfig,
        build_approved_simulator_corpus,
    )
    from .sqlite_identity_repository import SQLiteIdentityAccessRepository
    from .sqlite_telemetry_repository import SQLiteTelemetryStateRepository
    from .state_repository import telemetry_record_from_sample
    from .telemetry import TelemetrySource
    from .temporal_risk_baseline import (
        BASELINE_MODEL_VERSION,
        TrainingSourceKind,
        temporal_risk_feature_row,
        train_logistic_regression_baseline,
    )
    from .temporal_risk_calibration import (
        CALIBRATION_METHOD,
        SigmoidProbabilityCalibrator,
        analyze_temporal_risk_calibration,
    )
    from .temporal_risk_examples import (
        TEMPORAL_RISK_FEATURE_VERSION,
        TemporalRiskFeatureContext,
        build_temporal_risk_examples,
        build_temporal_risk_features_from_prefix,
    )
    from .temporal_risk_inference import (
        SIMULATOR_PERFORMANCE_SCOPE,
        TEMPORAL_RISK_ARTIFACT_DIR_ENV,
        TEMPORAL_RISK_MANIFEST_SHA256_ENV,
        TEMPORAL_RISK_MODE_ENV,
        TemporalRiskArtifactError,
        TemporalRiskConfigurationError,
        TemporalRiskInferenceArtifact,
        TemporalRiskInferenceConfig,
        TemporalRiskInferenceMode,
        TemporalRiskInferenceError,
        TemporalRiskInferenceService,
        TemporalRiskNotPredicted,
        TemporalRiskNotPredictedReason,
        TemporalRiskPrediction,
        compose_temporal_risk_inference,
        load_temporal_risk_inference_artifact,
        persist_temporal_risk_inference_artifact,
        temporal_risk_prediction_document,
        temporal_risk_inference_service_from_composition,
    )
    from .trip_identity import TripStatus
except ImportError:
    from decision_outbox import StatusDecisionRecord
    from repository_contract_suite import (
        CONTRACT_TIME,
        contract_assignment,
        contract_decision_record,
        contract_sample,
        contract_shipment_access,
        contract_state,
        contract_trip,
    )
    from repository_config import (
        RepositoryConfig,
        RepositoryMode,
        compose_repositories,
    )
    from risk_rules import ApplicationStatus
    from shipment_access import InMemoryIdentityAccessRepository
    from simulated_training_corpus import (
        SimulatedCorpusConfig,
        build_approved_simulator_corpus,
    )
    from sqlite_identity_repository import SQLiteIdentityAccessRepository
    from sqlite_telemetry_repository import SQLiteTelemetryStateRepository
    from state_repository import telemetry_record_from_sample
    from telemetry import TelemetrySource
    from temporal_risk_baseline import (
        BASELINE_MODEL_VERSION,
        TrainingSourceKind,
        temporal_risk_feature_row,
        train_logistic_regression_baseline,
    )
    from temporal_risk_calibration import (
        CALIBRATION_METHOD,
        SigmoidProbabilityCalibrator,
        analyze_temporal_risk_calibration,
    )
    from temporal_risk_examples import (
        TEMPORAL_RISK_FEATURE_VERSION,
        TemporalRiskFeatureContext,
        build_temporal_risk_examples,
        build_temporal_risk_features_from_prefix,
    )
    from temporal_risk_inference import (
        SIMULATOR_PERFORMANCE_SCOPE,
        TEMPORAL_RISK_ARTIFACT_DIR_ENV,
        TEMPORAL_RISK_MANIFEST_SHA256_ENV,
        TEMPORAL_RISK_MODE_ENV,
        TemporalRiskArtifactError,
        TemporalRiskConfigurationError,
        TemporalRiskInferenceArtifact,
        TemporalRiskInferenceConfig,
        TemporalRiskInferenceMode,
        TemporalRiskInferenceError,
        TemporalRiskInferenceService,
        TemporalRiskNotPredicted,
        TemporalRiskNotPredictedReason,
        TemporalRiskPrediction,
        compose_temporal_risk_inference,
        load_temporal_risk_inference_artifact,
        persist_temporal_risk_inference_artifact,
        temporal_risk_prediction_document,
        temporal_risk_inference_service_from_composition,
    )
    from trip_identity import TripStatus


class _FixedModel:
    def __init__(self, probability=0.2):
        self.probability = probability
        self.named_steps = {"preprocessor": object(), "classifier": object()}

    def predict_proba(self, values):
        return np.asarray(
            [[1.0 - self.probability, self.probability] for _ in values]
        )


class _FixedCalibratorEstimator:
    def __init__(self, probability=0.3):
        self.probability = probability

    def predict_proba(self, values):
        return np.asarray(
            [[1.0 - self.probability, self.probability] for _ in values]
        )


class _StaticIdentityReader:
    def __init__(self, trip):
        self.trip = trip

    def get_trip_by_lot_trip_id(self, lot_trip_id):
        return self.trip if self.trip and self.trip.lot_trip_id == lot_trip_id else None

    def register_trip_and_assignment(self, trip, assignment):
        raise NotImplementedError

    def unregister_planned_trip_and_assignment(self, trip_id, assignment_id):
        raise NotImplementedError

    def transition_trip_and_assignment(self, *args, **kwargs):
        raise NotImplementedError

    def register_trip(self, trip):
        raise NotImplementedError

    def get_trip_by_id(self, trip_id):
        return self.trip if self.trip and self.trip.trip_id == trip_id else None

    def register_device_assignment(self, assignment):
        raise NotImplementedError

    def get_device_assignments(self, device_id):
        return ()


class _StaticHistoryReader:
    def __init__(self, telemetry=(), decisions=()):
        self.telemetry = tuple(telemetry)
        self.decisions = tuple(decisions)

    def get_telemetry_history(self, lot_trip_id):
        return self.telemetry

    def get_decision_history(self, lot_trip_id):
        return self.decisions


class _GrowingHistoryReader(_StaticHistoryReader):
    def __init__(self, telemetry, decisions):
        super().__init__(telemetry, decisions)
        self.telemetry_reads = 0

    def get_telemetry_history(self, lot_trip_id):
        self.telemetry_reads += 1
        if self.telemetry_reads == 1:
            return self.telemetry[:-1]
        return self.telemetry


def _test_artifact(*, model_probability=0.2, calibrated_probability=0.3):
    return TemporalRiskInferenceArtifact(
        model=_FixedModel(model_probability),
        calibrator=SigmoidProbabilityCalibrator(
            _FixedCalibratorEstimator(calibrated_probability)
        ),
        manifest_sha256="a" * 64,
        model_version=BASELINE_MODEL_VERSION,
        calibration_method=CALIBRATION_METHOD,
        feature_version=TEMPORAL_RISK_FEATURE_VERSION,
        dataset_sha256="b" * 64,
        training_source_kind=TrainingSourceKind.APPROVED_SIMULATOR,
        performance_scope=SIMULATOR_PERFORMANCE_SCOPE,
        limitations=("Simulated-only test artifact",),
        risk_policy=None,
    )


def _active_trip():
    return contract_trip(status=TripStatus.ACTIVE)


def _single_prefix():
    sample = contract_sample(minutes=5)
    record = telemetry_record_from_sample(
        "contract-trip", "contract-lot-trip", sample
    )
    state = contract_state(sample)
    return record, state, contract_decision_record(sample, state)


def _seed_repositories(identity, history):
    trip = _active_trip()
    assignment = contract_assignment(active=True)
    identity.register_trip_assignment_and_access(
        trip, assignment, contract_shipment_access()
    )
    record, state, decision = _single_prefix()
    history.commit_processing_bundle(record, state, decision, None, None)
    return trip, record, decision


class TemporalRiskFeatureParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = build_approved_simulator_corpus(
            SimulatedCorpusConfig(trip_count=30, master_seed=24680)
        )

    def test_shared_prefix_builder_exactly_matches_training_features(self):
        source = self.corpus.records[0]
        examples = build_temporal_risk_examples(source)
        self.assertTrue(examples)
        example = examples[len(examples) // 2]
        cutoff_index = next(
            index
            for index, record in enumerate(source.telemetry_records)
            if record.sample_id == example.cutoff_sample_id
        )
        context = TemporalRiskFeatureContext(
            lot_trip_id=source.outcome.lot_trip_id,
            trip_id=source.outcome.trip_id,
            device_id=source.outcome.device_id,
            product_id=source.outcome.product_id,
            presentation=source.outcome.presentation,
            state=source.outcome.state,
            product_rule_version=source.outcome.product_rule_version,
            trip_started_at=source.outcome.trip_started_at,
        )
        rebuilt = build_temporal_risk_features_from_prefix(
            context,
            source.telemetry_records[: cutoff_index + 1],
            source.decision_records[: cutoff_index + 1],
        )
        self.assertEqual(rebuilt, example.features)
        np.testing.assert_equal(
            temporal_risk_feature_row(rebuilt),
            temporal_risk_feature_row(example.features),
        )

    def test_prefix_builder_rejects_misaligned_decision(self):
        record, _, decision = _single_prefix()
        context = TemporalRiskFeatureContext(
            lot_trip_id="contract-lot-trip",
            trip_id="contract-trip",
            device_id="contract-device",
            product_id="gardasil-9",
            presentation="single-dose-prefilled-syringe-0.5-ml",
            state="unopened",
            product_rule_version="uspi-v503-i-2503r017",
            trip_started_at=CONTRACT_TIME,
        )
        with self.assertRaisesRegex(Exception, "identities must align"):
            build_temporal_risk_features_from_prefix(
                context,
                (record,),
                (replace(decision, sample_id="different"),),
            )

    def test_shared_live_feature_builder_has_no_outcome_or_label_dependency(self):
        source = inspect.getsource(build_temporal_risk_features_from_prefix)
        self.assertNotIn("CompletedTripOutcome", source)
        self.assertNotIn("outcome", source)
        self.assertNotIn("label", source)
        self.assertNotIn("horizon", source)


class TemporalRiskInferenceServiceTests(unittest.TestCase):
    def test_real_device_record_remains_in_accepted_inference_history(self):
        composition = compose_repositories(RepositoryConfig(mode=RepositoryMode.MEMORY))
        identity = composition.identity_repository
        history = composition.telemetry_state_repository
        trip = _active_trip()
        identity.register_trip_assignment_and_access(
            trip, contract_assignment(active=True), contract_shipment_access()
        )
        sample = replace(contract_sample(minutes=5), source=TelemetrySource.REAL_DEVICE)
        record = telemetry_record_from_sample(
            trip.trip_id, trip.lot_trip_id, sample
        )
        state = contract_state(sample)
        decision = contract_decision_record(sample, state)
        history.commit_processing_bundle(record, state, decision, None, None)

        result = TemporalRiskInferenceService(
            identity, history, _test_artifact()
        ).predict(trip.lot_trip_id)

        self.assertIsInstance(result, TemporalRiskPrediction)
        self.assertEqual(history.get_telemetry_history(trip.lot_trip_id)[0].source, TelemetrySource.REAL_DEVICE)
        self.assertEqual(result.cutoff_sample_id, sample.sample_id)

    def test_memory_repository_path_returns_probability_only(self):
        composition = compose_repositories(
            RepositoryConfig(mode=RepositoryMode.MEMORY)
        )
        repository = composition.identity_repository
        _, record, _ = _seed_repositories(repository, repository)
        result = temporal_risk_inference_service_from_composition(
            composition, _test_artifact()
        ).predict("contract-lot-trip")
        self.assertIsInstance(result, TemporalRiskPrediction)
        self.assertEqual(result.cutoff_sample_id, record.sample_id)
        self.assertEqual(result.cutoff_at, record.timestamp)
        self.assertEqual(result.adverse_event_probability, 0.3)
        self.assertLessEqual(len(result.evidence_factors), 4)
        evidence_codes = {factor.code for factor in result.evidence_factors}
        self.assertIn("TEMPERATURE_TREND", evidence_codes)
        self.assertIn("OBSERVATION_SPAN", evidence_codes)
        document = temporal_risk_prediction_document(result)
        self.assertEqual(
            {factor["code"] for factor in document["evidenceFactors"]},
            evidence_codes,
        )
        self.assertNotIn("riskPolicy", document)
        lowered = {key.lower() for key in document}
        self.assertFalse({"status", "riskband", "risklevel"} & lowered)

    def test_sqlite_identity_and_history_path_returns_same_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "vitae.db"
            identity = SQLiteIdentityAccessRepository(database)
            history = SQLiteTelemetryStateRepository(database)
            _, record, _ = _seed_repositories(identity, history)
            result = TemporalRiskInferenceService(
                identity, history, _test_artifact()
            ).predict("contract-lot-trip")
        self.assertIsInstance(result, TemporalRiskPrediction)
        self.assertEqual(result.cutoff_at, record.timestamp)

    def test_dynamo_composition_binds_existing_identity_and_history_adapters(self):
        client = Mock()
        composition = compose_repositories(
            RepositoryConfig(
                mode=RepositoryMode.DYNAMODB,
                aws_region="us-east-1",
                identity_table="identity-test",
                telemetry_table="telemetry-test",
                alert_table="alert-test",
            ),
            dynamodb_client=client,
        )
        service = temporal_risk_inference_service_from_composition(
            composition, _test_artifact()
        )
        self.assertIs(
            service._identity_repository,
            composition.identity_repository,
        )
        self.assertIs(
            service._history_repository,
            composition.telemetry_state_repository,
        )
        self.assertEqual(client.describe_table.call_count, 2)

    def test_missing_inactive_empty_and_ineligible_are_not_predictions(self):
        cases = (
            (
                None,
                (),
                (),
                TemporalRiskNotPredictedReason.TRIP_NOT_FOUND,
            ),
            (
                contract_trip(status=TripStatus.PLANNED),
                (),
                (),
                TemporalRiskNotPredictedReason.TRIP_NOT_ACTIVE,
            ),
            (
                _active_trip(),
                (),
                (),
                TemporalRiskNotPredictedReason.NO_ACCEPTED_TELEMETRY,
            ),
        )
        for trip, records, decisions, reason in cases:
            with self.subTest(reason=reason):
                result = TemporalRiskInferenceService(
                    _StaticIdentityReader(trip),
                    _StaticHistoryReader(records, decisions),
                    _test_artifact(),
                ).predict("contract-lot-trip")
                self.assertIsInstance(result, TemporalRiskNotPredicted)
                self.assertEqual(result.reason_code, reason)

        record, _, decision = _single_prefix()
        result = TemporalRiskInferenceService(
            _StaticIdentityReader(_active_trip()),
            _StaticHistoryReader(
                (record,),
                (replace(decision, status=ApplicationStatus.DATA_ERROR),),
            ),
            _test_artifact(),
        ).predict("contract-lot-trip")
        self.assertEqual(
            result.reason_code,
            TemporalRiskNotPredictedReason.CURRENT_STATUS_NOT_ELIGIBLE,
        )

    def test_incoherent_history_fails_closed_after_bounded_retries(self):
        record, _, _ = _single_prefix()
        result = TemporalRiskInferenceService(
            _StaticIdentityReader(_active_trip()),
            _StaticHistoryReader((record,), ()),
            _test_artifact(),
        ).predict("contract-lot-trip")
        self.assertIsInstance(result, TemporalRiskNotPredicted)
        self.assertEqual(
            result.reason_code,
            TemporalRiskNotPredictedReason.HISTORY_NOT_COHERENT,
        )

    def test_atomic_history_growth_retries_to_one_coherent_latest_cutoff(self):
        first_record, first_state, first_decision = _single_prefix()
        second_sample = contract_sample(
            sample_id="contract-sample-2", minutes=10
        )
        second_record = telemetry_record_from_sample(
            "contract-trip", "contract-lot-trip", second_sample
        )
        second_state = contract_state(second_sample, previous=first_state)
        second_decision = contract_decision_record(second_sample, second_state)
        history = _GrowingHistoryReader(
            (first_record, second_record),
            (first_decision, second_decision),
        )
        result = TemporalRiskInferenceService(
            _StaticIdentityReader(_active_trip()), history, _test_artifact()
        ).predict("contract-lot-trip")
        self.assertIsInstance(result, TemporalRiskPrediction)
        self.assertEqual(result.cutoff_sample_id, "contract-sample-2")
        self.assertEqual(history.telemetry_reads, 2)

    def test_probability_must_be_finite_and_bounded(self):
        repository = InMemoryIdentityAccessRepository()
        _seed_repositories(repository, repository)
        with self.assertRaisesRegex(TemporalRiskInferenceError, "Probability"):
            TemporalRiskInferenceService(
                repository,
                repository,
                _test_artifact(calibrated_probability=float("nan")),
            ).predict("contract-lot-trip")


class TemporalRiskInferenceArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = build_approved_simulator_corpus(
            SimulatedCorpusConfig(trip_count=30, master_seed=13579)
        )
        cls.training = train_logistic_regression_baseline(
            cls.corpus.training_dataset
        )
        cls.calibration = analyze_temporal_risk_calibration(
            cls.corpus.training_dataset,
            cls.training,
            bootstrap_replicates=10,
        )

    def _persist(self, directory):
        return persist_temporal_risk_inference_artifact(
            self.training,
            self.calibration,
            directory,
            created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )

    def test_trusted_round_trip_binds_model_calibrator_and_preprocessor(self):
        with tempfile.TemporaryDirectory() as directory:
            files = self._persist(directory)
            artifact = load_temporal_risk_inference_artifact(
                directory,
                expected_manifest_sha256=files.manifest_sha256,
            )
        self.assertEqual(artifact.dataset_sha256, self.training.dataset_sha256)
        self.assertEqual(artifact.risk_policy, None)
        self.assertEqual(artifact.performance_scope, SIMULATOR_PERFORMANCE_SCOPE)
        self.assertTrue(artifact.limitations)
        self.assertEqual(
            set(artifact.model.named_steps), {"preprocessor", "classifier"}
        )

    def test_manifest_trust_hash_is_required_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            self._persist(directory)
            with self.assertRaisesRegex(
                TemporalRiskArtifactError, "manifest trust hash mismatch"
            ):
                load_temporal_risk_inference_artifact(
                    directory,
                    expected_manifest_sha256="0" * 64,
                )

    def test_model_tampering_is_rejected_before_deserialization(self):
        with tempfile.TemporaryDirectory() as directory:
            files = self._persist(directory)
            files.model_path.write_bytes(files.model_path.read_bytes() + b"tamper")
            with self.assertRaisesRegex(TemporalRiskArtifactError, "model hash"):
                load_temporal_risk_inference_artifact(
                    directory,
                    expected_manifest_sha256=files.manifest_sha256,
                )

    def test_manifest_cannot_promote_risk_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            files = self._persist(directory)
            document = json.loads(files.manifest_path.read_text("utf-8"))
            document["risk_policy"] = {"medium": 0.1, "high": 0.2}
            files.manifest_path.write_text(
                json.dumps(document, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            trusted_hash = sha256(files.manifest_path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(TemporalRiskArtifactError, "risk policy"):
                load_temporal_risk_inference_artifact(
                    directory,
                    expected_manifest_sha256=trusted_hash,
                )

    def test_default_configuration_is_disabled_and_builds_no_service(self):
        config = TemporalRiskInferenceConfig.from_environment({})
        self.assertEqual(
            config,
            TemporalRiskInferenceConfig(mode=TemporalRiskInferenceMode.DISABLED),
        )
        composition = compose_repositories(
            RepositoryConfig(mode=RepositoryMode.MEMORY)
        )
        self.assertIsNone(compose_temporal_risk_inference(config, composition))

    def test_partial_or_ambiguous_configuration_fails_explicitly(self):
        cases = (
            {TEMPORAL_RISK_MODE_ENV: "unknown"},
            {TEMPORAL_RISK_MODE_ENV: "artifact"},
            {
                TEMPORAL_RISK_MODE_ENV: "artifact",
                TEMPORAL_RISK_ARTIFACT_DIR_ENV: "artifact-dir",
            },
            {
                TEMPORAL_RISK_ARTIFACT_DIR_ENV: "artifact-dir",
                TEMPORAL_RISK_MANIFEST_SHA256_ENV: "a" * 64,
            },
        )
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(TemporalRiskConfigurationError):
                    TemporalRiskInferenceConfig.from_environment(values)

    def test_artifact_configuration_loads_against_memory_composition(self):
        with tempfile.TemporaryDirectory() as directory:
            files = self._persist(directory)
            config = TemporalRiskInferenceConfig.from_environment(
                {
                    TEMPORAL_RISK_MODE_ENV: "artifact",
                    TEMPORAL_RISK_ARTIFACT_DIR_ENV: directory,
                    TEMPORAL_RISK_MANIFEST_SHA256_ENV: files.manifest_sha256,
                }
            )
            composition = compose_repositories(
                RepositoryConfig(mode=RepositoryMode.MEMORY)
            )
            service = compose_temporal_risk_inference(config, composition)
        self.assertIsInstance(service, TemporalRiskInferenceService)

    def test_invalid_configured_artifact_is_not_silently_disabled(self):
        config = TemporalRiskInferenceConfig(
            mode=TemporalRiskInferenceMode.ARTIFACT,
            artifact_directory="missing-artifact-directory",
            expected_manifest_sha256="a" * 64,
        )
        composition = compose_repositories(
            RepositoryConfig(mode=RepositoryMode.MEMORY)
        )
        with self.assertRaisesRegex(
            TemporalRiskConfigurationError, "artifact is invalid"
        ):
            compose_temporal_risk_inference(config, composition)


if __name__ == "__main__":
    unittest.main()
