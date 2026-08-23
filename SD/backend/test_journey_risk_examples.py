import unittest
from datetime import timedelta

try:
    from .journey_risk_examples import build_journey_risk_examples
    from .risk_rules import ApplicationStatus
    from .test_temporal_risk_examples import completed_source_record, CONTRACT_TIME
except ImportError:
    from journey_risk_examples import build_journey_risk_examples
    from risk_rules import ApplicationStatus
    from test_temporal_risk_examples import completed_source_record, CONTRACT_TIME


class JourneyRiskExampleTests(unittest.TestCase):
    def test_adverse_before_planned_arrival_is_positive(self):
        source = completed_source_record(
            statuses=(ApplicationStatus.SAFE, ApplicationStatus.SAFE, ApplicationStatus.AT_RISK, ApplicationStatus.SAFE),
            temperatures=(6.0, 6.0, 10.0, 6.0),
            times=(0, 10, 20, 40),
            completed_minutes=50,
        )
        example = build_journey_risk_examples(source, planned_arrival_at=CONTRACT_TIME + timedelta(minutes=40))[0]
        self.assertTrue(example.label.deteriorates_before_destination)
        self.assertEqual(example.label.first_adverse_at, CONTRACT_TIME + timedelta(minutes=20))

    def test_adverse_after_planned_arrival_is_negative(self):
        source = completed_source_record(
            statuses=(ApplicationStatus.SAFE, ApplicationStatus.SAFE, ApplicationStatus.AT_RISK, ApplicationStatus.SAFE),
            temperatures=(6.0, 6.0, 10.0, 6.0),
            times=(0, 10, 40, 50),
            completed_minutes=60,
        )
        example = build_journey_risk_examples(source, planned_arrival_at=CONTRACT_TIME + timedelta(minutes=30))[0]
        self.assertFalse(example.label.deteriorates_before_destination)

    def test_no_adverse_before_arrival_is_negative(self):
        source = completed_source_record(
            statuses=(ApplicationStatus.SAFE, ApplicationStatus.MONITOR, ApplicationStatus.SAFE),
            temperatures=(6.0, 8.5, 6.0), times=(0, 10, 20), completed_minutes=30,
        )
        examples = build_journey_risk_examples(source, planned_arrival_at=CONTRACT_TIME + timedelta(minutes=30))
        self.assertTrue(examples)
        self.assertTrue(all(not item.label.deteriorates_before_destination for item in examples))

    def test_remaining_horizon_changes_without_future_feature_leakage(self):
        source = completed_source_record(
            statuses=(ApplicationStatus.SAFE, ApplicationStatus.SAFE, ApplicationStatus.SAFE),
            temperatures=(6.0, 6.1, 6.2), times=(0, 10, 20), completed_minutes=30,
        )
        examples = build_journey_risk_examples(source, planned_arrival_at=CONTRACT_TIME + timedelta(minutes=30))
        self.assertEqual([item.features.remaining_journey_minutes for item in examples], [30.0, 20.0, 10.0])
        self.assertEqual(examples[0].features.temporal_features.sample_count, 1)
        self.assertEqual(examples[1].features.temporal_features.sample_count, 2)


if __name__ == "__main__":
    unittest.main()
