"""Controlled real-device pilot capture, export, and honest evaluation."""

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from typing import Optional, Sequence, Tuple

try:
    from .completed_trip_dataset import CompletedTripDatasetRecord, validate_completed_trip_dataset_record
    from .risk_rules import ApplicationStatus
    from .temporal_risk_examples import TEMPORAL_RISK_ADVERSE_STATUSES
    from .telemetry import TelemetrySource
except ImportError:
    from completed_trip_dataset import CompletedTripDatasetRecord, validate_completed_trip_dataset_record
    from risk_rules import ApplicationStatus
    from temporal_risk_examples import TEMPORAL_RISK_ADVERSE_STATUSES
    from telemetry import TelemetrySource


PILOT_EXPORT_SCHEMA = "vitae.real_device_pilot_validation"
PILOT_EXPORT_SCHEMA_VERSION = 1
MIN_EVALUATION_TRIPS = 20


@dataclass(frozen=True)
class PilotPredictionObservation:
    lot_trip_id: str
    cutoff_at: datetime
    cutoff_sample_id: str
    current_status: ApplicationStatus
    predicted_probability: float
    predicted_category: str
    prediction_source: str
    horizon_minutes: float
    operational_action: str


@dataclass(frozen=True)
class PilotKnownEvent:
    occurred_at: datetime
    event_code: str
    note: str


@dataclass(frozen=True)
class PilotSessionRecord:
    completed_history: CompletedTripDatasetRecord
    planned_arrival_at: datetime
    predictions: Tuple[PilotPredictionObservation, ...]
    known_events: Tuple[PilotKnownEvent, ...] = ()


@dataclass(frozen=True)
class PilotValidationRow:
    anonymized_trip_id: str
    cutoff_at: datetime
    current_status: ApplicationStatus
    predicted_probability: float
    predicted_category: str
    prediction_source: str
    horizon_minutes: float
    observed_adverse: bool
    observed_adverse_at: Optional[datetime]
    telemetry_source: TelemetrySource


@dataclass(frozen=True)
class PilotEvaluation:
    independent_trip_count: int
    example_count: int
    positive_count: int
    negative_count: int
    statistically_interpretable: bool
    message: str
    threshold: Optional[float]
    true_negative: Optional[int]
    false_positive: Optional[int]
    false_negative: Optional[int]
    true_positive: Optional[int]
    precision: Optional[float]
    recall: Optional[float]
    brier_score: Optional[float]


def build_pilot_validation_rows(session, *, anonymization_salt):
    if not isinstance(session, PilotSessionRecord):
        raise TypeError("session must be PilotSessionRecord")
    history = validate_completed_trip_dataset_record(session.completed_history)
    _aware(session.planned_arrival_at, "planned_arrival_at")
    salt = _required_text(anonymization_salt, "anonymization_salt")
    records = history.telemetry_records
    decisions = history.decision_records
    _require_pilot_history(records)
    rows = []
    for observation in session.predictions:
        _validate_prediction(observation, history.lot_trip_id)
        evaluation_ends_at = min(
            observation.cutoff_at + timedelta(minutes=observation.horizon_minutes),
            history.outcome.completed_at,
        )
        future = tuple(
            decision for decision in decisions
            if observation.cutoff_at < decision.sample_timestamp <= evaluation_ends_at
        )
        first_adverse = next(
            (decision for decision in future if decision.status in TEMPORAL_RISK_ADVERSE_STATUSES),
            None,
        )
        cutoff_record = next(
            (record for record in records if record.sample_id == observation.cutoff_sample_id),
            None,
        )
        if cutoff_record is None or cutoff_record.timestamp != observation.cutoff_at:
            raise ValueError("Pilot prediction cutoff does not match accepted telemetry")
        rows.append(PilotValidationRow(
            anonymized_trip_id=_anonymized_id("trip", history.lot_trip_id, salt),
            cutoff_at=observation.cutoff_at,
            current_status=observation.current_status,
            predicted_probability=observation.predicted_probability,
            predicted_category=observation.predicted_category,
            prediction_source=observation.prediction_source,
            horizon_minutes=observation.horizon_minutes,
            observed_adverse=first_adverse is not None,
            observed_adverse_at=first_adverse.sample_timestamp if first_adverse else None,
            telemetry_source=cutoff_record.source,
        ))
    return tuple(rows)


def pilot_session_document(session, *, anonymization_salt):
    if not isinstance(session, PilotSessionRecord):
        raise TypeError("session must be PilotSessionRecord")
    history = validate_completed_trip_dataset_record(session.completed_history)
    _aware(session.planned_arrival_at, "planned_arrival_at")
    salt = _required_text(anonymization_salt, "anonymization_salt")
    _require_pilot_history(history.telemetry_records)
    for prediction in session.predictions:
        _validate_prediction(prediction, history.lot_trip_id)
    for event in session.known_events:
        _validate_known_event(event)
    return {
        "schema": PILOT_EXPORT_SCHEMA,
        "schemaVersion": PILOT_EXPORT_SCHEMA_VERSION,
        "validationStatus": "ENGINEERING_PILOT_NOT_CLINICAL_VALIDATION",
        "anonymizedTripId": _anonymized_id("trip", history.lot_trip_id, salt),
        "anonymizedDeviceId": _anonymized_id("device", history.outcome.device_id, salt),
        "plannedArrivalAt": session.planned_arrival_at.isoformat(),
        "actualCompletedAt": history.outcome.completed_at.isoformat(),
        "telemetry": [
            {
                "sampleId": record.sample_id,
                "timestamp": record.timestamp.isoformat(),
                "temperature": record.temperature,
                "batteryLevel": record.battery_level,
                "latitude": record.latitude,
                "longitude": record.longitude,
                "deviceHealth": record.device_health,
                "source": record.source.value,
            }
            for record in history.telemetry_records
        ],
        "decisions": [
            {"sampleId": value.sample_id, "timestamp": value.sample_timestamp.isoformat(), "status": value.status.value, "reasonCode": value.reason_code}
            for value in history.decision_records
        ],
        "predictions": [
            {
                "cutoffSampleId": value.cutoff_sample_id,
                "cutoffAt": value.cutoff_at.isoformat(),
                "currentStatus": value.current_status.value,
                "probability": value.predicted_probability,
                "category": value.predicted_category,
                "source": value.prediction_source,
                "horizonMinutes": value.horizon_minutes,
                "operationalAction": value.operational_action,
            }
            for value in session.predictions
        ],
        "knownEvents": [
            {"occurredAt": value.occurred_at.isoformat(), "eventCode": value.event_code, "note": value.note}
            for value in session.known_events
        ],
    }


def pilot_session_json(session, *, anonymization_salt):
    return json.dumps(
        pilot_session_document(session, anonymization_salt=anonymization_salt),
        sort_keys=True,
        separators=(",", ":"),
    )


def pilot_validation_csv(rows):
    output = io.StringIO(newline="")
    names = (
        "anonymized_trip_id", "cutoff_at", "current_status", "predicted_probability",
        "predicted_category", "prediction_source", "horizon_minutes",
        "observed_adverse", "observed_adverse_at", "telemetry_source",
    )
    writer = csv.DictWriter(output, fieldnames=names, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            "anonymized_trip_id": row.anonymized_trip_id,
            "cutoff_at": row.cutoff_at.isoformat(),
            "current_status": row.current_status.value,
            "predicted_probability": row.predicted_probability,
            "predicted_category": row.predicted_category,
            "prediction_source": row.prediction_source,
            "horizon_minutes": row.horizon_minutes,
            "observed_adverse": int(row.observed_adverse),
            "observed_adverse_at": row.observed_adverse_at.isoformat() if row.observed_adverse_at else "",
            "telemetry_source": row.telemetry_source.value,
        })
    return output.getvalue()


def evaluate_pilot_rows(rows, *, threshold=0.50):
    values = tuple(rows)
    trips = {row.anonymized_trip_id for row in values}
    positives = sum(row.observed_adverse for row in values)
    negatives = len(values) - positives
    ready = len(trips) >= MIN_EVALUATION_TRIPS and positives > 0 and negatives > 0
    if not ready:
        return PilotEvaluation(
            len(trips), len(values), positives, negatives, False,
            f"Too few independent completed trips/classes for meaningful performance conclusions; require at least {MIN_EVALUATION_TRIPS} trips and both outcomes.",
            None, None, None, None, None, None, None, None,
        )
    predicted = tuple(row.predicted_probability >= threshold for row in values)
    tn = sum(not row.observed_adverse and not guess for row, guess in zip(values, predicted))
    fp = sum(not row.observed_adverse and guess for row, guess in zip(values, predicted))
    fn = sum(row.observed_adverse and not guess for row, guess in zip(values, predicted))
    tp = sum(row.observed_adverse and guess for row, guess in zip(values, predicted))
    return PilotEvaluation(
        len(trips), len(values), positives, negatives, True,
        "Engineering pilot counts are sufficient for descriptive evaluation only.",
        threshold, tn, fp, fn, tp,
        0.0 if tp + fp == 0 else tp / (tp + fp),
        0.0 if tp + fn == 0 else tp / (tp + fn),
        sum((row.predicted_probability - int(row.observed_adverse)) ** 2 for row in values) / len(values),
    )


def compare_simulator_and_pilot(simulator_records, pilot_records):
    return {
        "status": "DESCRIPTIVE_DOMAIN_COMPARISON_ONLY",
        "simulator": _distribution_summary(simulator_records),
        "pilot": _distribution_summary(pilot_records),
        "warning": "Small pilot samples do not establish formal drift or real-world performance.",
    }


def _distribution_summary(records):
    histories = tuple(records)
    samples = tuple(record for history in histories for record in history.telemetry_records)
    temperatures = tuple(record.temperature for record in samples)
    intervals = tuple(
        (right.timestamp - left.timestamp).total_seconds() / 60
        for history in histories
        for left, right in zip(history.telemetry_records, history.telemetry_records[1:])
    )
    return {
        "tripCount": len(histories),
        "sampleCount": len(samples),
        "temperatureMin": min(temperatures) if temperatures else None,
        "temperatureMax": max(temperatures) if temperatures else None,
        "meanSamplingIntervalMinutes": sum(intervals) / len(intervals) if intervals else None,
        "batteryMissingCount": sum(record.battery_level is None for record in samples),
        "gpsMissingCount": sum(record.latitude is None or record.longitude is None for record in samples),
    }


def _validate_prediction(value, lot_trip_id):
    if not isinstance(value, PilotPredictionObservation) or value.lot_trip_id != lot_trip_id:
        raise ValueError("Pilot prediction identity is invalid")
    _aware(value.cutoff_at, "cutoff_at")
    _probability(value.predicted_probability)
    if not isfinite(value.horizon_minutes) or value.horizon_minutes <= 0:
        raise ValueError("Pilot prediction horizon must be positive")
    _required_text(value.cutoff_sample_id, "cutoff_sample_id")
    if value.predicted_category not in {"LOW", "MEDIUM", "HIGH"}:
        raise ValueError("Pilot predicted category is invalid")
    _required_text(value.prediction_source, "prediction_source")
    _required_text(value.operational_action, "operational_action")


def _validate_known_event(value):
    if not isinstance(value, PilotKnownEvent):
        raise ValueError("Pilot known event is invalid")
    _aware(value.occurred_at, "known_event.occurred_at")
    _required_text(value.event_code, "known_event.event_code")
    _required_text(value.note, "known_event.note")


def _require_pilot_history(records):
    if any(
        record.source not in (TelemetrySource.REAL_DEVICE, TelemetrySource.REPLAY)
        for record in records
    ):
        raise ValueError(
            "Pilot histories must contain only REAL_DEVICE or REPLAY telemetry"
        )


def _anonymized_id(kind, value, salt):
    return kind + "-" + sha256(f"{salt}|{kind}|{value}".encode("utf-8")).hexdigest()[:20]


def _probability(value):
    if not isinstance(value, (int, float)) or not isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Pilot probability must be between zero and one")


def _aware(value, field):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _required_text(value, field):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized
