import unittest
from dataclasses import fields
from datetime import timedelta

try:
    from .alerting import (
        AlertType,
        InMemoryAlertRepository,
    )
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

    def process(self, raw_sample):
        self.last_result = self._delegate.process(raw_sample)
        return self.last_result


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

    def test_failed_alert_can_be_retried_idempotently_from_processing_result(self):
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

        recovered = service.persist_alert_for_result(
            caught.exception.processing_result
        )
        repeated = service.persist_alert_for_result(
            caught.exception.processing_result
        )
        self.assertIs(recovered, repeated)
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

    def test_no_alert_result_retry_remains_no_op(self):
        result = self.process(
            sample_id="sample-safe",
            elapsed_minutes=0,
            temperature=6.0,
        )
        self.assertIsNone(
            self.service.persist_alert_for_result(result.processing_result)
        )
        self.assertEqual(self.alert_repository.save_calls, 0)

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


if __name__ == "__main__":
    unittest.main()
