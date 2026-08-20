import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any, Optional, Protocol, Tuple, Union, runtime_checkable

try:
    from .decision_outbox import StatusDecisionRecord
    from .state_repository import TelemetryRecord
    from .temporal_risk_baseline import (
        BASELINE_MODEL_VERSION,
        LogisticBaselineTrainingResult,
        TrainingSourceKind,
        temporal_risk_feature_row,
    )
    from .temporal_risk_calibration import (
        CALIBRATION_METHOD,
        SigmoidProbabilityCalibrator,
        TemporalRiskCalibrationAnalysis,
    )
    from .temporal_risk_examples import (
        TEMPORAL_RISK_CATEGORICAL_FEATURES,
        TEMPORAL_RISK_FEATURE_VERSION,
        TEMPORAL_RISK_NUMERIC_FEATURES,
        TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES,
        TEMPORAL_RISK_TARGET_NAME,
        TEMPORAL_RISK_ELIGIBLE_CUTOFF_STATUSES,
        TemporalRiskExampleError,
        TemporalRiskFeatureContext,
        build_temporal_risk_features_from_prefix,
    )
    from .trip_identity import TripIdentity, TripStatus
except ImportError:
    from decision_outbox import StatusDecisionRecord
    from state_repository import TelemetryRecord
    from temporal_risk_baseline import (
        BASELINE_MODEL_VERSION,
        LogisticBaselineTrainingResult,
        TrainingSourceKind,
        temporal_risk_feature_row,
    )
    from temporal_risk_calibration import (
        CALIBRATION_METHOD,
        SigmoidProbabilityCalibrator,
        TemporalRiskCalibrationAnalysis,
    )
    from temporal_risk_examples import (
        TEMPORAL_RISK_CATEGORICAL_FEATURES,
        TEMPORAL_RISK_FEATURE_VERSION,
        TEMPORAL_RISK_NUMERIC_FEATURES,
        TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES,
        TEMPORAL_RISK_TARGET_NAME,
        TEMPORAL_RISK_ELIGIBLE_CUTOFF_STATUSES,
        TemporalRiskExampleError,
        TemporalRiskFeatureContext,
        build_temporal_risk_features_from_prefix,
    )
    from trip_identity import TripIdentity, TripStatus


TEMPORAL_RISK_INFERENCE_ARTIFACT_SCHEMA = "vitae.temporal_risk_inference_artifact"
TEMPORAL_RISK_INFERENCE_ARTIFACT_SCHEMA_VERSION = 1
TEMPORAL_RISK_PREDICTION_VERSION = "temporal-risk-prediction-v1"
SIMULATOR_PERFORMANCE_SCOPE = "SIMULATED_ENGINEERING_ONLY"
REAL_PERFORMANCE_SCOPE = "REAL_OPERATIONAL"
_MODEL_FILENAME = "model.joblib"
_CALIBRATOR_FILENAME = "calibrator.joblib"
_MANIFEST_FILENAME = "inference-manifest.json"
_MAX_SNAPSHOT_ATTEMPTS = 3


class TemporalRiskNotPredictedReason(str, Enum):
    TRIP_NOT_FOUND = "TRIP_NOT_FOUND"
    TRIP_NOT_ACTIVE = "TRIP_NOT_ACTIVE"
    NO_ACCEPTED_TELEMETRY = "NO_ACCEPTED_TELEMETRY"
    HISTORY_NOT_COHERENT = "HISTORY_NOT_COHERENT"
    CURRENT_STATUS_NOT_ELIGIBLE = "CURRENT_STATUS_NOT_ELIGIBLE"
    CONCURRENT_UPDATE = "CONCURRENT_UPDATE"


@dataclass(frozen=True)
class TemporalRiskPrediction:
    prediction_version: str
    lot_trip_id: str
    trip_id: str
    cutoff_sample_id: str
    cutoff_at: datetime
    horizon_ends_at: datetime
    prediction_horizon_minutes: int
    adverse_event_probability: float
    model_version: str
    calibration_method: str
    feature_version: str
    artifact_manifest_sha256: str
    training_source_kind: TrainingSourceKind
    performance_scope: str
    limitations: Tuple[str, ...]


@dataclass(frozen=True)
class TemporalRiskNotPredicted:
    lot_trip_id: str
    reason_code: TemporalRiskNotPredictedReason
    detail: str


@dataclass(frozen=True)
class TemporalRiskInferenceArtifact:
    model: Any
    calibrator: SigmoidProbabilityCalibrator
    manifest_sha256: str
    model_version: str
    calibration_method: str
    feature_version: str
    dataset_sha256: str
    training_source_kind: TrainingSourceKind
    performance_scope: str
    limitations: Tuple[str, ...]
    risk_policy: None


@dataclass(frozen=True)
class TemporalRiskInferenceArtifactFiles:
    model_path: Path
    calibrator_path: Path
    manifest_path: Path
    manifest_sha256: str


class TemporalRiskInferenceError(ValueError):
    pass


class TemporalRiskArtifactError(TemporalRiskInferenceError):
    pass


@runtime_checkable
class TemporalRiskTripReader(Protocol):
    def get_trip_by_lot_trip_id(
        self, lot_trip_id: str
    ) -> Optional[TripIdentity]:
        ...


@runtime_checkable
class TemporalRiskInferenceHistoryReader(Protocol):
    def get_telemetry_history(
        self, lot_trip_id: str
    ) -> Tuple[TelemetryRecord, ...]:
        ...

    def get_decision_history(
        self, lot_trip_id: str
    ) -> Tuple[StatusDecisionRecord, ...]:
        ...


class TemporalRiskInferenceService:
    """Read a coherent ACTIVE prefix and emit probability, never status."""

    def __init__(
        self,
        identity_repository: TemporalRiskTripReader,
        history_repository: TemporalRiskInferenceHistoryReader,
        artifact: TemporalRiskInferenceArtifact,
    ):
        if not isinstance(identity_repository, TemporalRiskTripReader):
            raise TypeError("identity_repository must support TemporalRiskTripReader")
        if not isinstance(history_repository, TemporalRiskInferenceHistoryReader):
            raise TypeError(
                "history_repository must support TemporalRiskInferenceHistoryReader"
            )
        _validate_loaded_artifact(artifact)
        self._identity_repository = identity_repository
        self._history_repository = history_repository
        self._artifact = artifact

    def predict(
        self, lot_trip_id: str
    ) -> Union[TemporalRiskPrediction, TemporalRiskNotPredicted]:
        lot_trip = _required_text(lot_trip_id, "lot_trip_id")
        saw_concurrent_change = False
        saw_history_incoherence = False
        for _ in range(_MAX_SNAPSHOT_ATTEMPTS):
            trip_before = self._identity_repository.get_trip_by_lot_trip_id(
                lot_trip
            )
            terminal = _trip_eligibility_result(lot_trip, trip_before)
            if terminal is not None:
                return terminal
            telemetry = tuple(
                self._history_repository.get_telemetry_history(lot_trip)
            )
            decisions = tuple(
                self._history_repository.get_decision_history(lot_trip)
            )
            trip_after = self._identity_repository.get_trip_by_lot_trip_id(
                lot_trip
            )
            if trip_after != trip_before:
                saw_concurrent_change = True
                continue
            if not telemetry and not decisions:
                return TemporalRiskNotPredicted(
                    lot_trip,
                    TemporalRiskNotPredictedReason.NO_ACCEPTED_TELEMETRY,
                    "No accepted telemetry prefix exists",
                )
            try:
                records, aligned_decisions = _aligned_prefix(
                    trip_before, telemetry, decisions
                )
            except TemporalRiskInferenceError:
                saw_history_incoherence = True
                continue
            latest_decision = aligned_decisions[-1]
            if latest_decision.status not in TEMPORAL_RISK_ELIGIBLE_CUTOFF_STATUSES:
                return TemporalRiskNotPredicted(
                    lot_trip,
                    TemporalRiskNotPredictedReason.CURRENT_STATUS_NOT_ELIGIBLE,
                    "The latest deterministic status is not an eligible cutoff",
                )
            try:
                features = build_temporal_risk_features_from_prefix(
                    _feature_context(trip_before), records, aligned_decisions
                )
            except TemporalRiskExampleError as error:
                return TemporalRiskNotPredicted(
                    lot_trip,
                    TemporalRiskNotPredictedReason.HISTORY_NOT_COHERENT,
                    str(error),
                )
            probability = _predict_probability(self._artifact, features)
            latest = records[-1]
            return validate_temporal_risk_prediction(
                TemporalRiskPrediction(
                    prediction_version=TEMPORAL_RISK_PREDICTION_VERSION,
                    lot_trip_id=trip_before.lot_trip_id,
                    trip_id=trip_before.trip_id,
                    cutoff_sample_id=latest.sample_id,
                    cutoff_at=latest.timestamp,
                    horizon_ends_at=latest.timestamp
                    + timedelta(
                        minutes=TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES
                    ),
                    prediction_horizon_minutes=(
                        TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES
                    ),
                    adverse_event_probability=probability,
                    model_version=self._artifact.model_version,
                    calibration_method=self._artifact.calibration_method,
                    feature_version=self._artifact.feature_version,
                    artifact_manifest_sha256=(
                        self._artifact.manifest_sha256
                    ),
                    training_source_kind=(
                        self._artifact.training_source_kind
                    ),
                    performance_scope=self._artifact.performance_scope,
                    limitations=self._artifact.limitations,
                )
            )
        return TemporalRiskNotPredicted(
            lot_trip,
            (
                TemporalRiskNotPredictedReason.CONCURRENT_UPDATE
                if saw_concurrent_change
                else TemporalRiskNotPredictedReason.HISTORY_NOT_COHERENT
            ),
            (
                "Trip lifecycle changed while the inference prefix was read"
                if saw_concurrent_change and not saw_history_incoherence
                else "A coherent latest telemetry/decision prefix was not available"
            ),
        )


def temporal_risk_inference_service_from_composition(composition, artifact):
    """Bind inference to the already-selected repository composition."""
    try:
        identity_repository = composition.identity_repository
        history_repository = composition.telemetry_state_repository
    except AttributeError as error:
        raise TypeError("composition lacks inference repositories") from error
    return TemporalRiskInferenceService(
        identity_repository,
        history_repository,
        artifact,
    )


def persist_temporal_risk_inference_artifact(
    training_result: LogisticBaselineTrainingResult,
    calibration: TemporalRiskCalibrationAnalysis,
    directory,
    *,
    created_at: datetime,
) -> TemporalRiskInferenceArtifactFiles:
    if not isinstance(training_result, LogisticBaselineTrainingResult):
        raise TemporalRiskArtifactError("training_result must be logistic training")
    if not isinstance(calibration, TemporalRiskCalibrationAnalysis):
        raise TemporalRiskArtifactError("calibration must be calibration analysis")
    timestamp = _aware_datetime(created_at, "created_at")
    diagnostics = training_result.readiness.diagnostics
    if training_result.dataset_sha256 != calibration.dataset_sha256:
        raise TemporalRiskArtifactError("Model and calibrator datasets differ")
    if calibration.base_model_version != BASELINE_MODEL_VERSION:
        raise TemporalRiskArtifactError("Calibrator targets an unexpected model")
    if calibration.risk_policy is not None:
        raise TemporalRiskArtifactError("Inference artifact cannot contain risk policy")
    if diagnostics.source_kind not in (
        TrainingSourceKind.APPROVED_SIMULATOR,
        TrainingSourceKind.REAL_OPERATIONAL,
    ):
        raise TemporalRiskArtifactError("Training source is not approved for inference")
    try:
        import joblib
        import sklearn
    except ImportError as error:
        raise TemporalRiskArtifactError(
            "scikit-learn and joblib are required for inference artifacts"
        ) from error
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    model_path = destination / _MODEL_FILENAME
    calibrator_path = destination / _CALIBRATOR_FILENAME
    manifest_path = destination / _MANIFEST_FILENAME
    joblib.dump(training_result.model, model_path)
    joblib.dump(calibration.calibrator, calibrator_path)
    simulated = diagnostics.source_kind == TrainingSourceKind.APPROVED_SIMULATOR
    limitations = (
        (
            "Trained only on approved deterministic simulator histories; "
            "real-device performance is unknown."
        ),
    ) if simulated else ()
    manifest = {
        "schema": TEMPORAL_RISK_INFERENCE_ARTIFACT_SCHEMA,
        "schema_version": TEMPORAL_RISK_INFERENCE_ARTIFACT_SCHEMA_VERSION,
        "created_at": _utc_text(timestamp),
        "model_version": BASELINE_MODEL_VERSION,
        "calibration_method": calibration.calibration_method,
        "feature_version": TEMPORAL_RISK_FEATURE_VERSION,
        "target_name": TEMPORAL_RISK_TARGET_NAME,
        "prediction_horizon_minutes": TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES,
        "categorical_features": list(TEMPORAL_RISK_CATEGORICAL_FEATURES),
        "numeric_features": list(TEMPORAL_RISK_NUMERIC_FEATURES),
        "dataset_sha256": training_result.dataset_sha256,
        "training_source_id": diagnostics.source_id,
        "training_source_kind": diagnostics.source_kind.value,
        "performance_scope": (
            SIMULATOR_PERFORMANCE_SCOPE if simulated else REAL_PERFORMANCE_SCOPE
        ),
        "limitations": list(limitations),
        "risk_policy": None,
        "model_file": _MODEL_FILENAME,
        "model_sha256": _file_sha256(model_path),
        "calibrator_file": _CALIBRATOR_FILENAME,
        "calibrator_sha256": _file_sha256(calibrator_path),
        "scikit_learn_version": sklearn.__version__,
    }
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return TemporalRiskInferenceArtifactFiles(
        model_path=model_path,
        calibrator_path=calibrator_path,
        manifest_path=manifest_path,
        manifest_sha256=_file_sha256(manifest_path),
    )


def load_temporal_risk_inference_artifact(
    directory,
    *,
    expected_manifest_sha256: str,
) -> TemporalRiskInferenceArtifact:
    expected_hash = _sha256_text(
        expected_manifest_sha256, "expected_manifest_sha256"
    )
    destination = Path(directory)
    manifest_path = destination / _MANIFEST_FILENAME
    if not manifest_path.is_file() or _file_sha256(manifest_path) != expected_hash:
        raise TemporalRiskArtifactError("Inference manifest trust hash mismatch")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TemporalRiskArtifactError("Inference manifest is unreadable") from error
    _validate_manifest(manifest)
    model_path = destination / manifest["model_file"]
    calibrator_path = destination / manifest["calibrator_file"]
    if _file_sha256(model_path) != manifest["model_sha256"]:
        raise TemporalRiskArtifactError("Inference model hash mismatch")
    if _file_sha256(calibrator_path) != manifest["calibrator_sha256"]:
        raise TemporalRiskArtifactError("Inference calibrator hash mismatch")
    try:
        import joblib
        import sklearn
    except ImportError as error:
        raise TemporalRiskArtifactError(
            "scikit-learn and joblib are required for inference artifacts"
        ) from error
    if manifest["scikit_learn_version"] != sklearn.__version__:
        raise TemporalRiskArtifactError("Inference scikit-learn version mismatch")
    try:
        model = joblib.load(model_path)
        calibrator = joblib.load(calibrator_path)
    except Exception as error:
        raise TemporalRiskArtifactError("Inference artifact cannot be loaded") from error
    artifact = TemporalRiskInferenceArtifact(
        model=model,
        calibrator=calibrator,
        manifest_sha256=expected_hash,
        model_version=manifest["model_version"],
        calibration_method=manifest["calibration_method"],
        feature_version=manifest["feature_version"],
        dataset_sha256=manifest["dataset_sha256"],
        training_source_kind=TrainingSourceKind(
            manifest["training_source_kind"]
        ),
        performance_scope=manifest["performance_scope"],
        limitations=tuple(manifest["limitations"]),
        risk_policy=None,
    )
    return _validate_loaded_artifact(artifact)


def validate_temporal_risk_prediction(
    value: TemporalRiskPrediction,
) -> TemporalRiskPrediction:
    if not isinstance(value, TemporalRiskPrediction):
        raise TemporalRiskInferenceError("value must be TemporalRiskPrediction")
    if value.prediction_version != TEMPORAL_RISK_PREDICTION_VERSION:
        raise TemporalRiskInferenceError("Unexpected prediction version")
    for field in (
        "lot_trip_id",
        "trip_id",
        "cutoff_sample_id",
        "model_version",
        "calibration_method",
        "feature_version",
        "performance_scope",
    ):
        _required_text(getattr(value, field), field)
    cutoff = _aware_datetime(value.cutoff_at, "cutoff_at")
    horizon = _aware_datetime(value.horizon_ends_at, "horizon_ends_at")
    if value.prediction_horizon_minutes != TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES:
        raise TemporalRiskInferenceError("Unexpected prediction horizon")
    if horizon != cutoff + timedelta(minutes=value.prediction_horizon_minutes):
        raise TemporalRiskInferenceError("Prediction horizon does not match cutoff")
    _probability(value.adverse_event_probability)
    _sha256_text(value.artifact_manifest_sha256, "artifact_manifest_sha256")
    if not isinstance(value.training_source_kind, TrainingSourceKind):
        raise TemporalRiskInferenceError("training_source_kind is invalid")
    if not isinstance(value.limitations, tuple) or any(
        not isinstance(item, str) or not item.strip() for item in value.limitations
    ):
        raise TemporalRiskInferenceError("limitations must be immutable text")
    return value


def temporal_risk_prediction_document(
    value: Union[TemporalRiskPrediction, TemporalRiskNotPredicted],
) -> dict:
    if isinstance(value, TemporalRiskNotPredicted):
        return {
            "predicted": False,
            "lotTripId": value.lot_trip_id,
            "reasonCode": value.reason_code.value,
            "detail": value.detail,
        }
    prediction = validate_temporal_risk_prediction(value)
    return {
        "predicted": True,
        "predictionVersion": prediction.prediction_version,
        "lotTripId": prediction.lot_trip_id,
        "tripId": prediction.trip_id,
        "cutoffSampleId": prediction.cutoff_sample_id,
        "cutoffAt": _utc_text(prediction.cutoff_at),
        "horizonEndsAt": _utc_text(prediction.horizon_ends_at),
        "predictionHorizonMinutes": prediction.prediction_horizon_minutes,
        "adverseEventProbability": prediction.adverse_event_probability,
        "modelVersion": prediction.model_version,
        "calibrationMethod": prediction.calibration_method,
        "featureVersion": prediction.feature_version,
        "artifactManifestSha256": prediction.artifact_manifest_sha256,
        "trainingSourceKind": prediction.training_source_kind.value,
        "performanceScope": prediction.performance_scope,
        "limitations": list(prediction.limitations),
    }


def _aligned_prefix(trip, telemetry, decisions):
    telemetry_by_sample = {}
    for record in telemetry:
        key = (record.device_id, record.sample_id)
        if key in telemetry_by_sample:
            raise TemporalRiskInferenceError("Duplicate telemetry sample identity")
        telemetry_by_sample[key] = record
    decisions_by_sample = {}
    for decision in decisions:
        key = (decision.device_id, decision.sample_id)
        if key in decisions_by_sample:
            raise TemporalRiskInferenceError("Duplicate decision sample identity")
        decisions_by_sample[key] = decision
    if set(telemetry_by_sample) != set(decisions_by_sample):
        raise TemporalRiskInferenceError("Telemetry and decision histories differ")
    records = tuple(
        sorted(
            telemetry_by_sample.values(),
            key=lambda item: (item.timestamp, item.device_id, item.sample_id),
        )
    )
    aligned = tuple(
        decisions_by_sample[(record.device_id, record.sample_id)]
        for record in records
    )
    if not records:
        raise TemporalRiskInferenceError("No history exists")
    return records, aligned


def _trip_eligibility_result(lot_trip_id, trip):
    if trip is None:
        return TemporalRiskNotPredicted(
            lot_trip_id,
            TemporalRiskNotPredictedReason.TRIP_NOT_FOUND,
            "No TripIdentity exists for this lot_trip_id",
        )
    if trip.status != TripStatus.ACTIVE:
        return TemporalRiskNotPredicted(
            lot_trip_id,
            TemporalRiskNotPredictedReason.TRIP_NOT_ACTIVE,
            "Future-risk inference is available only for ACTIVE trips",
        )
    return None


def _feature_context(trip: TripIdentity) -> TemporalRiskFeatureContext:
    return TemporalRiskFeatureContext(
        lot_trip_id=trip.lot_trip_id,
        trip_id=trip.trip_id,
        device_id=trip.device_id,
        product_id=trip.product_id,
        presentation=trip.presentation,
        state=trip.state,
        product_rule_version=trip.product_rule_version,
        trip_started_at=trip.start_time,
    )


def _predict_probability(artifact, features):
    try:
        raw = float(
            artifact.model.predict_proba(
                (temporal_risk_feature_row(features),)
            )[0][1]
        )
        calibrated = float(artifact.calibrator.predict((raw,))[0])
    except Exception as error:
        raise TemporalRiskInferenceError("Temporal-risk inference failed") from error
    _probability(raw)
    return _probability(calibrated)


def _validate_loaded_artifact(artifact):
    if not isinstance(artifact, TemporalRiskInferenceArtifact):
        raise TemporalRiskArtifactError(
            "artifact must be TemporalRiskInferenceArtifact"
        )
    if artifact.model_version != BASELINE_MODEL_VERSION:
        raise TemporalRiskArtifactError("Unexpected inference model version")
    if artifact.calibration_method != CALIBRATION_METHOD:
        raise TemporalRiskArtifactError("Unexpected calibration method")
    if artifact.feature_version != TEMPORAL_RISK_FEATURE_VERSION:
        raise TemporalRiskArtifactError("Unexpected feature version")
    _sha256_text(artifact.manifest_sha256, "manifest_sha256")
    _sha256_text(artifact.dataset_sha256, "dataset_sha256")
    if artifact.training_source_kind not in (
        TrainingSourceKind.APPROVED_SIMULATOR,
        TrainingSourceKind.REAL_OPERATIONAL,
    ):
        raise TemporalRiskArtifactError("Inference source is not approved")
    if artifact.risk_policy is not None:
        raise TemporalRiskArtifactError("Inference artifact cannot contain risk policy")
    if not callable(getattr(artifact.model, "predict_proba", None)):
        raise TemporalRiskArtifactError("Inference model lacks predict_proba")
    if not isinstance(artifact.calibrator, SigmoidProbabilityCalibrator):
        raise TemporalRiskArtifactError("Inference calibrator type is invalid")
    named_steps = getattr(artifact.model, "named_steps", {})
    if set(named_steps) != {"preprocessor", "classifier"}:
        raise TemporalRiskArtifactError("Inference pipeline binding is invalid")
    return artifact


def _validate_manifest(value):
    if not isinstance(value, dict):
        raise TemporalRiskArtifactError("Inference manifest must be an object")
    if value.get("schema") != TEMPORAL_RISK_INFERENCE_ARTIFACT_SCHEMA or value.get(
        "schema_version"
    ) != TEMPORAL_RISK_INFERENCE_ARTIFACT_SCHEMA_VERSION:
        raise TemporalRiskArtifactError("Inference manifest schema is unsupported")
    if value.get("model_version") != BASELINE_MODEL_VERSION:
        raise TemporalRiskArtifactError("Inference manifest model is unsupported")
    if value.get("calibration_method") != CALIBRATION_METHOD:
        raise TemporalRiskArtifactError("Inference calibration is unsupported")
    if value.get("feature_version") != TEMPORAL_RISK_FEATURE_VERSION:
        raise TemporalRiskArtifactError("Inference feature version is unsupported")
    if value.get("target_name") != TEMPORAL_RISK_TARGET_NAME:
        raise TemporalRiskArtifactError("Inference target is unsupported")
    if value.get("prediction_horizon_minutes") != (
        TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES
    ):
        raise TemporalRiskArtifactError("Inference horizon is unsupported")
    if tuple(value.get("categorical_features", ())) != (
        TEMPORAL_RISK_CATEGORICAL_FEATURES
    ) or tuple(value.get("numeric_features", ())) != TEMPORAL_RISK_NUMERIC_FEATURES:
        raise TemporalRiskArtifactError("Inference feature manifest differs")
    if value.get("risk_policy") is not None:
        raise TemporalRiskArtifactError("Inference manifest contains risk policy")
    source = value.get("training_source_kind")
    if source not in {
        TrainingSourceKind.APPROVED_SIMULATOR.value,
        TrainingSourceKind.REAL_OPERATIONAL.value,
    }:
        raise TemporalRiskArtifactError("Inference source is not approved")
    expected_scope = (
        SIMULATOR_PERFORMANCE_SCOPE
        if source == TrainingSourceKind.APPROVED_SIMULATOR.value
        else REAL_PERFORMANCE_SCOPE
    )
    if value.get("performance_scope") != expected_scope:
        raise TemporalRiskArtifactError("Inference performance scope differs")
    if value.get("model_file") != _MODEL_FILENAME or value.get(
        "calibrator_file"
    ) != _CALIBRATOR_FILENAME:
        raise TemporalRiskArtifactError("Inference filenames are not canonical")
    for field in ("dataset_sha256", "model_sha256", "calibrator_sha256"):
        _sha256_text(value.get(field), field)
    limitations = value.get("limitations")
    if not isinstance(limitations, list) or any(
        not isinstance(item, str) or not item.strip() for item in limitations
    ):
        raise TemporalRiskArtifactError("Inference limitations are invalid")
    if source == TrainingSourceKind.APPROVED_SIMULATOR.value and not limitations:
        raise TemporalRiskArtifactError("Simulator artifact needs limitations")


def _probability(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TemporalRiskInferenceError("Probability must be numeric")
    result = float(value)
    if not isfinite(result) or result < 0.0 or result > 1.0:
        raise TemporalRiskInferenceError("Probability must be finite in [0, 1]")
    return result


def _file_sha256(path):
    try:
        return sha256(Path(path).read_bytes()).hexdigest()
    except OSError as error:
        raise TemporalRiskArtifactError("Inference artifact file is unavailable") from error


def _sha256_text(value, field):
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise TemporalRiskArtifactError(f"{field} must be lowercase SHA-256")
    return value


def _required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise TemporalRiskInferenceError(f"{field} must be non-empty")
    return value.strip()


def _aware_datetime(value, field):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TemporalRiskInferenceError(f"{field} must be timezone-aware")
    return value


def _utc_text(value):
    return _aware_datetime(value, "timestamp").astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
