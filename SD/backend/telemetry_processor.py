from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

try:
    from .risk_rules import (
        StatusDecision,
        TelemetrySample,
        evaluate_status,
    )
    from .state_repository import (
        DuplicateTelemetrySampleError,
        IdentityRepository,
        LiveState,
        OutOfOrderTelemetryError,
        TelemetryRecord,
        TelemetryStateRepository,
        live_state_from_decision,
        live_state_to_previous_state,
        telemetry_record_from_sample,
    )
    from .telemetry import validate_and_normalize_telemetry
    from .trip_identity import TripIdentity, resolve_trip_for_device, validate_trip_rule_context
except ImportError:
    from risk_rules import (
        StatusDecision,
        TelemetrySample,
        evaluate_status,
    )
    from state_repository import (
        DuplicateTelemetrySampleError,
        IdentityRepository,
        LiveState,
        OutOfOrderTelemetryError,
        TelemetryRecord,
        TelemetryStateRepository,
        live_state_from_decision,
        live_state_to_previous_state,
        telemetry_record_from_sample,
    )
    from telemetry import validate_and_normalize_telemetry
    from trip_identity import TripIdentity, resolve_trip_for_device, validate_trip_rule_context


@dataclass(frozen=True)
class ProcessingResult:
    previous_live_state: Optional[LiveState]
    telemetry_record: TelemetryRecord
    decision: StatusDecision
    live_state: LiveState


class TelemetryProcessor:
    def __init__(
        self,
        identity_repository: IdentityRepository,
        state_repository: TelemetryStateRepository,
    ):
        self._identity_repository = identity_repository
        self._state_repository = state_repository

    def process(self, raw_sample: Mapping[str, Any]) -> ProcessingResult:
        """Compatibility path for status-only callers without alert orchestration."""
        result = self.prepare(raw_sample)
        if hasattr(self._state_repository, "commit_processing_bundle"):
            try:
                from .decision_outbox import decision_record_from_processing_result
            except ImportError:
                from decision_outbox import decision_record_from_processing_result
            self.commit_processing_bundle(
                result,
                decision_record_from_processing_result(result),
                None,
            )
        else:
            previous = result.previous_live_state
            self._state_repository.commit_sample_and_state(
                result.telemetry_record,
                result.live_state,
                None if previous is None else previous.revision,
            )
        return result

    def prepare(self, raw_sample: Mapping[str, Any]) -> ProcessingResult:
        """Validate and evaluate one sample without changing repository state."""
        sample = validate_and_normalize_telemetry(raw_sample)

        if self._state_repository.has_sample(sample.device_id, sample.sample_id):
            raise DuplicateTelemetrySampleError(
                "Telemetry identity has already been committed"
            )

        assignments = self._identity_repository.get_device_assignments(
            sample.device_id
        )
        trips = self._trips_referenced_by(assignments)
        trip = resolve_trip_for_device(sample.device_id, trips, assignments)
        rules = validate_trip_rule_context(trip)

        previous_live_state = self._state_repository.get_live_state(
            trip.lot_trip_id
        )
        if (
            previous_live_state is not None
            and sample.timestamp <= previous_live_state.last_sample_timestamp
        ):
            raise OutOfOrderTelemetryError(
                "Telemetry timestamp must be newer than the current LiveState"
            )

        decision = evaluate_status(
            TelemetrySample(
                product_id=trip.product_id,
                temperature=sample.temperature,
                timestamp=sample.timestamp,
            ),
            rules,
            live_state_to_previous_state(previous_live_state),
        )
        record = telemetry_record_from_sample(
            trip.trip_id,
            trip.lot_trip_id,
            sample,
        )
        next_live_state = live_state_from_decision(
            lot_trip_id=trip.lot_trip_id,
            trip_id=trip.trip_id,
            product_id=trip.product_id,
            product_rule_version=trip.product_rule_version,
            sample=sample,
            decision=decision,
            previous_live_state=previous_live_state,
        )
        return ProcessingResult(
            previous_live_state=previous_live_state,
            telemetry_record=record,
            decision=decision,
            live_state=next_live_state,
        )

    def commit_processing_bundle(
        self,
        result: ProcessingResult,
        decision_record,
        alert_outbox_event,
    ) -> None:
        """Commit one prepared transition through the richer repository contract."""
        commit = getattr(self._state_repository, "commit_processing_bundle", None)
        if commit is None:
            raise TypeError(
                "state_repository must implement ProcessingBundleRepository"
            )
        previous = result.previous_live_state
        commit(
            result.telemetry_record,
            result.live_state,
            decision_record,
            alert_outbox_event,
            None if previous is None else previous.revision,
        )

    @property
    def processing_repository(self):
        return self._state_repository

    def _trips_referenced_by(self, assignments) -> Tuple[TripIdentity, ...]:
        trips = {}
        for assignment in assignments:
            trip = self._identity_repository.get_trip_by_id(assignment.trip_id)
            if trip is not None:
                trips[trip.trip_id] = trip
        return tuple(trips.values())
