import json
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol, Tuple, runtime_checkable

try:
    from .completed_trip_outcome import (
        CompletedTripOutcome,
        validate_completed_trip_outcome,
    )
    from .decision_outbox import StatusDecisionRecord
    from .state_repository import TelemetryRecord
except ImportError:
    from completed_trip_outcome import (
        CompletedTripOutcome,
        validate_completed_trip_outcome,
    )
    from decision_outbox import StatusDecisionRecord
    from state_repository import TelemetryRecord


@dataclass(frozen=True)
class CompletedTripDatasetRecord:
    lot_trip_id: str
    outcome: CompletedTripOutcome
    telemetry_records: Tuple[TelemetryRecord, ...]
    decision_records: Tuple[StatusDecisionRecord, ...]


class CompletedTripDatasetError(ValueError):
    pass


class CompletedTripDatasetNotFoundError(CompletedTripDatasetError):
    pass


class CompletedTripDatasetIntegrityError(CompletedTripDatasetError):
    pass


@runtime_checkable
class CompletedTripOutcomeReader(Protocol):
    def get_completed_trip_outcome(
        self, lot_trip_id: str
    ) -> Optional[CompletedTripOutcome]:
        ...


@runtime_checkable
class CompletedTripHistoryReader(Protocol):
    def get_telemetry_history(
        self, lot_trip_id: str
    ) -> Tuple[TelemetryRecord, ...]:
        ...

    def get_decision_history(
        self, lot_trip_id: str
    ) -> Tuple[StatusDecisionRecord, ...]:
        ...


class CompletedTripDatasetService:
    """Assemble persisted facts without deriving features, labels, or status."""

    def __init__(
        self,
        outcome_repository: CompletedTripOutcomeReader,
        history_repository: CompletedTripHistoryReader,
    ):
        if not isinstance(outcome_repository, CompletedTripOutcomeReader):
            raise TypeError("outcome_repository must support CompletedTripOutcomeReader")
        if not isinstance(history_repository, CompletedTripHistoryReader):
            raise TypeError("history_repository must support CompletedTripHistoryReader")
        self._outcome_repository = outcome_repository
        self._history_repository = history_repository

    def get_record(self, lot_trip_id: str) -> CompletedTripDatasetRecord:
        lot_trip = _required_text(lot_trip_id, "lot_trip_id")
        outcome = self._outcome_repository.get_completed_trip_outcome(lot_trip)
        if outcome is None:
            raise CompletedTripDatasetNotFoundError(
                "No finalized outcome exists for this lot_trip_id"
            )
        telemetry = tuple(
            sorted(
                self._history_repository.get_telemetry_history(lot_trip),
                key=lambda record: (
                    record.timestamp,
                    record.device_id,
                    record.sample_id,
                ),
            )
        )
        decisions_by_sample = {}
        for decision in self._history_repository.get_decision_history(lot_trip):
            key = (decision.device_id, decision.sample_id)
            if key in decisions_by_sample:
                raise CompletedTripDatasetIntegrityError(
                    "Decision history contains a duplicate sample identity"
                )
            decisions_by_sample[key] = decision
        decisions = []
        for record in telemetry:
            decision = decisions_by_sample.pop((record.device_id, record.sample_id), None)
            if decision is None:
                raise CompletedTripDatasetIntegrityError(
                    "Accepted telemetry is missing its deterministic decision"
                )
            decisions.append(decision)
        if decisions_by_sample:
            raise CompletedTripDatasetIntegrityError(
                "Decision history contains a sample absent from telemetry history"
            )
        value = CompletedTripDatasetRecord(
            lot_trip_id=lot_trip,
            outcome=outcome,
            telemetry_records=telemetry,
            decision_records=tuple(decisions),
        )
        return validate_completed_trip_dataset_record(value)

    def get_records(
        self, lot_trip_ids: Iterable[str]
    ) -> Tuple[CompletedTripDatasetRecord, ...]:
        normalized = tuple(_required_text(value, "lot_trip_id") for value in lot_trip_ids)
        if len(set(normalized)) != len(normalized):
            raise CompletedTripDatasetIntegrityError(
                "lot_trip_ids must not contain duplicates"
            )
        return tuple(self.get_record(value) for value in sorted(normalized))


def validate_completed_trip_dataset_record(
    value: CompletedTripDatasetRecord,
) -> CompletedTripDatasetRecord:
    if not isinstance(value, CompletedTripDatasetRecord):
        raise CompletedTripDatasetIntegrityError(
            "value must be a CompletedTripDatasetRecord"
        )
    lot_trip_id = _required_text(value.lot_trip_id, "lot_trip_id")
    if not isinstance(value.outcome, CompletedTripOutcome):
        raise CompletedTripDatasetIntegrityError(
            "outcome must be a CompletedTripOutcome"
        )
    outcome = validate_completed_trip_outcome(value.outcome)
    if outcome.lot_trip_id != lot_trip_id:
        raise CompletedTripDatasetIntegrityError(
            "Outcome and dataset lot_trip_id must match"
        )
    telemetry = value.telemetry_records
    decisions = value.decision_records
    if not isinstance(telemetry, tuple) or not isinstance(decisions, tuple):
        raise CompletedTripDatasetIntegrityError(
            "Dataset histories must be immutable tuples"
        )
    if len(telemetry) != len(decisions):
        raise CompletedTripDatasetIntegrityError(
            "Telemetry and decision histories must have equal lengths"
        )
    if not telemetry:
        if outcome.final_live_state_revision is not None:
            raise CompletedTripDatasetIntegrityError(
                "Outcome claims a final state but history contains no telemetry"
            )
        return value

    previous_timestamp = None
    for index, (record, decision) in enumerate(zip(telemetry, decisions), start=1):
        if not isinstance(record, TelemetryRecord):
            raise CompletedTripDatasetIntegrityError(
                "telemetry_records must contain TelemetryRecord values"
            )
        if not isinstance(decision, StatusDecisionRecord):
            raise CompletedTripDatasetIntegrityError(
                "decision_records must contain StatusDecisionRecord values"
            )
        if (
            record.lot_trip_id != lot_trip_id
            or record.trip_id != outcome.trip_id
            or record.device_id != outcome.device_id
        ):
            raise CompletedTripDatasetIntegrityError(
                "Telemetry identity does not match the completed outcome"
            )
        if record.timestamp < outcome.trip_started_at or record.timestamp > outcome.completed_at:
            raise CompletedTripDatasetIntegrityError(
                "Telemetry timestamp falls outside the completed trip"
            )
        if previous_timestamp is not None and record.timestamp <= previous_timestamp:
            raise CompletedTripDatasetIntegrityError(
                "Telemetry history must have strictly increasing timestamps"
            )
        previous_timestamp = record.timestamp
        if (
            decision.trip_id != record.trip_id
            or decision.lot_trip_id != record.lot_trip_id
            or decision.device_id != record.device_id
            or decision.sample_id != record.sample_id
            or decision.sample_timestamp != record.timestamp
            or decision.product_id != outcome.product_id
            or decision.product_rule_version != outcome.product_rule_version
        ):
            raise CompletedTripDatasetIntegrityError(
                "Decision identity or provenance does not match telemetry"
            )
        expected_previous_revision = None if index == 1 else index - 1
        if (
            decision.previous_live_state_revision != expected_previous_revision
            or decision.resulting_live_state_revision != index
        ):
            raise CompletedTripDatasetIntegrityError(
                "Decision history revisions are not contiguous"
            )

    final_record = telemetry[-1]
    final_decision = decisions[-1]
    expected_final = (
        final_decision.status,
        final_decision.reason_code,
        final_decision.active_rule_id,
        final_record.sample_id,
        final_record.timestamp,
        final_record.temperature,
        final_decision.resulting_live_state_revision,
        final_decision.excursion_episode_duration_minutes,
        final_decision.cumulative_excursion_duration_minutes,
        final_decision.excursion_utilization,
    )
    actual_final = (
        outcome.final_status,
        outcome.final_reason_code,
        outcome.final_active_rule_id,
        outcome.final_sample_id,
        outcome.final_sample_timestamp,
        outcome.final_temperature,
        outcome.final_live_state_revision,
        outcome.final_excursion_episode_duration_minutes,
        outcome.final_cumulative_excursion_duration_minutes,
        outcome.final_excursion_utilization,
    )
    if actual_final != expected_final:
        raise CompletedTripDatasetIntegrityError(
            "Outcome does not match the final persisted telemetry decision"
        )
    return value


def completed_trip_dataset_jsonl(
    records: Iterable[CompletedTripDatasetRecord],
) -> str:
    try:
        from .repository_serialization import serialize_completed_trip_dataset_record
    except ImportError:
        from repository_serialization import serialize_completed_trip_dataset_record

    values = tuple(records)
    lot_trip_ids = tuple(record.lot_trip_id for record in values)
    if len(set(lot_trip_ids)) != len(lot_trip_ids):
        raise CompletedTripDatasetIntegrityError(
            "Dataset export contains duplicate lot_trip_id values"
        )
    return "".join(
        json.dumps(
            serialize_completed_trip_dataset_record(
                validate_completed_trip_dataset_record(record)
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for record in sorted(values, key=lambda item: item.lot_trip_id)
    )


def _required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise CompletedTripDatasetIntegrityError(
            f"{field} must be a non-empty string"
        )
    return value.strip()
