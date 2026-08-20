import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from math import isfinite
from typing import Iterable, Optional, Tuple

try:
    from .completed_trip_dataset import (
        CompletedTripDatasetRecord,
        validate_completed_trip_dataset_record,
    )
    from .decision_outbox import StatusDecisionRecord
    from .risk_rules import ApplicationStatus
    from .state_repository import TelemetryRecord
except ImportError:
    from completed_trip_dataset import (
        CompletedTripDatasetRecord,
        validate_completed_trip_dataset_record,
    )
    from decision_outbox import StatusDecisionRecord
    from risk_rules import ApplicationStatus
    from state_repository import TelemetryRecord


TEMPORAL_RISK_FEATURE_VERSION = "temporal-risk-features-v1"
TEMPORAL_RISK_LABEL_VERSION = "adverse-status-within-30-minutes-v1"
TEMPORAL_RISK_EXAMPLE_VERSION = "temporal-risk-example-v1"
TEMPORAL_RISK_TARGET_NAME = "adverse_deterministic_status_within_horizon"
TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES = 30

TEMPORAL_RISK_CATEGORICAL_FEATURES = (
    "product_id",
    "presentation",
    "state",
    "product_rule_version",
    "current_status",
    "current_active_rule_id",
    "latest_device_health",
)

TEMPORAL_RISK_NUMERIC_FEATURES = (
    "sample_count",
    "trip_elapsed_minutes",
    "observation_span_minutes",
    "minutes_since_previous_sample",
    "minutes_since_previous_sample_missing",
    "latest_temperature_c",
    "mean_temperature_c",
    "minimum_temperature_c",
    "maximum_temperature_c",
    "temperature_range_c",
    "temperature_change_from_first_c",
    "temperature_slope_c_per_hour",
    "latest_battery_level_percent",
    "latest_battery_level_missing",
    "current_excursion_episode_duration_minutes",
    "current_cumulative_excursion_duration_minutes",
    "current_excursion_utilization",
    "current_excursion_utilization_missing",
    "safe_count_through_cutoff",
    "monitor_count_through_cutoff",
    "at_risk_count_through_cutoff",
    "critical_count_through_cutoff",
    "rule_violation_count_through_cutoff",
    "data_error_count_through_cutoff",
)

TEMPORAL_RISK_ADVERSE_STATUSES = frozenset(
    (
        ApplicationStatus.AT_RISK,
        ApplicationStatus.CRITICAL,
        ApplicationStatus.RULE_VIOLATION,
    )
)
TEMPORAL_RISK_ELIGIBLE_CUTOFF_STATUSES = frozenset(
    (ApplicationStatus.SAFE, ApplicationStatus.MONITOR)
)


@dataclass(frozen=True)
class TemporalRiskFeatures:
    product_id: str
    presentation: str
    state: str
    product_rule_version: str
    current_status: ApplicationStatus
    current_active_rule_id: Optional[str]
    latest_device_health: Optional[str]
    sample_count: int
    trip_elapsed_minutes: float
    observation_span_minutes: float
    minutes_since_previous_sample: Optional[float]
    minutes_since_previous_sample_missing: bool
    latest_temperature_c: float
    mean_temperature_c: float
    minimum_temperature_c: float
    maximum_temperature_c: float
    temperature_range_c: float
    temperature_change_from_first_c: float
    temperature_slope_c_per_hour: float
    latest_battery_level_percent: Optional[float]
    latest_battery_level_missing: bool
    current_excursion_episode_duration_minutes: float
    current_cumulative_excursion_duration_minutes: float
    current_excursion_utilization: Optional[float]
    current_excursion_utilization_missing: bool
    safe_count_through_cutoff: int
    monitor_count_through_cutoff: int
    at_risk_count_through_cutoff: int
    critical_count_through_cutoff: int
    rule_violation_count_through_cutoff: int
    data_error_count_through_cutoff: int


@dataclass(frozen=True)
class TemporalRiskFeatureContext:
    lot_trip_id: str
    trip_id: str
    device_id: str
    product_id: str
    presentation: str
    state: str
    product_rule_version: str
    trip_started_at: datetime


@dataclass(frozen=True)
class TemporalRiskLabel:
    adverse_event_within_horizon: bool
    first_adverse_status: Optional[ApplicationStatus]
    first_adverse_at: Optional[datetime]


@dataclass(frozen=True)
class TemporalRiskExample:
    example_id: str
    lot_trip_id: str
    trip_id: str
    cutoff_sample_id: str
    cutoff_at: datetime
    horizon_ends_at: datetime
    prediction_horizon_minutes: int
    example_version: str
    feature_version: str
    label_version: str
    features: TemporalRiskFeatures
    label: TemporalRiskLabel


class TemporalRiskExampleError(ValueError):
    pass


def build_temporal_risk_examples(
    record: CompletedTripDatasetRecord,
) -> Tuple[TemporalRiskExample, ...]:
    """Build v1 binary examples from finalized facts without recalculating status.

    Each eligible SAFE/MONITOR decision is a cutoff. Features use its prefix;
    labels use only later persisted decisions inside the fixed horizon.
    """
    source = validate_completed_trip_dataset_record(record)
    examples = []
    horizon = timedelta(minutes=TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES)
    paired_history = tuple(
        zip(source.telemetry_records, source.decision_records)
    )
    for index, (cutoff_record, cutoff_decision) in enumerate(paired_history):
        if cutoff_decision.status not in TEMPORAL_RISK_ELIGIBLE_CUTOFF_STATUSES:
            continue
        horizon_ends_at = cutoff_record.timestamp + horizon
        if horizon_ends_at > source.outcome.completed_at:
            continue
        future = tuple(
            pair
            for pair in paired_history[index + 1 :]
            if pair[1].sample_timestamp <= horizon_ends_at
        )
        if not future:
            continue
        if any(
            decision.status == ApplicationStatus.DATA_ERROR
            for _, decision in future
        ):
            continue
        observed_through_horizon = (
            source.outcome.completed_at == horizon_ends_at
            or any(
                decision.sample_timestamp >= horizon_ends_at
                for _, decision in paired_history[index + 1 :]
            )
        )
        if not observed_through_horizon:
            continue

        prefix = paired_history[: index + 1]
        first_adverse = next(
            (
                decision
                for _, decision in future
                if decision.status in TEMPORAL_RISK_ADVERSE_STATUSES
            ),
            None,
        )
        example = TemporalRiskExample(
            example_id=temporal_risk_example_id(
                source.lot_trip_id,
                cutoff_record.sample_id,
            ),
            lot_trip_id=source.lot_trip_id,
            trip_id=source.outcome.trip_id,
            cutoff_sample_id=cutoff_record.sample_id,
            cutoff_at=cutoff_record.timestamp,
            horizon_ends_at=horizon_ends_at,
            prediction_horizon_minutes=(
                TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES
            ),
            example_version=TEMPORAL_RISK_EXAMPLE_VERSION,
            feature_version=TEMPORAL_RISK_FEATURE_VERSION,
            label_version=TEMPORAL_RISK_LABEL_VERSION,
            features=build_temporal_risk_features_from_prefix(
                TemporalRiskFeatureContext(
                    lot_trip_id=source.outcome.lot_trip_id,
                    trip_id=source.outcome.trip_id,
                    device_id=source.outcome.device_id,
                    product_id=source.outcome.product_id,
                    presentation=source.outcome.presentation,
                    state=source.outcome.state,
                    product_rule_version=source.outcome.product_rule_version,
                    trip_started_at=source.outcome.trip_started_at,
                ),
                tuple(record for record, _ in prefix),
                tuple(decision for _, decision in prefix),
            ),
            label=TemporalRiskLabel(
                adverse_event_within_horizon=first_adverse is not None,
                first_adverse_status=(
                    None if first_adverse is None else first_adverse.status
                ),
                first_adverse_at=(
                    None
                    if first_adverse is None
                    else first_adverse.sample_timestamp
                ),
            ),
        )
        examples.append(validate_temporal_risk_example(example))
    return tuple(examples)


def build_temporal_risk_dataset(
    records: Iterable[CompletedTripDatasetRecord],
) -> Tuple[TemporalRiskExample, ...]:
    examples = []
    lot_trip_ids = set()
    for record in sorted(tuple(records), key=lambda value: value.lot_trip_id):
        if record.lot_trip_id in lot_trip_ids:
            raise TemporalRiskExampleError(
                "Completed-trip source records must have unique lot_trip_id values"
            )
        lot_trip_ids.add(record.lot_trip_id)
        examples.extend(build_temporal_risk_examples(record))
    return tuple(examples)


def temporal_risk_examples_jsonl(
    examples: Iterable[TemporalRiskExample],
) -> str:
    try:
        from .repository_serialization import serialize_temporal_risk_example
    except ImportError:
        from repository_serialization import serialize_temporal_risk_example

    values = tuple(validate_temporal_risk_example(value) for value in examples)
    example_ids = tuple(value.example_id for value in values)
    if len(set(example_ids)) != len(example_ids):
        raise TemporalRiskExampleError(
            "Temporal-risk export contains duplicate example_id values"
        )
    ordered = sorted(
        values,
        key=lambda value: (
            value.lot_trip_id,
            value.cutoff_at,
            value.example_id,
        ),
    )
    return "".join(
        json.dumps(
            serialize_temporal_risk_example(value),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for value in ordered
    )


def temporal_risk_example_id(lot_trip_id: str, cutoff_sample_id: str) -> str:
    raw = "|".join(
        (
            _required_text(lot_trip_id, "lot_trip_id"),
            _required_text(cutoff_sample_id, "cutoff_sample_id"),
            TEMPORAL_RISK_EXAMPLE_VERSION,
            TEMPORAL_RISK_FEATURE_VERSION,
            TEMPORAL_RISK_LABEL_VERSION,
        )
    )
    return "temporal-risk-" + sha256(raw.encode("utf-8")).hexdigest()[:24]


def validate_temporal_risk_example(
    value: TemporalRiskExample,
) -> TemporalRiskExample:
    if not isinstance(value, TemporalRiskExample):
        raise TemporalRiskExampleError("value must be a TemporalRiskExample")
    for field in ("example_id", "lot_trip_id", "trip_id", "cutoff_sample_id"):
        _required_text(getattr(value, field), field)
    for field in ("cutoff_at", "horizon_ends_at"):
        _aware_datetime(getattr(value, field), field)
    if value.prediction_horizon_minutes != TEMPORAL_RISK_PREDICTION_HORIZON_MINUTES:
        raise TemporalRiskExampleError("Unexpected prediction horizon")
    if value.horizon_ends_at != value.cutoff_at + timedelta(
        minutes=value.prediction_horizon_minutes
    ):
        raise TemporalRiskExampleError("horizon_ends_at does not match cutoff")
    if value.example_version != TEMPORAL_RISK_EXAMPLE_VERSION:
        raise TemporalRiskExampleError("Unexpected example version")
    if value.feature_version != TEMPORAL_RISK_FEATURE_VERSION:
        raise TemporalRiskExampleError("Unexpected feature version")
    if value.label_version != TEMPORAL_RISK_LABEL_VERSION:
        raise TemporalRiskExampleError("Unexpected label version")
    expected_id = temporal_risk_example_id(
        value.lot_trip_id,
        value.cutoff_sample_id,
    )
    if value.example_id != expected_id:
        raise TemporalRiskExampleError("example_id is not canonical")
    _validate_features(value.features)
    _validate_label(value.label, value.cutoff_at, value.horizon_ends_at)
    return value


def build_temporal_risk_features_from_prefix(
    context: TemporalRiskFeatureContext,
    telemetry_records: Iterable[TelemetryRecord],
    decision_records: Iterable[StatusDecisionRecord],
) -> TemporalRiskFeatures:
    """Build v1 features from facts available through one explicit cutoff."""
    if not isinstance(context, TemporalRiskFeatureContext):
        raise TemporalRiskExampleError(
            "context must be a TemporalRiskFeatureContext"
        )
    for field in (
        "lot_trip_id",
        "trip_id",
        "device_id",
        "product_id",
        "presentation",
        "state",
        "product_rule_version",
    ):
        _required_text(getattr(context, field), field)
    _aware_datetime(context.trip_started_at, "trip_started_at")
    records = tuple(telemetry_records)
    decisions = tuple(decision_records)
    if not records or len(records) != len(decisions):
        raise TemporalRiskExampleError(
            "Telemetry and decision prefixes must be non-empty and aligned"
        )
    for index, (record, decision) in enumerate(zip(records, decisions)):
        if not isinstance(record, TelemetryRecord):
            raise TemporalRiskExampleError(
                "telemetry_records must contain TelemetryRecord values"
            )
        if not isinstance(decision, StatusDecisionRecord):
            raise TemporalRiskExampleError(
                "decision_records must contain StatusDecisionRecord values"
            )
        expected = (
            context.lot_trip_id,
            context.trip_id,
            context.device_id,
            record.sample_id,
            record.timestamp,
            context.product_id,
            context.product_rule_version,
        )
        actual = (
            record.lot_trip_id,
            record.trip_id,
            record.device_id,
            decision.sample_id,
            decision.sample_timestamp,
            decision.product_id,
            decision.product_rule_version,
        )
        if actual != expected or (
            decision.lot_trip_id != context.lot_trip_id
            or decision.trip_id != context.trip_id
            or decision.device_id != context.device_id
        ):
            raise TemporalRiskExampleError(
                "Telemetry, decision, and feature context identities must align"
            )
        if index and record.timestamp <= records[index - 1].timestamp:
            raise TemporalRiskExampleError(
                "Telemetry prefix timestamps must be strictly increasing"
            )
    latest_record = records[-1]
    latest_decision = decisions[-1]
    temperatures = tuple(float(record.temperature) for record in records)
    statuses = tuple(decision.status for decision in decisions)
    features = TemporalRiskFeatures(
        product_id=context.product_id,
        presentation=context.presentation,
        state=context.state,
        product_rule_version=context.product_rule_version,
        current_status=latest_decision.status,
        current_active_rule_id=latest_decision.active_rule_id,
        latest_device_health=latest_record.device_health,
        sample_count=len(records),
        trip_elapsed_minutes=_minutes(
            latest_record.timestamp - context.trip_started_at
        ),
        observation_span_minutes=_minutes(
            latest_record.timestamp - records[0].timestamp
        ),
        minutes_since_previous_sample=(
            None
            if len(records) == 1
            else _minutes(records[-1].timestamp - records[-2].timestamp)
        ),
        minutes_since_previous_sample_missing=len(records) == 1,
        latest_temperature_c=temperatures[-1],
        mean_temperature_c=sum(temperatures) / len(temperatures),
        minimum_temperature_c=min(temperatures),
        maximum_temperature_c=max(temperatures),
        temperature_range_c=max(temperatures) - min(temperatures),
        temperature_change_from_first_c=temperatures[-1] - temperatures[0],
        temperature_slope_c_per_hour=_temperature_slope_per_hour(records),
        latest_battery_level_percent=latest_record.battery_level,
        latest_battery_level_missing=latest_record.battery_level is None,
        current_excursion_episode_duration_minutes=(
            latest_decision.excursion_episode_duration_minutes
        ),
        current_cumulative_excursion_duration_minutes=(
            latest_decision.cumulative_excursion_duration_minutes
        ),
        current_excursion_utilization=latest_decision.excursion_utilization,
        current_excursion_utilization_missing=(
            latest_decision.excursion_utilization is None
        ),
        safe_count_through_cutoff=statuses.count(ApplicationStatus.SAFE),
        monitor_count_through_cutoff=statuses.count(ApplicationStatus.MONITOR),
        at_risk_count_through_cutoff=statuses.count(ApplicationStatus.AT_RISK),
        critical_count_through_cutoff=statuses.count(ApplicationStatus.CRITICAL),
        rule_violation_count_through_cutoff=statuses.count(
            ApplicationStatus.RULE_VIOLATION
        ),
        data_error_count_through_cutoff=statuses.count(
            ApplicationStatus.DATA_ERROR
        ),
    )
    _validate_features(features)
    return features


def _temperature_slope_per_hour(records: Tuple[TelemetryRecord, ...]) -> float:
    if len(records) < 2:
        return 0.0
    origin = records[0].timestamp
    x_values = tuple(_minutes(record.timestamp - origin) for record in records)
    y_values = tuple(float(record.temperature) for record in records)
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    denominator = sum((value - x_mean) ** 2 for value in x_values)
    if denominator == 0:
        return 0.0
    slope_per_minute = sum(
        (x_value - x_mean) * (y_value - y_mean)
        for x_value, y_value in zip(x_values, y_values)
    ) / denominator
    return slope_per_minute * 60.0


def _validate_features(value):
    if not isinstance(value, TemporalRiskFeatures):
        raise TemporalRiskExampleError("features must be TemporalRiskFeatures")
    for field in ("product_id", "presentation", "state", "product_rule_version"):
        _required_text(getattr(value, field), field)
    if value.current_status not in TEMPORAL_RISK_ELIGIBLE_CUTOFF_STATUSES:
        raise TemporalRiskExampleError("Current status is not eligible at cutoff")
    for field in ("current_active_rule_id", "latest_device_health"):
        item = getattr(value, field)
        if item is not None:
            _required_text(item, field)
    for field in (
        "sample_count",
        "safe_count_through_cutoff",
        "monitor_count_through_cutoff",
        "at_risk_count_through_cutoff",
        "critical_count_through_cutoff",
        "rule_violation_count_through_cutoff",
        "data_error_count_through_cutoff",
    ):
        _nonnegative_integer(getattr(value, field), field)
    if value.sample_count < 1:
        raise TemporalRiskExampleError("sample_count must be positive")
    status_total = sum(
        getattr(value, field)
        for field in TEMPORAL_RISK_NUMERIC_FEATURES
        if field.endswith("_count_through_cutoff")
    )
    if status_total != value.sample_count:
        raise TemporalRiskExampleError("Status counts must equal sample_count")
    for field in (
        "trip_elapsed_minutes",
        "observation_span_minutes",
        "current_excursion_episode_duration_minutes",
        "current_cumulative_excursion_duration_minutes",
    ):
        if _finite_number(getattr(value, field), field) < 0:
            raise TemporalRiskExampleError(f"{field} must be non-negative")
    for field in (
        "latest_temperature_c",
        "mean_temperature_c",
        "minimum_temperature_c",
        "maximum_temperature_c",
        "temperature_range_c",
        "temperature_change_from_first_c",
        "temperature_slope_c_per_hour",
    ):
        _finite_number(getattr(value, field), field)
    _validate_optional_number_with_missing_indicator(
        value.minutes_since_previous_sample,
        value.minutes_since_previous_sample_missing,
        "minutes_since_previous_sample",
    )
    _validate_optional_number_with_missing_indicator(
        value.latest_battery_level_percent,
        value.latest_battery_level_missing,
        "latest_battery_level_percent",
    )
    _validate_optional_number_with_missing_indicator(
        value.current_excursion_utilization,
        value.current_excursion_utilization_missing,
        "current_excursion_utilization",
    )


def _validate_label(value, cutoff_at, horizon_ends_at):
    if not isinstance(value, TemporalRiskLabel):
        raise TemporalRiskExampleError("label must be TemporalRiskLabel")
    if not isinstance(value.adverse_event_within_horizon, bool):
        raise TemporalRiskExampleError(
            "adverse_event_within_horizon must be boolean"
        )
    if value.adverse_event_within_horizon:
        if value.first_adverse_status not in TEMPORAL_RISK_ADVERSE_STATUSES:
            raise TemporalRiskExampleError("Positive label needs an adverse status")
        timestamp = _aware_datetime(value.first_adverse_at, "first_adverse_at")
        if timestamp <= cutoff_at or timestamp > horizon_ends_at:
            raise TemporalRiskExampleError(
                "first_adverse_at must be inside the future horizon"
            )
    elif value.first_adverse_status is not None or value.first_adverse_at is not None:
        raise TemporalRiskExampleError(
            "Negative label cannot contain an adverse event"
        )


def _validate_optional_number_with_missing_indicator(value, missing, field):
    if not isinstance(missing, bool):
        raise TemporalRiskExampleError(f"{field}_missing must be boolean")
    if missing != (value is None):
        raise TemporalRiskExampleError(
            f"{field} and its missing indicator disagree"
        )
    if value is not None:
        _finite_number(value, field)


def _minutes(delta) -> float:
    return delta.total_seconds() / 60.0


def _required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise TemporalRiskExampleError(f"{field} must be a non-empty string")
    return value.strip()


def _aware_datetime(value, field):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TemporalRiskExampleError(f"{field} must be timezone-aware")
    return value


def _finite_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TemporalRiskExampleError(f"{field} must be numeric")
    numeric = float(value)
    if not isfinite(numeric):
        raise TemporalRiskExampleError(f"{field} must be finite")
    return numeric


def _nonnegative_integer(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TemporalRiskExampleError(f"{field} must be a non-negative integer")
    return value
