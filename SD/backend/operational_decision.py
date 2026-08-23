"""Conservative, explainable recommendations from current and forecast risk.

This layer does not evaluate ProductRules or retrain models. It translates the
authoritative current status and an optional future-risk probability into an
operator recommendation. Thresholds are engineering/demo settings, not
clinically validated limits.
"""

import os
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Optional, Sequence, Tuple

try:
    from .rerouting import (
        Coordinates,
        ReroutingEvaluation,
        ReroutingEvaluator,
        ReroutingStatus,
        RouteCandidate,
        RouteCandidateProvider,
        RouteOptions,
        coordinates_document,
        rerouting_evaluation_document,
    )
    from .risk_rules import ApplicationStatus
    from .state_repository import LiveState, TelemetryRecord
    from .trip_identity import TripIdentity
except ImportError:
    from rerouting import (
        Coordinates,
        ReroutingEvaluation,
        ReroutingEvaluator,
        ReroutingStatus,
        RouteCandidate,
        RouteCandidateProvider,
        RouteOptions,
        coordinates_document,
        rerouting_evaluation_document,
    )
    from risk_rules import ApplicationStatus
    from state_repository import LiveState, TelemetryRecord
    from trip_identity import TripIdentity


DECISION_ENGINE_VERSION = "operational-decision-v1"
MEDIUM_RISK_THRESHOLD_ENV = "VITAE_DECISION_MEDIUM_RISK_THRESHOLD"
HIGH_RISK_THRESHOLD_ENV = "VITAE_DECISION_HIGH_RISK_THRESHOLD"
DEFAULT_MEDIUM_RISK_THRESHOLD = 0.20
DEFAULT_HIGH_RISK_THRESHOLD = 0.50


class FutureRiskCategory(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RecommendedAction(str, Enum):
    CONTINUE = "CONTINUE"
    MONITOR = "MONITOR"
    INTERVENE = "INTERVENE"
    REROUTE = "REROUTE"
    EXPEDITE = "EXPEDITE"
    STOP_OR_REPLACE = "STOP_OR_REPLACE"


class OperationalUrgency(str, Enum):
    ROUTINE = "ROUTINE"
    ELEVATED = "ELEVATED"
    URGENT = "URGENT"
    CRITICAL = "CRITICAL"


class OperationalDecisionConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class OperationalDecisionConfig:
    """Central engineering thresholds for the optional forecast interpretation."""

    medium_risk_threshold: float = DEFAULT_MEDIUM_RISK_THRESHOLD
    high_risk_threshold: float = DEFAULT_HIGH_RISK_THRESHOLD

    def __post_init__(self):
        _validate_probability(self.medium_risk_threshold, "medium_risk_threshold")
        _validate_probability(self.high_risk_threshold, "high_risk_threshold")
        if self.medium_risk_threshold >= self.high_risk_threshold:
            raise OperationalDecisionConfigurationError(
                "medium_risk_threshold must be lower than high_risk_threshold"
            )

    @classmethod
    def from_environment(cls, environment=None):
        values = os.environ if environment is None else environment
        return cls(
            medium_risk_threshold=_environment_probability(
                values, MEDIUM_RISK_THRESHOLD_ENV, DEFAULT_MEDIUM_RISK_THRESHOLD
            ),
            high_risk_threshold=_environment_probability(
                values, HIGH_RISK_THRESHOLD_ENV, DEFAULT_HIGH_RISK_THRESHOLD
            ),
        )


@dataclass(frozen=True)
class JourneyContext:
    """Known journey facts only; progress remains absent until a reliable source exists."""

    trip_started_at: Optional[datetime] = None
    current_at: Optional[datetime] = None
    elapsed_minutes: Optional[float] = None
    source_coordinates: Optional[Coordinates] = None
    current_coordinates: Optional[Coordinates] = None
    intended_destination_coordinates: Optional[Coordinates] = None
    current_destination_id: Optional[str] = None
    estimated_remaining_travel_minutes: Optional[float] = None
    total_expected_route_minutes: Optional[float] = None
    progress_fraction: Optional[float] = None
    distance_remaining_km: Optional[float] = None
    remaining_viability_minutes: Optional[float] = None
    route_evidence_source: Optional[str] = None

    def __post_init__(self):
        for field_name, value in (
            ("trip_started_at", self.trip_started_at),
            ("current_at", self.current_at),
        ):
            if value is not None and (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise ValueError(f"{field_name} must be timezone-aware")
        for field_name, value in (
            ("elapsed_minutes", self.elapsed_minutes),
            ("estimated_remaining_travel_minutes", self.estimated_remaining_travel_minutes),
            ("total_expected_route_minutes", self.total_expected_route_minutes),
            ("progress_fraction", self.progress_fraction),
            ("distance_remaining_km", self.distance_remaining_km),
            ("remaining_viability_minutes", self.remaining_viability_minutes),
        ):
            if value is not None and (
                not isinstance(value, (int, float)) or not isfinite(value) or value < 0
            ):
                raise ValueError(f"{field_name} must be a non-negative finite number")
        if self.progress_fraction is not None and self.progress_fraction > 1:
            raise ValueError("progress_fraction must be at most 1")


@dataclass(frozen=True)
class OperationalDecision:
    decision_version: str
    current_status: Optional[ApplicationStatus]
    future_risk_probability: Optional[float]
    future_risk_category: Optional[FutureRiskCategory]
    future_risk_source: Optional[str]
    future_risk_horizon_minutes: Optional[float]
    recommended_action: RecommendedAction
    urgency: OperationalUrgency
    reason: str
    journey_context: JourneyContext
    decision_factors: Tuple[str, ...]
    rerouting: ReroutingEvaluation


class OperationalDecisionEngine:
    """Combine current deterministic danger with optional future deterioration risk."""

    def __init__(self, config: Optional[OperationalDecisionConfig] = None):
        self._config = config or OperationalDecisionConfig()

    def decide(
        self,
        current_status: Optional[ApplicationStatus],
        future_risk_probability: Optional[float],
        journey_context: Optional[JourneyContext] = None,
        rerouting: Optional[ReroutingEvaluation] = None,
        future_risk_source: Optional[str] = None,
        future_risk_horizon_minutes: Optional[float] = None,
    ) -> OperationalDecision:
        if current_status is not None and not isinstance(
            current_status, ApplicationStatus
        ):
            raise TypeError("current_status must be an ApplicationStatus or None")
        if future_risk_probability is not None:
            _validate_probability(future_risk_probability, "future_risk_probability")
        if future_risk_horizon_minutes is not None:
            future_risk_horizon_minutes = _non_negative_number(
                future_risk_horizon_minutes, "future_risk_horizon_minutes"
            )
        context = journey_context or JourneyContext()
        category = self._category(future_risk_probability)
        route_evaluation = rerouting or ReroutingEvaluator().evaluate(
            RouteOptions(current_destination=None, alternatives=())
        )
        action, urgency, reason, factors = self._recommend(
            current_status, category, context
        )
        if (
            route_evaluation.status == ReroutingStatus.REROUTE_AVAILABLE
            and current_status
            not in (
                ApplicationStatus.CRITICAL,
                ApplicationStatus.RULE_VIOLATION,
                ApplicationStatus.DATA_ERROR,
            )
            and (
                current_status == ApplicationStatus.AT_RISK
                or category == FutureRiskCategory.HIGH
            )
        ):
            route_evaluation = replace(
                route_evaluation,
                status=ReroutingStatus.REROUTE_RECOMMENDED,
            )
            action = RecommendedAction.REROUTE
            urgency = OperationalUrgency.URGENT
            reason = route_evaluation.reason
            factors = tuple(
                factor
                for factor in factors
                if factor != "NO_BETTER_ROUTE_CONFIRMED"
            ) + ("BETTER_ELIGIBLE_DESTINATION",)
        return OperationalDecision(
            decision_version=DECISION_ENGINE_VERSION,
            current_status=current_status,
            future_risk_probability=future_risk_probability,
            future_risk_category=category,
            future_risk_source=future_risk_source,
            future_risk_horizon_minutes=future_risk_horizon_minutes,
            recommended_action=action,
            urgency=urgency,
            reason=reason,
            journey_context=context,
            decision_factors=factors,
            rerouting=route_evaluation,
        )

    def _category(
        self, probability: Optional[float]
    ) -> Optional[FutureRiskCategory]:
        if probability is None:
            return None
        if probability >= self._config.high_risk_threshold:
            return FutureRiskCategory.HIGH
        if probability >= self._config.medium_risk_threshold:
            return FutureRiskCategory.MEDIUM
        return FutureRiskCategory.LOW

    @staticmethod
    def _recommend(status, category, context):
        if status is None:
            return (
                RecommendedAction.MONITOR,
                OperationalUrgency.ELEVATED,
                "No accepted telemetry is available yet; begin monitoring before acting on the shipment.",
                ("CURRENT_STATUS_UNAVAILABLE",),
            )
        if status in (ApplicationStatus.CRITICAL, ApplicationStatus.RULE_VIOLATION):
            return (
                RecommendedAction.STOP_OR_REPLACE,
                OperationalUrgency.CRITICAL,
                "Current deterministic controls identify an immediate cold-chain danger.",
                ("CURRENT_DETERMINISTIC_DANGER",),
            )
        if status == ApplicationStatus.AT_RISK:
            return (
                RecommendedAction.INTERVENE,
                OperationalUrgency.URGENT,
                "Current deterministic controls indicate escalating cold-chain risk; intervene while eligible alternatives are evaluated.",
                ("CURRENT_DETERMINISTIC_DANGER", "NO_BETTER_ROUTE_CONFIRMED"),
            )
        if status == ApplicationStatus.DATA_ERROR:
            return (
                RecommendedAction.INTERVENE,
                OperationalUrgency.URGENT,
                "Telemetry cannot support a reliable current assessment; inspect the sensor and cooling chain immediately.",
                ("CURRENT_DATA_QUALITY_FAILURE",),
            )
        if category == FutureRiskCategory.HIGH:
            if context.progress_fraction is not None and context.progress_fraction <= 0.10:
                return (
                    RecommendedAction.STOP_OR_REPLACE,
                    OperationalUrgency.URGENT,
                    "High forecast risk early in the journey supports replacement before further transport commitment.",
                    ("HIGH_FUTURE_RISK", "EARLY_JOURNEY"),
                )
            if context.progress_fraction is not None and context.progress_fraction >= 0.80:
                return (
                    RecommendedAction.EXPEDITE,
                    OperationalUrgency.URGENT,
                    "High forecast risk near destination supports expedited delivery or evaluation of a closer eligible facility.",
                    ("HIGH_FUTURE_RISK", "LATE_JOURNEY"),
                )
            return (
                RecommendedAction.INTERVENE,
                OperationalUrgency.URGENT,
                "High forecast risk requires immediate cooling-chain intervention and route review before deterioration is detected.",
                ("HIGH_FUTURE_RISK", "JOURNEY_PROGRESS_UNAVAILABLE" if context.progress_fraction is None else "MID_JOURNEY"),
            )
        if status == ApplicationStatus.MONITOR or category == FutureRiskCategory.MEDIUM:
            return (
                RecommendedAction.MONITOR,
                OperationalUrgency.ELEVATED,
                "Increase observation and verify cooling conditions before the risk escalates.",
                ("CURRENT_MONITOR_STATUS",) if status == ApplicationStatus.MONITOR else ("MEDIUM_FUTURE_RISK",),
            )
        return (
            RecommendedAction.CONTINUE,
            OperationalUrgency.ROUTINE,
            "Current deterministic controls are safe and no elevated future-risk forecast is available.",
            ("CURRENT_STATUS_SAFE", "FORECAST_UNAVAILABLE" if category is None else "LOW_FUTURE_RISK"),
        )


def journey_context_from_live_state(
    trip: TripIdentity,
    live_state: Optional[LiveState],
    telemetry_history: Sequence[TelemetryRecord] = (),
) -> JourneyContext:
    if live_state is None:
        return JourneyContext(trip_started_at=trip.start_time)
    elapsed = (live_state.last_sample_timestamp - trip.start_time).total_seconds() / 60
    matching_record = next(
        (
            record
            for record in reversed(tuple(telemetry_history))
            if record.sample_id == live_state.last_sample_id
            and record.lot_trip_id == live_state.lot_trip_id
        ),
        None,
    )
    current_coordinates = None
    if (
        matching_record is not None
        and matching_record.latitude is not None
        and matching_record.longitude is not None
    ):
        current_coordinates = Coordinates(
            matching_record.latitude, matching_record.longitude
        )
    return JourneyContext(
        trip_started_at=trip.start_time,
        current_at=live_state.last_sample_timestamp,
        elapsed_minutes=max(0.0, elapsed),
        current_coordinates=current_coordinates,
    )


def journey_context_with_route_options(
    context: JourneyContext, options: RouteOptions
) -> JourneyContext:
    current = options.current_destination
    current_route = current.route_evidence if current else None
    total_route = options.total_route
    remaining_minutes = (
        current_route.duration_seconds / 60
        if current_route is not None and current_route.status.value == "AVAILABLE"
        else None
    )
    total_minutes = (
        total_route.duration_seconds / 60
        if total_route is not None and total_route.status.value == "AVAILABLE"
        else None
    )
    progress = _route_progress_fraction(total_route, current_route)
    return replace(
        context,
        source_coordinates=options.source_coordinates,
        intended_destination_coordinates=(current.coordinates if current else None),
        current_destination_id=(current.facility_id if current else None),
        estimated_remaining_travel_minutes=remaining_minutes,
        total_expected_route_minutes=total_minutes,
        progress_fraction=progress,
        distance_remaining_km=(current.distance_km if current else None),
        route_evidence_source=(current_route.provider if remaining_minutes is not None else None),
    )


def _route_progress_fraction(total_route, remaining_route):
    if total_route is None or remaining_route is None:
        return None
    if total_route.status.value != "AVAILABLE" or remaining_route.status.value != "AVAILABLE":
        return None
    total_distance = total_route.distance_meters
    remaining_distance = remaining_route.distance_meters
    if (
        total_distance is None
        or total_distance <= 0
        or remaining_distance is None
        or remaining_distance > total_distance
    ):
        return None
    progress = 1 - (remaining_distance / total_distance)
    if -1e-12 <= progress <= 0:
        return 0.0
    if 1 <= progress <= 1 + 1e-12:
        return 1.0
    return progress if 0 < progress < 1 else None


def operational_decision_document(value: OperationalDecision) -> dict:
    return {
        "decisionVersion": value.decision_version,
        "currentStatus": value.current_status.value if value.current_status else None,
        "futureRiskProbability": value.future_risk_probability,
        "futureRiskSource": value.future_risk_source,
        "futureRiskHorizonMinutes": value.future_risk_horizon_minutes,
        "futureRiskCategory": (
            value.future_risk_category.value
            if value.future_risk_category is not None
            else None
        ),
        "recommendedAction": value.recommended_action.value,
        "urgency": value.urgency.value,
        "reason": value.reason,
        "journeyContext": {
            "tripStartedAt": _serialize_datetime(value.journey_context.trip_started_at),
            "currentAt": _serialize_datetime(value.journey_context.current_at),
            "elapsedMinutes": value.journey_context.elapsed_minutes,
            "sourceCoordinates": coordinates_document(
                value.journey_context.source_coordinates
            ),
            "currentCoordinates": coordinates_document(
                value.journey_context.current_coordinates
            ),
            "intendedDestinationCoordinates": coordinates_document(
                value.journey_context.intended_destination_coordinates
            ),
            "currentDestinationId": value.journey_context.current_destination_id,
            "remainingRouteMinutes": (
                value.journey_context.estimated_remaining_travel_minutes
            ),
            "totalRouteMinutes": value.journey_context.total_expected_route_minutes,
            "estimatedJourneyProgress": value.journey_context.progress_fraction,
            "routeEvidenceSource": value.journey_context.route_evidence_source,
            "estimatedRemainingTravelMinutes": (
                value.journey_context.estimated_remaining_travel_minutes
            ),
            "totalExpectedRouteMinutes": value.journey_context.total_expected_route_minutes,
            "tripProgress": value.journey_context.progress_fraction,
            "distanceRemainingKm": value.journey_context.distance_remaining_km,
            "remainingViabilityMinutes": value.journey_context.remaining_viability_minutes,
        },
        "decisionFactors": list(value.decision_factors),
        "rerouting": rerouting_evaluation_document(value.rerouting),
    }


def probability_from_future_risk(value) -> Optional[float]:
    probability = getattr(value, "adverse_event_probability", None)
    if probability is None:
        return None
    _validate_probability(probability, "future_risk_probability")
    return probability


def _environment_probability(values, name, default):
    raw = values.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise OperationalDecisionConfigurationError(f"{name} must be a number") from error
    _validate_probability(value, name)
    return value


def _validate_probability(value, field_name):
    if not isinstance(value, (int, float)) or not isfinite(value) or not 0 <= value <= 1:
        raise OperationalDecisionConfigurationError(
            f"{field_name} must be a finite probability between 0 and 1"
        )


def _non_negative_number(value, field_name):
    if not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        raise OperationalDecisionConfigurationError(
            f"{field_name} must be a non-negative finite number"
        )
    return float(value)


def _serialize_datetime(value):
    return value.isoformat() if value is not None else None
