from dataclasses import dataclass
from typing import Any, Mapping, Tuple

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
        """Validate, evaluate, and atomically persist one telemetry sample."""
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
        expected_revision = (
            None if previous_live_state is None else previous_live_state.revision
        )
        self._state_repository.commit_sample_and_state(
            record,
            next_live_state,
            expected_revision,
        )
        return ProcessingResult(
            telemetry_record=record,
            decision=decision,
            live_state=next_live_state,
        )

    def _trips_referenced_by(self, assignments) -> Tuple[TripIdentity, ...]:
        trips = {}
        for assignment in assignments:
            trip = self._identity_repository.get_trip_by_id(assignment.trip_id)
            if trip is not None:
                trips[trip.trip_id] = trip
        return tuple(trips.values())
