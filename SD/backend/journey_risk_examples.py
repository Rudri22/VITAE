"""Leakage-safe variable-horizon examples for deterioration before arrival."""

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from math import isfinite
from typing import Iterable, Mapping, Optional, Tuple

try:
    from .completed_trip_dataset import CompletedTripDatasetRecord, validate_completed_trip_dataset_record
    from .risk_rules import ApplicationStatus
    from .temporal_risk_examples import (
        TEMPORAL_RISK_ADVERSE_STATUSES,
        TEMPORAL_RISK_ELIGIBLE_CUTOFF_STATUSES,
        TemporalRiskFeatureContext,
        TemporalRiskFeatures,
        build_temporal_risk_features_from_prefix,
    )
except ImportError:
    from completed_trip_dataset import CompletedTripDatasetRecord, validate_completed_trip_dataset_record
    from risk_rules import ApplicationStatus
    from temporal_risk_examples import (
        TEMPORAL_RISK_ADVERSE_STATUSES,
        TEMPORAL_RISK_ELIGIBLE_CUTOFF_STATUSES,
        TemporalRiskFeatureContext,
        TemporalRiskFeatures,
        build_temporal_risk_features_from_prefix,
    )

JOURNEY_RISK_FEATURE_VERSION = "journey-risk-features-v1"
JOURNEY_RISK_LABEL_VERSION = "deterioration-before-destination-v1"
JOURNEY_RISK_EXAMPLE_VERSION = "journey-risk-example-v1"
JOURNEY_RISK_TARGET_NAME = "deteriorates_before_destination"


@dataclass(frozen=True)
class JourneyRiskFeatures:
    temporal_features: TemporalRiskFeatures
    remaining_journey_minutes: float


@dataclass(frozen=True)
class JourneyRiskLabel:
    deteriorates_before_destination: bool
    first_adverse_status: Optional[ApplicationStatus]
    first_adverse_at: Optional[datetime]


@dataclass(frozen=True)
class JourneyRiskExample:
    example_id: str
    lot_trip_id: str
    trip_id: str
    cutoff_sample_id: str
    cutoff_at: datetime
    planned_arrival_at: datetime
    prediction_horizon_minutes: float
    example_version: str
    feature_version: str
    label_version: str
    features: JourneyRiskFeatures
    label: JourneyRiskLabel


class JourneyRiskExampleError(ValueError):
    pass


def build_journey_risk_examples(record, *, planned_arrival_at):
    """Build examples using a horizon known independently of deterioration."""
    source = validate_completed_trip_dataset_record(record)
    arrival = _aware_datetime(planned_arrival_at, "planned_arrival_at")
    if arrival > source.outcome.completed_at:
        raise JourneyRiskExampleError("Planned arrival is not covered by completed history")
    paired = tuple(zip(source.telemetry_records, source.decision_records))
    examples = []
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
    for index, (cutoff_record, cutoff_decision) in enumerate(paired):
        if cutoff_decision.status not in TEMPORAL_RISK_ELIGIBLE_CUTOFF_STATUSES:
            continue
        if cutoff_record.timestamp >= arrival:
            continue
        future = tuple(
            decision
            for _, decision in paired[index + 1 :]
            if cutoff_record.timestamp < decision.sample_timestamp <= arrival
        )
        if any(decision.status == ApplicationStatus.DATA_ERROR for decision in future):
            continue
        first_adverse = next(
            (decision for decision in future if decision.status in TEMPORAL_RISK_ADVERSE_STATUSES),
            None,
        )
        prefix = paired[: index + 1]
        remaining = (arrival - cutoff_record.timestamp).total_seconds() / 60
        value = JourneyRiskExample(
            example_id=journey_risk_example_id(source.lot_trip_id, cutoff_record.sample_id),
            lot_trip_id=source.lot_trip_id,
            trip_id=source.outcome.trip_id,
            cutoff_sample_id=cutoff_record.sample_id,
            cutoff_at=cutoff_record.timestamp,
            planned_arrival_at=arrival,
            prediction_horizon_minutes=remaining,
            example_version=JOURNEY_RISK_EXAMPLE_VERSION,
            feature_version=JOURNEY_RISK_FEATURE_VERSION,
            label_version=JOURNEY_RISK_LABEL_VERSION,
            features=JourneyRiskFeatures(
                temporal_features=build_temporal_risk_features_from_prefix(
                    context,
                    tuple(item for item, _ in prefix),
                    tuple(item for _, item in prefix),
                ),
                remaining_journey_minutes=remaining,
            ),
            label=JourneyRiskLabel(
                deteriorates_before_destination=first_adverse is not None,
                first_adverse_status=(first_adverse.status if first_adverse else None),
                first_adverse_at=(first_adverse.sample_timestamp if first_adverse else None),
            ),
        )
        examples.append(validate_journey_risk_example(value))
    return tuple(examples)


def build_journey_risk_dataset(records, planned_arrivals: Mapping[str, datetime]):
    values = []
    seen = set()
    for record in sorted(tuple(records), key=lambda item: item.lot_trip_id):
        if record.lot_trip_id in seen:
            raise JourneyRiskExampleError("Completed trips must be unique")
        seen.add(record.lot_trip_id)
        if record.lot_trip_id not in planned_arrivals:
            raise JourneyRiskExampleError("Every trip needs an explicit planned arrival")
        values.extend(
            build_journey_risk_examples(
                record, planned_arrival_at=planned_arrivals[record.lot_trip_id]
            )
        )
    return tuple(values)


def journey_risk_example_id(lot_trip_id, cutoff_sample_id):
    raw = "|".join((str(lot_trip_id), str(cutoff_sample_id), JOURNEY_RISK_EXAMPLE_VERSION, JOURNEY_RISK_FEATURE_VERSION, JOURNEY_RISK_LABEL_VERSION))
    return "journey-risk-" + sha256(raw.encode("utf-8")).hexdigest()[:24]


def validate_journey_risk_example(value):
    if not isinstance(value, JourneyRiskExample):
        raise JourneyRiskExampleError("value must be JourneyRiskExample")
    cutoff = _aware_datetime(value.cutoff_at, "cutoff_at")
    arrival = _aware_datetime(value.planned_arrival_at, "planned_arrival_at")
    if arrival <= cutoff:
        raise JourneyRiskExampleError("planned_arrival_at must follow cutoff_at")
    expected_horizon = (arrival - cutoff).total_seconds() / 60
    if not isfinite(value.prediction_horizon_minutes) or value.prediction_horizon_minutes <= 0 or abs(value.prediction_horizon_minutes - expected_horizon) > 1e-9:
        raise JourneyRiskExampleError("prediction horizon must match planned arrival")
    if value.features.remaining_journey_minutes != value.prediction_horizon_minutes:
        raise JourneyRiskExampleError("feature horizon must match example horizon")
    if value.example_version != JOURNEY_RISK_EXAMPLE_VERSION or value.feature_version != JOURNEY_RISK_FEATURE_VERSION or value.label_version != JOURNEY_RISK_LABEL_VERSION:
        raise JourneyRiskExampleError("Journey-risk schema version is incompatible")
    if value.example_id != journey_risk_example_id(value.lot_trip_id, value.cutoff_sample_id):
        raise JourneyRiskExampleError("example_id is not canonical")
    label = value.label
    if label.deteriorates_before_destination:
        if label.first_adverse_status not in TEMPORAL_RISK_ADVERSE_STATUSES:
            raise JourneyRiskExampleError("Positive label requires an adverse status")
        adverse_at = _aware_datetime(label.first_adverse_at, "first_adverse_at")
        if not cutoff < adverse_at <= arrival:
            raise JourneyRiskExampleError("Adverse provenance is outside the journey horizon")
    elif label.first_adverse_status is not None or label.first_adverse_at is not None:
        raise JourneyRiskExampleError("Negative label cannot have adverse provenance")
    return value


def journey_risk_example_document(value):
    source = validate_journey_risk_example(value)
    base = source.features.temporal_features
    return {
        "schema": "vitae.journey_risk_example",
        "schemaVersion": 1,
        "exampleId": source.example_id,
        "lotTripId": source.lot_trip_id,
        "tripId": source.trip_id,
        "cutoffSampleId": source.cutoff_sample_id,
        "cutoffAt": source.cutoff_at.isoformat(),
        "plannedArrivalAt": source.planned_arrival_at.isoformat(),
        "predictionHorizonMinutes": source.prediction_horizon_minutes,
        "exampleVersion": source.example_version,
        "featureVersion": source.feature_version,
        "labelVersion": source.label_version,
        "features": {
            **{name: _feature_value(value) for name, value in vars(base).items()},
            "remaining_journey_minutes": source.features.remaining_journey_minutes,
        },
        "label": {
            "deterioratesBeforeDestination": source.label.deteriorates_before_destination,
            "firstAdverseStatus": source.label.first_adverse_status.value if source.label.first_adverse_status else None,
            "firstAdverseAt": source.label.first_adverse_at.isoformat() if source.label.first_adverse_at else None,
        },
    }


def journey_risk_examples_jsonl(examples: Iterable[JourneyRiskExample]):
    ordered = sorted(tuple(examples), key=lambda item: (item.lot_trip_id, item.cutoff_at, item.example_id))
    return "".join(json.dumps(journey_risk_example_document(item), sort_keys=True, separators=(",", ":")) + "\n" for item in ordered)


def _feature_value(value):
    return value.value if isinstance(value, ApplicationStatus) else value


def _aware_datetime(value, field):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise JourneyRiskExampleError(f"{field} must be timezone-aware")
    return value
