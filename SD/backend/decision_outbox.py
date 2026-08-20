from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from hashlib import sha256
from math import isfinite
from typing import Dict, Optional, Protocol, Tuple, runtime_checkable

try:
    from .alerting import Alert, AlertStatus
    from .risk_rules import ApplicationStatus
    from .state_repository import (
        InMemoryTelemetryStateRepository,
        LiveState,
        StateIntegrityError,
        TelemetryRecord,
        TelemetryStateRepository,
        validate_telemetry_state_commit,
    )
except ImportError:
    from alerting import Alert, AlertStatus
    from risk_rules import ApplicationStatus
    from state_repository import (
        InMemoryTelemetryStateRepository,
        LiveState,
        StateIntegrityError,
        TelemetryRecord,
        TelemetryStateRepository,
        validate_telemetry_state_commit,
    )


DETERMINISTIC_ENGINE_VERSION = "deterministic-status-v1"
DETERMINISTIC_ALERT_POLICY_VERSION = "deterministic-alert-policy-v1"
DETERMINISTIC_ALERT_EVENT_TYPE = "DETERMINISTIC_ALERT"


@dataclass(frozen=True)
class StatusDecisionRecord:
    decision_id: str
    trip_id: str
    lot_trip_id: str
    device_id: str
    sample_id: str
    sample_timestamp: datetime
    product_id: str
    product_rule_version: str
    engine_version: str
    previous_live_state_revision: Optional[int]
    resulting_live_state_revision: int
    status: ApplicationStatus
    reason_code: str
    active_rule_id: Optional[str]
    excursion_started_at: Optional[datetime]
    excursion_episode_duration_minutes: float
    cumulative_excursion_duration_minutes: float
    excursion_utilization: Optional[float]


class OutboxDeliveryStatus(str, Enum):
    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    DELIVERED = "DELIVERED"
    DEAD_LETTER = "DEAD_LETTER"


@dataclass(frozen=True)
class AlertOutboxEvent:
    event_id: str
    decision_id: str
    trip_id: str
    lot_trip_id: str
    device_id: str
    sample_id: str
    event_type: str
    alert_policy_version: str
    alert_candidate: Alert
    created_at: datetime
    delivery_status: OutboxDeliveryStatus
    attempt_count: int
    available_at: datetime
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    last_error_code: Optional[str] = None
    dead_lettered_at: Optional[datetime] = None
    dead_lettered_by: Optional[str] = None


@dataclass(frozen=True)
class OutboxDiscoveryBatch:
    events: Tuple[AlertOutboxEvent, ...]
    corrupt_quarantined_count: int = 0


class DecisionOutboxError(StateIntegrityError):
    pass


class OutboxClaimError(DecisionOutboxError):
    pass


class OutboxTransitionError(DecisionOutboxError):
    pass


@runtime_checkable
class ProcessingBundleRepository(TelemetryStateRepository, Protocol):
    def commit_processing_bundle(
        self,
        record: TelemetryRecord,
        new_state: LiveState,
        decision_record: StatusDecisionRecord,
        alert_outbox_event: Optional[AlertOutboxEvent],
        expected_revision: Optional[int],
    ) -> None:
        ...

    def get_decision(self, decision_id: str) -> Optional[StatusDecisionRecord]:
        ...

    def get_decision_history(
        self, lot_trip_id: str
    ) -> Tuple[StatusDecisionRecord, ...]:
        ...

    def get_outbox_event(self, event_id: str) -> Optional[AlertOutboxEvent]:
        ...

    def list_dispatchable_outbox_events(
        self, as_of: datetime
    ) -> Tuple[AlertOutboxEvent, ...]:
        ...

    def discover_dispatchable_outbox_events(
        self,
        as_of: datetime,
        *,
        limit: int,
    ) -> OutboxDiscoveryBatch:
        ...

    def claim_outbox_event(
        self,
        event_id: str,
        *,
        worker_id: str,
        claimed_at: datetime,
        lease_duration: timedelta,
    ) -> AlertOutboxEvent:
        ...

    def release_outbox_event(
        self,
        event_id: str,
        *,
        worker_id: str,
        released_at: datetime,
        retry_at: datetime,
        error_code: str,
    ) -> AlertOutboxEvent:
        ...

    def mark_outbox_delivered(
        self,
        event_id: str,
        *,
        worker_id: str,
        delivered_at: datetime,
    ) -> AlertOutboxEvent:
        ...

    def mark_outbox_dead_letter(
        self,
        event_id: str,
        *,
        worker_id: str,
        failed_at: datetime,
        error_code: str,
    ) -> AlertOutboxEvent:
        ...


def decision_record_from_processing_result(
    result,
    *,
    engine_version: str = DETERMINISTIC_ENGINE_VERSION,
) -> StatusDecisionRecord:
    record = result.telemetry_record
    state = result.live_state
    decision = result.decision
    previous = result.previous_live_state
    value = StatusDecisionRecord(
        decision_id=decision_id_for(record.lot_trip_id, record.device_id, record.sample_id),
        trip_id=record.trip_id,
        lot_trip_id=record.lot_trip_id,
        device_id=record.device_id,
        sample_id=record.sample_id,
        sample_timestamp=record.timestamp,
        product_id=state.product_id,
        product_rule_version=state.product_rule_version,
        engine_version=_required_text(engine_version, "engine_version"),
        previous_live_state_revision=None if previous is None else previous.revision,
        resulting_live_state_revision=state.revision,
        status=decision.status,
        reason_code=decision.reason_code,
        active_rule_id=decision.active_rule_id,
        excursion_started_at=decision.excursion_started_at,
        excursion_episode_duration_minutes=decision.excursion_episode_duration_minutes,
        cumulative_excursion_duration_minutes=decision.cumulative_excursion_duration_minutes,
        excursion_utilization=decision.excursion_utilization,
    )
    _validate_decision_record(value)
    return value


def alert_outbox_event_from_candidate(
    decision_record: StatusDecisionRecord,
    alert_candidate: Alert,
    *,
    alert_policy_version: str = DETERMINISTIC_ALERT_POLICY_VERSION,
) -> AlertOutboxEvent:
    value = AlertOutboxEvent(
        event_id=outbox_event_id_for(
            decision_record.decision_id,
            alert_candidate.alert_id,
        ),
        decision_id=decision_record.decision_id,
        trip_id=decision_record.trip_id,
        lot_trip_id=decision_record.lot_trip_id,
        device_id=decision_record.device_id,
        sample_id=decision_record.sample_id,
        event_type=DETERMINISTIC_ALERT_EVENT_TYPE,
        alert_policy_version=_required_text(
            alert_policy_version, "alert_policy_version"
        ),
        alert_candidate=alert_candidate,
        created_at=alert_candidate.detected_at,
        delivery_status=OutboxDeliveryStatus.PENDING,
        attempt_count=0,
        available_at=alert_candidate.detected_at,
        dead_lettered_at=None,
        dead_lettered_by=None,
    )
    _validate_outbox_event(value)
    _validate_outbox_matches_decision(value, decision_record)
    return value


def decision_id_for(lot_trip_id: str, device_id: str, sample_id: str) -> str:
    lot_trip = _required_text(lot_trip_id, "lot_trip_id")
    encoded_lot_trip = urlsafe_b64encode(lot_trip.encode("utf-8")).decode(
        "ascii"
    ).rstrip("=")
    return (
        f"decision-{encoded_lot_trip}-"
        f"{_identity_hash(lot_trip, device_id, sample_id)}"
    )


def lot_trip_id_from_decision_id(decision_id: str) -> str:
    value = _required_text(decision_id, "decision_id")
    if not value.startswith("decision-"):
        raise DecisionOutboxError("decision_id does not contain a lot trip identity")
    encoded_and_hash = value[len("decision-") :]
    try:
        encoded_lot_trip, hash_suffix = encoded_and_hash.rsplit("-", 1)
        if len(hash_suffix) != 24:
            raise ValueError
        padding = "=" * (-len(encoded_lot_trip) % 4)
        lot_trip_id = urlsafe_b64decode(encoded_lot_trip + padding).decode("utf-8")
    except (UnicodeDecodeError, ValueError) as error:
        raise DecisionOutboxError(
            "decision_id does not contain a valid lot trip identity"
        ) from error
    return _required_text(lot_trip_id, "lot_trip_id")


def outbox_event_id_for(decision_id: str, alert_id: str) -> str:
    return "outbox-" + _identity_hash(
        DETERMINISTIC_ALERT_EVENT_TYPE,
        decision_id,
        alert_id,
    )


def validate_status_decision_record(value: StatusDecisionRecord) -> None:
    _validate_decision_record(value)


def validate_alert_outbox_event(value: AlertOutboxEvent) -> None:
    _validate_outbox_event(value)


def validate_processing_bundle_commit(
    *,
    record: TelemetryRecord,
    new_state: LiveState,
    decision_record: StatusDecisionRecord,
    alert_outbox_event: Optional[AlertOutboxEvent],
    current_state: Optional[LiveState],
    expected_revision: Optional[int],
    sample_exists: bool,
) -> None:
    """Validate the complete transition before any repository writes occur."""
    validate_telemetry_state_commit(
        record=record,
        new_state=new_state,
        current_state=current_state,
        expected_revision=expected_revision,
        sample_exists=sample_exists,
    )
    _validate_decision_record(decision_record)
    _validate_decision_matches_transition(
        decision_record,
        record,
        new_state,
        current_state,
    )
    if alert_outbox_event is not None:
        _validate_outbox_event(alert_outbox_event)
        _validate_outbox_matches_decision(alert_outbox_event, decision_record)


class InMemoryProcessingBundleRepository(
    InMemoryTelemetryStateRepository,
    ProcessingBundleRepository,
):
    """Extend the one inherited telemetry/state store with decision and outbox data."""

    def __init__(self):
        super().__init__()
        self._decisions_by_id: Dict[str, StatusDecisionRecord] = {}
        self._decision_history: Dict[str, list] = {}
        self._outbox_events: Dict[str, AlertOutboxEvent] = {}
        self._outbox_record_versions: Dict[str, int] = {}

    def commit_processing_bundle(
        self,
        record: TelemetryRecord,
        new_state: LiveState,
        decision_record: StatusDecisionRecord,
        alert_outbox_event: Optional[AlertOutboxEvent],
        expected_revision: Optional[int],
    ) -> None:
        with self._lock:
            current_state = self._live_states.get(record.lot_trip_id)
            validate_processing_bundle_commit(
                record=record,
                new_state=new_state,
                decision_record=decision_record,
                alert_outbox_event=alert_outbox_event,
                current_state=current_state,
                expected_revision=expected_revision,
                sample_exists=(record.device_id, record.sample_id)
                in self._sample_identities,
            )
            if decision_record.decision_id in self._decisions_by_id:
                raise DecisionOutboxError("Decision ID is already committed")
            if alert_outbox_event is not None:
                if alert_outbox_event.event_id in self._outbox_events:
                    raise DecisionOutboxError("Outbox event ID is already committed")

            self._history.setdefault(record.lot_trip_id, []).append(record)
            self._live_states[record.lot_trip_id] = new_state
            self._sample_identities.add((record.device_id, record.sample_id))
            self._decisions_by_id[decision_record.decision_id] = decision_record
            self._decision_history.setdefault(record.lot_trip_id, []).append(
                decision_record
            )
            if alert_outbox_event is not None:
                self._outbox_events[alert_outbox_event.event_id] = alert_outbox_event
                self._outbox_record_versions[alert_outbox_event.event_id] = 1

    def get_decision(self, decision_id: str) -> Optional[StatusDecisionRecord]:
        with self._lock:
            return self._decisions_by_id.get(_required_text(decision_id, "decision_id"))

    def get_decision_history(
        self, lot_trip_id: str
    ) -> Tuple[StatusDecisionRecord, ...]:
        with self._lock:
            return tuple(
                self._decision_history.get(_required_text(lot_trip_id, "lot_trip_id"), ())
            )

    def get_outbox_event(self, event_id: str) -> Optional[AlertOutboxEvent]:
        with self._lock:
            return self._outbox_events.get(_required_text(event_id, "event_id"))

    def list_dispatchable_outbox_events(
        self, as_of: datetime
    ) -> Tuple[AlertOutboxEvent, ...]:
        return self.discover_dispatchable_outbox_events(
            as_of,
            limit=max(1, len(self._outbox_events)),
        ).events

    def discover_dispatchable_outbox_events(
        self,
        as_of: datetime,
        *,
        limit: int,
    ) -> OutboxDiscoveryBatch:
        timestamp = _aware_timestamp(as_of, "as_of")
        bounded_limit = _positive_integer(limit, "limit")
        with self._lock:
            events = tuple(self._outbox_events.values())
        dispatchable = (
            event
            for event in events
            if (
                event.delivery_status == OutboxDeliveryStatus.PENDING
                and event.available_at <= timestamp
            )
            or (
                event.delivery_status == OutboxDeliveryStatus.IN_FLIGHT
                and event.lease_expires_at is not None
                and event.lease_expires_at <= timestamp
            )
        )
        ordered = tuple(
            sorted(dispatchable, key=lambda event: (_effective_due_at(event), event.event_id))
        )
        return OutboxDiscoveryBatch(events=ordered[:bounded_limit])

    def claim_outbox_event(
        self,
        event_id: str,
        *,
        worker_id: str,
        claimed_at: datetime,
        lease_duration: timedelta,
    ) -> AlertOutboxEvent:
        worker = _required_text(worker_id, "worker_id")
        timestamp = _aware_timestamp(claimed_at, "claimed_at")
        if not isinstance(lease_duration, timedelta) or lease_duration <= timedelta(0):
            raise OutboxClaimError("lease_duration must be positive")
        with self._lock:
            event = self._require_outbox_event(event_id)
            available = (
                event.delivery_status == OutboxDeliveryStatus.PENDING
                and event.available_at <= timestamp
            ) or (
                event.delivery_status == OutboxDeliveryStatus.IN_FLIGHT
                and event.lease_expires_at is not None
                and event.lease_expires_at <= timestamp
            )
            if not available:
                raise OutboxClaimError("Outbox event is not available for claim")
            claimed = replace(
                event,
                delivery_status=OutboxDeliveryStatus.IN_FLIGHT,
                attempt_count=event.attempt_count + 1,
                lease_owner=worker,
                lease_expires_at=timestamp + lease_duration,
            )
            self._outbox_events[event.event_id] = claimed
            self._increment_outbox_record_version(event.event_id)
            return claimed

    def release_outbox_event(
        self,
        event_id: str,
        *,
        worker_id: str,
        released_at: datetime,
        retry_at: datetime,
        error_code: str,
    ) -> AlertOutboxEvent:
        worker = _required_text(worker_id, "worker_id")
        released = _aware_timestamp(released_at, "released_at")
        retry = _aware_timestamp(retry_at, "retry_at")
        error = _required_text(error_code, "error_code")
        if retry < released:
            raise OutboxTransitionError("retry_at cannot predate released_at")
        with self._lock:
            event = self._require_owned_lease(event_id, worker, released)
            pending = replace(
                event,
                delivery_status=OutboxDeliveryStatus.PENDING,
                available_at=retry,
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=error,
            )
            self._outbox_events[event.event_id] = pending
            self._increment_outbox_record_version(event.event_id)
            return pending

    def mark_outbox_delivered(
        self,
        event_id: str,
        *,
        worker_id: str,
        delivered_at: datetime,
    ) -> AlertOutboxEvent:
        worker = _required_text(worker_id, "worker_id")
        timestamp = _aware_timestamp(delivered_at, "delivered_at")
        with self._lock:
            event = self._require_owned_lease(event_id, worker, timestamp)
            delivered = replace(
                event,
                delivery_status=OutboxDeliveryStatus.DELIVERED,
                lease_owner=None,
                lease_expires_at=None,
                delivered_at=timestamp,
                last_error_code=None,
            )
            self._outbox_events[event.event_id] = delivered
            self._increment_outbox_record_version(event.event_id)
            return delivered

    def mark_outbox_dead_letter(
        self,
        event_id: str,
        *,
        worker_id: str,
        failed_at: datetime,
        error_code: str,
    ) -> AlertOutboxEvent:
        worker = _required_text(worker_id, "worker_id")
        timestamp = _aware_timestamp(failed_at, "failed_at")
        error = _required_text(error_code, "error_code")
        with self._lock:
            event = self._require_owned_lease(event_id, worker, timestamp)
            dead_letter = replace(
                event,
                delivery_status=OutboxDeliveryStatus.DEAD_LETTER,
                lease_owner=None,
                lease_expires_at=None,
                delivered_at=None,
                last_error_code=error,
                dead_lettered_at=timestamp,
                dead_lettered_by=worker,
            )
            _validate_outbox_event(dead_letter)
            self._outbox_events[event.event_id] = dead_letter
            self._increment_outbox_record_version(event.event_id)
            return dead_letter

    def _increment_outbox_record_version(self, event_id):
        self._outbox_record_versions[event_id] = (
            self._outbox_record_versions[event_id] + 1
        )

    def _require_outbox_event(self, event_id):
        normalized = _required_text(event_id, "event_id")
        event = self._outbox_events.get(normalized)
        if event is None:
            raise OutboxTransitionError("Outbox event does not exist")
        return event

    def _require_owned_lease(self, event_id, worker_id, timestamp):
        event = self._require_outbox_event(event_id)
        if (
            event.delivery_status != OutboxDeliveryStatus.IN_FLIGHT
            or event.lease_owner != worker_id
            or event.lease_expires_at is None
            or timestamp >= event.lease_expires_at
        ):
            raise OutboxTransitionError("Outbox event lease is not owned and active")
        return event


def _validate_decision_record(value):
    for field in (
        "decision_id",
        "trip_id",
        "lot_trip_id",
        "device_id",
        "sample_id",
        "product_id",
        "product_rule_version",
        "engine_version",
        "reason_code",
    ):
        _required_text(getattr(value, field), field)
    _aware_timestamp(value.sample_timestamp, "sample_timestamp")
    if value.excursion_started_at is not None:
        _aware_timestamp(value.excursion_started_at, "excursion_started_at")
    if not isinstance(value.status, ApplicationStatus):
        raise DecisionOutboxError("status must be an ApplicationStatus")
    if value.previous_live_state_revision is not None:
        _positive_integer(
            value.previous_live_state_revision, "previous_live_state_revision"
        )
    _positive_integer(
        value.resulting_live_state_revision, "resulting_live_state_revision"
    )
    if value.resulting_live_state_revision != (
        1
        if value.previous_live_state_revision is None
        else value.previous_live_state_revision + 1
    ):
        raise DecisionOutboxError("Decision revisions must describe one transition")
    for field in (
        "excursion_episode_duration_minutes",
        "cumulative_excursion_duration_minutes",
    ):
        _non_negative_number(getattr(value, field), field)
    if value.excursion_utilization is not None:
        _non_negative_number(
            value.excursion_utilization, "excursion_utilization"
        )


def _validate_decision_matches_transition(decision, record, state, previous_state):
    expected_previous_revision = None if previous_state is None else previous_state.revision
    if (
        decision.trip_id != record.trip_id
        or decision.lot_trip_id != record.lot_trip_id
        or decision.device_id != record.device_id
        or decision.sample_id != record.sample_id
        or decision.sample_timestamp != record.timestamp
        or decision.product_id != state.product_id
        or decision.product_rule_version != state.product_rule_version
        or decision.previous_live_state_revision != expected_previous_revision
        or decision.resulting_live_state_revision != state.revision
        or decision.status != state.status
        or decision.reason_code != state.reason_code
        or decision.active_rule_id != state.active_rule_id
        or decision.excursion_started_at != state.excursion_started_at
        or decision.excursion_episode_duration_minutes
        != state.excursion_episode_duration_minutes
        or decision.cumulative_excursion_duration_minutes
        != state.cumulative_excursion_duration_minutes
        or decision.excursion_utilization != state.excursion_utilization
    ):
        raise DecisionOutboxError(
            "StatusDecisionRecord does not match the telemetry transition"
        )


def _validate_outbox_event(value):
    for field in (
        "event_id",
        "decision_id",
        "trip_id",
        "lot_trip_id",
        "device_id",
        "sample_id",
        "event_type",
        "alert_policy_version",
    ):
        _required_text(getattr(value, field), field)
    _aware_timestamp(value.created_at, "created_at")
    _aware_timestamp(value.available_at, "available_at")
    if value.available_at < value.created_at:
        raise DecisionOutboxError("available_at cannot predate created_at")
    if not isinstance(value.alert_candidate, Alert):
        raise DecisionOutboxError("alert_candidate must be an Alert")
    if not isinstance(value.delivery_status, OutboxDeliveryStatus):
        raise DecisionOutboxError("delivery_status must be an OutboxDeliveryStatus")
    if not isinstance(value.attempt_count, int) or isinstance(value.attempt_count, bool) or value.attempt_count < 0:
        raise DecisionOutboxError("attempt_count must be a non-negative integer")
    if value.lease_expires_at is not None:
        _aware_timestamp(value.lease_expires_at, "lease_expires_at")
    if value.delivered_at is not None:
        _aware_timestamp(value.delivered_at, "delivered_at")
        if value.delivered_at < value.created_at:
            raise DecisionOutboxError("delivered_at cannot predate created_at")
    if value.dead_lettered_at is not None:
        _aware_timestamp(value.dead_lettered_at, "dead_lettered_at")
        if value.dead_lettered_at < value.created_at:
            raise DecisionOutboxError("dead_lettered_at cannot predate created_at")
    if value.delivery_status == OutboxDeliveryStatus.PENDING and (
        value.lease_owner is not None
        or value.lease_expires_at is not None
        or value.delivered_at is not None
        or value.dead_lettered_at is not None
        or value.dead_lettered_by is not None
    ):
        raise DecisionOutboxError("PENDING outbox metadata is inconsistent")
    if value.delivery_status == OutboxDeliveryStatus.IN_FLIGHT and (
        not value.lease_owner
        or value.lease_expires_at is None
        or value.delivered_at is not None
        or value.dead_lettered_at is not None
        or value.dead_lettered_by is not None
    ):
        raise DecisionOutboxError("IN_FLIGHT outbox metadata is inconsistent")
    if value.delivery_status == OutboxDeliveryStatus.DELIVERED and (
        value.lease_owner is not None
        or value.lease_expires_at is not None
        or value.delivered_at is None
        or value.dead_lettered_at is not None
        or value.dead_lettered_by is not None
    ):
        raise DecisionOutboxError("DELIVERED outbox metadata is inconsistent")
    if value.delivery_status == OutboxDeliveryStatus.DEAD_LETTER and (
        value.lease_owner is not None
        or value.lease_expires_at is not None
        or value.delivered_at is not None
        or value.dead_lettered_at is None
        or not value.dead_lettered_by
        or not value.last_error_code
    ):
        raise DecisionOutboxError("DEAD_LETTER outbox metadata is inconsistent")


def _effective_due_at(event):
    if event.delivery_status == OutboxDeliveryStatus.IN_FLIGHT:
        return event.lease_expires_at
    return event.available_at


def _positive_integer(value, field):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _validate_outbox_matches_decision(event, decision):
    alert = event.alert_candidate
    if (
        event.event_type != DETERMINISTIC_ALERT_EVENT_TYPE
        or event.decision_id != decision.decision_id
        or event.trip_id != decision.trip_id
        or event.lot_trip_id != decision.lot_trip_id
        or event.device_id != decision.device_id
        or event.sample_id != decision.sample_id
        or alert.trip_id != decision.trip_id
        or alert.lot_trip_id != decision.lot_trip_id
        or alert.device_id != decision.device_id
        or alert.sample_id != decision.sample_id
        or alert.source_status != decision.status
        or alert.reason_code != decision.reason_code
        or alert.active_rule_id != decision.active_rule_id
        or alert.detected_at != decision.sample_timestamp
        or alert.status != AlertStatus.OPEN
    ):
        raise DecisionOutboxError(
            "AlertOutboxEvent does not match its StatusDecisionRecord"
        )


def _identity_hash(*values):
    identity = "|".join(_required_text(value, "identity") for value in values)
    return sha256(identity.encode("utf-8")).hexdigest()[:24]


def _required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise DecisionOutboxError(f"{field} must be a non-empty string")
    return value.strip()


def _aware_timestamp(value, field):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DecisionOutboxError(f"{field} must be timezone-aware")
    return value


def _positive_integer(value, field):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise DecisionOutboxError(f"{field} must be a positive integer")
    return value


def _non_negative_number(value, field):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise DecisionOutboxError(f"{field} must be a finite non-negative number")
    return float(value)
