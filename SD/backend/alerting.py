from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from hashlib import sha256
from threading import RLock
from typing import Dict, Optional, Protocol, Tuple, runtime_checkable

try:
    from .risk_rules import ApplicationStatus
    from .state_repository import LiveState
    from .telemetry_processor import ProcessingResult
except ImportError:
    from risk_rules import ApplicationStatus
    from state_repository import LiveState
    from telemetry_processor import ProcessingResult


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertType(str, Enum):
    EXCURSION_MONITOR = "EXCURSION_MONITOR"
    EXCURSION_AT_RISK = "EXCURSION_AT_RISK"
    TEMPERATURE_CRITICAL = "TEMPERATURE_CRITICAL"
    PRODUCT_RULE_VIOLATION = "PRODUCT_RULE_VIOLATION"
    TELEMETRY_DATA_ERROR = "TELEMETRY_DATA_ERROR"


class AlertStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True)
class AlertAction:
    action_id: str
    description: str
    actor_id: str
    recorded_at: datetime


@dataclass(frozen=True)
class Alert:
    alert_id: str
    alert_type: AlertType
    severity: AlertSeverity
    status: AlertStatus
    trip_id: str
    lot_trip_id: str
    device_id: str
    sample_id: str
    source_status: ApplicationStatus
    reason_code: str
    active_rule_id: Optional[str]
    message: str
    recommended_action: str
    detected_at: datetime
    updated_at: datetime
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    actions: Tuple[AlertAction, ...] = ()
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None


class AlertError(ValueError):
    pass


class AlertPolicyError(AlertError):
    pass


class AlertNotFoundError(AlertError):
    pass


class AlertConflictError(AlertError):
    pass


class AlertTransitionError(AlertError):
    pass


_ALERT_POLICY = {
    ApplicationStatus.MONITOR: (
        AlertType.EXCURSION_MONITOR,
        AlertSeverity.INFO,
        "Product is in a verified permitted excursion",
        "Continue monitoring the excursion duration",
    ),
    ApplicationStatus.AT_RISK: (
        AlertType.EXCURSION_AT_RISK,
        AlertSeverity.WARNING,
        "Permitted excursion duration is at least 50 percent utilized",
        "Inspect cooling conditions and prepare corrective action",
    ),
    ApplicationStatus.CRITICAL: (
        AlertType.TEMPERATURE_CRITICAL,
        AlertSeverity.CRITICAL,
        "Permitted excursion duration is at least 90 percent utilized",
        "Intervene immediately to restore verified storage conditions",
    ),
    ApplicationStatus.RULE_VIOLATION: (
        AlertType.PRODUCT_RULE_VIOLATION,
        AlertSeverity.CRITICAL,
        "A verified product rule has been violated",
        "Quarantine the affected lot and begin disposition review",
    ),
    ApplicationStatus.DATA_ERROR: (
        AlertType.TELEMETRY_DATA_ERROR,
        AlertSeverity.WARNING,
        "Reliable product status could not be computed",
        "Inspect the sensor and telemetry data path",
    ),
}


def evaluate_alert_policy(
    previous_live_state: Optional[LiveState],
    result: ProcessingResult,
) -> Optional[Alert]:
    """Create an alert only when an authoritative status meaningfully changes."""
    _validate_processing_result(result)
    _validate_previous_live_state(previous_live_state, result.live_state)
    if (
        previous_live_state is not None
        and previous_live_state.status == result.decision.status
    ):
        return None

    policy = _ALERT_POLICY.get(result.decision.status)
    if policy is None:
        return None

    alert_type, severity, message, recommended_action = policy
    record = result.telemetry_record
    return Alert(
        alert_id=_alert_id(record.device_id, record.sample_id, alert_type),
        alert_type=alert_type,
        severity=severity,
        status=AlertStatus.OPEN,
        trip_id=record.trip_id,
        lot_trip_id=record.lot_trip_id,
        device_id=record.device_id,
        sample_id=record.sample_id,
        source_status=result.decision.status,
        reason_code=result.decision.reason_code,
        active_rule_id=result.decision.active_rule_id,
        message=message,
        recommended_action=recommended_action,
        detected_at=record.timestamp,
        updated_at=record.timestamp,
    )


@runtime_checkable
class AlertRepository(Protocol):
    def save_alert(self, alert: Alert) -> Alert:
        ...

    def get_alert(self, alert_id: str) -> Optional[Alert]:
        ...

    def list_alerts(
        self,
        *,
        lot_trip_id: Optional[str] = None,
        status: Optional[AlertStatus] = None,
    ) -> Tuple[Alert, ...]:
        ...

    def acknowledge_alert(
        self,
        alert_id: str,
        *,
        actor_id: str,
        acknowledged_at: datetime,
    ) -> Alert:
        ...

    def record_action(
        self,
        alert_id: str,
        *,
        description: str,
        actor_id: str,
        recorded_at: datetime,
    ) -> Alert:
        ...

    def resolve_alert(
        self,
        alert_id: str,
        *,
        actor_id: str,
        resolved_at: datetime,
        resolution_note: str,
    ) -> Alert:
        ...


class InMemoryAlertRepository(AlertRepository):
    def __init__(self):
        self._alerts: Dict[str, Alert] = {}
        self._lock = RLock()

    def save_alert(self, alert: Alert) -> Alert:
        validate_new_alert_candidate(alert)
        with self._lock:
            existing = self._alerts.get(alert.alert_id)
            if existing is not None:
                if alert_creation_identity(existing) != alert_creation_identity(alert):
                    raise AlertConflictError(
                        "Alert ID is already associated with different content"
                    )
                return existing
            self._alerts[alert.alert_id] = alert
            return alert

    def get_alert(self, alert_id: str) -> Optional[Alert]:
        with self._lock:
            return self._alerts.get(alert_id)

    def list_alerts(
        self,
        *,
        lot_trip_id: Optional[str] = None,
        status: Optional[AlertStatus] = None,
    ) -> Tuple[Alert, ...]:
        with self._lock:
            alerts = tuple(self._alerts.values())
        if lot_trip_id is not None:
            alerts = tuple(alert for alert in alerts if alert.lot_trip_id == lot_trip_id)
        if status is not None:
            alerts = tuple(alert for alert in alerts if alert.status == status)
        return alerts

    def acknowledge_alert(
        self,
        alert_id: str,
        *,
        actor_id: str,
        acknowledged_at: datetime,
    ) -> Alert:
        actor = _required_text(actor_id, "actor_id")
        timestamp = _aware_timestamp(acknowledged_at, "acknowledged_at")
        with self._lock:
            alert = self._require_alert(alert_id)
            if alert.status != AlertStatus.OPEN:
                raise AlertTransitionError("Only an OPEN alert can be acknowledged")
            _require_not_before_detection(alert, timestamp)
            if timestamp < alert.updated_at:
                raise AlertTransitionError(
                    "Acknowledgement cannot predate alert activity"
                )
            updated = replace(
                alert,
                status=AlertStatus.ACKNOWLEDGED,
                acknowledged_by=actor,
                acknowledged_at=timestamp,
                updated_at=timestamp,
            )
            self._alerts[alert_id] = updated
            return updated

    def record_action(
        self,
        alert_id: str,
        *,
        description: str,
        actor_id: str,
        recorded_at: datetime,
    ) -> Alert:
        action_description = _required_text(description, "description")
        actor = _required_text(actor_id, "actor_id")
        timestamp = _aware_timestamp(recorded_at, "recorded_at")
        with self._lock:
            alert = self._require_alert(alert_id)
            if alert.status == AlertStatus.RESOLVED:
                raise AlertTransitionError("A RESOLVED alert cannot receive actions")
            action = build_alert_action(
                alert_id,
                description=action_description,
                actor_id=actor,
                recorded_at=timestamp,
            )
            for existing_action in alert.actions:
                if existing_action.action_id == action.action_id:
                    if existing_action != action:
                        raise AlertConflictError(
                            "Alert action ID is associated with different content"
                        )
                    return alert
            if timestamp < alert.updated_at:
                raise AlertTransitionError("Alert action cannot predate its current state")
            updated = replace(
                alert,
                actions=alert.actions + (action,),
                updated_at=timestamp,
            )
            self._alerts[alert_id] = updated
            return updated

    def resolve_alert(
        self,
        alert_id: str,
        *,
        actor_id: str,
        resolved_at: datetime,
        resolution_note: str,
    ) -> Alert:
        actor = _required_text(actor_id, "actor_id")
        timestamp = _aware_timestamp(resolved_at, "resolved_at")
        note = _required_text(resolution_note, "resolution_note")
        with self._lock:
            alert = self._require_alert(alert_id)
            if alert.status == AlertStatus.RESOLVED:
                raise AlertTransitionError("Alert is already RESOLVED")
            if timestamp < alert.updated_at:
                raise AlertTransitionError("Resolution cannot predate alert activity")
            updated = replace(
                alert,
                status=AlertStatus.RESOLVED,
                resolved_by=actor,
                resolved_at=timestamp,
                resolution_note=note,
                updated_at=timestamp,
            )
            self._alerts[alert_id] = updated
            return updated

    def _require_alert(self, alert_id):
        alert = self._alerts.get(alert_id)
        if alert is None:
            raise AlertNotFoundError("Alert does not exist")
        return alert


def _validate_processing_result(result):
    record = result.telemetry_record
    state = result.live_state
    decision = result.decision
    if (
        record.trip_id != state.trip_id
        or record.lot_trip_id != state.lot_trip_id
        or record.device_id != state.device_id
        or record.sample_id != state.last_sample_id
        or record.timestamp != state.last_sample_timestamp
    ):
        raise AlertPolicyError("ProcessingResult record and LiveState do not agree")
    if (
        decision.status != state.status
        or decision.reason_code != state.reason_code
        or decision.active_rule_id != state.active_rule_id
    ):
        raise AlertPolicyError("ProcessingResult decision and LiveState do not agree")


def _validate_previous_live_state(previous_state, current_state):
    if previous_state is None:
        return
    previous_identity = (
        previous_state.trip_id,
        previous_state.lot_trip_id,
        previous_state.device_id,
        previous_state.product_id,
        previous_state.product_rule_version,
    )
    current_identity = (
        current_state.trip_id,
        current_state.lot_trip_id,
        current_state.device_id,
        current_state.product_id,
        current_state.product_rule_version,
    )
    if previous_identity != current_identity:
        raise AlertPolicyError(
            "Previous and current LiveState identity do not agree"
        )


def alert_creation_identity(alert: Alert) -> tuple:
    return (
        alert.alert_id,
        alert.alert_type,
        alert.severity,
        alert.trip_id,
        alert.lot_trip_id,
        alert.device_id,
        alert.sample_id,
        alert.source_status,
        alert.reason_code,
        alert.active_rule_id,
        alert.message,
        alert.recommended_action,
        alert.detected_at,
    )


def build_alert_action(
    alert_id: str,
    *,
    description: str,
    actor_id: str,
    recorded_at: datetime,
) -> AlertAction:
    normalized_alert_id = _required_text(alert_id, "alert_id")
    normalized_description = _required_text(description, "description")
    normalized_actor = _required_text(actor_id, "actor_id")
    timestamp = _aware_timestamp(recorded_at, "recorded_at")
    return AlertAction(
        action_id=_action_id(
            normalized_alert_id,
            normalized_actor,
            timestamp,
            normalized_description,
        ),
        description=normalized_description,
        actor_id=normalized_actor,
        recorded_at=timestamp,
    )


def validate_new_alert_candidate(alert: Alert) -> None:
    validate_persisted_alert(alert)
    if (
        alert.status != AlertStatus.OPEN
        or alert.updated_at != alert.detected_at
        or alert.acknowledged_by is not None
        or alert.acknowledged_at is not None
        or alert.actions
        or alert.resolved_by is not None
        or alert.resolved_at is not None
        or alert.resolution_note is not None
    ):
        raise AlertConflictError("A newly saved alert must be a clean OPEN candidate")


def validate_persisted_alert(alert: Alert) -> None:
    if not isinstance(alert, Alert):
        raise AlertConflictError("alert must be an Alert")
    for field in (
        "alert_id",
        "trip_id",
        "lot_trip_id",
        "device_id",
        "sample_id",
        "reason_code",
        "message",
        "recommended_action",
    ):
        _required_text(getattr(alert, field), field)
    _aware_timestamp(alert.detected_at, "detected_at")
    _aware_timestamp(alert.updated_at, "updated_at")
    if alert.updated_at < alert.detected_at:
        raise AlertTransitionError("Alert update cannot predate detection")
    if not isinstance(alert.alert_type, AlertType):
        raise AlertTransitionError("alert_type is invalid")
    if not isinstance(alert.severity, AlertSeverity):
        raise AlertTransitionError("severity is invalid")
    if not isinstance(alert.status, AlertStatus):
        raise AlertTransitionError("status is invalid")
    if not isinstance(alert.source_status, ApplicationStatus):
        raise AlertTransitionError("source_status is invalid")

    acknowledged = alert.acknowledged_by is not None or alert.acknowledged_at is not None
    if acknowledged:
        _required_text(alert.acknowledged_by, "acknowledged_by")
        acknowledged_at = _aware_timestamp(alert.acknowledged_at, "acknowledged_at")
        _require_not_before_detection(alert, acknowledged_at)
        if acknowledged_at > alert.updated_at:
            raise AlertTransitionError("Acknowledgement cannot follow updated_at")
    if alert.status == AlertStatus.ACKNOWLEDGED and not acknowledged:
        raise AlertTransitionError("ACKNOWLEDGED alert is missing acknowledgement")
    if alert.status == AlertStatus.OPEN and acknowledged:
        raise AlertTransitionError("OPEN alert cannot contain acknowledgement fields")

    action_ids = set()
    for action in alert.actions:
        if not isinstance(action, AlertAction):
            raise AlertTransitionError("actions must contain AlertAction values")
        _required_text(action.action_id, "action_id")
        _required_text(action.description, "description")
        _required_text(action.actor_id, "actor_id")
        recorded_at = _aware_timestamp(action.recorded_at, "recorded_at")
        _require_not_before_detection(alert, recorded_at)
        if recorded_at > alert.updated_at:
            raise AlertTransitionError("Alert action cannot follow updated_at")
        if action.action_id in action_ids:
            raise AlertConflictError("Alert action IDs must be unique")
        action_ids.add(action.action_id)

    resolved = (
        alert.resolved_by is not None
        or alert.resolved_at is not None
        or alert.resolution_note is not None
    )
    if resolved:
        _required_text(alert.resolved_by, "resolved_by")
        resolved_at = _aware_timestamp(alert.resolved_at, "resolved_at")
        _required_text(alert.resolution_note, "resolution_note")
        _require_not_before_detection(alert, resolved_at)
        if resolved_at != alert.updated_at:
            raise AlertTransitionError("Resolution timestamp must equal updated_at")
    if (alert.status == AlertStatus.RESOLVED) != resolved:
        raise AlertTransitionError("Alert resolution fields and status do not agree")


def _require_not_before_detection(alert, timestamp):
    if timestamp < alert.detected_at:
        raise AlertTransitionError("Lifecycle event cannot predate alert detection")


def _required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise AlertTransitionError(f"{field} must be a non-empty string")
    return value.strip()


def _aware_timestamp(value, field):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AlertTransitionError(f"{field} must be timezone-aware")
    return value


def _alert_id(device_id, sample_id, alert_type):
    identity = f"{device_id}|{sample_id}|{alert_type.value}".encode("utf-8")
    return "alert-" + sha256(identity).hexdigest()[:24]


def _action_id(alert_id, actor_id, recorded_at, description):
    identity = (
        f"{alert_id}|{actor_id}|{recorded_at.isoformat()}|{description}"
    ).encode("utf-8")
    return "action-" + sha256(identity).hexdigest()[:24]
