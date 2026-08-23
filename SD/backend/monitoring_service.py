from dataclasses import dataclass
from typing import Optional, Protocol, Tuple, Union, runtime_checkable

try:
    from .alerting import Alert, AlertRepository, AlertStatus
    from .operational_decision import (
        OperationalDecision,
        OperationalDecisionEngine,
        journey_context_from_live_state,
        journey_context_with_route_options,
        probability_from_future_risk,
    )
    from .rerouting import (
        ReroutingEvaluator,
        RouteCandidateProvider,
        RouteOptions,
    )
    from .state_repository import IdentityRepository, LiveState, TelemetryStateRepository
    from .journey_risk_inference import (
        JourneyRiskNotPredicted,
        JourneyRiskNotPredictedReason,
        JourneyRiskPrediction,
        journey_risk_document,
        probability_from_journey_risk,
    )
    from .temporal_risk_inference import (
        TemporalRiskNotPredicted,
        TemporalRiskNotPredictedReason,
        TemporalRiskPrediction,
        temporal_risk_prediction_document,
    )
    from .trip_identity import TripIdentity
except ImportError:
    from alerting import Alert, AlertRepository, AlertStatus
    from operational_decision import (
        OperationalDecision,
        OperationalDecisionEngine,
        journey_context_from_live_state,
        journey_context_with_route_options,
        probability_from_future_risk,
    )
    from rerouting import ReroutingEvaluator, RouteCandidateProvider, RouteOptions
    from state_repository import IdentityRepository, LiveState, TelemetryStateRepository
    from journey_risk_inference import (
        JourneyRiskNotPredicted,
        JourneyRiskNotPredictedReason,
        JourneyRiskPrediction,
        journey_risk_document,
        probability_from_journey_risk,
    )
    from temporal_risk_inference import (
        TemporalRiskNotPredicted,
        TemporalRiskNotPredictedReason,
        TemporalRiskPrediction,
        temporal_risk_prediction_document,
    )
    from trip_identity import TripIdentity


@dataclass(frozen=True)
class FutureRiskNotConfigured:
    pass


@runtime_checkable
class TemporalRiskPredictor(Protocol):
    def predict(
        self, lot_trip_id: str
    ) -> Union[TemporalRiskPrediction, TemporalRiskNotPredicted]:
        ...


@runtime_checkable
class JourneyRiskPredictor(Protocol):
    def predict(self, lot_trip_id: str, remaining_journey_minutes: float):
        ...


@dataclass(frozen=True)
class MonitoringSnapshot:
    trip_identity: TripIdentity
    live_state: LiveState | None
    open_alert_count: int
    latest_alert: Alert | None
    latest_telemetry_source: str | None
    future_risk: Union[
        FutureRiskNotConfigured,
        TemporalRiskPrediction,
        TemporalRiskNotPredicted,
    ]
    journey_risk: Union[JourneyRiskPrediction, JourneyRiskNotPredicted]
    operational_decision: OperationalDecision


class LotTripNotFoundError(LookupError):
    pass


class MonitoringService:
    """Read authoritative current state and alerts without deriving status."""

    def __init__(
        self,
        identity_repository: IdentityRepository,
        state_repository: TelemetryStateRepository,
        alert_repository: AlertRepository,
        future_risk_service: Optional[TemporalRiskPredictor] = None,
        decision_engine: Optional[OperationalDecisionEngine] = None,
        route_candidate_provider: Optional[RouteCandidateProvider] = None,
        rerouting_evaluator: Optional[ReroutingEvaluator] = None,
        journey_risk_service: Optional[JourneyRiskPredictor] = None,
    ):
        self._identity_repository = identity_repository
        self._state_repository = state_repository
        self._alert_repository = alert_repository
        if future_risk_service is not None and not isinstance(
            future_risk_service, TemporalRiskPredictor
        ):
            raise TypeError("future_risk_service must support TemporalRiskPredictor")
        self._future_risk_service = future_risk_service
        self._decision_engine = decision_engine or OperationalDecisionEngine()
        if route_candidate_provider is not None and not isinstance(
            route_candidate_provider, RouteCandidateProvider
        ):
            raise TypeError(
                "route_candidate_provider must support RouteCandidateProvider"
            )
        self._route_candidate_provider = route_candidate_provider
        self._rerouting_evaluator = rerouting_evaluator or ReroutingEvaluator()
        if journey_risk_service is not None and not isinstance(
            journey_risk_service, JourneyRiskPredictor
        ):
            raise TypeError("journey_risk_service must support JourneyRiskPredictor")
        self._journey_risk_service = journey_risk_service

    def get_live_snapshot(self, lot_trip_id: str) -> MonitoringSnapshot:
        normalized_id = _required_lot_trip_id(lot_trip_id)
        trip = self._require_trip(normalized_id)
        alerts = self._sorted_alerts(normalized_id)
        active_alerts = tuple(
            alert for alert in alerts if alert.status != AlertStatus.RESOLVED
        )
        live_state = self._state_repository.get_live_state(normalized_id)
        future_risk = self._future_risk(normalized_id)
        telemetry_history = (
            self._state_repository.get_telemetry_history(normalized_id)
            if live_state is not None
            else ()
        )
        latest_telemetry_source = (
            telemetry_history[-1].source.value if telemetry_history else None
        )
        journey_context = journey_context_from_live_state(
            trip, live_state, telemetry_history
        )
        route_options = self._route_options(trip, journey_context.current_coordinates)
        journey_context = journey_context_with_route_options(
            journey_context, route_options
        )
        journey_risk = self._journey_risk(
            normalized_id, journey_context.estimated_remaining_travel_minutes
        )
        journey_probability = probability_from_journey_risk(journey_risk)
        fixed_probability = probability_from_future_risk(future_risk)
        selected_probability = (
            journey_probability if journey_probability is not None else fixed_probability
        )
        selected_source = (
            "JOURNEY_AWARE_MODEL"
            if journey_probability is not None
            else "FIXED_30_MINUTE_FALLBACK" if fixed_probability is not None else None
        )
        selected_horizon = (
            journey_context.estimated_remaining_travel_minutes
            if journey_probability is not None
            else 30 if fixed_probability is not None else None
        )
        rerouting = self._rerouting_evaluator.evaluate(route_options)
        return MonitoringSnapshot(
            trip_identity=trip,
            live_state=live_state,
            open_alert_count=len(active_alerts),
            latest_alert=active_alerts[0] if active_alerts else None,
            latest_telemetry_source=latest_telemetry_source,
            future_risk=future_risk,
            journey_risk=journey_risk,
            operational_decision=self._decision_engine.decide(
                live_state.status if live_state is not None else None,
                selected_probability,
                journey_context,
                rerouting,
                future_risk_source=selected_source,
                future_risk_horizon_minutes=selected_horizon,
            ),
        )

    def list_alerts(self, lot_trip_id: str) -> Tuple[Alert, ...]:
        normalized_id = _required_lot_trip_id(lot_trip_id)
        self._require_trip(normalized_id)
        return self._sorted_alerts(normalized_id)

    def _require_trip(self, lot_trip_id: str) -> TripIdentity:
        trip = self._identity_repository.get_trip_by_lot_trip_id(lot_trip_id)
        if trip is None:
            raise LotTripNotFoundError("Lot trip is not registered")
        return trip

    def _sorted_alerts(self, lot_trip_id: str) -> Tuple[Alert, ...]:
        alerts = self._alert_repository.list_alerts(lot_trip_id=lot_trip_id)
        return tuple(
            sorted(
                alerts,
                key=lambda alert: (alert.detected_at, alert.alert_id),
                reverse=True,
            )
        )

    def _future_risk(self, lot_trip_id: str):
        if self._future_risk_service is None:
            return FutureRiskNotConfigured()
        try:
            result = self._future_risk_service.predict(lot_trip_id)
        except Exception:
            return TemporalRiskNotPredicted(
                lot_trip_id=lot_trip_id,
                reason_code=(
                    TemporalRiskNotPredictedReason.INFERENCE_UNAVAILABLE
                ),
                detail="Future-risk inference is temporarily unavailable",
            )
        if isinstance(result, (TemporalRiskPrediction, TemporalRiskNotPredicted)):
            return result
        return TemporalRiskNotPredicted(
            lot_trip_id=lot_trip_id,
            reason_code=TemporalRiskNotPredictedReason.INFERENCE_UNAVAILABLE,
            detail="Future-risk inference returned an invalid result",
        )

    def _route_options(self, trip, current_coordinates):
        if self._route_candidate_provider is None:
            return RouteOptions(current_destination=None, alternatives=())
        try:
            value = self._route_candidate_provider.route_options(
                trip, current_coordinates
            )
        except Exception:
            return RouteOptions(current_destination=None, alternatives=())
        if isinstance(value, RouteOptions):
            return value
        return RouteOptions(current_destination=None, alternatives=())

    def _journey_risk(self, lot_trip_id, remaining_minutes):
        if remaining_minutes is None:
            return JourneyRiskNotPredicted(
                lot_trip_id,
                JourneyRiskNotPredictedReason.REMAINING_JOURNEY_DURATION_UNAVAILABLE,
                "Authoritative remaining route duration is unavailable",
            )
        if self._journey_risk_service is None:
            return JourneyRiskNotPredicted(
                lot_trip_id,
                JourneyRiskNotPredictedReason.INFERENCE_UNAVAILABLE,
                "Journey-aware inference is not configured",
            )
        try:
            value = self._journey_risk_service.predict(lot_trip_id, remaining_minutes)
        except Exception:
            return JourneyRiskNotPredicted(
                lot_trip_id,
                JourneyRiskNotPredictedReason.INFERENCE_UNAVAILABLE,
                "Journey-aware inference is temporarily unavailable",
            )
        if isinstance(value, (JourneyRiskPrediction, JourneyRiskNotPredicted)):
            return value
        return JourneyRiskNotPredicted(
            lot_trip_id,
            JourneyRiskNotPredictedReason.INFERENCE_UNAVAILABLE,
            "Journey-aware inference returned an invalid result",
        )


def serialize_future_risk(value) -> dict:
    if isinstance(value, FutureRiskNotConfigured):
        return {"state": "NOT_CONFIGURED"}
    if isinstance(value, TemporalRiskPrediction):
        document = temporal_risk_prediction_document(value)
        document.pop("predicted", None)
        return {"state": "PREDICTED", **document}
    if isinstance(value, TemporalRiskNotPredicted):
        return {
            "state": "NOT_PREDICTED",
            "reasonCode": value.reason_code.value,
            "detail": value.detail,
        }
    raise TypeError("future risk result is invalid")


def serialize_journey_risk(value) -> dict:
    return journey_risk_document(value)


def _required_lot_trip_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("lot_trip_id is required")
    return normalized
