"""Optional journey-aware inference over an authoritative telemetry prefix."""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any, Optional, Tuple, Union

try:
    from .journey_risk_examples import (
        JOURNEY_RISK_FEATURE_VERSION,
        JOURNEY_RISK_LABEL_VERSION,
        JOURNEY_RISK_TARGET_NAME,
        JourneyRiskFeatures,
    )
    from .journey_risk_training import (
        JOURNEY_RISK_CALIBRATION_METHOD,
        JOURNEY_RISK_MODEL_VERSION,
        JOURNEY_RISK_VALIDATION_STATUS,
        JourneyRiskTrainingResult,
        journey_risk_feature_row,
    )
    from .temporal_risk_baseline import TrainingSourceKind
    from .temporal_risk_examples import (
        TEMPORAL_RISK_ELIGIBLE_CUTOFF_STATUSES,
        TemporalRiskExampleError,
        build_temporal_risk_features_from_prefix,
    )
    from .temporal_risk_inference import (
        TemporalRiskInferenceError,
        _aligned_prefix,
        _feature_context,
    )
    from .trip_identity import TripStatus
except ImportError:
    from journey_risk_examples import (
        JOURNEY_RISK_FEATURE_VERSION,
        JOURNEY_RISK_LABEL_VERSION,
        JOURNEY_RISK_TARGET_NAME,
        JourneyRiskFeatures,
    )
    from journey_risk_training import (
        JOURNEY_RISK_CALIBRATION_METHOD,
        JOURNEY_RISK_MODEL_VERSION,
        JOURNEY_RISK_VALIDATION_STATUS,
        JourneyRiskTrainingResult,
        journey_risk_feature_row,
    )
    from temporal_risk_baseline import TrainingSourceKind
    from temporal_risk_examples import (
        TEMPORAL_RISK_ELIGIBLE_CUTOFF_STATUSES,
        TemporalRiskExampleError,
        build_temporal_risk_features_from_prefix,
    )
    from temporal_risk_inference import TemporalRiskInferenceError, _aligned_prefix, _feature_context
    from trip_identity import TripStatus


JOURNEY_RISK_ARTIFACT_SCHEMA = "vitae.journey_risk_inference_artifact"
JOURNEY_RISK_ARTIFACT_SCHEMA_VERSION = 1
JOURNEY_RISK_PREDICTION_VERSION = "journey-risk-prediction-v1"
JOURNEY_RISK_MODE_ENV = "VITAE_JOURNEY_RISK_MODE"
JOURNEY_RISK_ARTIFACT_DIR_ENV = "VITAE_JOURNEY_RISK_ARTIFACT_DIR"
JOURNEY_RISK_MANIFEST_SHA256_ENV = "VITAE_JOURNEY_RISK_MANIFEST_SHA256"
_ESTIMATOR_FILENAME = "journey-estimator.joblib"
_MANIFEST_FILENAME = "journey-inference-manifest.json"
_MAX_SNAPSHOT_ATTEMPTS = 3


class JourneyRiskInferenceMode(str, Enum):
    DISABLED = "disabled"
    ARTIFACT = "artifact"


class JourneyRiskNotPredictedReason(str, Enum):
    REMAINING_JOURNEY_DURATION_UNAVAILABLE = "REMAINING_JOURNEY_DURATION_UNAVAILABLE"
    TRIP_NOT_FOUND = "TRIP_NOT_FOUND"
    TRIP_NOT_ACTIVE = "TRIP_NOT_ACTIVE"
    NO_ACCEPTED_TELEMETRY = "NO_ACCEPTED_TELEMETRY"
    HISTORY_NOT_COHERENT = "HISTORY_NOT_COHERENT"
    CURRENT_STATUS_NOT_ELIGIBLE = "CURRENT_STATUS_NOT_ELIGIBLE"
    CONCURRENT_UPDATE = "CONCURRENT_UPDATE"
    INFERENCE_UNAVAILABLE = "INFERENCE_UNAVAILABLE"


@dataclass(frozen=True)
class JourneyRiskPrediction:
    prediction_version: str
    lot_trip_id: str
    cutoff_sample_id: str
    cutoff_at: datetime
    remaining_journey_minutes: float
    deterioration_probability: float
    target: str
    model_version: str
    selected_strategy: str
    feature_version: str
    label_version: str
    artifact_manifest_sha256: str
    training_source_kind: TrainingSourceKind
    validation_status: str
    limitations: Tuple[str, ...]


@dataclass(frozen=True)
class JourneyRiskNotPredicted:
    lot_trip_id: str
    reason_code: JourneyRiskNotPredictedReason
    detail: str


@dataclass(frozen=True)
class JourneyRiskInferenceArtifact:
    estimator: Any
    manifest_sha256: str
    model_version: str
    selected_strategy: str
    feature_version: str
    label_version: str
    dataset_sha256: str
    training_source_kind: TrainingSourceKind
    validation_status: str
    limitations: Tuple[str, ...]


@dataclass(frozen=True)
class JourneyRiskInferenceConfig:
    mode: JourneyRiskInferenceMode
    artifact_directory: Optional[str] = None
    expected_manifest_sha256: Optional[str] = None

    @classmethod
    def from_environment(cls, environment=None):
        values = os.environ if environment is None else environment
        raw = str(values.get(JOURNEY_RISK_MODE_ENV, "disabled")).strip().lower()
        try:
            mode = JourneyRiskInferenceMode(raw)
        except ValueError as error:
            raise JourneyRiskConfigurationError("Journey-risk mode must be disabled or artifact") from error
        directory = _optional(values.get(JOURNEY_RISK_ARTIFACT_DIR_ENV))
        expected = _optional(values.get(JOURNEY_RISK_MANIFEST_SHA256_ENV))
        if mode == JourneyRiskInferenceMode.DISABLED:
            if directory is not None or expected is not None:
                raise JourneyRiskConfigurationError("Journey-risk artifact settings require artifact mode")
            return cls(mode)
        if directory is None or expected is None or not _is_sha256(expected):
            raise JourneyRiskConfigurationError("Artifact mode requires a directory and trusted manifest SHA-256")
        return cls(mode, directory, expected.lower())


class JourneyRiskInferenceError(ValueError):
    pass


class JourneyRiskArtifactError(JourneyRiskInferenceError):
    pass


class JourneyRiskConfigurationError(JourneyRiskInferenceError):
    pass


class JourneyRiskInferenceService:
    def __init__(self, identity_repository, history_repository, artifact):
        _validate_artifact(artifact)
        self._identity_repository = identity_repository
        self._history_repository = history_repository
        self._artifact = artifact

    def predict(self, lot_trip_id, remaining_journey_minutes):
        lot_trip = str(lot_trip_id or "").strip()
        if not lot_trip:
            raise ValueError("lot_trip_id is required")
        if remaining_journey_minutes is None:
            return JourneyRiskNotPredicted(
                lot_trip,
                JourneyRiskNotPredictedReason.REMAINING_JOURNEY_DURATION_UNAVAILABLE,
                "Authoritative remaining route duration is unavailable",
            )
        remaining = _positive_number(remaining_journey_minutes, "remaining_journey_minutes")
        saw_change = False
        for _ in range(_MAX_SNAPSHOT_ATTEMPTS):
            trip = self._identity_repository.get_trip_by_lot_trip_id(lot_trip)
            if trip is None:
                return JourneyRiskNotPredicted(lot_trip, JourneyRiskNotPredictedReason.TRIP_NOT_FOUND, "Lot trip is not registered")
            if trip.status != TripStatus.ACTIVE:
                return JourneyRiskNotPredicted(lot_trip, JourneyRiskNotPredictedReason.TRIP_NOT_ACTIVE, "Journey risk is available only for active trips")
            records = tuple(self._history_repository.get_telemetry_history(lot_trip))
            decisions = tuple(self._history_repository.get_decision_history(lot_trip))
            if self._identity_repository.get_trip_by_lot_trip_id(lot_trip) != trip:
                saw_change = True
                continue
            if not records or not decisions:
                return JourneyRiskNotPredicted(lot_trip, JourneyRiskNotPredictedReason.NO_ACCEPTED_TELEMETRY, "No accepted telemetry prefix exists")
            try:
                records, decisions = _aligned_prefix(trip, records, decisions)
                if decisions[-1].status not in TEMPORAL_RISK_ELIGIBLE_CUTOFF_STATUSES:
                    return JourneyRiskNotPredicted(lot_trip, JourneyRiskNotPredictedReason.CURRENT_STATUS_NOT_ELIGIBLE, "Latest deterministic status is not an eligible forecast cutoff")
                temporal = build_temporal_risk_features_from_prefix(_feature_context(trip), records, decisions)
                probability = _predict(self._artifact.estimator, JourneyRiskFeatures(temporal, remaining))
            except (TemporalRiskInferenceError, TemporalRiskExampleError, ValueError) as error:
                return JourneyRiskNotPredicted(lot_trip, JourneyRiskNotPredictedReason.HISTORY_NOT_COHERENT, str(error))
            latest = records[-1]
            return JourneyRiskPrediction(
                JOURNEY_RISK_PREDICTION_VERSION,
                lot_trip,
                latest.sample_id,
                latest.timestamp,
                remaining,
                probability,
                JOURNEY_RISK_TARGET_NAME,
                self._artifact.model_version,
                self._artifact.selected_strategy,
                self._artifact.feature_version,
                self._artifact.label_version,
                self._artifact.manifest_sha256,
                self._artifact.training_source_kind,
                self._artifact.validation_status,
                self._artifact.limitations,
            )
        return JourneyRiskNotPredicted(
            lot_trip,
            JourneyRiskNotPredictedReason.CONCURRENT_UPDATE if saw_change else JourneyRiskNotPredictedReason.HISTORY_NOT_COHERENT,
            "Trip lifecycle changed while the inference prefix was read",
        )


def compose_journey_risk_inference(config, composition):
    if config.mode == JourneyRiskInferenceMode.DISABLED:
        return None
    try:
        artifact = load_journey_risk_artifact(config.artifact_directory, expected_manifest_sha256=config.expected_manifest_sha256)
        return JourneyRiskInferenceService(composition.identity_repository, composition.telemetry_state_repository, artifact)
    except Exception as error:
        raise JourneyRiskConfigurationError("Configured journey-risk artifact is invalid") from error


def persist_journey_risk_artifact(result, directory, *, created_at):
    if not isinstance(result, JourneyRiskTrainingResult):
        raise JourneyRiskArtifactError("result must be JourneyRiskTrainingResult")
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise JourneyRiskArtifactError("created_at must be timezone-aware")
    try:
        import joblib
        import sklearn
    except ImportError as error:
        raise JourneyRiskArtifactError("scikit-learn and joblib are required") from error
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    estimator_path = destination / _ESTIMATOR_FILENAME
    manifest_path = destination / _MANIFEST_FILENAME
    joblib.dump(result.estimator, estimator_path)
    manifest = {
        "schema": JOURNEY_RISK_ARTIFACT_SCHEMA,
        "schemaVersion": JOURNEY_RISK_ARTIFACT_SCHEMA_VERSION,
        "createdAt": created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "modelVersion": JOURNEY_RISK_MODEL_VERSION,
        "target": JOURNEY_RISK_TARGET_NAME,
        "horizonSemantics": "VARIABLE_REMAINING_JOURNEY_TO_DESTINATION",
        "featureVersion": JOURNEY_RISK_FEATURE_VERSION,
        "labelVersion": JOURNEY_RISK_LABEL_VERSION,
        "selectedStrategy": result.selected_strategy,
        "calibrationMethod": JOURNEY_RISK_CALIBRATION_METHOD,
        "trainingSourceKind": TrainingSourceKind.APPROVED_SIMULATOR.value,
        "validationStatus": JOURNEY_RISK_VALIDATION_STATUS,
        "datasetSha256": result.dataset_sha256,
        "riskPolicy": None,
        "thresholds": {"medium": 0.20, "high": 0.50, "status": "ENGINEERING_DEMO_POLICY"},
        "limitations": ["Simulator-trained engineering proof of concept; real-device and clinical performance are unknown."],
        "estimatorFile": _ESTIMATOR_FILENAME,
        "estimatorSha256": _file_sha256(estimator_path),
        "scikitLearnVersion": sklearn.__version__,
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest_path, _file_sha256(manifest_path)


def load_journey_risk_artifact(directory, *, expected_manifest_sha256):
    destination = Path(directory)
    manifest_path = destination / _MANIFEST_FILENAME
    if not manifest_path.is_file() or _file_sha256(manifest_path) != str(expected_manifest_sha256).lower():
        raise JourneyRiskArtifactError("Journey-risk manifest trust hash mismatch")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        import joblib
        import sklearn
    except Exception as error:
        raise JourneyRiskArtifactError("Journey-risk artifact cannot be read") from error
    required = {
        "schema": JOURNEY_RISK_ARTIFACT_SCHEMA,
        "schemaVersion": 1,
        "modelVersion": JOURNEY_RISK_MODEL_VERSION,
        "target": JOURNEY_RISK_TARGET_NAME,
        "horizonSemantics": "VARIABLE_REMAINING_JOURNEY_TO_DESTINATION",
        "featureVersion": JOURNEY_RISK_FEATURE_VERSION,
        "labelVersion": JOURNEY_RISK_LABEL_VERSION,
        "validationStatus": JOURNEY_RISK_VALIDATION_STATUS,
        "riskPolicy": None,
    }
    if any(manifest.get(key) != value for key, value in required.items()):
        raise JourneyRiskArtifactError("Journey-risk manifest is incompatible")
    if manifest.get("scikitLearnVersion") != sklearn.__version__:
        raise JourneyRiskArtifactError("Journey-risk scikit-learn version mismatch")
    path = destination / manifest.get("estimatorFile", "")
    if _file_sha256(path) != manifest.get("estimatorSha256"):
        raise JourneyRiskArtifactError("Journey-risk estimator hash mismatch")
    try:
        source_kind = TrainingSourceKind(manifest["trainingSourceKind"])
        estimator = joblib.load(path)
    except Exception as error:
        raise JourneyRiskArtifactError("Journey-risk estimator is invalid") from error
    artifact = JourneyRiskInferenceArtifact(
        estimator, _file_sha256(manifest_path), manifest["modelVersion"], manifest["selectedStrategy"],
        manifest["featureVersion"], manifest["labelVersion"], manifest["datasetSha256"], source_kind,
        manifest["validationStatus"], tuple(manifest.get("limitations", ())),
    )
    _validate_artifact(artifact)
    return artifact


def journey_risk_document(value):
    if isinstance(value, JourneyRiskPrediction):
        return {
            "available": True,
            "probability": value.deterioration_probability,
            "horizonMinutes": value.remaining_journey_minutes,
            "horizon": "UNTIL_DESTINATION",
            "target": "DETERIORATION_BEFORE_DESTINATION",
            "modelVersion": value.model_version,
            "selectedStrategy": value.selected_strategy,
            "source": "JOURNEY_AWARE_MODEL",
            "validationStatus": value.validation_status,
            "trainingSource": value.training_source_kind.value,
            "limitations": list(value.limitations),
            "cutoffSampleId": value.cutoff_sample_id,
            "cutoffAt": value.cutoff_at.isoformat(),
        }
    if isinstance(value, JourneyRiskNotPredicted):
        return {"available": False, "reason": value.reason_code.value, "detail": value.detail}
    raise TypeError("journey risk result is invalid")


def probability_from_journey_risk(value):
    return value.deterioration_probability if isinstance(value, JourneyRiskPrediction) else None


def _validate_artifact(value):
    if not isinstance(value, JourneyRiskInferenceArtifact):
        raise JourneyRiskArtifactError("artifact must be JourneyRiskInferenceArtifact")
    if value.model_version != JOURNEY_RISK_MODEL_VERSION or value.feature_version != JOURNEY_RISK_FEATURE_VERSION or value.label_version != JOURNEY_RISK_LABEL_VERSION:
        raise JourneyRiskArtifactError("Journey-risk artifact metadata is incompatible")
    if value.training_source_kind not in (TrainingSourceKind.APPROVED_SIMULATOR, TrainingSourceKind.REAL_OPERATIONAL):
        raise JourneyRiskArtifactError("Journey-risk training source is not approved")


def _predict(estimator, features):
    values = estimator.predict_probabilities((journey_risk_feature_row(features),))
    if len(values) != 1:
        raise JourneyRiskInferenceError("Journey-risk estimator returned an invalid result")
    return _probability(values[0])


def _probability(value):
    if not isinstance(value, (int, float)) or not isfinite(value) or not 0 <= value <= 1:
        raise JourneyRiskInferenceError("Journey-risk probability must be between zero and one")
    return float(value)


def _positive_number(value, name):
    if not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return float(value)


def _optional(value):
    normalized = str(value or "").strip()
    return normalized or None


def _is_sha256(value):
    return len(value) == 64 and all(character in "0123456789abcdefABCDEF" for character in value)


def _file_sha256(path):
    try:
        return sha256(Path(path).read_bytes()).hexdigest()
    except OSError as error:
        raise JourneyRiskArtifactError("Journey-risk artifact file is unreadable") from error
