from dataclasses import dataclass
from typing import Tuple

try:
    from .alerting import Alert, AlertRepository, AlertStatus
    from .state_repository import IdentityRepository, LiveState, TelemetryStateRepository
    from .trip_identity import TripIdentity
except ImportError:
    from alerting import Alert, AlertRepository, AlertStatus
    from state_repository import IdentityRepository, LiveState, TelemetryStateRepository
    from trip_identity import TripIdentity


@dataclass(frozen=True)
class MonitoringSnapshot:
    trip_identity: TripIdentity
    live_state: LiveState | None
    open_alert_count: int
    latest_alert: Alert | None


class LotTripNotFoundError(LookupError):
    pass


class MonitoringService:
    """Read authoritative current state and alerts without deriving status."""

    def __init__(
        self,
        identity_repository: IdentityRepository,
        state_repository: TelemetryStateRepository,
        alert_repository: AlertRepository,
    ):
        self._identity_repository = identity_repository
        self._state_repository = state_repository
        self._alert_repository = alert_repository

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


def _required_lot_trip_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("lot_trip_id is required")
    return normalized
