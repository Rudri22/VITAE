from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable

try:
    from .completed_trip_outcome import (
        CompletedTripOutcome,
        completed_trip_outcome_from_state,
    )
    from .state_repository import LiveState
    from .trip_identity import DeviceAssignment, TripIdentity, TripStatus
except ImportError:
    from completed_trip_outcome import (
        CompletedTripOutcome,
        completed_trip_outcome_from_state,
    )
    from state_repository import LiveState
    from trip_identity import DeviceAssignment, TripIdentity, TripStatus


@dataclass(frozen=True)
class TripCompletionResult:
    trip: TripIdentity
    assignment: DeviceAssignment
    final_live_state: Optional[LiveState]
    outcome: CompletedTripOutcome


class TripCompletionError(ValueError):
    pass


class TripCompletionConflictError(TripCompletionError):
    pass


class TripCompletionIntegrityError(TripCompletionError):
    pass


@runtime_checkable
class TripCompletionRepository(Protocol):
    def complete_trip(
        self,
        trip_id: str,
        assignment_id: str,
        *,
        completed_at: datetime,
    ) -> TripCompletionResult:
        ...

    def get_completed_trip_outcome(
        self, lot_trip_id: str
    ) -> Optional[CompletedTripOutcome]:
        ...


def completed_trip_replay_result(
    trip: TripIdentity,
    assignment: DeviceAssignment,
    final_live_state: Optional[LiveState],
    existing_outcome: Optional[CompletedTripOutcome],
    completed_at: datetime,
) -> TripCompletionResult:
    if trip.status != TripStatus.COMPLETED:
        raise TripCompletionIntegrityError("Trip is not completed")
    if trip.completed_at != completed_at:
        raise TripCompletionConflictError(
            "Trip is already completed at a different timestamp"
        )
    if assignment.active or existing_outcome is None:
        raise TripCompletionIntegrityError(
            "Completed trip lifecycle and outcome are inconsistent"
        )
    expected = completed_trip_outcome_from_state(
        trip, trip.completed_at, final_live_state
    )
    if existing_outcome != expected:
        raise TripCompletionIntegrityError(
            "Completed outcome does not match the authoritative final LiveState"
        )
    return TripCompletionResult(
        trip=trip,
        assignment=assignment,
        final_live_state=final_live_state,
        outcome=existing_outcome,
    )
