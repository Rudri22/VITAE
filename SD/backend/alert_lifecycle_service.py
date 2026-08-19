from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Mapping, Optional, Tuple

try:
    from .alerting import Alert, AlertNotFoundError, AlertRepository
except ImportError:
    from alerting import Alert, AlertNotFoundError, AlertRepository


class AlertActorRole(str, Enum):
    ORGANIZATION = "ORGANIZATION"
    DRIVER = "DRIVER"


@dataclass(frozen=True)
class AlertActor:
    actor_id: str
    role: AlertActorRole
    organization_id: Optional[str] = None
    driver_id: Optional[str] = None


@dataclass(frozen=True)
class AlertShipmentAccess:
    shipment_id: str
    lot_trip_id: str
    organization_id: str
    driver_id: str


class AlertLifecycleAccessDeniedError(PermissionError):
    pass


class AlertLifecycleService:
    """Authorize alert commands, then delegate lifecycle changes to the repository."""

    def __init__(
        self,
        alert_repository: AlertRepository,
        shipment_access_resolver: Callable[[str], Optional[Mapping[str, str]]],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._alert_repository = alert_repository
        self._shipment_access_resolver = shipment_access_resolver
        self._clock = clock

    def list_alerts(
        self,
        lot_trip_id: str,
        actor: AlertActor,
    ) -> Tuple[Alert, ...]:
        normalized_lot_trip_id = _required_text(lot_trip_id, "lot_trip_id")
        self._authorize_scope(normalized_lot_trip_id, actor, allow_driver=True)
        alerts = self._alert_repository.list_alerts(
            lot_trip_id=normalized_lot_trip_id
        )
        return tuple(
            sorted(
                alerts,
                key=lambda alert: (alert.detected_at, alert.alert_id),
                reverse=True,
            )
        )

    def get_alert(
        self,
        lot_trip_id: str,
        alert_id: str,
        actor: AlertActor,
    ) -> Alert:
        return self._authorized_alert(
            lot_trip_id,
            alert_id,
            actor,
            allow_driver=True,
        )

    def acknowledge(
        self,
        lot_trip_id: str,
        alert_id: str,
        actor: AlertActor,
    ) -> Alert:
        alert = self._authorized_alert(
            lot_trip_id,
            alert_id,
            actor,
            allow_driver=True,
        )
        return self._alert_repository.acknowledge_alert(
            alert.alert_id,
            actor_id=actor.actor_id,
            acknowledged_at=self._timestamp(),
        )

    def record_action(
        self,
        lot_trip_id: str,
        alert_id: str,
        description: str,
        actor: AlertActor,
    ) -> Alert:
        alert = self._authorized_alert(
            lot_trip_id,
            alert_id,
            actor,
            allow_driver=True,
        )
        return self._alert_repository.record_action(
            alert.alert_id,
            description=description,
            actor_id=actor.actor_id,
            recorded_at=self._timestamp(),
        )

    def resolve(
        self,
        lot_trip_id: str,
        alert_id: str,
        resolution_note: str,
        actor: AlertActor,
    ) -> Alert:
        alert = self._authorized_alert(
            lot_trip_id,
            alert_id,
            actor,
            allow_driver=False,
        )
        return self._alert_repository.resolve_alert(
            alert.alert_id,
            actor_id=actor.actor_id,
            resolved_at=self._timestamp(),
            resolution_note=resolution_note,
        )

    def _authorized_alert(
        self,
        lot_trip_id: str,
        alert_id: str,
        actor: AlertActor,
        *,
        allow_driver: bool,
    ) -> Alert:
        normalized_lot_trip_id = _required_text(lot_trip_id, "lot_trip_id")
        normalized_alert_id = _required_text(alert_id, "alert_id")
        alert = self._alert_repository.get_alert(normalized_alert_id)
        if alert is None or alert.lot_trip_id != normalized_lot_trip_id:
            raise AlertNotFoundError("Alert does not exist")
        self._authorize_scope(
            normalized_lot_trip_id,
            actor,
            allow_driver=allow_driver,
        )
        return alert

    def _authorize_scope(
        self,
        lot_trip_id: str,
        actor: AlertActor,
        *,
        allow_driver: bool,
    ) -> None:
        _validate_actor(actor)
        access = _shipment_access(
            self._shipment_access_resolver(lot_trip_id),
            lot_trip_id,
        )
        if actor.role == AlertActorRole.ORGANIZATION:
            allowed = actor.organization_id == access.organization_id
        elif actor.role == AlertActorRole.DRIVER and allow_driver:
            allowed = (
                actor.driver_id == access.driver_id
                and actor.organization_id == access.organization_id
            )
        else:
            allowed = False
        if not allowed:
            raise AlertLifecycleAccessDeniedError(
                "Alert lifecycle access is not permitted"
            )

    def _timestamp(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Alert lifecycle clock must return a timezone-aware datetime")
        return value


def _shipment_access(
    value: Optional[Mapping[str, str]],
    expected_lot_trip_id: str,
) -> AlertShipmentAccess:
    if value is None:
        raise AlertLifecycleAccessDeniedError(
            "Alert is not linked to an accessible shipment"
        )
    try:
        access = AlertShipmentAccess(
            shipment_id=_required_text(value.get("shipmentId"), "shipment_id"),
            lot_trip_id=_required_text(value.get("lotTripId"), "lot_trip_id"),
            organization_id=_required_text(
                value.get("organizationId"),
                "organization_id",
            ),
            driver_id=_required_text(value.get("driverId"), "driver_id"),
        )
    except (AttributeError, ValueError) as error:
        raise AlertLifecycleAccessDeniedError(
            "Alert shipment access is incomplete"
        ) from error
    if access.lot_trip_id != expected_lot_trip_id:
        raise AlertLifecycleAccessDeniedError(
            "Alert shipment identity does not match"
        )
    return access


def _validate_actor(actor: AlertActor) -> None:
    if not isinstance(actor, AlertActor):
        raise AlertLifecycleAccessDeniedError("Authenticated alert actor is required")
    _required_text(actor.actor_id, "actor_id")
    if actor.role == AlertActorRole.ORGANIZATION:
        _required_text(actor.organization_id, "organization_id")
    elif actor.role == AlertActorRole.DRIVER:
        _required_text(actor.organization_id, "organization_id")
        _required_text(actor.driver_id, "driver_id")
    else:
        raise AlertLifecycleAccessDeniedError("Unsupported alert actor role")


def _required_text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()
