"""Opt-in local demo flow over the real V2 telemetry and lifecycle services."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import RLock
from typing import Callable, Mapping, Optional, Tuple

try:
    from .alert_lifecycle_service import AlertActor, AlertLifecycleService
    from .alerting import AlertStatus
    from .monitoring_service import MonitoringService
    from .telemetry_http import TelemetryHttpAdapter
except ImportError:
    from alert_lifecycle_service import AlertActor, AlertLifecycleService
    from alerting import AlertStatus
    from monitoring_service import MonitoringService
    from telemetry_http import TelemetryHttpAdapter


LOCAL_DEMO_CONTROLS_ENV = "VITAE_LOCAL_DEMO_CONTROLS"
LOCAL_DEMO_SEQUENCE_VERSION = "vitae-local-demo-sequence-v1"


class LocalDemoStepKind(str, Enum):
    TELEMETRY = "TELEMETRY"
    INTERVENTION = "INTERVENTION"
    COMPLETION = "COMPLETION"


@dataclass(frozen=True)
class LocalDemoStep:
    step_id: str
    label: str
    kind: LocalDemoStepKind
    elapsed_minutes: Optional[float] = None
    temperature: Optional[float] = None
    battery_level: Optional[float] = None
    telemetry_samples: Tuple[Tuple[float, float, float], ...] = ()


@dataclass(frozen=True)
class LocalDemoStepResult:
    sequence_version: str
    step_number: int
    step: LocalDemoStep
    telemetry_response: Optional[Mapping]
    accepted_sample_count: int
    affected_alert_ids: Tuple[str, ...]
    complete: bool


_ML_MONITOR_BASELINE_SAMPLES = tuple(
    (1 + 105 * index, 8.1, 95.0 - index) for index in range(1, 12)
)


DEMO_STEPS = (
    LocalDemoStep("safe", "Healthy reading", LocalDemoStepKind.TELEMETRY, 1, 6.0, 96.0),
    LocalDemoStep(
        "ml-monitor-baseline",
        "Accumulated MONITOR history",
        LocalDemoStepKind.TELEMETRY,
        temperature=8.1,
        battery_level=84.0,
        telemetry_samples=_ML_MONITOR_BASELINE_SAMPLES,
    ),
    LocalDemoStep(
        "ml-monitor-intervene",
        "Future risk crosses intervention threshold",
        LocalDemoStepKind.TELEMETRY,
        1261,
        8.1,
        83.0,
    ),
    LocalDemoStep("at-risk", "Excursion reaches 50%", LocalDemoStepKind.TELEMETRY, 2266, 9.0, 90.0),
    LocalDemoStep("critical", "Excursion reaches 90%", LocalDemoStepKind.TELEMETRY, 3994, 9.0, 86.0),
    LocalDemoStep("rule-violation", "Excursion limit reached", LocalDemoStepKind.TELEMETRY, 4426, 9.0, 82.0),
    LocalDemoStep("intervention", "Cooling intervention recorded", LocalDemoStepKind.INTERVENTION),
    LocalDemoStep("recovery", "Verified storage restored", LocalDemoStepKind.TELEMETRY, 4427, 6.0, 88.0),
    LocalDemoStep("completion", "Destination handoff completed", LocalDemoStepKind.COMPLETION),
)


class LocalDemoSequenceError(RuntimeError):
    pass


class LocalDemoSequence:
    """Advance one dedicated shipment without bypassing domain validation."""

    def __init__(
        self,
        telemetry_adapter: TelemetryHttpAdapter,
        monitoring_service: MonitoringService,
        alert_lifecycle_service: AlertLifecycleService,
        alert_actor: AlertActor,
        *,
        lot_trip_id: str,
        device_id: str,
        trip_started_at: datetime,
        complete_shipment: Callable[[], object],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._telemetry_adapter = telemetry_adapter
        self._monitoring_service = monitoring_service
        self._alert_lifecycle_service = alert_lifecycle_service
        self._alert_actor = alert_actor
        self._lot_trip_id = _required_text(lot_trip_id, "lot_trip_id")
        self._device_id = _required_text(device_id, "device_id")
        self._trip_started_at = _aware(trip_started_at, "trip_started_at")
        self._complete_shipment = complete_shipment
        self._clock = clock
        self._step_index = 0
        self._base_timestamp = self._trip_started_at + timedelta(minutes=2865)
        self._last_result = None
        self._hero_comparison = {}
        self._lock = RLock()

    def status_document(self) -> dict:
        with self._lock:
            next_step = DEMO_STEPS[self._step_index] if self._step_index < len(DEMO_STEPS) else None
            return {
                "enabled": True,
                "sequenceVersion": LOCAL_DEMO_SEQUENCE_VERSION,
                "lotTripId": self._lot_trip_id,
                "currentStep": self._step_index,
                "totalSteps": len(DEMO_STEPS),
                "complete": next_step is None,
                "nextStep": _step_document(next_step),
                "lastResult": (
                    local_demo_step_result_document(self._last_result)
                    if self._last_result is not None
                    else None
                ),
                "heroComparison": dict(self._hero_comparison),
                "steps": [_step_document(step) for step in DEMO_STEPS],
            }

    def advance(self) -> LocalDemoStepResult:
        with self._lock:
            if self._step_index >= len(DEMO_STEPS):
                raise LocalDemoSequenceError("The local demo sequence is already complete")
            step = DEMO_STEPS[self._step_index]
            telemetry_response = None
            accepted_sample_count = 0
            affected_alert_ids = ()
            if step.kind == LocalDemoStepKind.TELEMETRY:
                responses = self._submit_telemetry(step)
                response = responses[-1]
                telemetry_response = response.body
                accepted_sample_count = len(responses)
                if response.status_code != 200:
                    code = response.body.get("error", {}).get("code", "UNKNOWN")
                    raise LocalDemoSequenceError(
                        f"Demo telemetry was rejected at {step.step_id}: {code}"
                    )
                self._capture_hero_comparison(step)
                if step.step_id == "recovery":
                    affected_alert_ids = self._resolve_active_alerts()
            elif step.kind == LocalDemoStepKind.INTERVENTION:
                affected_alert_ids = self._record_intervention()
            else:
                self._complete_shipment()
            self._step_index += 1
            result = LocalDemoStepResult(
                sequence_version=LOCAL_DEMO_SEQUENCE_VERSION,
                step_number=self._step_index,
                step=step,
                telemetry_response=telemetry_response,
                accepted_sample_count=accepted_sample_count,
                affected_alert_ids=affected_alert_ids,
                complete=self._step_index == len(DEMO_STEPS),
            )
            self._last_result = result
            return result

    def _submit_telemetry(self, step):
        samples = step.telemetry_samples or (
            (step.elapsed_minutes, step.temperature, step.battery_level),
        )
        responses = []
        for index, (elapsed_minutes, temperature, battery_level) in enumerate(samples):
            timestamp = self._base_timestamp + timedelta(minutes=elapsed_minutes)
            suffix = f"-{index + 1}" if len(samples) > 1 else ""
            response = self._telemetry_adapter.handle_post({
                "sample_id": f"local-demo-{step.step_id}{suffix}",
                "device_id": self._device_id,
                "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "temperature": temperature,
                "battery_level": battery_level,
                "latitude": 33.8890,
                "longitude": 35.5070,
                "device_health": "OK",
                "source": "SIMULATOR",
            })
            responses.append(response)
            if response.status_code != 200:
                break
        return tuple(responses)

    def _capture_hero_comparison(self, step):
        key = {
            "ml-monitor-baseline": "baseline",
            "ml-monitor-intervene": "intervene",
        }.get(step.step_id)
        if key is None:
            return
        snapshot = self._monitoring_service.get_live_snapshot(self._lot_trip_id)
        live = snapshot.live_state
        probability = getattr(snapshot.future_risk, "adverse_event_probability", None)
        self._hero_comparison[key] = {
            "label": "A" if key == "baseline" else "B",
            "currentCondition": live.status.value,
            "adverseEventProbability": probability,
            "recommendedAction": snapshot.operational_decision.recommended_action.value,
            "revision": live.revision,
            "acceptedSamples": live.revision,
            "excursionMinutes": live.excursion_episode_duration_minutes,
            "excursionUtilization": live.excursion_utilization,
        }

    def _record_intervention(self):
        alerts = self._alert_lifecycle_service.list_alerts(
            self._lot_trip_id, self._alert_actor
        )
        active = next((alert for alert in alerts if alert.status != AlertStatus.RESOLVED), None)
        if active is None:
            raise LocalDemoSequenceError("No active alert exists for intervention")
        if active.status == AlertStatus.OPEN:
            active = self._alert_lifecycle_service.acknowledge(
                self._lot_trip_id, active.alert_id, self._alert_actor
            )
        active = self._alert_lifecycle_service.record_action(
            self._lot_trip_id,
            active.alert_id,
            "Cooling unit inspected and verified storage conditions restored",
            self._alert_actor,
        )
        return (active.alert_id,)

    def _resolve_active_alerts(self):
        affected = []
        for alert in self._alert_lifecycle_service.list_alerts(
            self._lot_trip_id, self._alert_actor
        ):
            if alert.status == AlertStatus.RESOLVED:
                continue
            self._alert_lifecycle_service.resolve(
                self._lot_trip_id,
                alert.alert_id,
                "Accepted recovery telemetry confirms current storage is safe",
                self._alert_actor,
            )
            affected.append(alert.alert_id)
        return tuple(affected)


def local_demo_step_result_document(result: LocalDemoStepResult) -> dict:
    return {
        "sequenceVersion": result.sequence_version,
        "stepNumber": result.step_number,
        "step": _step_document(result.step),
        "telemetryResponse": result.telemetry_response,
        "acceptedSampleCount": result.accepted_sample_count,
        "affectedAlertIds": list(result.affected_alert_ids),
        "complete": result.complete,
    }


def _step_document(step):
    if step is None:
        return None
    return {
        "id": step.step_id,
        "label": step.label,
        "kind": step.kind.value,
        "temperature": step.temperature,
        "batteryLevel": step.battery_level,
    }


def _required_text(value, field):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


def _aware(value, field):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)
