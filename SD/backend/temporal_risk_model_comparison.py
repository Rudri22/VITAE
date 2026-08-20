import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Tuple

try:
    from .temporal_risk_baseline import (
        BASELINE_RANDOM_SEED,
        LogisticBaselineTrainingResult,
        TemporalRiskTrainingDataset,
        TrainingReadinessError,
        assess_training_readiness,
        build_temporal_risk_preprocessor,
        equal_trip_sample_weights,
        temporal_risk_dataset_fingerprint,
        temporal_risk_model_inputs,
        temporal_risk_targets,
    )
    from .temporal_risk_calibration import (
        ProbabilityMetrics,
        TemporalRiskCalibrationAnalysis,
        analyze_model_calibration,
        analyze_temporal_risk_calibration,
        probability_metrics,
    )
    from .temporal_risk_examples import (
        TEMPORAL_RISK_CATEGORICAL_FEATURES,
        TEMPORAL_RISK_FEATURE_VERSION,
        TEMPORAL_RISK_LABEL_VERSION,
        TEMPORAL_RISK_NUMERIC_FEATURES,
    )
except ImportError:
    from temporal_risk_baseline import (
        BASELINE_RANDOM_SEED,
        LogisticBaselineTrainingResult,
        TemporalRiskTrainingDataset,
        TrainingReadinessError,
        assess_training_readiness,
        build_temporal_risk_preprocessor,
        equal_trip_sample_weights,
        temporal_risk_dataset_fingerprint,
        temporal_risk_model_inputs,
        temporal_risk_targets,
    )
    from temporal_risk_calibration import (
        ProbabilityMetrics,
        TemporalRiskCalibrationAnalysis,
        analyze_model_calibration,
        analyze_temporal_risk_calibration,
        probability_metrics,
    )
    from temporal_risk_examples import (
        TEMPORAL_RISK_CATEGORICAL_FEATURES,
        TEMPORAL_RISK_FEATURE_VERSION,
        TEMPORAL_RISK_LABEL_VERSION,
        TEMPORAL_RISK_NUMERIC_FEATURES,
    )


BOOSTED_MODEL_VERSION = "temporal-risk-gradient-boosting-v1"
MODEL_COMPARISON_SCHEMA = "vitae.temporal_risk_model_comparison"
MODEL_COMPARISON_SCHEMA_VERSION = 1
MODEL_COMPARISON_BOOTSTRAP_SEED = 314159
MODEL_COMPARISON_BOOTSTRAP_REPLICATES = 500


@dataclass(frozen=True)
class BoostedTrainingResult:
    model: Any
    dataset_sha256: str
    split: Any


@dataclass(frozen=True)
class ModelOverfitAudit:
    model_name: str
    raw_train_metrics: ProbabilityMetrics
    raw_validation_metrics: ProbabilityMetrics
    roc_auc_gap: float
    average_precision_gap: float
    log_loss_gap: float
    brier_score_gap: float


@dataclass(frozen=True)
class PairedBootstrapDelta:
    metric_name: str
    lower_95: float
    median: float
    upper_95: float
    valid_replicates: int


@dataclass(frozen=True)
class BoostedFeatureImportance:
    feature_name: str
    importance: float


@dataclass(frozen=True)
class TemporalRiskModelComparison:
    dataset_sha256: str
    logistic_calibration: TemporalRiskCalibrationAnalysis
    boosted_calibration: TemporalRiskCalibrationAnalysis
    logistic_overfit_audit: ModelOverfitAudit
    boosted_overfit_audit: ModelOverfitAudit
    validation_paired_deltas: Tuple[PairedBootstrapDelta, ...]
    boosted_feature_importance: Tuple[BoostedFeatureImportance, ...]
    boosted_advantage_material: bool
    preferred_engineering_candidate: str
    preference_basis: str
    risk_policy: None
    warnings: Tuple[str, ...]


class TemporalRiskModelComparisonError(ValueError):
    pass


def train_boosted_temporal_risk_model(
    dataset: TemporalRiskTrainingDataset,
    *,
    reference_split,
) -> BoostedTrainingResult:
    assessment = assess_training_readiness(dataset)
    if not assessment.ready:
        raise TrainingReadinessError(assessment)
    if assessment.split != reference_split:
        raise TemporalRiskModelComparisonError(
            "Boosted model must use the exact frozen logistic grouped split"
        )
    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.pipeline import Pipeline
    except ImportError as error:
        raise TemporalRiskModelComparisonError(
            "scikit-learn is required for boosted comparison"
        ) from error
    model = Pipeline(
        steps=(
            (
                "preprocessor",
                build_temporal_risk_preprocessor(sparse_output=False),
            ),
            (
                "classifier",
                GradientBoostingClassifier(
                    n_estimators=100,
                    learning_rate=0.05,
                    max_depth=2,
                    min_samples_leaf=20,
                    subsample=0.8,
                    random_state=BASELINE_RANDOM_SEED,
                ),
            ),
        )
    )
    examples = dataset.examples
    matrix = temporal_risk_model_inputs(examples)
    labels = temporal_risk_targets(examples)
    train_indices = reference_split.train_indices
    model.fit(
        [matrix[index] for index in train_indices],
        [labels[index] for index in train_indices],
        classifier__sample_weight=equal_trip_sample_weights(
            examples, train_indices
        ),
    )
    return BoostedTrainingResult(
        model=model,
        dataset_sha256=temporal_risk_dataset_fingerprint(dataset),
        split=reference_split,
    )


def compare_temporal_risk_models(
    dataset: TemporalRiskTrainingDataset,
    logistic_result: LogisticBaselineTrainingResult,
    *,
    bootstrap_replicates: int = MODEL_COMPARISON_BOOTSTRAP_REPLICATES,
) -> Tuple[BoostedTrainingResult, TemporalRiskModelComparison]:
    if not isinstance(logistic_result, LogisticBaselineTrainingResult):
        raise TemporalRiskModelComparisonError(
            "logistic_result must be LogisticBaselineTrainingResult"
        )
    split = logistic_result.readiness.split
    if split is None:
        raise TemporalRiskModelComparisonError("Logistic result has no frozen split")
    boosted = train_boosted_temporal_risk_model(
        dataset,
        reference_split=split,
    )
    logistic_calibration = analyze_temporal_risk_calibration(
        dataset,
        logistic_result,
        bootstrap_replicates=bootstrap_replicates,
    )
    boosted_calibration = analyze_model_calibration(
        dataset,
        model=boosted.model,
        dataset_sha256=boosted.dataset_sha256,
        split=boosted.split,
        base_model_version=BOOSTED_MODEL_VERSION,
        bootstrap_replicates=bootstrap_replicates,
    )
    examples = dataset.examples
    matrix = temporal_risk_model_inputs(examples)
    labels = temporal_risk_targets(examples)
    logistic_probabilities = tuple(
        float(value) for value in logistic_result.model.predict_proba(matrix)[:, 1]
    )
    boosted_probabilities = tuple(
        float(value) for value in boosted.model.predict_proba(matrix)[:, 1]
    )
    logistic_calibrated = logistic_calibration.calibrator.predict(
        logistic_probabilities
    )
    boosted_calibrated = boosted_calibration.calibrator.predict(
        boosted_probabilities
    )
    deltas = paired_group_bootstrap_deltas(
        tuple(labels[index] for index in split.validation_indices),
        tuple(logistic_calibrated[index] for index in split.validation_indices),
        tuple(boosted_calibrated[index] for index in split.validation_indices),
        tuple(
            examples[index].lot_trip_id for index in split.validation_indices
        ),
        replicates=bootstrap_replicates,
    )
    material = _boosted_advantage_is_material(deltas)
    preference = "BOOSTED" if material else "LOGISTIC"
    basis = (
        "Paired validation-trip bootstrap intervals consistently favor boosted."
        if material
        else "Boosted advantage is not consistent across paired validation-trip intervals; prefer the simpler logistic model."
    )
    comparison = TemporalRiskModelComparison(
        dataset_sha256=logistic_result.dataset_sha256,
        logistic_calibration=logistic_calibration,
        boosted_calibration=boosted_calibration,
        logistic_overfit_audit=_overfit_audit(
            "LOGISTIC",
            labels,
            logistic_probabilities,
            split.train_indices,
            split.validation_indices,
        ),
        boosted_overfit_audit=_overfit_audit(
            "BOOSTED",
            labels,
            boosted_probabilities,
            split.train_indices,
            split.validation_indices,
        ),
        validation_paired_deltas=deltas,
        boosted_feature_importance=_boosted_feature_importance(boosted.model),
        boosted_advantage_material=material,
        preferred_engineering_candidate=preference,
        preference_basis=basis,
        risk_policy=None,
        warnings=(
            "SIMULATED ONLY: neither model has real-device performance evidence.",
            "Validation has very few positive trips and model comparison is unstable.",
            "Deterministic simulator thresholds can make nonlinear performance look unrealistically strong.",
            "No LOW/MEDIUM/HIGH risk policy is approved.",
        ),
    )
    return boosted, comparison


def paired_group_bootstrap_deltas(
    labels,
    logistic_probabilities,
    boosted_probabilities,
    groups,
    *,
    replicates=MODEL_COMPARISON_BOOTSTRAP_REPLICATES,
) -> Tuple[PairedBootstrapDelta, ...]:
    import numpy as np

    values = tuple(int(value) for value in labels)
    logistic = tuple(float(value) for value in logistic_probabilities)
    boosted = tuple(float(value) for value in boosted_probabilities)
    group_values = tuple(groups)
    if not values or not (
        len(values) == len(logistic) == len(boosted) == len(group_values)
    ):
        raise TemporalRiskModelComparisonError(
            "Paired bootstrap inputs must align and be non-empty"
        )
    if isinstance(replicates, bool) or not isinstance(replicates, int) or replicates < 1:
        raise TemporalRiskModelComparisonError("replicates must be positive")
    unique_groups = tuple(sorted(set(group_values)))
    if len(unique_groups) < 2:
        raise TemporalRiskModelComparisonError(
            "Paired bootstrap requires at least two independent trips"
        )
    by_group = {
        group: tuple(index for index, value in enumerate(group_values) if value == group)
        for group in unique_groups
    }
    generator = np.random.default_rng(MODEL_COMPARISON_BOOTSTRAP_SEED)
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
        sampled_labels = tuple(values[index] for index in indices)
        if set(sampled_labels) != {0, 1}:
            continue
        logistic_metrics = probability_metrics(
            sampled_labels, tuple(logistic[index] for index in indices)
        )
        boosted_metrics = probability_metrics(
            sampled_labels, tuple(boosted[index] for index in indices)
        )
        for name in collected:
            collected[name].append(
                getattr(boosted_metrics, name) - getattr(logistic_metrics, name)
            )
    if not collected["roc_auc"]:
        raise TemporalRiskModelComparisonError(
            "No paired bootstrap replicate contained both classes"
        )
    return tuple(
        PairedBootstrapDelta(
            metric_name=name,
            lower_95=float(np.percentile(metric_values, 2.5)),
            median=float(np.percentile(metric_values, 50.0)),
            upper_95=float(np.percentile(metric_values, 97.5)),
            valid_replicates=len(metric_values),
        )
        for name, metric_values in collected.items()
    )


def persist_model_comparison(
    boosted: BoostedTrainingResult,
    comparison: TemporalRiskModelComparison,
    directory,
    *,
    created_at: datetime,
) -> Mapping[str, Path]:
    timestamp = _aware_datetime(created_at, "created_at")
    try:
        import joblib
        import sklearn
    except ImportError as error:
        raise TemporalRiskModelComparisonError(
            "scikit-learn and joblib are required for comparison artifacts"
        ) from error
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    model_path = destination / "boosted_model.joblib"
    metadata_path = destination / "comparison.json"
    joblib.dump(boosted.model, model_path)
    document = {
        "schema": MODEL_COMPARISON_SCHEMA,
        "schema_version": MODEL_COMPARISON_SCHEMA_VERSION,
        "created_at": timestamp.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "dataset_sha256": comparison.dataset_sha256,
        "feature_version": TEMPORAL_RISK_FEATURE_VERSION,
        "label_version": TEMPORAL_RISK_LABEL_VERSION,
        "logistic_model_version": comparison.logistic_calibration.base_model_version,
        "boosted_model_version": BOOSTED_MODEL_VERSION,
        "boosted_configuration": {
            "n_estimators": 100,
            "learning_rate": 0.05,
            "max_depth": 2,
            "min_samples_leaf": 20,
            "subsample": 0.8,
            "random_seed": BASELINE_RANDOM_SEED,
        },
        "split": {
            "train_lot_trip_ids": list(boosted.split.train_lot_trip_ids),
            "validation_lot_trip_ids": list(
                boosted.split.validation_lot_trip_ids
            ),
            "test_lot_trip_ids": list(boosted.split.test_lot_trip_ids),
        },
        "calibrated_logistic_validation_metrics": vars(
            comparison.logistic_calibration.calibrated_validation_metrics
        ),
        "calibrated_boosted_validation_metrics": vars(
            comparison.boosted_calibration.calibrated_validation_metrics
        ),
        "final_logistic_test_metrics": vars(
            comparison.logistic_calibration.calibrated_test_metrics
        ),
        "final_boosted_test_metrics": vars(
            comparison.boosted_calibration.calibrated_test_metrics
        ),
        "paired_validation_bootstrap_deltas": [
            vars(item) for item in comparison.validation_paired_deltas
        ],
        "logistic_validation_bootstrap_intervals": [
            vars(item)
            for item in comparison.logistic_calibration.validation_bootstrap_intervals
        ],
        "boosted_validation_bootstrap_intervals": [
            vars(item)
            for item in comparison.boosted_calibration.validation_bootstrap_intervals
        ],
        "logistic_overfit_audit": _audit_document(
            comparison.logistic_overfit_audit
        ),
        "boosted_overfit_audit": _audit_document(
            comparison.boosted_overfit_audit
        ),
        "boosted_feature_importance": [
            vars(item) for item in comparison.boosted_feature_importance
        ],
        "boosted_advantage_material": comparison.boosted_advantage_material,
        "preferred_engineering_candidate": (
            comparison.preferred_engineering_candidate
        ),
        "preference_basis": comparison.preference_basis,
        "risk_policy": comparison.risk_policy,
        "warnings": list(comparison.warnings),
        "simulator_artifact_findings": [
            "Excursion duration and utilization dominate boosted importance.",
            "Deterministic ProductRules create unusually separable simulated boundaries.",
            "The corpus contains only one product, presentation, state, and rule version.",
        ],
        "boosted_model_sha256": sha256(model_path.read_bytes()).hexdigest(),
        "scikit_learn_version": sklearn.__version__,
    }
    metadata_path.write_text(
        json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return {"model": model_path, "metadata": metadata_path}


def _overfit_audit(model_name, labels, probabilities, train_indices, validation_indices):
    train = probability_metrics(
        tuple(labels[index] for index in train_indices),
        tuple(probabilities[index] for index in train_indices),
    )
    validation = probability_metrics(
        tuple(labels[index] for index in validation_indices),
        tuple(probabilities[index] for index in validation_indices),
    )
    return ModelOverfitAudit(
        model_name=model_name,
        raw_train_metrics=train,
        raw_validation_metrics=validation,
        roc_auc_gap=train.roc_auc - validation.roc_auc,
        average_precision_gap=train.average_precision - validation.average_precision,
        log_loss_gap=validation.log_loss - train.log_loss,
        brier_score_gap=validation.brier_score - train.brier_score,
    )


def _boosted_feature_importance(model):
    preprocessor = model.named_steps["preprocessor"]
    names = preprocessor.get_feature_names_out(
        TEMPORAL_RISK_CATEGORICAL_FEATURES + TEMPORAL_RISK_NUMERIC_FEATURES
    )
    importance = model.named_steps["classifier"].feature_importances_
    return tuple(
        sorted(
            (
                BoostedFeatureImportance(str(name), float(value))
                for name, value in zip(names, importance)
            ),
            key=lambda item: item.importance,
            reverse=True,
        )
    )


def _boosted_advantage_is_material(deltas):
    values = {item.metric_name: item for item in deltas}
    return (
        values["roc_auc"].lower_95 > 0
        and values["average_precision"].lower_95 > 0
        and values["log_loss"].upper_95 < 0
        and values["brier_score"].upper_95 < 0
    )


def _audit_document(audit):
    return {
        "model_name": audit.model_name,
        "raw_train_metrics": vars(audit.raw_train_metrics),
        "raw_validation_metrics": vars(audit.raw_validation_metrics),
        "roc_auc_gap": audit.roc_auc_gap,
        "average_precision_gap": audit.average_precision_gap,
        "log_loss_gap": audit.log_loss_gap,
        "brier_score_gap": audit.brier_score_gap,
    }


def _aware_datetime(value, field):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TemporalRiskModelComparisonError(f"{field} must be timezone-aware")
    return value
