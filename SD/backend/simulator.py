from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Optional, Tuple

try:
    from .alerting import InMemoryAlertRepository
    from .decision_outbox import InMemoryProcessingBundleRepository
    from .operational_service import OperationalTelemetryService
    from .product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
    )
    from .telemetry import TelemetryValidationError
    from .telemetry_processor import ProcessingResult, TelemetryProcessor
    from .trip_identity import DeviceAssignment, TripIdentity, TripStatus
except ImportError:
    from alerting import InMemoryAlertRepository
    from decision_outbox import InMemoryProcessingBundleRepository
    from operational_service import OperationalTelemetryService
    from product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
    )
    from telemetry import TelemetryValidationError
    from telemetry_processor import ProcessingResult, TelemetryProcessor
    from trip_identity import DeviceAssignment, TripIdentity, TripStatus


SIMULATION_START = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)
SIMULATION_DEVICE_ID = "sim-vitae-temperature-001"
SIMULATION_TRIP_ID = "sim-vitae-trip-001"
SIMULATION_LOT_TRIP_ID = "sim-vitae-lot-trip-001"


@dataclass(frozen=True)
class ScenarioPoint:
    elapsed_minutes: float
    temperature: Any
    battery_level: Optional[Any] = None
    device_health: Optional[Any] = None


@dataclass(frozen=True)
class SimulationScenario:
    scenario_id: str
    name: str
    points: Tuple[ScenarioPoint, ...]


@dataclass(frozen=True)
class SimulationStep:
    elapsed_minutes: float
    result: ProcessingResult


@dataclass(frozen=True)
class LocalSimulationEnvironment:
    processor: TelemetryProcessor
    operational_service: OperationalTelemetryService
    repository: InMemoryProcessingBundleRepository
    alert_repository: InMemoryAlertRepository
    device_id: str
    start_time: datetime


class SimulationScenarioError(ValueError):
    pass


NORMAL_SCENARIO = SimulationScenario(
    scenario_id="normal",
    name="NORMAL",
    points=(
        ScenarioPoint(elapsed_minutes=0.0, temperature=5.0),
        ScenarioPoint(elapsed_minutes=30.0, temperature=6.0),
        ScenarioPoint(elapsed_minutes=60.0, temperature=7.0),
    ),
)


STATUS_LADDER_SCENARIO = SimulationScenario(
    scenario_id="status-ladder",
    name="STATUS LADDER",
    points=(
        ScenarioPoint(elapsed_minutes=0.0, temperature=6.0),
        ScenarioPoint(elapsed_minutes=10.0, temperature=9.0),
        ScenarioPoint(elapsed_minutes=2170.0, temperature=9.0),
        ScenarioPoint(elapsed_minutes=3898.0, temperature=9.0),
        ScenarioPoint(elapsed_minutes=4330.0, temperature=9.0),
    ),
)


RECOVERY_CUMULATIVE_SCENARIO = SimulationScenario(
    scenario_id="recovery-cumulative",
    name="RECOVERY",
    points=(
        ScenarioPoint(elapsed_minutes=0.0, temperature=6.0),
        ScenarioPoint(elapsed_minutes=10.0, temperature=9.0),
        ScenarioPoint(elapsed_minutes=2170.0, temperature=9.0),
        ScenarioPoint(elapsed_minutes=2180.0, temperature=6.0),
        ScenarioPoint(elapsed_minutes=2190.0, temperature=9.0),
        ScenarioPoint(elapsed_minutes=4340.0, temperature=9.0),
    ),
)


LOW_TEMPERATURE_SCENARIO = SimulationScenario(
    scenario_id="low-temperature",
    name="LOW TEMPERATURE",
    points=(
        ScenarioPoint(elapsed_minutes=0.0, temperature=6.0),
        ScenarioPoint(elapsed_minutes=10.0, temperature=1.0),
        ScenarioPoint(elapsed_minutes=2170.0, temperature=1.0),
    ),
)


OUTSIDE_ALL_RULES_SCENARIO = SimulationScenario(
    scenario_id="outside-all-rules",
    name="OUTSIDE ALL RULES",
    points=(
        ScenarioPoint(elapsed_minutes=0.0, temperature=6.0),
        ScenarioPoint(elapsed_minutes=10.0, temperature=26.0),
    ),
)


COOLING_FAILURE_SCENARIO = SimulationScenario(
    scenario_id="cooling-failure",
    name="COOLING FAILURE",
    points=(
        ScenarioPoint(elapsed_minutes=0.0, temperature=6.0, battery_level=100.0),
        ScenarioPoint(elapsed_minutes=30.0, temperature=7.5, battery_level=70.0),
        ScenarioPoint(elapsed_minutes=60.0, temperature=9.0, battery_level=30.0),
        ScenarioPoint(elapsed_minutes=180.0, temperature=15.0, battery_level=10.0),
    ),
)


INVALID_TELEMETRY_SCENARIO = SimulationScenario(
    scenario_id="invalid-telemetry",
    name="INVALID TELEMETRY",
    points=(
        ScenarioPoint(elapsed_minutes=0.0, temperature="sensor-fault"),
    ),
)


# Mixed products require separate TripIdentity and lot_trip_id contexts and remain a later expansion.
BUILT_IN_SCENARIOS = (
    NORMAL_SCENARIO,
    STATUS_LADDER_SCENARIO,
    RECOVERY_CUMULATIVE_SCENARIO,
    LOW_TEMPERATURE_SCENARIO,
    OUTSIDE_ALL_RULES_SCENARIO,
    COOLING_FAILURE_SCENARIO,
)


def build_local_environment(
    start_time: datetime = SIMULATION_START,
) -> LocalSimulationEnvironment:
    """Create an isolated processor with one explicit trip and assignment."""
    repository = InMemoryProcessingBundleRepository()
    trip = TripIdentity(
        trip_id=SIMULATION_TRIP_ID,
        lot_trip_id=SIMULATION_LOT_TRIP_ID,
        lot_id="sim-vitae-lot-001",
        device_id=SIMULATION_DEVICE_ID,
        product_id=GARDASIL_9_PRODUCT_ID,
        presentation=GARDASIL_9_PRESENTATION,
        state=GARDASIL_9_STATE,
        product_rule_version=GARDASIL_9_SOURCE_VERSION,
        origin="Local Simulation Origin",
        destination="Local Simulation Destination",
        start_time=start_time - timedelta(minutes=1),
        status=TripStatus.ACTIVE,
    )
    assignment = DeviceAssignment(
        assignment_id="sim-vitae-assignment-001",
        device_id=SIMULATION_DEVICE_ID,
        trip_id=SIMULATION_TRIP_ID,
        lot_trip_id=SIMULATION_LOT_TRIP_ID,
        assigned_at=start_time - timedelta(minutes=1),
        active=True,
    )
    repository.register_trip(trip)
    repository.register_device_assignment(assignment)
    processor = TelemetryProcessor(repository, repository)
    alert_repository = InMemoryAlertRepository()
    return LocalSimulationEnvironment(
        processor=processor,
        operational_service=OperationalTelemetryService(
            processor,
            alert_repository,
        ),
        repository=repository,
        alert_repository=alert_repository,
        device_id=SIMULATION_DEVICE_ID,
        start_time=start_time,
    )


def generate_samples(
    scenario: SimulationScenario,
    *,
    device_id: str,
    start_time: datetime,
) -> Tuple[dict, ...]:
    """Generate deterministic raw payloads from elapsed time and sensor facts."""
    _validate_schedule(scenario)
    samples = []
    for index, point in enumerate(scenario.points, start=1):
        payload = {
            "sample_id": f"{scenario.scenario_id}-{index:03d}",
            "device_id": device_id,
            "timestamp": (
                start_time + timedelta(minutes=point.elapsed_minutes)
            ).isoformat(),
            "temperature": point.temperature,
        }
        if point.battery_level is not None:
            payload["battery_level"] = point.battery_level
        if point.device_health is not None:
            payload["device_health"] = point.device_health
        samples.append(payload)
    return tuple(samples)


def run_scenario(
    operational_service: OperationalTelemetryService,
    scenario: SimulationScenario,
    *,
    device_id: str,
    start_time: datetime,
) -> Tuple[SimulationStep, ...]:
    """Send a scenario through the same operational path as V2 ingestion."""
    samples = generate_samples(
        scenario,
        device_id=device_id,
        start_time=start_time,
    )
    return tuple(
        SimulationStep(
            elapsed_minutes=point.elapsed_minutes,
            result=operational_service.process(payload).processing_result,
        )
        for point, payload in zip(scenario.points, samples)
    )


def format_run(scenario: SimulationScenario, steps: Tuple[SimulationStep, ...]) -> str:
    lines = [scenario.name, "minute | temp C | status | utilization | cumulative min"]
    for step in steps:
        decision = step.result.decision
        utilization = (
            "-"
            if decision.excursion_utilization is None
            else f"{decision.excursion_utilization * 100:.1f}%"
        )
        lines.append(
            f"{step.elapsed_minutes:6.0f} | "
            f"{step.result.live_state.latest_temperature:6.1f} | "
            f"{decision.status.value:14} | "
            f"{utilization:11} | "
            f"{decision.cumulative_excursion_duration_minutes:14.1f}"
        )
    return "\n".join(lines)


def format_compact_run(
    scenario: SimulationScenario,
    steps: Tuple[SimulationStep, ...],
) -> str:
    rendered_steps = []
    for step in steps:
        record = step.result.telemetry_record
        battery = (
            ""
            if record.battery_level is None
            else f" battery={record.battery_level:.0f}%"
        )
        rendered_steps.append(
            f"{step.elapsed_minutes:.0f}m {record.temperature:.1f}C{battery} "
            f"{step.result.decision.status.value}"
        )
    return f"{scenario.name}: " + " -> ".join(rendered_steps)


def main() -> None:
    for scenario in BUILT_IN_SCENARIOS:
        environment = build_local_environment()
        steps = run_scenario(
            environment.operational_service,
            scenario,
            device_id=environment.device_id,
            start_time=environment.start_time,
        )
        print(format_compact_run(scenario, steps))

    invalid_environment = build_local_environment()
    try:
        run_scenario(
            invalid_environment.operational_service,
            INVALID_TELEMETRY_SCENARIO,
            device_id=invalid_environment.device_id,
            start_time=invalid_environment.start_time,
        )
    except TelemetryValidationError as error:
        print(
            f"{INVALID_TELEMETRY_SCENARIO.name}: rejected ({error.reason_code})"
        )


def _validate_schedule(scenario):
    if not isinstance(scenario.scenario_id, str) or not scenario.scenario_id.strip():
        raise SimulationScenarioError("Scenario requires a non-empty scenario_id")
    if not scenario.points:
        raise SimulationScenarioError("Scenario requires at least one point")

    previous_elapsed = None
    for point in scenario.points:
        elapsed = point.elapsed_minutes
        if (
            isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or not isfinite(elapsed)
            or elapsed < 0
        ):
            raise SimulationScenarioError(
                "Scenario elapsed_minutes must be finite and non-negative"
            )
        if previous_elapsed is not None and elapsed <= previous_elapsed:
            raise SimulationScenarioError(
                "Scenario elapsed_minutes must be strictly increasing"
            )
        previous_elapsed = elapsed


if __name__ == "__main__":
    main()
