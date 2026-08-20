from dataclasses import dataclass
from typing import Optional, Protocol, Tuple, Union, runtime_checkable

try:
    from .alerting import Alert, AlertRepository, AlertStatus
    from .state_repository import IdentityRepository, LiveState, TelemetryStateRepository
    from .temporal_risk_inference import (
        TemporalRiskNotPredicted,
        TemporalRiskNotPredictedReason,
        TemporalRiskPrediction,
        temporal_risk_prediction_document,
    )
    from .trip_identity import TripIdentity
except ImportError:
    from alerting import Alert, AlertRepository, AlertStatus
    from state_repository import IdentityRepository, LiveState, TelemetryStateRepository
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


@dataclass(frozen=True)
class MonitoringSnapshot:
    trip_identity: TripIdentity
    live_state: LiveState | None
    open_alert_count: int
    latest_alert: Alert | None
    future_risk: Union[
        FutureRiskNotConfigured,
        TemporalRiskPrediction,
        TemporalRiskNotPredicted,
    ]


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
    ):
        self._identity_repository = identity_repository
        self._state_repository = state_repository
        self._alert_repository = alert_repository
        if future_risk_service is not None and not isinstance(
            future_risk_service, TemporalRiskPredictor
        ):
            raise TypeError("future_risk_service must support TemporalRiskPredictor")
        self._future_risk_service = future_risk_service

    def get_live_snapshot(self, lot_trip_id: str) -> MonitoringSnapshot:
        normalized_id = _required_lot_trip_id(lot_trip_id)
        trip = self._require_trip(normalized_id)
        alerts = self._sorted_alerts(normalized_id)
        active_alerts = tuple(
            alert for alert in alerts if alert.status != AlertStatus.RESOLVED
        )
        return MonitoringSnapshot(
            trip_identity=trip,
            live_state=self._state_repository.get_live_state(normalized_id),
            open_alert_count=len(active_alerts),
            latest_alert=active_alerts[0] if active_alerts else None,
            future_risk=self._future_risk(normalized_id),
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


def _required_lot_trip_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("lot_trip_id is required")
    return normalized
