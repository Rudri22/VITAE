from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from threading import RLock
from typing import Dict, Optional, Protocol, runtime_checkable

try:
    from .risk_rules import ApplicationStatus
    from .state_repository import LiveState
    from .trip_identity import TripIdentity, TripStatus, validate_trip_identity
except ImportError:
    from risk_rules import ApplicationStatus
    from state_repository import LiveState
    from trip_identity import TripIdentity, TripStatus, validate_trip_identity


@dataclass(frozen=True)
class CompletedTripOutcome:
    lot_trip_id: str
    trip_id: str
    lot_id: str
    device_id: str
    product_id: str
    presentation: str
    state: str
    product_rule_version: str
    trip_started_at: datetime
    completed_at: datetime
    final_status: Optional[ApplicationStatus]
    final_reason_code: Optional[str]
    final_active_rule_id: Optional[str]
    final_sample_id: Optional[str]
    final_sample_timestamp: Optional[datetime]
    final_temperature: Optional[float]
    final_live_state_revision: Optional[int]
    final_excursion_episode_duration_minutes: Optional[float]
    final_cumulative_excursion_duration_minutes: Optional[float]
    final_excursion_utilization: Optional[float]


class CompletedTripOutcomeError(ValueError):
    pass


class CompletedTripOutcomeValidationError(CompletedTripOutcomeError):
    pass


class CompletedTripOutcomeConflictError(CompletedTripOutcomeError):
    pass


@runtime_checkable
class CompletedTripOutcomeRepository(Protocol):
    def save_outcome(self, outcome: CompletedTripOutcome) -> CompletedTripOutcome:
        ...

    def get_outcome(
        self, lot_trip_id: str
    ) -> Optional[CompletedTripOutcome]:
        ...


class InMemoryCompletedTripOutcomeRepository:
    def __init__(self) -> None:
        self._outcomes: Dict[str, CompletedTripOutcome] = {}
        self._lock = RLock()

    def save_outcome(self, outcome: CompletedTripOutcome) -> CompletedTripOutcome:
        validate_completed_trip_outcome(outcome)
        with self._lock:
            existing = self._outcomes.get(outcome.lot_trip_id)
            if existing is None:
                self._outcomes[outcome.lot_trip_id] = outcome
                return outcome
            if existing == outcome:
                return existing
            raise CompletedTripOutcomeConflictError(
                "A different outcome is already finalized for this lot_trip_id"
            )

    def get_outcome(
        self, lot_trip_id: str
    ) -> Optional[CompletedTripOutcome]:
        normalized_lot_trip_id = _required_text(lot_trip_id, "lot_trip_id")
        with self._lock:
            return self._outcomes.get(normalized_lot_trip_id)


def completed_trip_outcome_from_state(
    trip: TripIdentity,
    completed_at: datetime,
    live_state: Optional[LiveState],
) -> CompletedTripOutcome:
    """Finalize an outcome from an explicit completed-trip lifecycle event."""
    validate_trip_identity(trip)
    if trip.status != TripStatus.COMPLETED:
        raise CompletedTripOutcomeValidationError(
            "Only a COMPLETED trip can be finalized"
        )
    if not _is_timezone_aware(completed_at):
        raise CompletedTripOutcomeValidationError(
            "completed_at must be timezone-aware"
        )
    if completed_at < trip.start_time:
        raise CompletedTripOutcomeValidationError(
            "completed_at cannot precede trip_started_at"
        )

    if live_state is None:
        outcome = CompletedTripOutcome(
            lot_trip_id=trip.lot_trip_id,
            trip_id=trip.trip_id,
            lot_id=trip.lot_id,
            device_id=trip.device_id,
            product_id=trip.product_id,
            presentation=trip.presentation,
            state=trip.state,
            product_rule_version=trip.product_rule_version,
            trip_started_at=trip.start_time,
            completed_at=completed_at,
            final_status=None,
            final_reason_code=None,
            final_active_rule_id=None,
            final_sample_id=None,
            final_sample_timestamp=None,
            final_temperature=None,
            final_live_state_revision=None,
            final_excursion_episode_duration_minutes=None,
            final_cumulative_excursion_duration_minutes=None,
            final_excursion_utilization=None,
        )
    else:
        _validate_state_identity(trip, live_state)
        if completed_at < live_state.last_sample_timestamp:
            raise CompletedTripOutcomeValidationError(
                "completed_at cannot precede the final accepted sample"
            )
        outcome = CompletedTripOutcome(
            lot_trip_id=trip.lot_trip_id,
            trip_id=trip.trip_id,
            lot_id=trip.lot_id,
            device_id=trip.device_id,
            product_id=trip.product_id,
            presentation=trip.presentation,
            state=trip.state,
            product_rule_version=trip.product_rule_version,
            trip_started_at=trip.start_time,
            completed_at=completed_at,
            final_status=live_state.status,
            final_reason_code=live_state.reason_code,
            final_active_rule_id=live_state.active_rule_id,
            final_sample_id=live_state.last_sample_id,
            final_sample_timestamp=live_state.last_sample_timestamp,
            final_temperature=live_state.latest_temperature,
            final_live_state_revision=live_state.revision,
            final_excursion_episode_duration_minutes=(
                live_state.excursion_episode_duration_minutes
            ),
            final_cumulative_excursion_duration_minutes=(
                live_state.cumulative_excursion_duration_minutes
            ),
            final_excursion_utilization=live_state.excursion_utilization,
        )
    return validate_completed_trip_outcome(outcome)


def validate_completed_trip_outcome(
    outcome: CompletedTripOutcome,
) -> CompletedTripOutcome:
    for field in (
        "lot_trip_id",
        "trip_id",
        "lot_id",
        "device_id",
        "product_id",
        "presentation",
        "state",
        "product_rule_version",
    ):
        _required_text(getattr(outcome, field), field)

    for field in ("trip_started_at", "completed_at"):
        if not _is_timezone_aware(getattr(outcome, field)):
            raise CompletedTripOutcomeValidationError(
                f"{field} must be timezone-aware"
            )
    if outcome.completed_at < outcome.trip_started_at:
        raise CompletedTripOutcomeValidationError(
            "completed_at cannot precede trip_started_at"
        )

    if outcome.final_live_state_revision is None:
        final_fields = (
            outcome.final_status,
            outcome.final_reason_code,
            outcome.final_active_rule_id,
            outcome.final_sample_id,
            outcome.final_sample_timestamp,
            outcome.final_temperature,
            outcome.final_excursion_episode_duration_minutes,
            outcome.final_cumulative_excursion_duration_minutes,
            outcome.final_excursion_utilization,
        )
        if any(value is not None for value in final_fields):
            raise CompletedTripOutcomeValidationError(
                "A no-telemetry outcome cannot contain final LiveState fields"
            )
        return outcome

    if isinstance(outcome.final_live_state_revision, bool) or (
        not isinstance(outcome.final_live_state_revision, int)
        or outcome.final_live_state_revision < 1
    ):
        raise CompletedTripOutcomeValidationError(
            "final_live_state_revision must be a positive integer"
        )
    if not isinstance(outcome.final_status, ApplicationStatus):
        raise CompletedTripOutcomeValidationError("final_status is invalid")
    _required_text(outcome.final_reason_code, "final_reason_code")
    _required_text(outcome.final_sample_id, "final_sample_id")
    if not _is_timezone_aware(outcome.final_sample_timestamp):
        raise CompletedTripOutcomeValidationError(
            "final_sample_timestamp must be timezone-aware"
        )
    if outcome.final_sample_timestamp > outcome.completed_at:
        raise CompletedTripOutcomeValidationError(
            "final_sample_timestamp cannot follow completed_at"
        )
    _finite_number(outcome.final_temperature, "final_temperature")
    for field in (
        "final_excursion_episode_duration_minutes",
        "final_cumulative_excursion_duration_minutes",
    ):
        value = _finite_number(getattr(outcome, field), field)
        if value < 0:
            raise CompletedTripOutcomeValidationError(
                f"{field} cannot be negative"
            )
    if outcome.final_excursion_utilization is not None:
        utilization = _finite_number(
            outcome.final_excursion_utilization,
            "final_excursion_utilization",
        )
        if utilization < 0:
            raise CompletedTripOutcomeValidationError(
                "final_excursion_utilization cannot be negative"
            )
    if outcome.final_active_rule_id is not None:
        _required_text(outcome.final_active_rule_id, "final_active_rule_id")
    return outcome


def _validate_state_identity(trip: TripIdentity, state: LiveState) -> None:
    expected = (
        trip.lot_trip_id,
        trip.trip_id,
        trip.device_id,
        trip.product_id,
        trip.product_rule_version,
    )
    actual = (
        state.lot_trip_id,
        state.trip_id,
        state.device_id,
        state.product_id,
        state.product_rule_version,
    )
    if actual != expected:
        raise CompletedTripOutcomeValidationError(
            "TripIdentity and final LiveState identity/provenance do not match"
        )


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompletedTripOutcomeValidationError(f"{field} is required")
    return value.strip()


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompletedTripOutcomeValidationError(f"{field} must be numeric")
    normalized = float(value)
    if not isfinite(normalized):
        raise CompletedTripOutcomeValidationError(f"{field} must be finite")
    return normalized


def _is_timezone_aware(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )
