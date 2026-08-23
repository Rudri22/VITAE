"""Simulator-only journey-risk model comparison and engineering evaluation."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping, Tuple

try:
    from .journey_risk_examples import (
        JOURNEY_RISK_FEATURE_VERSION,
        JOURNEY_RISK_LABEL_VERSION,
        JOURNEY_RISK_TARGET_NAME,
        JourneyRiskExample,
        journey_risk_example_document,
        validate_journey_risk_example,
    )
    from .risk_rules import ApplicationStatus
    from .temporal_risk_baseline import (
        BASELINE_RANDOM_SEED,
        MISSING_CATEGORY_TOKEN,
        TrainingReadinessPolicy,
        TrainingSourceKind,
        equal_trip_sample_weights,
        grouped_train_validation_test_split,
        temporal_risk_feature_row,
    )
    from .temporal_risk_calibration import SigmoidProbabilityCalibrator
    from .temporal_risk_examples import TEMPORAL_RISK_CATEGORICAL_FEATURES, TEMPORAL_RISK_NUMERIC_FEATURES
except ImportError:
    from journey_risk_examples import (
        JOURNEY_RISK_FEATURE_VERSION,
        JOURNEY_RISK_LABEL_VERSION,
        JOURNEY_RISK_TARGET_NAME,
        JourneyRiskExample,
        journey_risk_example_document,
        validate_journey_risk_example,
    )
    from risk_rules import ApplicationStatus
    from temporal_risk_baseline import (
        BASELINE_RANDOM_SEED,
        MISSING_CATEGORY_TOKEN,
        TrainingReadinessPolicy,
        TrainingSourceKind,
        equal_trip_sample_weights,
        grouped_train_validation_test_split,
        temporal_risk_feature_row,
    )
    from temporal_risk_calibration import SigmoidProbabilityCalibrator
    from temporal_risk_examples import TEMPORAL_RISK_CATEGORICAL_FEATURES, TEMPORAL_RISK_NUMERIC_FEATURES

JOURNEY_RISK_MODEL_VERSION = "journey-risk-variable-horizon-v1"
JOURNEY_RISK_LOGISTIC_VERSION = "journey-risk-logistic-v1"
JOURNEY_RISK_BOOSTED_VERSION = "journey-risk-gradient-boosting-v1"
JOURNEY_RISK_CALIBRATION_METHOD = "held-out-validation-platt-sigmoid-v1"
JOURNEY_RISK_VALIDATION_STATUS = "ENGINEERING_POC"
JOURNEY_RISK_MEDIUM_THRESHOLD = 0.20
JOURNEY_RISK_HIGH_THRESHOLD = 0.50


@dataclass(frozen=True)
class JourneyRiskTrainingDataset:
    source_id: str
    source_kind: TrainingSourceKind
    examples: Tuple[JourneyRiskExample, ...]


@dataclass(frozen=True)
class JourneyRiskMetrics:
    threshold: float
    roc_auc: float
    average_precision: float
    brier_score: float
    precision: float
    recall: float
    f1: float
    f2: float
    true_negative: int
    false_positive: int
    false_negative: int
    true_positive: int
    false_negative_rate: float
    false_positive_rate: float


@dataclass(frozen=True)
class JourneyRiskEstimator:
    strategy: str
    logistic_model: Any
    logistic_calibrator: SigmoidProbabilityCalibrator
    boosted_model: Any
    boosted_calibrator: SigmoidProbabilityCalibrator

    def predict_probabilities(self, rows):
        logistic = _calibrated(self.logistic_model, self.logistic_calibrator, rows)
        boosted = _calibrated(self.boosted_model, self.boosted_calibrator, rows)
        if self.strategy == "LOGISTIC":
            return logistic
        if self.strategy == "BOOSTED":
            return boosted
        if self.strategy == "CALIBRATED_AVERAGE":
            return tuple((left + right) / 2 for left, right in zip(logistic, boosted))
        raise JourneyRiskTrainingError("Unknown journey-risk strategy")


@dataclass(frozen=True)
class JourneyRiskTrainingResult:
    estimator: JourneyRiskEstimator
    split: Any
    dataset_sha256: str
    selected_strategy: str
    validation_metrics: Tuple[Tuple[str, JourneyRiskMetrics], ...]
    test_metrics: Tuple[Tuple[str, JourneyRiskMetrics], ...]
    raw_validation_metrics: Tuple[Tuple[str, JourneyRiskMetrics], ...]
    raw_test_metrics: Tuple[Tuple[str, JourneyRiskMetrics], ...]
    selected_threshold_analysis: Tuple[Tuple[str, JourneyRiskMetrics], ...]
    heuristic_test_metrics: JourneyRiskMetrics
    train_trip_count: int
    validation_trip_count: int
    test_trip_count: int
    positive_count: int
    negative_count: int
    warnings: Tuple[str, ...]


class JourneyRiskTrainingError(ValueError):
    pass


def validate_journey_risk_dataset(dataset):
    if not isinstance(dataset, JourneyRiskTrainingDataset):
        raise JourneyRiskTrainingError("dataset must be JourneyRiskTrainingDataset")
    if dataset.source_kind not in (TrainingSourceKind.APPROVED_SIMULATOR, TrainingSourceKind.REAL_OPERATIONAL):
        raise JourneyRiskTrainingError("training source is not approved")
    if len(dataset.examples) < 100 or len({item.lot_trip_id for item in dataset.examples}) < 30:
        raise JourneyRiskTrainingError("journey-risk dataset is not ready")
    for value in dataset.examples:
        validate_journey_risk_example(value)
    if {int(item.label.deteriorates_before_destination) for item in dataset.examples} != {0, 1}:
        raise JourneyRiskTrainingError("journey-risk dataset needs both classes")
    return dataset


def journey_risk_feature_row(features):
    return temporal_risk_feature_row(features.temporal_features) + [float(features.remaining_journey_minutes)]


def train_and_compare_journey_risk_models(dataset):
    source = validate_journey_risk_dataset(dataset)
    examples = source.examples
    split = grouped_train_validation_test_split(examples, TrainingReadinessPolicy())
    labels = tuple(int(item.label.deteriorates_before_destination) for item in examples)
    _require_split_classes(labels, split)
    rows = tuple(journey_risk_feature_row(item.features) for item in examples)
    logistic = _fit_model("LOGISTIC", rows, labels, examples, split.train_indices)
    boosted = _fit_model("BOOSTED", rows, labels, examples, split.train_indices)
    logistic_calibrator = _fit_calibrator(logistic, rows, labels, examples, split.validation_indices)
    boosted_calibrator = _fit_calibrator(boosted, rows, labels, examples, split.validation_indices)
    candidates = {
        "LOGISTIC": JourneyRiskEstimator("LOGISTIC", logistic, logistic_calibrator, boosted, boosted_calibrator),
        "BOOSTED": JourneyRiskEstimator("BOOSTED", logistic, logistic_calibrator, boosted, boosted_calibrator),
        "CALIBRATED_AVERAGE": JourneyRiskEstimator("CALIBRATED_AVERAGE", logistic, logistic_calibrator, boosted, boosted_calibrator),
    }
    raw_probabilities = {
        "LOGISTIC": tuple(float(value) for value in logistic.predict_proba(rows)[:, 1]),
        "BOOSTED": tuple(float(value) for value in boosted.predict_proba(rows)[:, 1]),
    }
    raw_probabilities["CALIBRATED_AVERAGE"] = tuple(
        (left + right) / 2
        for left, right in zip(raw_probabilities["LOGISTIC"], raw_probabilities["BOOSTED"])
    )
    raw_validation = tuple(
        (name, _metrics(labels, probabilities, split.validation_indices, JOURNEY_RISK_HIGH_THRESHOLD))
        for name, probabilities in raw_probabilities.items()
    )
    raw_test = tuple(
        (name, _metrics(labels, probabilities, split.test_indices, JOURNEY_RISK_HIGH_THRESHOLD))
        for name, probabilities in raw_probabilities.items()
    )
    validation = tuple(
        (name, _metrics(labels, estimator.predict_probabilities(rows), split.validation_indices, JOURNEY_RISK_HIGH_THRESHOLD))
        for name, estimator in candidates.items()
    )
    selected = _select_strategy(dict(validation))
    test = tuple(
        (name, _metrics(labels, estimator.predict_probabilities(rows), split.test_indices, JOURNEY_RISK_HIGH_THRESHOLD))
        for name, estimator in candidates.items()
    )
    selected_probabilities = candidates[selected].predict_probabilities(rows)
    threshold_analysis = tuple(
        (
            f"VALIDATION_{threshold:.2f}",
            _metrics(labels, selected_probabilities, split.validation_indices, threshold),
        )
        for threshold in (JOURNEY_RISK_MEDIUM_THRESHOLD, JOURNEY_RISK_HIGH_THRESHOLD)
    ) + tuple(
        (
            f"TEST_{threshold:.2f}",
            _metrics(labels, selected_probabilities, split.test_indices, threshold),
        )
        for threshold in (JOURNEY_RISK_MEDIUM_THRESHOLD, JOURNEY_RISK_HIGH_THRESHOLD)
    )
    heuristic = tuple(
        0.65 if item.features.temporal_features.current_status == ApplicationStatus.MONITOR else 0.05
        for item in examples
    )
    return JourneyRiskTrainingResult(
        estimator=candidates[selected],
        split=split,
        dataset_sha256=journey_risk_dataset_fingerprint(source),
        selected_strategy=selected,
        validation_metrics=validation,
        test_metrics=test,
        raw_validation_metrics=raw_validation,
        raw_test_metrics=raw_test,
        selected_threshold_analysis=threshold_analysis,
        heuristic_test_metrics=_metrics(labels, heuristic, split.test_indices, JOURNEY_RISK_HIGH_THRESHOLD),
        train_trip_count=len(split.train_lot_trip_ids),
        validation_trip_count=len(split.validation_lot_trip_ids),
        test_trip_count=len(split.test_lot_trip_ids),
        positive_count=sum(labels),
        negative_count=len(labels) - sum(labels),
        warnings=(
            "SIMULATOR-BASED ENGINEERING EVALUATION; not real-world performance or clinical validation.",
            "The validation split is also used for Platt calibration and engineering model selection.",
            "The 0.20 and 0.50 thresholds are unchanged engineering demonstration policy values.",
        ),
    )


def journey_risk_dataset_fingerprint(dataset):
    digest = sha256()
    for item in sorted(dataset.examples, key=lambda value: value.example_id):
        digest.update(json.dumps(journey_risk_example_document(item), sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def journey_risk_evaluation_document(result, *, created_at):
    if not isinstance(result, JourneyRiskTrainingResult):
        raise JourneyRiskTrainingError("result must be JourneyRiskTrainingResult")
    timestamp = _aware_datetime(created_at)
    return {
        "schema": "vitae.journey_risk_engineering_evaluation",
        "schemaVersion": 1,
        "createdAt": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "disclaimer": "SIMULATOR-BASED ENGINEERING EVALUATION; NOT REAL-WORLD PERFORMANCE; NOT CLINICAL VALIDATION",
        "target": JOURNEY_RISK_TARGET_NAME,
        "featureVersion": JOURNEY_RISK_FEATURE_VERSION,
        "labelVersion": JOURNEY_RISK_LABEL_VERSION,
        "modelVersion": JOURNEY_RISK_MODEL_VERSION,
        "selectedStrategy": result.selected_strategy,
        "trainingSource": "SIMULATOR",
        "validationStatus": JOURNEY_RISK_VALIDATION_STATUS,
        "tripCounts": {"train": result.train_trip_count, "validation": result.validation_trip_count, "test": result.test_trip_count},
        "labelCounts": {"positive": result.positive_count, "negative": result.negative_count},
        "validationMetrics": {name: vars(metrics) for name, metrics in result.validation_metrics},
        "testMetrics": {name: vars(metrics) for name, metrics in result.test_metrics},
        "rawValidationMetrics": {name: vars(metrics) for name, metrics in result.raw_validation_metrics},
        "rawTestMetrics": {name: vars(metrics) for name, metrics in result.raw_test_metrics},
        "thresholdAnalysis": {name: vars(metrics) for name, metrics in result.selected_threshold_analysis},
        "heuristicTestMetrics": vars(result.heuristic_test_metrics),
        "warnings": list(result.warnings),
    }


def _build_preprocessor(*, sparse_output):
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    categorical_count = len(TEMPORAL_RISK_CATEGORICAL_FEATURES)
    numeric_count = len(TEMPORAL_RISK_NUMERIC_FEATURES) + 1
    return ColumnTransformer((
        ("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=sparse_output), list(range(categorical_count))),
        ("numeric", Pipeline((
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
        )), list(range(categorical_count, categorical_count + numeric_count))),
    ))


def _fit_model(kind, rows, labels, examples, indices):
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    classifier = (
        LogisticRegression(C=1.0, solver="liblinear", max_iter=1000, random_state=BASELINE_RANDOM_SEED)
        if kind == "LOGISTIC"
        else GradientBoostingClassifier(n_estimators=100, learning_rate=0.05, max_depth=2, min_samples_leaf=20, subsample=0.8, random_state=BASELINE_RANDOM_SEED)
    )
    model = Pipeline(
        steps=(
            ("preprocessor", _build_preprocessor(sparse_output=kind == "LOGISTIC")),
            ("classifier", classifier),
        )
    )
    model.fit(
        [rows[index] for index in indices],
        [labels[index] for index in indices],
        classifier__sample_weight=equal_trip_sample_weights(examples, indices),
    )
    return model


def _fit_calibrator(model, rows, labels, examples, indices):
    from sklearn.linear_model import LogisticRegression

    raw = model.predict_proba([rows[index] for index in indices])[:, 1]
    logits = [[_logit(value)] for value in raw]
    estimator = LogisticRegression(C=1_000_000.0, solver="lbfgs", max_iter=1000, random_state=BASELINE_RANDOM_SEED)
    estimator.fit(logits, [labels[index] for index in indices], sample_weight=equal_trip_sample_weights(examples, indices))
    return SigmoidProbabilityCalibrator(estimator)


def _calibrated(model, calibrator, rows):
    raw = tuple(float(value) for value in model.predict_proba(rows)[:, 1])
    return calibrator.predict(raw)


def _metrics(labels, probabilities, indices, threshold):
    from sklearn.metrics import average_precision_score, brier_score_loss, f1_score, precision_score, recall_score, roc_auc_score

    expected = [labels[index] for index in indices]
    predicted_probabilities = [float(probabilities[index]) for index in indices]
    predicted = [int(value >= threshold) for value in predicted_probabilities]
    tn = sum(a == 0 and b == 0 for a, b in zip(expected, predicted))
    fp = sum(a == 0 and b == 1 for a, b in zip(expected, predicted))
    fn = sum(a == 1 and b == 0 for a, b in zip(expected, predicted))
    tp = sum(a == 1 and b == 1 for a, b in zip(expected, predicted))
    precision = float(precision_score(expected, predicted, zero_division=0))
    recall = float(recall_score(expected, predicted, zero_division=0))
    return JourneyRiskMetrics(
        threshold=float(threshold),
        roc_auc=float(roc_auc_score(expected, predicted_probabilities)),
        average_precision=float(average_precision_score(expected, predicted_probabilities)),
        brier_score=float(brier_score_loss(expected, predicted_probabilities)),
        precision=precision,
        recall=recall,
        f1=float(f1_score(expected, predicted, zero_division=0)),
        f2=(0.0 if 4 * precision + recall == 0 else 5 * precision * recall / (4 * precision + recall)),
        true_negative=tn,
        false_positive=fp,
        false_negative=fn,
        true_positive=tp,
        false_negative_rate=(0.0 if fn + tp == 0 else fn / (fn + tp)),
        false_positive_rate=(0.0 if fp + tn == 0 else fp / (fp + tn)),
    )


def _select_strategy(metrics):
    order = {"LOGISTIC": 0, "BOOSTED": 1, "CALIBRATED_AVERAGE": 2}
    best = max(metrics, key=lambda name: (metrics[name].recall, metrics[name].f2, metrics[name].average_precision, -order[name]))
    # Retain logistic when added complexity has no meaningful safety-oriented gain.
    logistic = metrics["LOGISTIC"]
    candidate = metrics[best]
    if best != "LOGISTIC" and candidate.recall <= logistic.recall and candidate.f2 < logistic.f2 + 0.02:
        return "LOGISTIC"
    return best


def _require_split_classes(labels, split):
    for name, indices in (("train", split.train_indices), ("validation", split.validation_indices), ("test", split.test_indices)):
        if {labels[index] for index in indices} != {0, 1}:
            raise JourneyRiskTrainingError(f"{name} split lacks both classes")


def _logit(value):
    from math import log

    probability = min(max(float(value), 1e-12), 1 - 1e-12)
    return log(probability / (1 - probability))


def _aware_datetime(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise JourneyRiskTrainingError("created_at must be timezone-aware")
    return value
