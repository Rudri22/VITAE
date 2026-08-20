from dataclasses import asdict, dataclass
from datetime import timedelta
import json
import os
from threading import RLock
from typing import Any, Mapping, Optional

try:
    from .dynamo_alert_repository import DynamoAlertRepository
    from .dynamo_telemetry_repository import DynamoTelemetryStateRepository
    from .outbox_dispatcher import (
        DispatchBatchResult,
        OutboxDispatcher,
        OutboxRetryPolicy,
    )
except ImportError:
    from dynamo_alert_repository import DynamoAlertRepository
    from dynamo_telemetry_repository import DynamoTelemetryStateRepository
    from outbox_dispatcher import (
        DispatchBatchResult,
        OutboxDispatcher,
        OutboxRetryPolicy,
    )


class OutboxWorkerConfigurationError(ValueError):
    pass


class OutboxWorkerInvocationError(RuntimeError):
    def __init__(self, message: str, *, result: Optional[dict] = None):
        super().__init__(message)
        self.result = result


MIN_RUN_BUDGET_MS = 10_000


@dataclass(frozen=True)
class OutboxWorkerConfig:
    aws_region: str
    telemetry_table: str
    alert_table: str
    key_namespace: str = ""
    batch_size: int = 25
    lease_seconds: int = 120
    base_delay_seconds: int = 5
    max_delay_seconds: int = 900
    max_attempts: int = 96

    def __post_init__(self):
        for name in ("aws_region", "telemetry_table", "alert_table"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise OutboxWorkerConfigurationError(f"{name} is required")
        for name, minimum, maximum in (
            ("batch_size", 1, 100),
            ("lease_seconds", 1, 900),
            ("base_delay_seconds", 1, 900),
            ("max_delay_seconds", 1, 86_400),
            ("max_attempts", 1, 10_000),
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < minimum
                or value > maximum
            ):
                raise OutboxWorkerConfigurationError(
                    f"{name} must be between {minimum} and {maximum}"
                )
        if self.max_delay_seconds < self.base_delay_seconds:
            raise OutboxWorkerConfigurationError(
                "max_delay_seconds cannot be less than base_delay_seconds"
            )

    @classmethod
    def from_environment(
        cls,
        environment: Optional[Mapping[str, str]] = None,
    ) -> "OutboxWorkerConfig":
        values = os.environ if environment is None else environment
        region = _required_setting(values, "VITAE_AWS_REGION")
        telemetry_table = _required_setting(values, "VITAE_TELEMETRY_TABLE")
        alert_table = _required_setting(values, "VITAE_ALERT_TABLE")
        return cls(
            aws_region=region,
            telemetry_table=telemetry_table,
            alert_table=alert_table,
            key_namespace=str(
                values.get("VITAE_DYNAMODB_KEY_NAMESPACE", "")
            ).strip(),
            batch_size=_integer_setting(
                values,
                "VITAE_OUTBOX_BATCH_SIZE",
                default=25,
                minimum=1,
                maximum=100,
            ),
            lease_seconds=_integer_setting(
                values,
                "VITAE_OUTBOX_LEASE_SECONDS",
                default=120,
                minimum=1,
                maximum=900,
            ),
            base_delay_seconds=_integer_setting(
                values,
                "VITAE_OUTBOX_BASE_DELAY_SECONDS",
                default=5,
                minimum=1,
                maximum=900,
            ),
            max_delay_seconds=_integer_setting(
                values,
                "VITAE_OUTBOX_MAX_DELAY_SECONDS",
                default=900,
                minimum=1,
                maximum=86_400,
            ),
            max_attempts=_integer_setting(
                values,
                "VITAE_OUTBOX_MAX_ATTEMPTS",
                default=96,
                minimum=1,
                maximum=10_000,
            ),
        ).validated()

    def validated(self) -> "OutboxWorkerConfig":
        return self

    @property
    def lease_duration(self) -> timedelta:
        return timedelta(seconds=self.lease_seconds)

    @property
    def retry_policy(self) -> OutboxRetryPolicy:
        return OutboxRetryPolicy(
            base_delay=timedelta(seconds=self.base_delay_seconds),
            max_delay=timedelta(seconds=self.max_delay_seconds),
            max_attempts=self.max_attempts,
        )


@dataclass(frozen=True)
class OutboxWorkerRuntime:
    config: OutboxWorkerConfig
    telemetry_repository: DynamoTelemetryStateRepository
    alert_repository: DynamoAlertRepository


_RUNTIME_LOCK = RLock()
_CACHED_RUNTIME: Optional[OutboxWorkerRuntime] = None


def lambda_handler(event: Mapping[str, Any], context) -> dict:
    if not isinstance(event, Mapping):
        raise OutboxWorkerInvocationError("Lambda event must be a mapping")
    config = OutboxWorkerConfig.from_environment()
    remaining_time = _remaining_time_ms(context)
    if remaining_time < MIN_RUN_BUDGET_MS:
        raise OutboxWorkerInvocationError(
            "Insufficient Lambda execution time remains before dispatch"
        )
    request_id = _required_context_text(context, "aws_request_id")
    runtime = _get_runtime(config)
    dispatcher = OutboxDispatcher(
        runtime.telemetry_repository,
        runtime.alert_repository,
        worker_id=f"lambda:{request_id}",
        lease_duration=config.lease_duration,
        retry_policy=config.retry_policy,
    )
    result = dispatcher.run_once(batch_size=config.batch_size)
    response = serialize_dispatch_batch_result(result, request_id=request_id)
    print(json.dumps({"event": "vitae.outbox.dispatch", **response}, sort_keys=True))
    if result.system_failure_count:
        raise OutboxWorkerInvocationError(
            "Outbox dispatch completed with system failures",
            result=response,
        )
    return response


def build_worker_runtime(
    config: OutboxWorkerConfig,
    *,
    dynamodb_client=None,
) -> OutboxWorkerRuntime:
    validated = config.validated()
    client = dynamodb_client or _build_dynamodb_client(validated)
    _require_table(client, validated.telemetry_table, "telemetry")
    _require_table(client, validated.alert_table, "alert")
    return OutboxWorkerRuntime(
        config=validated,
        telemetry_repository=DynamoTelemetryStateRepository(
            client,
            validated.telemetry_table,
            key_namespace=validated.key_namespace,
        ),
        alert_repository=DynamoAlertRepository(
            client,
            validated.alert_table,
            key_namespace=validated.key_namespace,
        ),
    )


def serialize_dispatch_batch_result(
    result: DispatchBatchResult,
    *,
    request_id: str,
) -> dict:
    return {
        "schemaVersion": 1,
        "requestId": request_id,
        "workerId": result.worker_id,
        "startedAt": result.started_at.isoformat(),
        "finishedAt": result.finished_at.isoformat(),
        "discoveredCount": result.discovered_count,
        "corruptQuarantinedCount": result.corrupt_quarantined_count,
        "claimedCount": result.claimed_count,
        "deliveredCount": result.delivered_count,
        "alreadyDeliveredCount": result.already_delivered_count,
        "claimConflictCount": result.claim_conflict_count,
        "retryScheduledCount": result.retry_scheduled_count,
        "deadLetteredCount": result.dead_lettered_count,
        "maxAttemptsExceededCount": result.max_attempts_exceeded_count,
        "systemFailureCount": result.system_failure_count,
        "eventResults": [
            {
                **asdict(event_result),
                "outcome": event_result.outcome.value,
            }
            for event_result in result.event_results
        ],
    }


def _get_runtime(config: OutboxWorkerConfig) -> OutboxWorkerRuntime:
    global _CACHED_RUNTIME
    with _RUNTIME_LOCK:
        if _CACHED_RUNTIME is None or _CACHED_RUNTIME.config != config:
            _CACHED_RUNTIME = build_worker_runtime(config)
        return _CACHED_RUNTIME


def _reset_runtime_cache_for_tests() -> None:
    global _CACHED_RUNTIME
    with _RUNTIME_LOCK:
        _CACHED_RUNTIME = None


def _build_dynamodb_client(config: OutboxWorkerConfig):
    try:
        import boto3
    except ImportError as error:  # pragma: no cover - deployment dependency
        raise OutboxWorkerConfigurationError(
            "boto3 is required by the outbox worker"
        ) from error
    try:
        return boto3.client(
            "dynamodb",
            region_name=config.aws_region,
        )
    except Exception as error:
        raise OutboxWorkerConfigurationError(
            "Unable to initialize the outbox worker DynamoDB client"
        ) from error


def _require_table(client, table_name: str, purpose: str) -> None:
    try:
        client.describe_table(TableName=table_name)
    except Exception as error:
        raise OutboxWorkerConfigurationError(
            f"Configured DynamoDB {purpose} table is unavailable: {table_name}"
        ) from error


def _remaining_time_ms(context) -> int:
    callback = getattr(context, "get_remaining_time_in_millis", None)
    if not callable(callback):
        raise OutboxWorkerInvocationError(
            "Lambda context must expose get_remaining_time_in_millis()"
        )
    value = callback()
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OutboxWorkerInvocationError(
            "Lambda remaining execution time is invalid"
        )
    return value


def _required_context_text(context, name: str) -> str:
    value = getattr(context, name, None)
    if not isinstance(value, str) or not value.strip():
        raise OutboxWorkerInvocationError(f"Lambda context {name} is required")
    return value.strip()


def _required_setting(values: Mapping[str, str], name: str) -> str:
    value = _optional_setting(values, name)
    if value is None:
        raise OutboxWorkerConfigurationError(f"{name} is required")
    return value


def _optional_setting(values: Mapping[str, str], name: str) -> Optional[str]:
    value = values.get(name)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _integer_setting(
    values: Mapping[str, str],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = values.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise OutboxWorkerConfigurationError(f"{name} must be an integer") from error
    if isinstance(raw, bool) or value < minimum or value > maximum:
        raise OutboxWorkerConfigurationError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value
