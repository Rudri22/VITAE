import unittest
from datetime import datetime, timedelta, timezone

try:
    from .risk_rules import (
        ApplicationStatus,
        PreviousState,
        ProductRule,
        ProductRuleType,
        TelemetrySample,
        evaluate_status,
    )
except ImportError:
    from risk_rules import (
        ApplicationStatus,
        PreviousState,
        ProductRule,
        ProductRuleType,
        TelemetrySample,
        evaluate_status,
    )


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
PRODUCT_ID = "product-a"


def rules(*, cumulative=False, verified=True):
    return [
        ProductRule(
            rule_id="normal-storage",
            product_id=PRODUCT_ID,
            rule_type=ProductRuleType.NORMAL_STORAGE,
            min_temperature=2.0,
            max_temperature=8.0,
            verified=verified,
            version="1",
            source="test-registry",
        ),
        ProductRule(
            rule_id="permitted-excursion",
            product_id=PRODUCT_ID,
            rule_type=ProductRuleType.PERMITTED_EXCURSION,
            min_temperature=8.1,
            max_temperature=12.0,
            maximum_duration_minutes=100.0,
            cumulative=cumulative,
            verified=verified,
            version="1",
            source="test-registry",
        ),
    ]


def sample(temperature, minute=0):
    return TelemetrySample(
        product_id=PRODUCT_ID,
        temperature=temperature,
        timestamp=BASE_TIME + timedelta(minutes=minute),
    )


def active_state(duration, *, cumulative_duration=None):
    return PreviousState(
        last_sample_timestamp=BASE_TIME + timedelta(minutes=duration),
        active_rule_id="permitted-excursion",
        excursion_started_at=BASE_TIME,
        cumulative_excursion_duration_minutes=(
            duration if cumulative_duration is None else cumulative_duration
        ),
    )


class EvaluateStatusTests(unittest.TestCase):
    def test_status_boundaries_table(self):
        cases = [
            {
                "name": "normal temperature is safe",
                "temperature": 5.0,
                "minute": 0,
                "state": None,
                "expected": ApplicationStatus.SAFE,
                "utilization": None,
            },
            {
                "name": "beginning excursion is monitor",
                "temperature": 9.0,
                "minute": 0,
                "state": None,
                "expected": ApplicationStatus.MONITOR,
                "utilization": 0.0,
            },
            {
                "name": "exactly 50 percent is at risk",
                "temperature": 9.0,
                "minute": 50,
                "state": active_state(49),
                "expected": ApplicationStatus.AT_RISK,
                "utilization": 0.5,
            },
            {
                "name": "just below 90 percent is at risk",
                "temperature": 9.0,
                "minute": 89.9,
                "state": active_state(89),
                "expected": ApplicationStatus.AT_RISK,
                "utilization": 0.899,
            },
            {
                "name": "exactly 90 percent is critical",
                "temperature": 9.0,
                "minute": 90,
                "state": active_state(89),
                "expected": ApplicationStatus.CRITICAL,
                "utilization": 0.9,
            },
            {
                "name": "just below 100 percent is critical",
                "temperature": 9.0,
                "minute": 99.9,
                "state": active_state(99),
                "expected": ApplicationStatus.CRITICAL,
                "utilization": 0.999,
            },
            {
                "name": "exactly 100 percent violates rule",
                "temperature": 9.0,
                "minute": 100,
                "state": active_state(99),
                "expected": ApplicationStatus.RULE_VIOLATION,
                "utilization": 1.0,
            },
            {
                "name": "outside every verified rule violates rule",
                "temperature": 15.0,
                "minute": 0,
                "state": None,
                "expected": ApplicationStatus.RULE_VIOLATION,
                "utilization": None,
            },
        ]

        for case in cases:
            with self.subTest(case["name"]):
                decision = evaluate_status(
                    sample(case["temperature"], case["minute"]),
                    rules(),
                    case["state"],
                )
                self.assertEqual(decision.status, case["expected"])
                if case["utilization"] is None:
                    self.assertIsNone(decision.excursion_utilization)
                else:
                    self.assertAlmostEqual(decision.excursion_utilization, case["utilization"])

    def test_data_errors_table(self):
        cases = [
            {
                "name": "missing temperature",
                "sample": sample(None),
                "rules": rules(),
                "state": None,
                "reason": "MISSING_TEMPERATURE",
            },
            {
                "name": "non numeric temperature",
                "sample": sample("warm"),
                "rules": rules(),
                "state": None,
                "reason": "INVALID_TEMPERATURE",
            },
            {
                "name": "no verified rule",
                "sample": sample(5.0),
                "rules": rules(verified=False),
                "state": None,
                "reason": "NO_VERIFIED_APPLICABLE_RULES",
            },
            {
                "name": "invalid state timestamp",
                "sample": sample(9.0, 5),
                "rules": rules(),
                "state": PreviousState(
                    last_sample_timestamp="not-a-timestamp",
                    active_rule_id="permitted-excursion",
                    excursion_started_at=BASE_TIME,
                ),
                "reason": "INVALID_PREVIOUS_STATE",
            },
        ]

        for case in cases:
            with self.subTest(case["name"]):
                decision = evaluate_status(case["sample"], case["rules"], case["state"])
                self.assertEqual(decision.status, ApplicationStatus.DATA_ERROR)
                self.assertEqual(decision.reason_code, case["reason"])

    def test_recovery_is_safe_and_preserves_cumulative_duration(self):
        decision = evaluate_status(
            sample(5.0, 25),
            rules(),
            active_state(20),
        )

        self.assertEqual(decision.status, ApplicationStatus.SAFE)
        self.assertEqual(decision.excursion_episode_duration_minutes, 0.0)
        self.assertEqual(decision.cumulative_excursion_duration_minutes, 25.0)
        self.assertIsNone(decision.active_rule_id)
        self.assertIsNone(decision.excursion_started_at)

    def test_cumulative_rule_uses_duration_across_episodes(self):
        recovered_state = PreviousState(
            last_sample_timestamp=BASE_TIME + timedelta(minutes=60),
            cumulative_excursion_duration_minutes=55.0,
        )

        cumulative_decision = evaluate_status(sample(9.0, 70), rules(cumulative=True), recovered_state)
        episode_decision = evaluate_status(sample(9.0, 70), rules(cumulative=False), recovered_state)

        self.assertEqual(cumulative_decision.status, ApplicationStatus.AT_RISK)
        self.assertEqual(cumulative_decision.excursion_episode_duration_minutes, 0.0)
        self.assertEqual(cumulative_decision.cumulative_excursion_duration_minutes, 55.0)
        self.assertEqual(cumulative_decision.excursion_utilization, 0.55)
        self.assertEqual(episode_decision.status, ApplicationStatus.MONITOR)
        self.assertEqual(episode_decision.excursion_utilization, 0.0)


if __name__ == "__main__":
    unittest.main()
