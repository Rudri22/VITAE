import unittest
import inspect
from datetime import datetime, timezone

try:
    from .risk_rules import ApplicationStatus
    from .simulator import (
        RECOVERY_CUMULATIVE_SCENARIO,
        BUILT_IN_SCENARIOS,
        COOLING_FAILURE_SCENARIO,
        INVALID_TELEMETRY_SCENARIO,
        LOW_TEMPERATURE_SCENARIO,
        NORMAL_SCENARIO,
        OUTSIDE_ALL_RULES_SCENARIO,
        STATUS_LADDER_SCENARIO,
        ScenarioPoint,
        SimulationScenario,
        SimulationScenarioError,
        build_local_environment,
        format_compact_run,
        format_run,
        generate_samples,
        run_scenario,
    )
    from .telemetry import TelemetryValidationError
    from .telemetry_processor import ProcessingResult
except ImportError:
    from risk_rules import ApplicationStatus
    from simulator import (
        RECOVERY_CUMULATIVE_SCENARIO,
        BUILT_IN_SCENARIOS,
        COOLING_FAILURE_SCENARIO,
        INVALID_TELEMETRY_SCENARIO,
        LOW_TEMPERATURE_SCENARIO,
        NORMAL_SCENARIO,
        OUTSIDE_ALL_RULES_SCENARIO,
        STATUS_LADDER_SCENARIO,
        ScenarioPoint,
        SimulationScenario,
        SimulationScenarioError,
        build_local_environment,
        format_compact_run,
        format_run,
        generate_samples,
        run_scenario,
    )
    from telemetry import TelemetryValidationError
    from telemetry_processor import ProcessingResult


class SimulatorTests(unittest.TestCase):
    def run_builtin(self, scenario):
        environment = build_local_environment()
        steps = run_scenario(
            environment.processor,
            scenario,
            device_id=environment.device_id,
            start_time=environment.start_time,
        )
        return environment, steps

    def test_normal_scenario_remains_safe(self):
        _, steps = self.run_builtin(NORMAL_SCENARIO)
        self.assertEqual(
            [step.result.decision.status for step in steps],
            [ApplicationStatus.SAFE] * 3,
        )

    def test_status_ladder_is_decided_by_real_processor(self):
        environment = build_local_environment()
        steps = run_scenario(
            environment.processor,
            STATUS_LADDER_SCENARIO,
            device_id=environment.device_id,
            start_time=environment.start_time,
        )

        self.assertEqual(
            [step.result.decision.status for step in steps],
            [
                ApplicationStatus.SAFE,
                ApplicationStatus.MONITOR,
                ApplicationStatus.AT_RISK,
                ApplicationStatus.CRITICAL,
                ApplicationStatus.RULE_VIOLATION,
            ],
        )

    def test_recovery_returns_safe_and_preserves_cumulative_time(self):
        environment = build_local_environment()
        steps = run_scenario(
            environment.processor,
            RECOVERY_CUMULATIVE_SCENARIO,
            device_id=environment.device_id,
            start_time=environment.start_time,
        )

        statuses = [step.result.decision.status for step in steps]
        self.assertEqual(statuses[3], ApplicationStatus.SAFE)
        self.assertEqual(statuses[-1], ApplicationStatus.RULE_VIOLATION)
        self.assertEqual(
            steps[3].result.decision.cumulative_excursion_duration_minutes,
            2170.0,
        )
        self.assertEqual(
            steps[-1].result.decision.cumulative_excursion_duration_minutes,
            4320.0,
        )

    def test_low_temperature_scenario_uses_verified_low_excursion_rule(self):
        _, steps = self.run_builtin(LOW_TEMPERATURE_SCENARIO)
        self.assertEqual(
            [step.result.decision.status for step in steps],
            [
                ApplicationStatus.SAFE,
                ApplicationStatus.MONITOR,
                ApplicationStatus.AT_RISK,
            ],
        )
        self.assertIn("low-temp", steps[-1].result.decision.active_rule_id)

    def test_outside_all_rules_is_rule_violation(self):
        _, steps = self.run_builtin(OUTSIDE_ALL_RULES_SCENARIO)
        self.assertEqual(
            steps[-1].result.decision.status,
            ApplicationStatus.RULE_VIOLATION,
        )
        self.assertEqual(
            steps[-1].result.decision.reason_code,
            "TEMPERATURE_OUTSIDE_VERIFIED_RULES",
        )

    def test_cooling_failure_retains_battery_facts(self):
        _, steps = self.run_builtin(COOLING_FAILURE_SCENARIO)
        self.assertEqual(
            [step.result.telemetry_record.battery_level for step in steps],
            [100.0, 70.0, 30.0, 10.0],
        )
        self.assertEqual(
            [step.result.decision.status for step in steps],
            [
                ApplicationStatus.SAFE,
                ApplicationStatus.SAFE,
                ApplicationStatus.MONITOR,
                ApplicationStatus.MONITOR,
            ],
        )

    def test_invalid_telemetry_is_rejected_without_persistence(self):
        environment = build_local_environment()
        with self.assertRaises(TelemetryValidationError) as caught:
            run_scenario(
                environment.processor,
                INVALID_TELEMETRY_SCENARIO,
                device_id=environment.device_id,
                start_time=environment.start_time,
            )
        self.assertEqual(caught.exception.reason_code, "INVALID_TEMPERATURE")
        self.assertEqual(
            environment.repository.get_telemetry_history("sim-vitae-lot-trip-001"),
            (),
        )

    def test_all_successful_scenarios_persist_one_record_per_result(self):
        for scenario in BUILT_IN_SCENARIOS:
            with self.subTest(scenario=scenario.scenario_id):
                environment, steps = self.run_builtin(scenario)
                history = environment.repository.get_telemetry_history(
                    "sim-vitae-lot-trip-001"
                )
                self.assertEqual(len(history), len(steps))
                self.assertEqual(len(steps), len(scenario.points))

    def test_all_successful_simulation_results_are_processing_results(self):
        for scenario in BUILT_IN_SCENARIOS:
            with self.subTest(scenario=scenario.scenario_id):
                _, steps = self.run_builtin(scenario)
                self.assertTrue(
                    all(isinstance(step.result, ProcessingResult) for step in steps)
                )

    def test_simulator_does_not_calculate_application_status(self):
        try:
            from . import simulator
        except ImportError:
            import simulator

        self.assertNotIn("ApplicationStatus", inspect.getsource(simulator))

    def test_samples_have_unique_deterministic_ids(self):
        environment = build_local_environment()
        samples = generate_samples(
            STATUS_LADDER_SCENARIO,
            device_id=environment.device_id,
            start_time=environment.start_time,
        )
        self.assertEqual(
            [sample["sample_id"] for sample in samples],
            [
                "status-ladder-001",
                "status-ladder-002",
                "status-ladder-003",
                "status-ladder-004",
                "status-ladder-005",
            ],
        )

    def test_samples_use_requested_device_and_elapsed_timestamps(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        samples = generate_samples(
            STATUS_LADDER_SCENARIO,
            device_id="device-test",
            start_time=start,
        )
        self.assertTrue(all(sample["device_id"] == "device-test" for sample in samples))
        self.assertEqual(samples[0]["timestamp"], start.isoformat())
        self.assertEqual(
            samples[-1]["timestamp"],
            "2026-01-04T00:10:00+00:00",
        )

    def test_optional_battery_is_only_emitted_when_supplied(self):
        scenario = SimulationScenario(
            scenario_id="battery",
            name="Battery facts",
            points=(
                ScenarioPoint(0.0, 6.0),
                ScenarioPoint(1.0, 6.0, battery_level=75.0),
            ),
        )
        samples = generate_samples(
            scenario,
            device_id="device-test",
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertNotIn("battery_level", samples[0])
        self.assertEqual(samples[1]["battery_level"], 75.0)

    def test_run_persists_every_generated_sample(self):
        environment = build_local_environment()
        steps = run_scenario(
            environment.processor,
            STATUS_LADDER_SCENARIO,
            device_id=environment.device_id,
            start_time=environment.start_time,
        )
        history = environment.repository.get_telemetry_history(
            steps[-1].result.telemetry_record.lot_trip_id
        )
        self.assertEqual(len(history), len(STATUS_LADDER_SCENARIO.points))
        self.assertIs(history[-1], steps[-1].result.telemetry_record)

    def test_final_live_state_is_last_simulation_result(self):
        environment = build_local_environment()
        steps = run_scenario(
            environment.processor,
            STATUS_LADDER_SCENARIO,
            device_id=environment.device_id,
            start_time=environment.start_time,
        )
        current = environment.repository.get_live_state(
            steps[-1].result.live_state.lot_trip_id
        )
        self.assertIs(current, steps[-1].result.live_state)

    def test_empty_scenario_fails_before_processing(self):
        environment = build_local_environment()
        scenario = SimulationScenario("empty", "Empty", ())
        with self.assertRaises(SimulationScenarioError):
            run_scenario(
                environment.processor,
                scenario,
                device_id=environment.device_id,
                start_time=environment.start_time,
            )
        self.assertIsNone(environment.repository.get_live_state("sim-vitae-lot-trip-001"))

    def test_non_increasing_schedule_fails_before_processing(self):
        environment = build_local_environment()
        scenario = SimulationScenario(
            "unordered",
            "Unordered",
            (ScenarioPoint(1.0, 6.0), ScenarioPoint(1.0, 9.0)),
        )
        with self.assertRaises(SimulationScenarioError):
            run_scenario(
                environment.processor,
                scenario,
                device_id=environment.device_id,
                start_time=environment.start_time,
            )
        self.assertIsNone(environment.repository.get_live_state("sim-vitae-lot-trip-001"))

    def test_negative_or_non_finite_elapsed_time_fails(self):
        environment = build_local_environment()
        for elapsed in (-1.0, float("nan"), float("inf"), True):
            with self.subTest(elapsed=elapsed):
                scenario = SimulationScenario(
                    "invalid-time",
                    "Invalid time",
                    (ScenarioPoint(elapsed, 6.0),),
                )
                with self.assertRaises(SimulationScenarioError):
                    generate_samples(
                        scenario,
                        device_id=environment.device_id,
                        start_time=environment.start_time,
                    )

    def test_format_run_uses_processor_results(self):
        environment = build_local_environment()
        steps = run_scenario(
            environment.processor,
            STATUS_LADDER_SCENARIO,
            device_id=environment.device_id,
            start_time=environment.start_time,
        )
        output = format_run(STATUS_LADDER_SCENARIO, steps)
        self.assertIn("SAFE", output)
        self.assertIn("RULE_VIOLATION", output)
        self.assertIn("4320.0", output)

    def test_compact_output_includes_battery_when_available(self):
        _, steps = self.run_builtin(COOLING_FAILURE_SCENARIO)
        output = format_compact_run(COOLING_FAILURE_SCENARIO, steps)
        self.assertIn("battery=100%", output)
        self.assertIn("battery=10%", output)

    def test_scenario_points_only_define_time_and_sensor_values(self):
        point = STATUS_LADDER_SCENARIO.points[0]
        self.assertEqual(
            set(point.__dataclass_fields__),
            {"elapsed_minutes", "temperature", "battery_level"},
        )
        self.assertFalse(hasattr(point, "status"))


if __name__ == "__main__":
    unittest.main()
