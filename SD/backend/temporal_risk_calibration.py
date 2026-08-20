import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite, log
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

try:
    from .temporal_risk_baseline import (
        BASELINE_MODEL_VERSION,
        LogisticBaselineTrainingResult,
        TemporalRiskBaselineError,
        TemporalRiskTrainingDataset,
        equal_trip_sample_weights,
        temporal_risk_dataset_fingerprint,
        temporal_risk_model_inputs,
        temporal_risk_targets,
        validate_training_dataset,
    )
    from .temporal_risk_examples import (
        TEMPORAL_RISK_CATEGORICAL_FEATURES,
        TEMPORAL_RISK_NUMERIC_FEATURES,
    )
except ImportError:
    from temporal_risk_baseline import (
        BASELINE_MODEL_VERSION,
        LogisticBaselineTrainingResult,
        TemporalRiskBaselineError,
        TemporalRiskTrainingDataset,
        equal_trip_sample_weights,
        temporal_risk_dataset_fingerprint,
        temporal_risk_model_inputs,
        temporal_risk_targets,
        validate_training_dataset,
    )
    from temporal_risk_examples import (
        TEMPORAL_RISK_CATEGORICAL_FEATURES,
        TEMPORAL_RISK_NUMERIC_FEATURES,
    )


CALIBRATION_SCHEMA = "vitae.temporal_risk_calibration_analysis"
CALIBRATION_SCHEMA_VERSION = 1
CALIBRATION_METHOD = "held-out-validation-platt-sigmoid-v1"
CALIBRATION_BOOTSTRAP_SEED = 2718
CALIBRATION_BOOTSTRAP_REPLICATES = 500
CALIBRATION_ECE_BINS = 10


@dataclass(frozen=True)
class ProbabilityMetrics:
    roc_auc: float
    average_precision: float
    log_loss: float
    brier_score: float
    expected_calibration_error: float


@dataclass(frozen=True)
class ThresholdMetrics:
    threshold: float
    true_negative: int
    false_positive: int
    false_negative: int
    true_positive: int
    recall: float
    specificity: float
    precision: float
    f1: float
    f2: float
    balanced_accuracy: float


@dataclass(frozen=True)
class ThresholdCandidate:
    name: str
    criterion: str
    metrics: ThresholdMetrics


@dataclass(frozen=True)
class BootstrapInterval:
    metric_name: str
    lower_95: float
    median: float
    upper_95: float
    valid_replicates: int


@dataclass(frozen=True)
class ConstantFeature:
    feature_name: str
    value: str


@dataclass(frozen=True)
class SigmoidProbabilityCalibrator:
    estimator: Any

    def predict(self, probabilities) -> Tuple[float, ...]:
        logits = [[_probability_logit(value)] for value in probabilities]
        return tuple(float(value) for value in self.estimator.predict_proba(logits)[:, 1])


@dataclass(frozen=True)
class TemporalRiskCalibrationAnalysis:
    calibrator: SigmoidProbabilityCalibrator
    dataset_sha256: str
    calibration_method: str
    calibration_lot_trip_ids: Tuple[str, ...]
    threshold_analysis_lot_trip_ids: Tuple[str, ...]
    test_lot_trip_ids: Tuple[str, ...]
    raw_validation_metrics: ProbabilityMetrics
    calibrated_validation_metrics: ProbabilityMetrics
    raw_test_metrics: ProbabilityMetrics
    calibrated_test_metrics: ProbabilityMetrics
    threshold_sweep: Tuple[ThresholdMetrics, ...]
    validation_candidates: Tuple[ThresholdCandidate, ...]
    validation_bootstrap_intervals: Tuple[BootstrapInterval, ...]
    constant_training_features: Tuple[ConstantFeature, ...]
    risk_policy: Optional[Mapping[str, float]]
    policy_status: str
    warnings: Tuple[str, ...]


class TemporalRiskCalibrationError(ValueError):
    pass


def analyze_temporal_risk_calibration(
    dataset: TemporalRiskTrainingDataset,
    training_result: LogisticBaselineTrainingResult,
    *,
    bootstrap_replicates: int = CALIBRATION_BOOTSTRAP_REPLICATES,
) -> TemporalRiskCalibrationAnalysis:
    source = validate_training_dataset(dataset)
    if not isinstance(training_result, LogisticBaselineTrainingResult):
        raise TemporalRiskCalibrationError(
            "training_result must be LogisticBaselineTrainingResult"
        )
    if temporal_risk_dataset_fingerprint(source) != training_result.dataset_sha256:
        raise TemporalRiskCalibrationError(
            "Training result and calibration dataset fingerprints differ"
        )
    split = training_result.readiness.split
    if split is None:
        raise TemporalRiskCalibrationError("Training result has no frozen split")
    if isinstance(bootstrap_replicates, bool) or not isinstance(
        bootstrap_replicates, int
    ) or bootstrap_replicates < 1:
        raise TemporalRiskCalibrationError("bootstrap_replicates must be positive")

    examples = source.examples
    matrix = temporal_risk_model_inputs(examples)
    labels = temporal_risk_targets(examples)
    raw_probabilities = tuple(
        float(value) for value in training_result.model.predict_proba(matrix)[:, 1]
    )
    calibrator = _fit_sigmoid_calibrator(
        raw_probabilities,
        labels,
        examples,
        split.validation_indices,
    )
    calibrated_probabilities = calibrator.predict(raw_probabilities)
    validation_labels, validation_raw, validation_calibrated = _select_values(
        labels,
        raw_probabilities,
        calibrated_probabilities,
        split.validation_indices,
    )
    sweep = threshold_sweep(validation_labels, validation_calibrated)
    candidates = validation_threshold_candidates(sweep)
    # Test values are touched only after calibration and candidates are frozen.
    test_labels, test_raw, test_calibrated = _select_values(
        labels,
        raw_probabilities,
        calibrated_probabilities,
        split.test_indices,
    )
    validation_groups = tuple(
        examples[index].lot_trip_id for index in split.validation_indices
    )
    constants = audit_constant_training_features(examples, split.train_indices)
    warnings = [
        "Calibration and threshold analysis reuse the small validation split.",
        "Candidate thresholds optimize statistical metrics, not operational costs.",
        "No LOW/MEDIUM/HIGH policy is created without an approved cost or capacity model.",
    ]
    positive_validation_trips = len(
        {
            examples[index].lot_trip_id
            for index in split.validation_indices
            if labels[index] == 1
        }
    )
    if positive_validation_trips < 10:
        warnings.append(
            "Calibration has fewer than 10 positive validation trips and is unstable."
        )
    if constants:
        warnings.append(
            "Constant training features make some fitted coefficients non-identifiable."
        )
    return TemporalRiskCalibrationAnalysis(
        calibrator=calibrator,
        dataset_sha256=training_result.dataset_sha256,
        calibration_method=CALIBRATION_METHOD,
        calibration_lot_trip_ids=split.validation_lot_trip_ids,
        threshold_analysis_lot_trip_ids=split.validation_lot_trip_ids,
        test_lot_trip_ids=split.test_lot_trip_ids,
        raw_validation_metrics=probability_metrics(
            validation_labels, validation_raw
        ),
        calibrated_validation_metrics=probability_metrics(
            validation_labels, validation_calibrated
        ),
        raw_test_metrics=probability_metrics(test_labels, test_raw),
        calibrated_test_metrics=probability_metrics(
            test_labels, test_calibrated
        ),
        threshold_sweep=sweep,
        validation_candidates=candidates,
        validation_bootstrap_intervals=group_bootstrap_probability_metrics(
            validation_labels,
            validation_calibrated,
            validation_groups,
            replicates=bootstrap_replicates,
        ),
        constant_training_features=constants,
        risk_policy=None,
        policy_status="NOT_CREATED_OPERATIONAL_COSTS_UNSPECIFIED",
        warnings=tuple(warnings),
    )


def probability_metrics(labels, probabilities) -> ProbabilityMetrics:
    try:
        from sklearn.metrics import (
            average_precision_score,
            brier_score_loss,
            log_loss,
            roc_auc_score,
        )
    except ImportError as error:
        raise TemporalRiskCalibrationError(
            "scikit-learn is required for calibration metrics"
        ) from error
    expected, predicted = _validated_labels_probabilities(labels, probabilities)
    if set(expected) != {0, 1}:
        raise TemporalRiskCalibrationError(
            "Probability metrics require both label classes"
        )
    return ProbabilityMetrics(
        roc_auc=float(roc_auc_score(expected, predicted)),
        average_precision=float(average_precision_score(expected, predicted)),
        log_loss=float(log_loss(expected, predicted, labels=[0, 1])),
        brier_score=float(brier_score_loss(expected, predicted)),
        expected_calibration_error=_expected_calibration_error(
            expected, predicted, CALIBRATION_ECE_BINS
        ),
    )


def threshold_sweep(labels, probabilities) -> Tuple[ThresholdMetrics, ...]:
    expected, predicted = _validated_labels_probabilities(labels, probabilities)
    thresholds = tuple(sorted({0.0, 0.5, 1.0, *predicted}))
    return tuple(
        _threshold_metrics(expected, predicted, threshold)
        for threshold in thresholds
    )


def validation_threshold_candidates(
    sweep: Tuple[ThresholdMetrics, ...]
) -> Tuple[ThresholdCandidate, ...]:
    if not sweep:
        raise TemporalRiskCalibrationError("threshold sweep must not be empty")
    definitions = (
        ("MAX_F1", "maximum validation F1", lambda item: item.f1),
        ("MAX_F2", "maximum validation F2", lambda item: item.f2),
        (
            "MAX_BALANCED_ACCURACY",
            "maximum validation balanced accuracy",
            lambda item: item.balanced_accuracy,
        ),
    )
    return tuple(
        ThresholdCandidate(
            name=name,
            criterion=criterion,
            metrics=max(sweep, key=lambda item: (metric(item), item.threshold)),
        )
        for name, criterion, metric in definitions
    )


def audit_constant_training_features(examples, indices) -> Tuple[ConstantFeature, ...]:
    names = TEMPORAL_RISK_CATEGORICAL_FEATURES + TEMPORAL_RISK_NUMERIC_FEATURES
    constants = []
    for name in names:
        values = {
            _audit_value(getattr(examples[index].features, name)) for index in indices
        }
        if len(values) == 1:
            constants.append(ConstantFeature(name, next(iter(values))))
    return tuple(constants)


def group_bootstrap_probability_metrics(
    labels,
    probabilities,
    groups,
    *,
    replicates=CALIBRATION_BOOTSTRAP_REPLICATES,
) -> Tuple[BootstrapInterval, ...]:
    import numpy as np

    expected, predicted = _validated_labels_probabilities(labels, probabilities)
    group_values = tuple(groups)
    if len(group_values) != len(expected):
        raise TemporalRiskCalibrationError("groups must align with labels")
    unique_groups = tuple(sorted(set(group_values)))
    if len(unique_groups) < 2:
        raise TemporalRiskCalibrationError(
            "Group bootstrap requires at least two independent trips"
        )
    by_group = {
        group: tuple(index for index, value in enumerate(group_values) if value == group)
        for group in unique_groups
    }
    generator = np.random.default_rng(CALIBRATION_BOOTSTRAP_SEED)
    collected = {
        "roc_auc": [],
        "average_precision": [],
        "log_loss": [],
        "brier_score": [],
        "expected_calibration_error": [],
    }
    for _ in range(replicates):
        sampled = generator.choice(unique_groups, size=len(unique_groups), replace=True)
        indices = tuple(index for group in sampled for index in by_group[str(group)])
        sample_labels = tuple(expected[index] for index in indices)
        if set(sample_labels) != {0, 1}:
            continue
        sample_probabilities = tuple(predicted[index] for index in indices)
        metrics = probability_metrics(sample_labels, sample_probabilities)
        for name in collected:
            collected[name].append(getattr(metrics, name))
    if not collected["roc_auc"]:
        raise TemporalRiskCalibrationError(
            "No bootstrap replicate contained both label classes"
        )
    return tuple(
        BootstrapInterval(
            metric_name=name,
            lower_95=float(np.percentile(values, 2.5)),
            median=float(np.percentile(values, 50.0)),
            upper_95=float(np.percentile(values, 97.5)),
            valid_replicates=len(values),
        )
        for name, values in collected.items()
    )


def persist_calibration_analysis(
    analysis: TemporalRiskCalibrationAnalysis,
    directory,
    *,
    created_at: datetime,
) -> Mapping[str, Path]:
    if not isinstance(analysis, TemporalRiskCalibrationAnalysis):
        raise TemporalRiskCalibrationError(
            "analysis must be TemporalRiskCalibrationAnalysis"
        )
    timestamp = _aware_datetime(created_at, "created_at")
    try:
        import joblib
        import sklearn
    except ImportError as error:
        raise TemporalRiskCalibrationError(
            "scikit-learn and joblib are required for artifact persistence"
        ) from error
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    calibrator_path = destination / "calibrator.joblib"
    metadata_path = destination / "calibration.json"
    joblib.dump(analysis.calibrator, calibrator_path)
    document = {
        "schema": CALIBRATION_SCHEMA,
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "created_at": timestamp.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "base_model_version": BASELINE_MODEL_VERSION,
        "dataset_sha256": analysis.dataset_sha256,
        "calibration_method": analysis.calibration_method,
        "calibration_lot_trip_ids": list(analysis.calibration_lot_trip_ids),
        "threshold_analysis_lot_trip_ids": list(
            analysis.threshold_analysis_lot_trip_ids
        ),
        "test_lot_trip_ids": list(analysis.test_lot_trip_ids),
        "raw_validation_metrics": vars(analysis.raw_validation_metrics),
        "calibrated_validation_metrics": vars(
            analysis.calibrated_validation_metrics
        ),
        "raw_test_metrics": vars(analysis.raw_test_metrics),
        "calibrated_test_metrics": vars(analysis.calibrated_test_metrics),
        "validation_candidates": [
            {
                "name": item.name,
                "criterion": item.criterion,
                "metrics": vars(item.metrics),
            }
            for item in analysis.validation_candidates
        ],
        "threshold_sweep_count": len(analysis.threshold_sweep),
        "risk_policy": analysis.risk_policy,
        "policy_status": analysis.policy_status,
        "constant_training_features": [
            vars(item) for item in analysis.constant_training_features
        ],
        "validation_bootstrap_intervals": [
            vars(item) for item in analysis.validation_bootstrap_intervals
        ],
        "warnings": list(analysis.warnings),
        "calibrator_sha256": sha256(calibrator_path.read_bytes()).hexdigest(),
        "scikit_learn_version": sklearn.__version__,
    }
    metadata_path.write_text(
        json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return {"calibrator": calibrator_path, "metadata": metadata_path}


def _fit_sigmoid_calibrator(probabilities, labels, examples, indices):
    try:
        from sklearn.linear_model import LogisticRegression
    except ImportError as error:
        raise TemporalRiskCalibrationError(
            "scikit-learn is required for calibration"
        ) from error
    selected_probabilities = tuple(probabilities[index] for index in indices)
    selected_labels = tuple(labels[index] for index in indices)
    if set(selected_labels) != {0, 1}:
        raise TemporalRiskCalibrationError(
            "Calibration partition must contain both label classes"
        )
    estimator = LogisticRegression(
        C=1.0,
        solver="liblinear",
        class_weight=None,
        max_iter=1000,
        random_state=CALIBRATION_BOOTSTRAP_SEED,
    )
    estimator.fit(
        [[_probability_logit(value)] for value in selected_probabilities],
        selected_labels,
        sample_weight=equal_trip_sample_weights(examples, indices),
    )
    return SigmoidProbabilityCalibrator(estimator)


def _select_values(labels, raw, calibrated, indices):
    return (
        tuple(labels[index] for index in indices),
        tuple(raw[index] for index in indices),
        tuple(calibrated[index] for index in indices),
    )


def _threshold_metrics(labels, probabilities, threshold):
    predicted = tuple(value >= threshold for value in probabilities)
    tn = sum(label == 0 and not value for label, value in zip(labels, predicted))
    fp = sum(label == 0 and value for label, value in zip(labels, predicted))
    fn = sum(label == 1 and not value for label, value in zip(labels, predicted))
    tp = sum(label == 1 and value for label, value in zip(labels, predicted))
    recall = _ratio(tp, tp + fn)
    specificity = _ratio(tn, tn + fp)
    precision = _ratio(tp, tp + fp)
    return ThresholdMetrics(
        threshold=float(threshold),
        true_negative=tn,
        false_positive=fp,
        false_negative=fn,
        true_positive=tp,
        recall=recall,
        specificity=specificity,
        precision=precision,
        f1=_fbeta(precision, recall, 1.0),
        f2=_fbeta(precision, recall, 2.0),
        balanced_accuracy=(recall + specificity) / 2.0,
    )


def _validated_labels_probabilities(labels, probabilities):
    expected = tuple(labels)
    predicted = tuple(float(value) for value in probabilities)
    if not expected or len(expected) != len(predicted):
        raise TemporalRiskCalibrationError(
            "labels and probabilities must be aligned and non-empty"
        )
    if any(value not in {0, 1, False, True} for value in expected):
        raise TemporalRiskCalibrationError("labels must be binary")
    if any(not isfinite(value) or not 0 <= value <= 1 for value in predicted):
        raise TemporalRiskCalibrationError("probabilities must be finite in [0, 1]")
    return tuple(int(value) for value in expected), predicted


def _expected_calibration_error(labels, probabilities, bins):
    total = len(labels)
    error = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        members = tuple(
            item
            for item, probability in enumerate(probabilities)
            if probability >= lower
            and (probability < upper or (index == bins - 1 and probability <= upper))
        )
        if not members:
            continue
        confidence = sum(probabilities[item] for item in members) / len(members)
        observed = sum(labels[item] for item in members) / len(members)
        error += len(members) / total * abs(observed - confidence)
    return error


def _probability_logit(value):
    probability = min(max(float(value), 1e-12), 1.0 - 1e-12)
    return log(probability / (1.0 - probability))


def _audit_value(value):
    if hasattr(value, "value"):
        value = value.value
    if value is None:
        return "__MISSING__"
    return str(value)


def _ratio(numerator, denominator):
    return 0.0 if denominator == 0 else numerator / denominator


def _fbeta(precision, recall, beta):
    denominator = beta * beta * precision + recall
    return (
        0.0
        if denominator == 0
        else (1.0 + beta * beta) * precision * recall / denominator
    )


def _aware_datetime(value, field):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TemporalRiskCalibrationError(f"{field} must be timezone-aware")
    return value
