import inspect
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier

import boto3

try:
    from .alerting import InMemoryAlertRepository
    from .decision_outbox import InMemoryProcessingBundleRepository, OutboxDeliveryStatus
    from .dynamo_alert_repository import DynamoAlertRepository
    from .dynamo_telemetry_repository import DynamoTelemetryStateRepository
    from .outbox_dispatcher import (
        DispatchEventOutcome,
        OutboxDispatcher,
        OutboxRetryPolicy,
    )
    from .repository_contract_suite import (
        CONTRACT_TIME,
        contract_alert_outbox_event,
        contract_decision_record,
        contract_sample,
        contract_state,
    )
    from .state_repository import telemetry_record_from_sample
except ImportError:
    from alerting import InMemoryAlertRepository
    from decision_outbox import InMemoryProcessingBundleRepository, OutboxDeliveryStatus
    from dynamo_alert_repository import DynamoAlertRepository
    from dynamo_telemetry_repository import DynamoTelemetryStateRepository
    from outbox_dispatcher import DispatchEventOutcome, OutboxDispatcher, OutboxRetryPolicy
    from repository_contract_suite import (
        CONTRACT_TIME,
        contract_alert_outbox_event,
        contract_decision_record,
        contract_sample,
        contract_state,
    )
    from state_repository import telemetry_record_from_sample


class FakeClock:
    def __init__(self, value=CONTRACT_TIME):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, delta):
        self.value += delta


class FailingAlertRepository(InMemoryAlertRepository):
    def __init__(self, failures=None):
        super().__init__()
        self.failures = failures

    def save_alert(self, alert):
        if self.failures is None or self.failures > 0:
            if self.failures is not None:
                self.failures -= 1
            raise RuntimeError("simulated transient alert store failure")
        return super().save_alert(alert)


class FailingDeliveryMarkRepository:
    def __init__(self, delegate):
        self.delegate = delegate
        self.failed = False

    def mark_outbox_delivered(self, *args, **kwargs):
        if not self.failed:
            self.failed = True
            raise RuntimeError("simulated crash after durable alert save")
        return self.delegate.mark_outbox_delivered(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.delegate, name)


class BarrierDiscoveryRepository:
    def __init__(self, delegate, barrier):
        self.delegate = delegate
        self.barrier = barrier

    def discover_dispatchable_outbox_events(self, *args, **kwargs):
        result = self.delegate.discover_dispatchable_outbox_events(*args, **kwargs)
        self.barrier.wait()
        return result

    def __getattr__(self, name):
        return getattr(self.delegate, name)


class OutboxDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryProcessingBundleRepository()
        self.alert_repository = InMemoryAlertRepository()
        self.clock = FakeClock()
        sample = contract_sample()
        state = contract_state(sample)
        decision = contract_decision_record(sample, state)
        self.event = contract_alert_outbox_event(decision)
        self.repository.commit_processing_bundle(
            telemetry_record_from_sample(
                "contract-trip", "contract-lot-trip", sample
            ),
            state,
            decision,
            self.event,
            expected_revision=None,
        )

    def dispatcher(self, repository=None, alerts=None, **kwargs):
        return OutboxDispatcher(
            repository or self.repository,
            alerts or self.alert_repository,
            worker_id=kwargs.pop("worker_id", "worker-a"),
            clock=self.clock,
            lease_duration=kwargs.pop("lease_duration", timedelta(minutes=1)),
            **kwargs,
        )

    def test_pending_event_is_automatically_delivered(self):
        result = self.dispatcher().run_once()
        self.assertEqual(result.delivered_count, 1)
        self.assertEqual(
            self.repository.get_outbox_event(self.event.event_id).delivery_status,
            OutboxDeliveryStatus.DELIVERED,
        )
        self.assertEqual(
            self.alert_repository.get_alert(self.event.alert_candidate.alert_id),
            self.event.alert_candidate,
        )

    def test_expired_lease_is_recovered(self):
        self.repository.claim_outbox_event(
            self.event.event_id,
            worker_id="dead-worker",
            claimed_at=self.clock(),
            lease_duration=timedelta(minutes=1),
        )
        self.clock.advance(timedelta(minutes=1))
        result = self.dispatcher(worker_id="restart-worker").run_once()
        self.assertEqual(result.delivered_count, 1)
        self.assertEqual(
            self.repository.get_outbox_event(self.event.event_id).attempt_count,
            2,
        )

    def test_transient_failure_schedules_bounded_retry(self):
        alerts = FailingAlertRepository(failures=1)
        dispatcher = self.dispatcher(alerts=alerts)
        failed = dispatcher.run_once()
        pending = self.repository.get_outbox_event(self.event.event_id)
        self.assertEqual(failed.retry_scheduled_count, 1)
        self.assertEqual(pending.delivery_status, OutboxDeliveryStatus.PENDING)
        self.assertGreater(pending.available_at, self.clock())
        self.assertLessEqual(
            pending.available_at - self.clock(),
            timedelta(seconds=6),
        )
        self.clock.value = pending.available_at
        recovered = dispatcher.run_once()
        self.assertEqual(recovered.delivered_count, 1)

    def test_alert_conflict_is_dead_lettered_without_overwrite(self):
        conflicting = replace(
            self.event.alert_candidate,
            message="Different immutable creation content",
        )
        self.alert_repository.save_alert(conflicting)
        result = self.dispatcher().run_once()
        stored = self.repository.get_outbox_event(self.event.event_id)
        self.assertEqual(result.dead_lettered_count, 1)
        self.assertEqual(
            result.event_results[0].error_code,
            "ALERT_CREATION_CONFLICT",
        )
        self.assertEqual(stored.delivery_status, OutboxDeliveryStatus.DEAD_LETTER)
        self.assertEqual(stored.alert_candidate, self.event.alert_candidate)
        self.assertEqual(
            self.alert_repository.get_alert(conflicting.alert_id), conflicting
        )

    def test_max_attempt_failure_is_dead_lettered(self):
        alerts = FailingAlertRepository()
        policy = OutboxRetryPolicy(max_attempts=2)
        dispatcher = self.dispatcher(alerts=alerts, retry_policy=policy)
        first = dispatcher.run_once()
        self.assertEqual(first.retry_scheduled_count, 1)
        self.clock.value = self.repository.get_outbox_event(
            self.event.event_id
        ).available_at
        second = dispatcher.run_once()
        self.assertEqual(second.dead_lettered_count, 1)
        self.assertEqual(second.max_attempts_exceeded_count, 1)

    def test_crash_after_save_recovers_idempotently_after_restart(self):
        wrapper = FailingDeliveryMarkRepository(self.repository)
        first = self.dispatcher(repository=wrapper).run_once()
        in_flight = self.repository.get_outbox_event(self.event.event_id)
        durable_alert = self.alert_repository.get_alert(
            self.event.alert_candidate.alert_id
        )
        self.assertEqual(first.system_failure_count, 1)
        self.assertEqual(in_flight.delivery_status, OutboxDeliveryStatus.IN_FLIGHT)
        self.assertIsNotNone(durable_alert)
        self.clock.value = in_flight.lease_expires_at
        restarted = self.dispatcher(worker_id="restart-worker").run_once()
        self.assertEqual(restarted.delivered_count, 1)
        self.assertEqual(len(self.alert_repository.list_alerts()), 1)

    def test_two_workers_have_one_delivery_winner(self):
        barrier = Barrier(2)
        wrapper = BarrierDiscoveryRepository(self.repository, barrier)

        def run(worker):
            return self.dispatcher(repository=wrapper, worker_id=worker).run_once()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(run, ("worker-a", "worker-b")))
        outcomes = [
            event.outcome
            for result in results
            for event in result.event_results
        ]
        self.assertEqual(outcomes.count(DispatchEventOutcome.DELIVERED), 1)
        self.assertIn(
            next(outcome for outcome in outcomes if outcome != DispatchEventOutcome.DELIVERED),
            {
                DispatchEventOutcome.CLAIM_CONFLICT,
                DispatchEventOutcome.ALREADY_DELIVERED,
            },
        )
        self.assertEqual(len(self.alert_repository.list_alerts()), 1)

    def test_dispatcher_never_recalculates_status_or_alert_policy(self):
        try:
            from . import outbox_dispatcher
        except ImportError:
            import outbox_dispatcher
        source = inspect.getsource(outbox_dispatcher)
        self.assertNotIn("evaluate_status", source)
        self.assertNotIn("evaluate_alert_policy", source)


class DynamoLocalOutboxDispatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        endpoint = os.environ.get("VITAE_DYNAMODB_LOCAL_ENDPOINT")
        if not endpoint:
            raise unittest.SkipTest(
                "Set VITAE_DYNAMODB_LOCAL_ENDPOINT for dispatcher integration"
            )
        cls.client = boto3.client(
            "dynamodb",
            endpoint_url=endpoint,
            region_name="us-east-1",
            aws_access_key_id="local",
            aws_secret_access_key="local",
        )
        suffix = uuid.uuid4().hex
        cls.telemetry_table = f"vitae-dispatch-telemetry-{suffix}"
        cls.alert_table = f"vitae-dispatch-alert-{suffix}"
        cls.client.create_table(
            TableName=cls.telemetry_table,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "outboxWorkPartition", "AttributeType": "S"},
                {"AttributeName": "outboxWorkSort", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "OutboxWorkIndex",
                    "KeySchema": [
                        {"AttributeName": "outboxWorkPartition", "KeyType": "HASH"},
                        {"AttributeName": "outboxWorkSort", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "KEYS_ONLY"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        cls.client.create_table(
            TableName=cls.alert_table,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        for table in (cls.telemetry_table, cls.alert_table):
            cls.client.get_waiter("table_exists").wait(TableName=table)

    @classmethod
    def tearDownClass(cls):
        try:
            for table in (cls.telemetry_table, cls.alert_table):
                cls.client.delete_table(TableName=table)
        finally:
            super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.namespace = uuid.uuid4().hex
        self.clock = FakeClock()
        self.repository = self.telemetry_repository()
        self.alerts = self.alert_repository()
        sample = contract_sample()
        state = contract_state(sample)
        decision = contract_decision_record(sample, state)
        self.event = contract_alert_outbox_event(decision)
        self.repository.commit_processing_bundle(
            telemetry_record_from_sample(
                "contract-trip", "contract-lot-trip", sample
            ),
            state,
            decision,
            self.event,
            expected_revision=None,
        )

    def telemetry_repository(self):
        return DynamoTelemetryStateRepository(
            self.client,
            self.telemetry_table,
            key_namespace=self.namespace,
        )

    def alert_repository(self):
        return DynamoAlertRepository(
            self.client,
            self.alert_table,
            key_namespace=self.namespace,
        )

    def dispatcher(self, repository=None, alerts=None, worker="worker-a"):
        return OutboxDispatcher(
            repository or self.repository,
            alerts or self.alerts,
            worker_id=worker,
            clock=self.clock,
            lease_duration=timedelta(minutes=1),
        )

    def test_restart_discovers_and_delivers_pending_event(self):
        restarted_repository = self.telemetry_repository()
        restarted_alerts = self.alert_repository()
        result = self.dispatcher(
            restarted_repository,
            restarted_alerts,
            worker="restart-worker",
        ).run_once()
        self.assertEqual(result.delivered_count, 1)
        self.assertEqual(
            self.telemetry_repository().get_outbox_event(
                self.event.event_id
            ).delivery_status,
            OutboxDeliveryStatus.DELIVERED,
        )
        self.assertEqual(
            self.alert_repository().get_alert(
                self.event.alert_candidate.alert_id
            ),
            self.event.alert_candidate,
        )

    def test_restart_recovers_expired_lease(self):
        self.repository.claim_outbox_event(
            self.event.event_id,
            worker_id="dead-worker",
            claimed_at=self.clock(),
            lease_duration=timedelta(minutes=1),
        )
        self.clock.advance(timedelta(minutes=1))
        result = self.dispatcher(
            self.telemetry_repository(),
            self.alert_repository(),
            worker="restart-worker",
        ).run_once()
        self.assertEqual(result.delivered_count, 1)

    def test_crash_after_durable_save_replays_idempotently(self):
        wrapper = FailingDeliveryMarkRepository(self.repository)
        failed = self.dispatcher(repository=wrapper).run_once()
        self.assertEqual(failed.system_failure_count, 1)
        persisted = self.telemetry_repository().get_outbox_event(
            self.event.event_id
        )
        self.assertEqual(persisted.delivery_status, OutboxDeliveryStatus.IN_FLIGHT)
        self.assertIsNotNone(
            self.alert_repository().get_alert(
                self.event.alert_candidate.alert_id
            )
        )
        self.clock.value = persisted.lease_expires_at
        recovered = self.dispatcher(
            self.telemetry_repository(),
            self.alert_repository(),
            worker="restart-worker",
        ).run_once()
        self.assertEqual(recovered.delivered_count, 1)
        self.assertEqual(
            len(self.alert_repository().list_alerts()),
            1,
        )

    def test_durable_alert_conflict_dead_letters_event(self):
        conflicting = replace(
            self.event.alert_candidate,
            message="Different durable creation payload",
        )
        self.alerts.save_alert(conflicting)
        result = self.dispatcher().run_once()
        self.assertEqual(result.dead_lettered_count, 1)
        self.assertEqual(
            self.telemetry_repository().get_outbox_event(
                self.event.event_id
            ).delivery_status,
            OutboxDeliveryStatus.DEAD_LETTER,
        )
        self.assertEqual(
            self.alert_repository().get_alert(conflicting.alert_id),
            conflicting,
        )

    def test_dynamo_multi_worker_delivery_is_safe(self):
        barrier = Barrier(2)
        wrapper = BarrierDiscoveryRepository(self.repository, barrier)

        def run(worker):
            return self.dispatcher(
                wrapper,
                self.alerts,
                worker=worker,
            ).run_once()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(run, ("worker-a", "worker-b")))
        outcomes = [
            event.outcome
            for result in results
            for event in result.event_results
        ]
        self.assertEqual(outcomes.count(DispatchEventOutcome.DELIVERED), 1)
        self.assertIn(
            next(outcome for outcome in outcomes if outcome != DispatchEventOutcome.DELIVERED),
            {
                DispatchEventOutcome.CLAIM_CONFLICT,
                DispatchEventOutcome.ALREADY_DELIVERED,
            },
        )
        self.assertEqual(len(self.alert_repository().list_alerts()), 1)


if __name__ == "__main__":
    unittest.main()
