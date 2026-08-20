import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from math import isfinite
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Optional, Tuple

try:
    from .repository_serialization import (
        deserialize_temporal_risk_example,
        serialize_temporal_risk_example,
    )
    from .temporal_risk_examples import (
        TEMPORAL_RISK_CATEGORICAL_FEATURES,
        TEMPORAL_RISK_EXAMPLE_VERSION,
        TEMPORAL_RISK_FEATURE_VERSION,
        TEMPORAL_RISK_LABEL_VERSION,
        TEMPORAL_RISK_NUMERIC_FEATURES,
        TEMPORAL_RISK_TARGET_NAME,
        TemporalRiskExample,
        validate_temporal_risk_example,
    )
except ImportError:
    from repository_serialization import (
        deserialize_temporal_risk_example,
        serialize_temporal_risk_example,
    )
    from temporal_risk_examples import (
        TEMPORAL_RISK_CATEGORICAL_FEATURES,
        TEMPORAL_RISK_EXAMPLE_VERSION,
        TEMPORAL_RISK_FEATURE_VERSION,
        TEMPORAL_RISK_LABEL_VERSION,
        TEMPORAL_RISK_NUMERIC_FEATURES,
        TEMPORAL_RISK_TARGET_NAME,
        TemporalRiskExample,
        validate_temporal_risk_example,
    )


BASELINE_MODEL_VERSION = "temporal-risk-logistic-regression-v1"
BASELINE_ARTIFACT_SCHEMA = "vitae.temporal_risk_logistic_baseline"
BASELINE_ARTIFACT_SCHEMA_VERSION = 1
BASELINE_RANDOM_SEED = 1729
BASELINE_TRAIN_FRACTION = 0.60
BASELINE_VALIDATION_FRACTION = 0.20
BASELINE_TEST_FRACTION = 0.20
MISSING_CATEGORY_TOKEN = "__MISSING__"


class TrainingSourceKind(str, Enum):
    REAL_OPERATIONAL = "REAL_OPERATIONAL"
    APPROVED_SIMULATOR = "APPROVED_SIMULATOR"
    TEST_FIXTURE = "TEST_FIXTURE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TemporalRiskTrainingDataset:
    source_id: str
    source_kind: TrainingSourceKind
    examples: Tuple[TemporalRiskExample, ...]


@dataclass(frozen=True)
class FeatureMissingness:
    feature_name: str
    missing_count: int
    missing_fraction: Optional[float]


@dataclass(frozen=True)
class TemporalRiskDatasetDiagnostics:
    source_id: str
    source_kind: TrainingSourceKind
    unique_trip_count: int
    example_count: int
    positive_count: int
    negative_count: int
    positive_prevalence: Optional[float]
    positive_trip_count: int
    negative_trip_count: int
    examples_per_trip_minimum: int
    examples_per_trip_maximum: int
    examples_per_trip_mean: float
    examples_per_trip_median: float
    feature_missingness: Tuple[FeatureMissingness, ...]


@dataclass(frozen=True)
class TrainingReadinessPolicy:
    minimum_unique_trips: int = 30
    minimum_examples: int = 100
    minimum_positive_trips: int = 10
    minimum_negative_trips: int = 10
    test_fraction: float = BASELINE_TEST_FRACTION
    validation_fraction: float = BASELINE_VALIDATION_FRACTION
    random_seed: int = BASELINE_RANDOM_SEED


@dataclass(frozen=True)
class GroupedDatasetSplit:
    train_indices: Tuple[int, ...]
    validation_indices: Tuple[int, ...]
    test_indices: Tuple[int, ...]
    train_lot_trip_ids: Tuple[str, ...]
    validation_lot_trip_ids: Tuple[str, ...]
    test_lot_trip_ids: Tuple[str, ...]
    random_seed: int
    validation_fraction: float
    test_fraction: float


@dataclass(frozen=True)
class TrainingReadinessAssessment:
    diagnostics: TemporalRiskDatasetDiagnostics
    hard_failures: Tuple[str, ...]
    statistical_warnings: Tuple[str, ...]
    split: Optional[GroupedDatasetSplit]

    @property
    def ready(self) -> bool:
        return not self.hard_failures and self.split is not None


@dataclass(frozen=True)
class BaselineMetrics:
    roc_auc: float
    average_precision: float
    log_loss: float
    brier_score: float
    balanced_accuracy: float
    precision: float
    recall: float
    f1: float


@dataclass(frozen=True)
class CoefficientSummary:
    feature_name: str
    coefficient: float


@dataclass(frozen=True)
class LogisticBaselineTrainingResult:
    model: Any
    readiness: TrainingReadinessAssessment
    dataset_sha256: str
    validation_metrics: BaselineMetrics
    test_metrics: BaselineMetrics
    coefficients_by_absolute_magnitude: Tuple[CoefficientSummary, ...]


class TemporalRiskBaselineError(ValueError):
    pass


class TrainingReadinessError(TemporalRiskBaselineError):
    def __init__(self, assessment: TrainingReadinessAssessment):
        super().__init__("; ".join(assessment.hard_failures))
        self.assessment = assessment


def discover_local_temporal_risk_sources(workspace_root) -> Tuple[Path, ...]:
    root = Path(workspace_root)
    patterns = (
        "SD/backend/generated_datasets/*.jsonl",
        "SD/backend/ml_runs/*.jsonl",
    )
    return tuple(
        sorted(
            path
            for pattern in patterns
            for path in root.glob(pattern)
            if path.is_file()
        )
    )


def load_temporal_risk_jsonl(
    path,
    *,
    source_kind: TrainingSourceKind,
    source_id: Optional[str] = None,
) -> TemporalRiskTrainingDataset:
    source_path = Path(path)
    values = []
    with source_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                values.append(deserialize_temporal_risk_example(payload))
            except (ValueError, TypeError) as error:
                raise TemporalRiskBaselineError(
                    f"Invalid temporal-risk JSONL at line {line_number}"
                ) from error
    return validate_training_dataset(
        TemporalRiskTrainingDataset(
            source_id=source_id or source_path.name,
            source_kind=source_kind,
            examples=tuple(values),
        )
    )


def validate_training_dataset(
    dataset: TemporalRiskTrainingDataset,
) -> TemporalRiskTrainingDataset:
    if not isinstance(dataset, TemporalRiskTrainingDataset):
        raise TemporalRiskBaselineError(
            "dataset must be a TemporalRiskTrainingDataset"
        )
    if not isinstance(dataset.source_id, str) or not dataset.source_id.strip():
        raise TemporalRiskBaselineError("source_id must be a non-empty string")
    if not isinstance(dataset.source_kind, TrainingSourceKind):
        raise TemporalRiskBaselineError("source_kind is invalid")
    if not isinstance(dataset.examples, tuple):
        raise TemporalRiskBaselineError("examples must be an immutable tuple")
    example_ids = set()
    for example in dataset.examples:
        validate_temporal_risk_example(example)
        if example.example_id in example_ids:
            raise TemporalRiskBaselineError("Duplicate example_id in dataset")
        example_ids.add(example.example_id)
    return dataset


def diagnose_temporal_risk_dataset(
    dataset: TemporalRiskTrainingDataset,
) -> TemporalRiskDatasetDiagnostics:
    value = validate_training_dataset(dataset)
    examples = value.examples
    by_trip = Counter(example.lot_trip_id for example in examples)
    positive_count = sum(
        example.label.adverse_event_within_horizon for example in examples
    )
    negative_count = len(examples) - positive_count
    positive_trips = {
        example.lot_trip_id
        for example in examples
        if example.label.adverse_event_within_horizon
    }
    negative_trips = {
        example.lot_trip_id
        for example in examples
        if not example.label.adverse_event_within_horizon
    }
    trip_counts = tuple(by_trip.values())
    return TemporalRiskDatasetDiagnostics(
        source_id=value.source_id,
        source_kind=value.source_kind,
        unique_trip_count=len(by_trip),
        example_count=len(examples),
        positive_count=positive_count,
        negative_count=negative_count,
        positive_prevalence=(
            None if not examples else positive_count / len(examples)
        ),
        positive_trip_count=len(positive_trips),
        negative_trip_count=len(negative_trips),
        examples_per_trip_minimum=min(trip_counts, default=0),
        examples_per_trip_maximum=max(trip_counts, default=0),
        examples_per_trip_mean=(
            0.0 if not trip_counts else sum(trip_counts) / len(trip_counts)
        ),
        examples_per_trip_median=(
            0.0 if not trip_counts else float(median(trip_counts))
        ),
        feature_missingness=_feature_missingness(examples),
    )


def temporal_risk_dataset_fingerprint(
    dataset: TemporalRiskTrainingDataset,
) -> str:
    value = validate_training_dataset(dataset)
    digest = sha256()
    for example in sorted(value.examples, key=lambda item: item.example_id):
        document = serialize_temporal_risk_example(example)
        digest.update(
            json.dumps(
                document,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def assess_training_readiness(
    dataset: TemporalRiskTrainingDataset,
    policy: TrainingReadinessPolicy = TrainingReadinessPolicy(),
) -> TrainingReadinessAssessment:
    diagnostics = diagnose_temporal_risk_dataset(dataset)
    _validate_readiness_policy(policy)
    failures = []
    warnings = []
    if diagnostics.examples_per_trip_maximum > 1:
        warnings.append(
            "Multiple cutoffs from one trip are correlated; evaluation is trip-grouped."
        )
    if dataset.source_kind not in {
        TrainingSourceKind.REAL_OPERATIONAL,
        TrainingSourceKind.APPROVED_SIMULATOR,
    }:
        failures.append("SOURCE_NOT_APPROVED_FOR_TRAINING")
    if diagnostics.example_count == 0:
        failures.append("NO_TEMPORAL_EXAMPLES")
    if diagnostics.example_count < policy.minimum_examples:
        warnings.append("BELOW_POLICY_MINIMUM_EXAMPLES")
    if diagnostics.unique_trip_count < policy.minimum_unique_trips:
        warnings.append("BELOW_POLICY_MINIMUM_INDEPENDENT_TRIPS")
    if diagnostics.positive_count == 0 or diagnostics.negative_count == 0:
        failures.append("SINGLE_LABEL_CLASS")
    if diagnostics.positive_trip_count < policy.minimum_positive_trips:
        warnings.append("BELOW_POLICY_MINIMUM_POSITIVE_TRIPS")
    if diagnostics.negative_trip_count < policy.minimum_negative_trips:
        warnings.append("BELOW_POLICY_MINIMUM_NEGATIVE_TRIPS")
    if (
        diagnostics.positive_prevalence is not None
        and (
            diagnostics.positive_prevalence < 0.10
            or diagnostics.positive_prevalence > 0.90
        )
    ):
        warnings.append("Severe class imbalance requires careful PR-based evaluation.")
    if diagnostics.examples_per_trip_maximum > max(
        10, diagnostics.examples_per_trip_mean * 3
    ):
        warnings.append("Dense-trip imbalance is present; equal-trip weighting is required.")
    if dataset.source_kind == TrainingSourceKind.APPROVED_SIMULATOR:
        warnings.append("Simulator-trained performance may not generalize to real devices.")

    split = None
    if not failures:
        try:
            split = grouped_train_validation_test_split(dataset.examples, policy)
        except TemporalRiskBaselineError:
            failures.append("GROUPED_SPLIT_NOT_POSSIBLE")
        if split is not None and not _every_split_has_both_classes(
            dataset.examples, split
        ):
            failures.append("SPLIT_LABEL_COVERAGE_FAILURE")
            split = None
    return TrainingReadinessAssessment(
        diagnostics=diagnostics,
        hard_failures=tuple(failures),
        statistical_warnings=tuple(warnings),
        split=split,
    )


def grouped_train_validation_test_split(
    examples: Tuple[TemporalRiskExample, ...],
    policy: TrainingReadinessPolicy = TrainingReadinessPolicy(),
) -> GroupedDatasetSplit:
    _validate_readiness_policy(policy)
    if len({example.lot_trip_id for example in examples}) < 3:
        raise TemporalRiskBaselineError(
            "At least three independent trips are required for grouped splitting"
        )
    try:
        from sklearn.model_selection import GroupShuffleSplit
    except ImportError as error:
        raise TemporalRiskBaselineError(
            "scikit-learn is required for baseline splitting"
        ) from error

    groups = [example.lot_trip_id for example in examples]
    indices = list(range(len(examples)))
    outer = GroupShuffleSplit(
        n_splits=1,
        test_size=policy.test_fraction,
        random_state=policy.random_seed,
    )
    train_validation_indices, test_indices = next(
        outer.split(indices, groups=groups)
    )
    remaining_groups = [groups[index] for index in train_validation_indices]
    validation_share_of_remaining = policy.validation_fraction / (
        1.0 - policy.test_fraction
    )
    inner = GroupShuffleSplit(
        n_splits=1,
        test_size=validation_share_of_remaining,
        random_state=policy.random_seed + 1,
    )
    train_relative, validation_relative = next(
        inner.split(train_validation_indices, groups=remaining_groups)
    )
    train_indices = tuple(
        sorted(int(train_validation_indices[index]) for index in train_relative)
    )
    validation_indices = tuple(
        sorted(int(train_validation_indices[index]) for index in validation_relative)
    )
    test_indices = tuple(sorted(int(index) for index in test_indices))
    return _validate_grouped_split(
        examples,
        GroupedDatasetSplit(
            train_indices=train_indices,
            validation_indices=validation_indices,
            test_indices=test_indices,
            train_lot_trip_ids=_split_groups(examples, train_indices),
            validation_lot_trip_ids=_split_groups(examples, validation_indices),
            test_lot_trip_ids=_split_groups(examples, test_indices),
            random_seed=policy.random_seed,
            validation_fraction=policy.validation_fraction,
            test_fraction=policy.test_fraction,
        ),
    )


def equal_trip_sample_weights(
    examples: Tuple[TemporalRiskExample, ...],
    indices: Tuple[int, ...],
) -> Tuple[float, ...]:
    counts = Counter(examples[index].lot_trip_id for index in indices)
    if not counts:
        raise TemporalRiskBaselineError("Cannot weight an empty split")
    example_count = len(indices)
    trip_count = len(counts)
    return tuple(
        example_count / (trip_count * counts[examples[index].lot_trip_id])
        for index in indices
    )


def temporal_risk_model_inputs(examples):
    return tuple(_feature_row(example) for example in examples)


def temporal_risk_targets(examples):
    return tuple(
        int(example.label.adverse_event_within_horizon) for example in examples
    )


def build_temporal_risk_preprocessor(*, sparse_output=True):
    try:
        from sklearn.compose import ColumnTransformer
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder, StandardScaler
    except ImportError as error:
        raise TemporalRiskBaselineError(
            "scikit-learn is required for baseline preprocessing"
        ) from error
    categorical_count = len(TEMPORAL_RISK_CATEGORICAL_FEATURES)
    categorical_indices = list(range(categorical_count))
    numeric_indices = list(
        range(
            categorical_count,
            categorical_count + len(TEMPORAL_RISK_NUMERIC_FEATURES),
        )
    )
    return ColumnTransformer(
        transformers=(
            (
                "categorical",
                OneHotEncoder(
                    handle_unknown="ignore",
                    sparse_output=sparse_output,
                ),
                categorical_indices,
            ),
            (
                "numeric",
                Pipeline(
                    steps=(
                        (
                            "imputer",
                            SimpleImputer(
                                strategy="median",
                                keep_empty_features=True,
                            ),
                        ),
                        ("scaler", StandardScaler()),
                    )
                ),
                numeric_indices,
            ),
        )
    )


def train_logistic_regression_baseline(
    dataset: TemporalRiskTrainingDataset,
    policy: TrainingReadinessPolicy = TrainingReadinessPolicy(),
) -> LogisticBaselineTrainingResult:
    assessment = assess_training_readiness(dataset, policy)
    if not assessment.ready:
        raise TrainingReadinessError(assessment)
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
    except ImportError as error:
        raise TemporalRiskBaselineError(
            "scikit-learn is required for baseline training"
        ) from error

    examples = dataset.examples
    split = assessment.split
    matrix = temporal_risk_model_inputs(examples)
    labels = temporal_risk_targets(examples)
    preprocessor = build_temporal_risk_preprocessor()
    model = Pipeline(
        steps=(
            ("preprocessor", preprocessor),
            (
                "classifier",
                LogisticRegression(
                    C=1.0,
                    l1_ratio=0.0,
                    solver="liblinear",
                    class_weight=None,
                    max_iter=1000,
                    random_state=policy.random_seed,
                ),
            ),
        )
    )
    train_x, train_y = _select(matrix, labels, split.train_indices)
    weights = equal_trip_sample_weights(examples, split.train_indices)
    model.fit(train_x, train_y, classifier__sample_weight=weights)
    validation_metrics = _evaluate_model(
        model, matrix, labels, split.validation_indices
    )
    test_metrics = _evaluate_model(model, matrix, labels, split.test_indices)
    names = model.named_steps["preprocessor"].get_feature_names_out(
        TEMPORAL_RISK_CATEGORICAL_FEATURES + TEMPORAL_RISK_NUMERIC_FEATURES
    )
    coefficients = model.named_steps["classifier"].coef_[0]
    summary = tuple(
        sorted(
            (
                CoefficientSummary(str(name), float(coefficient))
                for name, coefficient in zip(names, coefficients)
            ),
            key=lambda item: abs(item.coefficient),
            reverse=True,
        )
    )
    return LogisticBaselineTrainingResult(
        model=model,
        readiness=assessment,
        dataset_sha256=temporal_risk_dataset_fingerprint(dataset),
        validation_metrics=validation_metrics,
        test_metrics=test_metrics,
        coefficients_by_absolute_magnitude=summary,
    )


def persist_logistic_baseline_artifact(
    result: LogisticBaselineTrainingResult,
    directory,
    *,
    created_at: datetime,
) -> Mapping[str, Any]:
    if not isinstance(result, LogisticBaselineTrainingResult):
        raise TemporalRiskBaselineError("result must be a training result")
    timestamp = _aware_datetime(created_at, "created_at")
    try:
        import joblib
        import sklearn
    except ImportError as error:
        raise TemporalRiskBaselineError(
            "scikit-learn and joblib are required for artifact persistence"
        ) from error
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    model_path = destination / "model.joblib"
    joblib.dump(result.model, model_path)
    model_sha256 = sha256(model_path.read_bytes()).hexdigest()
    metadata = {
        "schema": BASELINE_ARTIFACT_SCHEMA,
        "schema_version": BASELINE_ARTIFACT_SCHEMA_VERSION,
        "model_version": BASELINE_MODEL_VERSION,
        "created_at": timestamp.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "target_name": TEMPORAL_RISK_TARGET_NAME,
        "example_version": TEMPORAL_RISK_EXAMPLE_VERSION,
        "feature_version": TEMPORAL_RISK_FEATURE_VERSION,
        "label_version": TEMPORAL_RISK_LABEL_VERSION,
        "categorical_features": list(TEMPORAL_RISK_CATEGORICAL_FEATURES),
        "numeric_features": list(TEMPORAL_RISK_NUMERIC_FEATURES),
        "source_id": result.readiness.diagnostics.source_id,
        "source_kind": result.readiness.diagnostics.source_kind.value,
        "unique_trip_count": result.readiness.diagnostics.unique_trip_count,
        "example_count": result.readiness.diagnostics.example_count,
        "dataset_sha256": result.dataset_sha256,
        "split": {
            "random_seed": result.readiness.split.random_seed,
            "validation_fraction": result.readiness.split.validation_fraction,
            "test_fraction": result.readiness.split.test_fraction,
            "train_lot_trip_ids": list(
                result.readiness.split.train_lot_trip_ids
            ),
            "validation_lot_trip_ids": list(
                result.readiness.split.validation_lot_trip_ids
            ),
            "test_lot_trip_ids": list(
                result.readiness.split.test_lot_trip_ids
            ),
        },
        "validation_metrics": vars(result.validation_metrics),
        "test_metrics": vars(result.test_metrics),
        "coefficient_summary": [
            vars(item) for item in result.coefficients_by_absolute_magnitude
        ],
        "classifier": {
            "type": "LogisticRegression",
            "regularization": "l2",
            "C": 1.0,
            "l1_ratio": 0.0,
            "solver": "liblinear",
            "class_weight": None,
            "max_iter": 1000,
        },
        "dense_trip_weighting": (
            "example_count / (unique_trip_count * examples_for_trip)"
        ),
        "model_sha256": model_sha256,
        "scikit_learn_version": sklearn.__version__,
    }
    (destination / "metadata.json").write_text(
        json.dumps(metadata, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def _feature_missingness(examples):
    feature_names = (
        TEMPORAL_RISK_CATEGORICAL_FEATURES + TEMPORAL_RISK_NUMERIC_FEATURES
    )
    values = []
    for name in feature_names:
        missing = sum(getattr(example.features, name) is None for example in examples)
        values.append(
            FeatureMissingness(
                feature_name=name,
                missing_count=missing,
                missing_fraction=None if not examples else missing / len(examples),
            )
        )
    return tuple(values)


def _feature_row(example):
    categorical = []
    for name in TEMPORAL_RISK_CATEGORICAL_FEATURES:
        value = getattr(example.features, name)
        if isinstance(value, Enum):
            value = value.value
        categorical.append(MISSING_CATEGORY_TOKEN if value is None else str(value))
    numeric = []
    for name in TEMPORAL_RISK_NUMERIC_FEATURES:
        value = getattr(example.features, name)
        numeric.append(float("nan") if value is None else float(value))
    return categorical + numeric


def _evaluate_model(model, matrix, labels, indices):
    from sklearn.metrics import (
        average_precision_score,
        balanced_accuracy_score,
        brier_score_loss,
        f1_score,
        log_loss,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    values, expected = _select(matrix, labels, indices)
    probabilities = model.predict_proba(values)[:, 1]
    predicted = (probabilities >= 0.5).astype(int)
    return BaselineMetrics(
        roc_auc=float(roc_auc_score(expected, probabilities)),
        average_precision=float(average_precision_score(expected, probabilities)),
        log_loss=float(log_loss(expected, probabilities, labels=[0, 1])),
        brier_score=float(brier_score_loss(expected, probabilities)),
        balanced_accuracy=float(balanced_accuracy_score(expected, predicted)),
        precision=float(precision_score(expected, predicted, zero_division=0)),
        recall=float(recall_score(expected, predicted, zero_division=0)),
        f1=float(f1_score(expected, predicted, zero_division=0)),
    )


def _select(matrix, labels, indices):
    return [matrix[index] for index in indices], [labels[index] for index in indices]


def _split_groups(examples, indices):
    return tuple(sorted({examples[index].lot_trip_id for index in indices}))


def _validate_grouped_split(examples, split):
    train = set(split.train_lot_trip_ids)
    validation = set(split.validation_lot_trip_ids)
    test = set(split.test_lot_trip_ids)
    if train & validation or train & test or validation & test:
        raise TemporalRiskBaselineError("Grouped split leaked a lot_trip_id")
    expected_indices = set(range(len(examples)))
    actual_indices = (
        set(split.train_indices)
        | set(split.validation_indices)
        | set(split.test_indices)
    )
    if actual_indices != expected_indices:
        raise TemporalRiskBaselineError("Grouped split lost or duplicated examples")
    return split


def _every_split_has_both_classes(examples, split):
    for indices in (
        split.train_indices,
        split.validation_indices,
        split.test_indices,
    ):
        labels = {
            examples[index].label.adverse_event_within_horizon for index in indices
        }
        if labels != {False, True}:
            return False
    return True


def _validate_readiness_policy(policy):
    if not isinstance(policy, TrainingReadinessPolicy):
        raise TemporalRiskBaselineError("policy must be TrainingReadinessPolicy")
    for field in (
        "minimum_unique_trips",
        "minimum_examples",
        "minimum_positive_trips",
        "minimum_negative_trips",
    ):
        value = getattr(policy, field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise TemporalRiskBaselineError(f"{field} must be positive")
    if (
        not 0 < policy.test_fraction < 1
        or not 0 < policy.validation_fraction < 1
        or policy.test_fraction + policy.validation_fraction >= 1
    ):
        raise TemporalRiskBaselineError("Split fractions are invalid")


def _aware_datetime(value, field):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TemporalRiskBaselineError(f"{field} must be timezone-aware")
    return value
