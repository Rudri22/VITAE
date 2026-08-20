from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
from typing import Callable, Optional, Tuple

try:
    from botocore.exceptions import (
        ClientError,
        ConnectionClosedError,
        EndpointConnectionError,
        ReadTimeoutError,
    )
except ImportError:  # pragma: no cover - boto3 is an existing backend dependency
    ClientError = ConnectionClosedError = EndpointConnectionError = ReadTimeoutError = ()

try:
    from .alerting import (
        Alert,
        AlertConflictError,
        AlertRepository,
        AlertRepositoryCorruptionError,
    )
    from .decision_outbox import (
        AlertOutboxEvent,
        OutboxClaimError,
        OutboxDeliveryStatus,
        ProcessingBundleRepository,
    )
except ImportError:
    from alerting import (
        Alert,
        AlertConflictError,
        AlertRepository,
        AlertRepositoryCorruptionError,
    )
    from decision_outbox import (
        AlertOutboxEvent,
        OutboxClaimError,
        OutboxDeliveryStatus,
        ProcessingBundleRepository,
    )


class DeliveryFailureKind(str, Enum):
    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"


@dataclass(frozen=True)
class ClassifiedDeliveryFailure:
    kind: DeliveryFailureKind
    error_code: str


class OutboxDeliveryOutcome(str, Enum):
    DELIVERED = "DELIVERED"
    ALREADY_DELIVERED = "ALREADY_DELIVERED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    DEAD_LETTERED = "DEAD_LETTERED"


@dataclass(frozen=True)
class OutboxDeliveryResult:
    outcome: OutboxDeliveryOutcome
    event: AlertOutboxEvent
    alert: Optional[Alert]
    error_code: Optional[str] = None


class DispatchEventOutcome(str, Enum):
    DELIVERED = "DELIVERED"
    ALREADY_DELIVERED = "ALREADY_DELIVERED"
    CLAIM_CONFLICT = "CLAIM_CONFLICT"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    DEAD_LETTERED = "DEAD_LETTERED"
    SYSTEM_FAILURE = "SYSTEM_FAILURE"


@dataclass(frozen=True)
class DispatchEventResult:
    event_id: str
    outcome: DispatchEventOutcome
    attempt_count: int
    error_code: Optional[str] = None


@dataclass(frozen=True)
class DispatchBatchResult:
    worker_id: str
    started_at: datetime
    finished_at: datetime
    discovered_count: int
    corrupt_quarantined_count: int
    claimed_count: int
    delivered_count: int
    already_delivered_count: int
    claim_conflict_count: int
    retry_scheduled_count: int
    dead_lettered_count: int
    max_attempts_exceeded_count: int
    system_failure_count: int
    event_results: Tuple[DispatchEventResult, ...]


@dataclass(frozen=True)
class OutboxRetryPolicy:
    base_delay: timedelta = timedelta(seconds=5)
    max_delay: timedelta = timedelta(minutes=15)
    max_attempts: int = 96

    def __post_init__(self):
        if self.base_delay <= timedelta(0):
            raise ValueError("base_delay must be positive")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay cannot be less than base_delay")
        if (
            not isinstance(self.max_attempts, int)
            or isinstance(self.max_attempts, bool)
            or self.max_attempts <= 0
        ):
            raise ValueError("max_attempts must be a positive integer")

    def retry_at(self, event: AlertOutboxEvent, failed_at: datetime) -> datetime:
        timestamp = _aware_timestamp(failed_at, "failed_at")
        exponent = max(0, min(event.attempt_count - 1, 30))
        uncapped = self.base_delay.total_seconds() * (2**exponent)
        capped = min(uncapped, self.max_delay.total_seconds())
        digest = sha256(
            f"{event.event_id}:{event.attempt_count}".encode("utf-8")
        ).digest()
        jitter = 0.9 + (int.from_bytes(digest[:2], "big") / 65535) * 0.2
        return timestamp + timedelta(seconds=capped * jitter)


class AlertOutboxDeliveryService:
    """Deliver the stored candidate without recalculating status or alert policy."""

    def __init__(
        self,
        processing_repository: ProcessingBundleRepository,
        alert_repository: AlertRepository,
        *,
        retry_policy: OutboxRetryPolicy = OutboxRetryPolicy(),
    ):
        self._processing_repository = processing_repository
        self._alert_repository = alert_repository
        self._retry_policy = retry_policy

    def deliver(
        self,
        event_id: str,
        *,
        worker_id: str,
        attempted_at: datetime,
        lease_duration: timedelta,
    ) -> OutboxDeliveryResult:
        timestamp = _aware_timestamp(attempted_at, "attempted_at")
        worker = _required_text(worker_id, "worker_id")
        event = self._processing_repository.get_outbox_event(event_id)
        if event is None:
            raise ValueError("Outbox event does not exist")
        if event.delivery_status == OutboxDeliveryStatus.DELIVERED:
            existing = self._alert_repository.get_alert(
                event.alert_candidate.alert_id
            )
            alert = existing or self._alert_repository.save_alert(
                event.alert_candidate
            )
            return OutboxDeliveryResult(
                OutboxDeliveryOutcome.ALREADY_DELIVERED,
                event,
                alert,
            )

        claimed = self._processing_repository.claim_outbox_event(
            event_id,
            worker_id=worker,
            claimed_at=timestamp,
            lease_duration=lease_duration,
        )
        try:
            alert = self._alert_repository.save_alert(claimed.alert_candidate)
        except Exception as error:
            failure = classify_delivery_failure(error)
            if (
                failure.kind == DeliveryFailureKind.PERMANENT
                or claimed.attempt_count >= self._retry_policy.max_attempts
            ):
                error_code = (
                    failure.error_code
                    if failure.kind == DeliveryFailureKind.PERMANENT
                    else "MAX_DELIVERY_ATTEMPTS_EXCEEDED"
                )
                dead = self._processing_repository.mark_outbox_dead_letter(
                    event_id,
                    worker_id=worker,
                    failed_at=timestamp,
                    error_code=error_code,
                )
                return OutboxDeliveryResult(
                    OutboxDeliveryOutcome.DEAD_LETTERED,
                    dead,
                    None,
                    error_code,
                )
            pending = self._processing_repository.release_outbox_event(
                event_id,
                worker_id=worker,
                released_at=timestamp,
                retry_at=self._retry_policy.retry_at(claimed, timestamp),
                error_code=failure.error_code,
            )
            return OutboxDeliveryResult(
                OutboxDeliveryOutcome.RETRY_SCHEDULED,
                pending,
                None,
                failure.error_code,
            )

        delivered = self._processing_repository.mark_outbox_delivered(
            event_id,
            worker_id=worker,
            delivered_at=timestamp,
        )
        return OutboxDeliveryResult(
            OutboxDeliveryOutcome.DELIVERED,
            delivered,
            alert,
        )


class OutboxDispatcher:
    def __init__(
        self,
        processing_repository: ProcessingBundleRepository,
        alert_repository: AlertRepository,
        *,
        worker_id: str,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        lease_duration: timedelta = timedelta(minutes=5),
        retry_policy: OutboxRetryPolicy = OutboxRetryPolicy(),
    ):
        self._processing_repository = processing_repository
        self._worker_id = _required_text(worker_id, "worker_id")
        self._clock = clock
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        self._lease_duration = lease_duration
        self._delivery = AlertOutboxDeliveryService(
            processing_repository,
            alert_repository,
            retry_policy=retry_policy,
        )

    def run_once(self, *, batch_size: int = 50) -> DispatchBatchResult:
        started = _aware_timestamp(self._clock(), "clock")
        discovery = self._processing_repository.discover_dispatchable_outbox_events(
            started,
            limit=_positive_integer(batch_size, "batch_size"),
        )
        outcomes = []
        claimed_count = 0
        for event in discovery.events:
            try:
                result = self._delivery.deliver(
                    event.event_id,
                    worker_id=self._worker_id,
                    attempted_at=started,
                    lease_duration=self._lease_duration,
                )
                claimed_count += int(
                    result.outcome != OutboxDeliveryOutcome.ALREADY_DELIVERED
                )
                outcomes.append(
                    DispatchEventResult(
                        event_id=event.event_id,
                        outcome=DispatchEventOutcome(result.outcome.value),
                        attempt_count=result.event.attempt_count,
                        error_code=result.error_code,
                    )
                )
            except OutboxClaimError:
                outcomes.append(
                    DispatchEventResult(
                        event.event_id,
                        DispatchEventOutcome.CLAIM_CONFLICT,
                        event.attempt_count,
                    )
                )
            except Exception:
                outcomes.append(
                    DispatchEventResult(
                        event.event_id,
                        DispatchEventOutcome.SYSTEM_FAILURE,
                        event.attempt_count,
                        "DISPATCH_SYSTEM_FAILURE",
                    )
                )
        finished = _aware_timestamp(self._clock(), "clock")
        return DispatchBatchResult(
            worker_id=self._worker_id,
            started_at=started,
            finished_at=finished,
            discovered_count=len(discovery.events),
            corrupt_quarantined_count=discovery.corrupt_quarantined_count,
            claimed_count=claimed_count,
            delivered_count=_count(outcomes, DispatchEventOutcome.DELIVERED),
            already_delivered_count=_count(
                outcomes, DispatchEventOutcome.ALREADY_DELIVERED
            ),
            claim_conflict_count=_count(
                outcomes, DispatchEventOutcome.CLAIM_CONFLICT
            ),
            retry_scheduled_count=_count(
                outcomes, DispatchEventOutcome.RETRY_SCHEDULED
            ),
            dead_lettered_count=_count(
                outcomes, DispatchEventOutcome.DEAD_LETTERED
            ),
            max_attempts_exceeded_count=sum(
                outcome.error_code == "MAX_DELIVERY_ATTEMPTS_EXCEEDED"
                for outcome in outcomes
            ),
            system_failure_count=_count(
                outcomes, DispatchEventOutcome.SYSTEM_FAILURE
            ),
            event_results=tuple(outcomes),
        )


def classify_delivery_failure(error: Exception) -> ClassifiedDeliveryFailure:
    if isinstance(error, AlertConflictError):
        return ClassifiedDeliveryFailure(
            DeliveryFailureKind.PERMANENT,
            "ALERT_CREATION_CONFLICT",
        )
    if isinstance(error, AlertRepositoryCorruptionError):
        return ClassifiedDeliveryFailure(
            DeliveryFailureKind.PERMANENT,
            "ALERT_REPOSITORY_CORRUPTION",
        )
    if isinstance(error, (ValueError, TypeError)):
        return ClassifiedDeliveryFailure(
            DeliveryFailureKind.PERMANENT,
            "ALERT_CANDIDATE_INVALID",
        )
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "")
        if code in {
            "ProvisionedThroughputExceededException",
            "RequestLimitExceeded",
            "ThrottlingException",
        }:
            stable_code = "ALERT_STORE_THROTTLED"
        elif code in {"TransactionConflictException", "TransactionCanceledException"}:
            stable_code = "ALERT_STORE_TRANSACTION_CONFLICT"
        else:
            stable_code = "ALERT_STORE_UNAVAILABLE"
        return ClassifiedDeliveryFailure(DeliveryFailureKind.TRANSIENT, stable_code)
    if isinstance(error, (EndpointConnectionError, ConnectionClosedError, ReadTimeoutError)):
        return ClassifiedDeliveryFailure(
            DeliveryFailureKind.TRANSIENT,
            "ALERT_STORE_UNAVAILABLE",
        )
    return ClassifiedDeliveryFailure(
        DeliveryFailureKind.TRANSIENT,
        "DELIVERY_UNEXPECTED_ERROR",
    )


def _count(outcomes, value):
    return sum(outcome.outcome == value for outcome in outcomes)


def _required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _positive_integer(value, field):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _aware_timestamp(value, field):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field} must be timezone-aware")
    return value
