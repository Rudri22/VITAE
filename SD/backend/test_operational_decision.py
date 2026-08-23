import unittest
from datetime import datetime, timezone

try:
    from .operational_decision import (
        FutureRiskCategory,
        JourneyContext,
        OperationalDecisionConfig,
        OperationalDecisionEngine,
        RecommendedAction,
        journey_context_with_route_options,
    )
    from .risk_rules import ApplicationStatus
    from .rerouting import (
        ReroutingEvaluation,
        ReroutingStatus,
        RouteCandidate,
        RouteOptions,
    )
    from .route_duration import RouteEvidence, RouteStatus
except ImportError:
    from operational_decision import (
        FutureRiskCategory,
        JourneyContext,
        OperationalDecisionConfig,
        OperationalDecisionEngine,
        RecommendedAction,
        journey_context_with_route_options,
    )
    from risk_rules import ApplicationStatus
    from rerouting import ReroutingEvaluation, ReroutingStatus, RouteCandidate, RouteOptions
    from route_duration import RouteEvidence, RouteStatus


class OperationalDecisionEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = OperationalDecisionEngine(
            OperationalDecisionConfig(
                medium_risk_threshold=0.20, high_risk_threshold=0.50
            )
        )

    def test_safe_low_forecast_continues(self):
        result = self.engine.decide(ApplicationStatus.SAFE, 0.19)
        self.assertEqual(result.future_risk_category, FutureRiskCategory.LOW)
        self.assertEqual(result.recommended_action, RecommendedAction.CONTINUE)

    def test_safe_high_forecast_requires_intervention(self):
        result = self.engine.decide(ApplicationStatus.SAFE, 0.50)
        self.assertEqual(result.future_risk_category, FutureRiskCategory.HIGH)
        self.assertEqual(result.recommended_action, RecommendedAction.INTERVENE)

    def test_monitor_high_forecast_remains_actionable(self):
        result = self.engine.decide(ApplicationStatus.MONITOR, 0.76)
        self.assertEqual(result.recommended_action, RecommendedAction.INTERVENE)

    def test_current_deterministic_danger_overrides_lower_forecast(self):
        result = self.engine.decide(ApplicationStatus.CRITICAL, 0.01)
        self.assertEqual(result.recommended_action, RecommendedAction.STOP_OR_REPLACE)
        self.assertIn("CURRENT_DETERMINISTIC_DANGER", result.decision_factors)

    def test_high_forecast_changes_action_with_known_progress(self):
        early = self.engine.decide(
            ApplicationStatus.SAFE, 0.70, JourneyContext(progress_fraction=0.05)
        )
        late = self.engine.decide(
            ApplicationStatus.SAFE, 0.70, JourneyContext(progress_fraction=0.90)
        )
        self.assertEqual(early.recommended_action, RecommendedAction.STOP_OR_REPLACE)
        self.assertEqual(late.recommended_action, RecommendedAction.EXPEDITE)

    def test_missing_optional_journey_context_is_safe(self):
        result = self.engine.decide(ApplicationStatus.SAFE, 0.70)
        self.assertEqual(result.recommended_action, RecommendedAction.INTERVENE)
        self.assertIn("JOURNEY_PROGRESS_UNAVAILABLE", result.decision_factors)

    def test_route_distance_progress_reaches_early_and_late_policy(self):
        early_context = journey_context_with_route_options(
            JourneyContext(), route_options(total_distance=10000, remaining_distance=9500)
        )
        late_context = journey_context_with_route_options(
            JourneyContext(), route_options(total_distance=10000, remaining_distance=1000)
        )
        self.assertAlmostEqual(early_context.progress_fraction, 0.05)
        self.assertAlmostEqual(late_context.progress_fraction, 0.90)
        self.assertEqual(
            self.engine.decide(ApplicationStatus.SAFE, 0.70, early_context).recommended_action,
            RecommendedAction.STOP_OR_REPLACE,
        )
        self.assertEqual(
            self.engine.decide(ApplicationStatus.SAFE, 0.70, late_context).recommended_action,
            RecommendedAction.EXPEDITE,
        )

    def test_logically_inconsistent_route_distance_leaves_progress_unknown(self):
        context = journey_context_with_route_options(
            JourneyContext(), route_options(total_distance=10000, remaining_distance=11000)
        )
        self.assertIsNone(context.progress_fraction)

    def test_threshold_boundaries_are_consistent(self):
        self.assertEqual(
            self.engine.decide(ApplicationStatus.SAFE, 0.20).future_risk_category,
            FutureRiskCategory.MEDIUM,
        )
        self.assertEqual(
            self.engine.decide(ApplicationStatus.SAFE, 0.50).future_risk_category,
            FutureRiskCategory.HIGH,
        )

    def test_high_risk_with_better_eligible_destination_recommends_reroute(self):
        result = self.engine.decide(
            ApplicationStatus.SAFE,
            0.70,
            rerouting=available_reroute(),
        )
        self.assertEqual(result.recommended_action, RecommendedAction.REROUTE)
        self.assertEqual(
            result.rerouting.status, ReroutingStatus.REROUTE_RECOMMENDED
        )
        self.assertEqual(result.rerouting.recommended_candidate.facility_id, "facility-2")
        self.assertIn("BETTER_ELIGIBLE_DESTINATION", result.decision_factors)
        self.assertNotIn("NO_BETTER_ROUTE_CONFIRMED", result.decision_factors)

    def test_high_risk_without_better_destination_remains_intervention(self):
        result = self.engine.decide(
            ApplicationStatus.SAFE,
            0.70,
            rerouting=ReroutingEvaluation(
                status=ReroutingStatus.NO_BETTER_ALTERNATIVE,
                current_destination=None,
                recommended_candidate=None,
                alternatives_considered=2,
                reason="No eligible destination is better.",
            ),
        )
        self.assertEqual(result.recommended_action, RecommendedAction.INTERVENE)


def available_reroute():
    candidate = RouteCandidate(
        facility_id="facility-2",
        display_name="Facility 2",
        coordinates=None,
        eligible=True,
        eligibility_reason="Eligible receiving facility.",
        eta_minutes=20,
        capability_basis="TEST_FIXTURE",
    )
    return ReroutingEvaluation(
        status=ReroutingStatus.REROUTE_AVAILABLE,
        current_destination=None,
        recommended_candidate=candidate,
        alternatives_considered=1,
        reason="Eligible facility reduces estimated travel time by 20.0 minutes.",
        comparison_metric="ROUTE_DURATION_MINUTES",
        current_value=40,
        candidate_value=20,
        improvement=20,
    )


def route_options(*, total_distance, remaining_distance):
    calculated_at = datetime(2026, 8, 23, tzinfo=timezone.utc)
    remaining = RouteEvidence(
        RouteStatus.AVAILABLE,
        "FAKE_ROUTES",
        calculated_at,
        "DRIVE",
        1200,
        remaining_distance,
    )
    total = RouteEvidence(
        RouteStatus.AVAILABLE,
        "FAKE_ROUTES",
        calculated_at,
        "DRIVE",
        3600,
        total_distance,
    )
    current = RouteCandidate(
        "destination",
        "Destination",
        None,
        True,
        "Configured destination.",
        eta_minutes=20,
        route_evidence=remaining,
    )
    return RouteOptions(current, (), total_route=total)


if __name__ == "__main__":
    unittest.main()
