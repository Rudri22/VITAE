import unittest
from dataclasses import fields
from datetime import timedelta
from unittest.mock import patch

try:
    from .alerting import (
        AlertType,
        InMemoryAlertRepository,
    )
    from .decision_outbox import (
        OutboxDeliveryStatus,
        alert_outbox_event_from_candidate,
        decision_record_from_processing_result,
    )
    from . import operational_service, telemetry_processor
    from .operational_service import (
        AlertProcessingError,
        OperationalProcessingResult,
        OperationalTelemetryService,
    )
    from .risk_rules import ApplicationStatus
    from .simulator import (
        STATUS_LADDER_SCENARIO,
        build_local_environment,
        generate_samples,
    )
    from .state_repository import DuplicateTelemetrySampleError
    from .telemetry import TelemetryValidationError
    from .telemetry_processor import ProcessingResult
except ImportError:
    from alerting import (
        AlertType,
        InMemoryAlertRepository,
    )
    from decision_outbox import (
        OutboxDeliveryStatus,
        alert_outbox_event_from_candidate,
        decision_record_from_processing_result,
    )
    import operational_service
    import telemetry_processor
    from operational_service import (
        AlertProcessingError,
        OperationalProcessingResult,
        OperationalTelemetryService,
    )
    from risk_rules import ApplicationStatus
    from simulator import (
        STATUS_LADDER_SCENARIO,
        build_local_environment,
        generate_samples,
    )
    from state_repository import DuplicateTelemetrySampleError
    from telemetry import TelemetryValidationError
    from telemetry_processor import ProcessingResult


def raw_sample(environment, *, sample_id, elapsed_minutes, temperature):
    return {
        "sample_id": sample_id,
        "device_id": environment.device_id,
        "timestamp": (
            environment.start_time + timedelta(minutes=elapsed_minutes)
        ).isoformat(),
        "temperature": temperature,
    }


class CountingAlertRepository(InMemoryAlertRepository):
    def __init__(self):
        super().__init__()
        self.save_calls = 0

    def save_alert(self, alert):
        self.save_calls += 1
        return super().save_alert(alert)


class FailingOnceAlertRepository(InMemoryAlertRepository):
    def __init__(self):
        super().__init__()
        self.should_fail = True

    def save_alert(self, alert):
        if self.should_fail:
            self.should_fail = False
            raise RuntimeError("simulated alert storage failure")
        return super().save_alert(alert)


class CommitObservingAlertRepository(InMemoryAlertRepository):
    def __init__(self, telemetry_repository):
        super().__init__()
        self._telemetry_repository = telemetry_repository
        self.telemetry_was_committed_before_save = False

    def save_alert(self, alert):
        history = self._telemetry_repository.get_telemetry_history(
            alert.lot_trip_id
        )
        state = self._telemetry_repository.get_live_state(alert.lot_trip_id)
        self.telemetry_was_committed_before_save = (
            bool(history)
            and history[-1].sample_id == alert.sample_id
            and state.last_sample_id == alert.sample_id
        )
        return super().save_alert(alert)


class CapturingTelemetryProcessor:
    def __init__(self, delegate):
        self._delegate = delegate
        self.last_result = None

    def prepare(self, raw_sample):
        self.last_result = self._delegate.prepare(raw_sample)
        return self.last_result

    def commit_processing_bundle(self, *args):
        return self._delegate.commit_processing_bundle(*args)

    @property
    def processing_repository(self):
        return self._delegate.processing_repository


class OperationalTelemetryServiceTests(unittest.TestCase):
    def setUp(self):
        self.environment = build_local_environment()
        self.alert_repository = CountingAlertRepository()
        self.service = OperationalTelemetryService(
            self.environment.processor,
            self.alert_repository,
        )

    def process(self, *, sample_id, elapsed_minutes, temperature):
        return self.service.process(
            raw_sample(
                self.environment,
                sample_id=sample_id,
                elapsed_minutes=elapsed_minutes,
                temperature=temperature,
            )
        )

    def test_operational_result_wraps_authoritative_objects_only(self):
        result = self.process(
            sample_id="sample-safe",
            elapsed_minutes=0,
            temperature=6.0,
        )
        self.assertIsInstance(result, OperationalProcessingResult)
        self.assertIsInstance(result.processing_result, ProcessingResult)
        self.assertEqual(
            tuple(field.name for field in fields(result)),
            ("processing_result", "alert"),
        )

    def test_service_returns_exact_processing_result_from_processor(self):
        processor = CapturingTelemetryProcessor(self.environment.processor)
        service = OperationalTelemetryService(processor, self.alert_repository)
        result = service.process(
            raw_sample(
                self.environment,
                sample_id="sample-safe",
                elapsed_minutes=0,
                temperature=6.0,
            )
        )
        self.assertIs(result.processing_result, processor.last_result)

    def test_safe_result_creates_no_alert_and_does_not_write_repository(self):
        result = self.process(
            sample_id="sample-safe",
            elapsed_minutes=0,
            temperature=6.0,
        )
        self.assertEqual(
            result.processing_result.decision.status,
            ApplicationStatus.SAFE,
        )
        self.assertIsNone(result.alert)
        self.assertEqual(self.alert_repository.save_calls, 0)
        self.assertEqual(self.alert_repository.list_alerts(), ())

    def test_monitor_transition_creates_and_persists_alert(self):
        self.process(sample_id="sample-safe", elapsed_minutes=0, temperature=6.0)
        result = self.process(
            sample_id="sample-monitor",
            elapsed_minutes=10,
            temperature=9.0,
        )
        self.assertEqual(result.alert.alert_type, AlertType.EXCURSION_MONITOR)
        self.assertIs(
            self.alert_repository.get_alert(result.alert.alert_id),
            result.alert,
        )

    def test_repeated_monitor_creates_no_repeated_alert(self):
        self.process(sample_id="sample-safe", elapsed_minutes=0, temperature=6.0)
        first = self.process(
            sample_id="sample-monitor-1",
            elapsed_minutes=10,
            temperature=9.0,
        )
        second = self.process(
            sample_id="sample-monitor-2",
            elapsed_minutes=20,
            temperature=9.0,
        )
        self.assertIsNotNone(first.alert)
        self.assertIsNone(second.alert)
        self.assertEqual(len(self.alert_repository.list_alerts()), 1)

    def test_alert_is_saved_after_telemetry_and_live_state_commit(self):
        repository = CommitObservingAlertRepository(self.environment.repository)
        service = OperationalTelemetryService(self.environment.processor, repository)
        service.process(
            raw_sample(
                self.environment,
                sample_id="sample-monitor",
                elapsed_minutes=0,
                temperature=9.0,
            )
        )
        self.assertTrue(repository.telemetry_was_committed_before_save)

    def test_alert_policy_runs_before_bundle_commit(self):
        observed_history_lengths = []
        original_policy = operational_service.evaluate_alert_policy

        def invoke_original(previous_state, result):
            observed_history_lengths.append(
                len(
                    self.environment.repository.get_telemetry_history(
                        result.live_state.lot_trip_id
                    )
                )
            )
            return original_policy(previous_state, result)

        with patch.object(
            operational_service,
            "evaluate_alert_policy",
            side_effect=invoke_original,
        ):
            self.process(
                sample_id="sample-policy-before-commit",
                elapsed_minutes=0,
                temperature=9.0,
            )
        self.assertEqual(observed_history_lengths, [0])

    def test_bundle_commit_failure_persists_nothing_and_saves_no_alert(self):
        repository = self.environment.repository
        with patch.object(
            repository,
            "commit_processing_bundle",
            side_effect=RuntimeError("simulated bundle failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.process(
                    sample_id="sample-bundle-failure",
                    elapsed_minutes=0,
                    temperature=9.0,
                )
        self.assertEqual(
            repository.get_telemetry_history("sim-vitae-lot-trip-001"),
            (),
        )
        self.assertEqual(self.alert_repository.save_calls, 0)

    def test_alert_failure_preserves_committed_processing_result(self):
        repository = FailingOnceAlertRepository()
        service = OperationalTelemetryService(self.environment.processor, repository)
        payload = raw_sample(
            self.environment,
            sample_id="sample-monitor",
            elapsed_minutes=0,
            temperature=9.0,
        )

        with self.assertRaises(AlertProcessingError) as caught:
            service.process(payload)

        result = caught.exception.processing_result
        self.assertEqual(result.decision.status, ApplicationStatus.MONITOR)
        self.assertIs(
            self.environment.repository.get_live_state(
                result.live_state.lot_trip_id
            ),
            result.live_state,
        )
        self.assertEqual(
            len(
                self.environment.repository.get_telemetry_history(
                    result.live_state.lot_trip_id
                )
            ),
            1,
        )
        outbox = self.environment.repository.get_outbox_event(
            caught.exception.outbox_event_id
        )
        self.assertEqual(outbox.delivery_status, OutboxDeliveryStatus.PENDING)
        self.assertEqual(outbox.alert_candidate, caught.exception.alert_candidate)

    def test_failed_alert_can_be_retried_from_exact_outbox_candidate(self):
        repository = FailingOnceAlertRepository()
        service = OperationalTelemetryService(self.environment.processor, repository)
        payload = raw_sample(
            self.environment,
            sample_id="sample-monitor",
            elapsed_minutes=0,
            temperature=9.0,
        )
        with self.assertRaises(AlertProcessingError) as caught:
            service.process(payload)

        with patch.object(
            operational_service,
            "evaluate_alert_policy",
        ) as alert_evaluator:
            recovered = service.deliver_outbox_event(
                caught.exception.outbox_event_id,
                attempted_at=caught.exception.processing_result.telemetry_record.timestamp,
            )
            repeated = service.deliver_outbox_event(
                caught.exception.outbox_event_id,
                attempted_at=caught.exception.processing_result.telemetry_record.timestamp,
            )
        alert_evaluator.assert_not_called()
        self.assertIs(recovered, repeated)
        self.assertEqual(recovered, caught.exception.alert_candidate)
        self.assertEqual(len(repository.list_alerts()), 1)

    def test_processing_failure_does_not_invoke_alert_repository(self):
        payload = raw_sample(
            self.environment,
            sample_id="sample-invalid",
            elapsed_minutes=0,
            temperature="invalid",
        )
        with self.assertRaises(TelemetryValidationError):
            self.service.process(payload)
        self.assertEqual(self.alert_repository.save_calls, 0)

    def test_duplicate_processing_error_propagates_before_alerting(self):
        payload = raw_sample(
            self.environment,
            sample_id="sample-safe",
            elapsed_minutes=0,
            temperature=6.0,
        )
        self.service.process(payload)
        with self.assertRaises(DuplicateTelemetrySampleError):
            self.service.process(payload)
        self.assertEqual(self.alert_repository.save_calls, 0)

    def test_no_alert_result_commits_decision_without_outbox(self):
        result = self.process(
            sample_id="sample-safe",
            elapsed_minutes=0,
            temperature=6.0,
        )
        decisions = self.environment.repository.get_decision_history(
            result.processing_result.live_state.lot_trip_id
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(
            self.environment.repository.list_dispatchable_outbox_events(
                result.processing_result.telemetry_record.timestamp
            ),
            (),
        )
        self.assertEqual(self.alert_repository.save_calls, 0)

    def test_status_and_alert_policy_are_each_evaluated_once(self):
        with patch.object(
            telemetry_processor,
            "evaluate_status",
            wraps=telemetry_processor.evaluate_status,
        ) as status_evaluator, patch.object(
            operational_service,
            "evaluate_alert_policy",
            wraps=operational_service.evaluate_alert_policy,
        ) as alert_evaluator:
            self.process(
                sample_id="sample-monitor-once",
                elapsed_minutes=0,
                temperature=9.0,
            )
        self.assertEqual(status_evaluator.call_count, 1)
        self.assertEqual(alert_evaluator.call_count, 1)

    def test_alert_outbox_is_delivered_after_bundle_commit(self):
        result = self.process(
            sample_id="sample-monitor-outbox",
            elapsed_minutes=0,
            temperature=9.0,
        )
        events = self.environment.repository.list_dispatchable_outbox_events(
            result.processing_result.telemetry_record.timestamp
        )
        self.assertEqual(events, ())
        decisions = self.environment.repository.get_decision_history(
            result.processing_result.live_state.lot_trip_id
        )
        self.assertEqual(len(decisions), 1)
        expected_event = alert_outbox_event_from_candidate(
            decision_record_from_processing_result(result.processing_result),
            result.alert,
        )
        stored = self.environment.repository.get_outbox_event(
            expected_event.event_id
        )
        self.assertEqual(stored.delivery_status, OutboxDeliveryStatus.DELIVERED)
        self.assertEqual(stored.alert_candidate, result.alert)

    def test_failure_after_alert_save_keeps_recoverable_inflight_event(self):
        repository = self.environment.repository
        payload = raw_sample(
            self.environment,
            sample_id="sample-monitor-mark-failure",
            elapsed_minutes=0,
            temperature=9.0,
        )
        with patch.object(
            repository,
            "mark_outbox_delivered",
            side_effect=RuntimeError("simulated delivery marker failure"),
        ):
            with self.assertRaises(AlertProcessingError) as caught:
                self.service.process(payload)

        event = repository.get_outbox_event(caught.exception.outbox_event_id)
        self.assertEqual(event.delivery_status, OutboxDeliveryStatus.IN_FLIGHT)
        self.assertIsNotNone(
            self.alert_repository.get_alert(event.alert_candidate.alert_id)
        )
        recovered = self.service.deliver_outbox_event(
            event.event_id,
            attempted_at=event.lease_expires_at,
        )
        self.assertEqual(recovered, event.alert_candidate)
        self.assertEqual(
            repository.get_outbox_event(event.event_id).delivery_status,
            OutboxDeliveryStatus.DELIVERED,
        )

    def test_status_ladder_produces_transition_alert_progression(self):
        samples = generate_samples(
            STATUS_LADDER_SCENARIO,
            device_id=self.environment.device_id,
            start_time=self.environment.start_time,
        )
        results = tuple(self.service.process(sample) for sample in samples)

        self.assertEqual(
            [result.processing_result.decision.status for result in results],
            [
                ApplicationStatus.SAFE,
                ApplicationStatus.MONITOR,
                ApplicationStatus.AT_RISK,
                ApplicationStatus.CRITICAL,
                ApplicationStatus.RULE_VIOLATION,
            ],
        )
        self.assertEqual(
            [result.alert is not None for result in results],
            [False, True, True, True, True],
        )
        history = self.environment.repository.get_telemetry_history(
            results[-1].processing_result.live_state.lot_trip_id
        )
        self.assertEqual(
            tuple(result.processing_result.telemetry_record for result in results),
            history,
        )
        self.assertEqual(len(self.alert_repository.list_alerts()), 4)
        decisions = self.environment.repository.get_decision_history(
            results[-1].processing_result.live_state.lot_trip_id
        )
        self.assertEqual(len(decisions), len(results))
        outbox_count = sum(
            1
            for result in results
            if result.alert is not None
            and self.environment.repository.get_outbox_event(
                alert_outbox_event_from_candidate(
                    decision_record_from_processing_result(
                        result.processing_result
                    ),
                    result.alert,
                ).event_id
            )
            is not None
        )
        self.assertEqual(outbox_count, 4)


if __name__ == "__main__":
    unittest.main()
