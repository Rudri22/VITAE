import inspect
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from unittest.mock import MagicMock

import boto3

try:
    from .alerting import (
        AlertConflictError,
        AlertStatus,
    )
    from .decision_outbox import OutboxDeliveryStatus
    from .dynamo_alert_repository import (
        AlertRepositoryCorruptionError,
        DynamoAlertRepository,
        _marshal_item,
    )
    from .dynamo_identity_repository import DynamoIdentityAccessRepository
    from .dynamo_telemetry_repository import DynamoTelemetryStateRepository
    from .operational_service import OperationalTelemetryService
    from .repository_contract_suite import (
        AlertRepositoryContractMixin,
        CONTRACT_TIME,
        contract_alert,
        contract_alert_outbox_event,
        contract_assignment,
        contract_decision_record,
        contract_sample,
        contract_state,
        contract_trip,
    )
    from .state_repository import telemetry_record_from_sample
    from .trip_identity import TripStatus
except ImportError:
    from alerting import AlertConflictError, AlertStatus
    from decision_outbox import OutboxDeliveryStatus
    from dynamo_alert_repository import (
        AlertRepositoryCorruptionError,
        DynamoAlertRepository,
        _marshal_item,
    )
    from dynamo_identity_repository import DynamoIdentityAccessRepository
    from dynamo_telemetry_repository import DynamoTelemetryStateRepository
    from operational_service import OperationalTelemetryService
    from repository_contract_suite import (
        AlertRepositoryContractMixin,
        CONTRACT_TIME,
        contract_alert,
        contract_alert_outbox_event,
        contract_assignment,
        contract_decision_record,
        contract_sample,
        contract_state,
        contract_trip,
    )
    from state_repository import telemetry_record_from_sample
    from trip_identity import TripStatus


LOCAL_ENDPOINT_ENV = "VITAE_DYNAMODB_LOCAL_ENDPOINT"


class DynamoAlertLocalMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        endpoint = os.environ.get(LOCAL_ENDPOINT_ENV)
        if not endpoint:
            raise unittest.SkipTest(
                f"Set {LOCAL_ENDPOINT_ENV} to run DynamoDB Local tests"
            )
        cls.client = boto3.client(
            "dynamodb",
            endpoint_url=endpoint,
            region_name="us-east-1",
            aws_access_key_id="local",
            aws_secret_access_key="local",
        )
        cls.table_name = f"vitae-alert-test-{uuid.uuid4().hex}"
        cls.client.create_table(
            TableName=cls.table_name,
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
        cls.client.get_waiter("table_exists").wait(TableName=cls.table_name)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.client.delete_table(TableName=cls.table_name)
        finally:
            super().tearDownClass()

    def new_repository(self, namespace=None, client=None):
        return DynamoAlertRepository(
            client or self.client,
            self.table_name,
            key_namespace=namespace or uuid.uuid4().hex,
        )


class DynamoAlertRepositoryContractTests(
    DynamoAlertLocalMixin,
    AlertRepositoryContractMixin,
    unittest.TestCase,
):
    def make_alert_repository(self):
        return self.new_repository()


class _ProcessingRepositoryHolder:
    def __init__(self, repository):
        self.processing_repository = repository


class DynamoAlertPersistenceTests(DynamoAlertLocalMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.namespace = uuid.uuid4().hex
        self.repository = self.new_repository(self.namespace)
        self.alert = contract_alert()

    def test_create_is_three_item_transaction_and_record_starts_at_version_one(self):
        self.repository.save_alert(self.alert)
        response = self.client.query(
            TableName=self.table_name,
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues=_marshal_item(
                {":pk": self.repository._lot_partition(self.alert.lot_trip_id)}
            ),
            ConsistentRead=True,
        )
        canonical = response["Items"][0]
        self.assertEqual(canonical["Revision"]["N"], "1")
        scan = self.client.scan(
            TableName=self.table_name,
            FilterExpression="begins_with(PK, :namespace)",
            ExpressionAttributeValues=_marshal_item(
                {":namespace": f"{self.namespace}#"}
            ),
            ConsistentRead=True,
        )
        self.assertEqual(scan["Count"], 3)

    def test_lifecycle_mutations_increment_internal_record_version(self):
        self.repository.save_alert(self.alert)
        acknowledged = self.repository.acknowledge_alert(
            self.alert.alert_id,
            actor_id="driver",
            acknowledged_at=CONTRACT_TIME + timedelta(minutes=1),
        )
        actioned = self.repository.record_action(
            self.alert.alert_id,
            description="Checked cooling unit",
            actor_id="driver",
            recorded_at=CONTRACT_TIME + timedelta(minutes=2),
        )
        resolved = self.repository.resolve_alert(
            self.alert.alert_id,
            actor_id="organization",
            resolved_at=CONTRACT_TIME + timedelta(minutes=3),
            resolution_note="Disposition complete",
        )
        item = self.repository._get_item(self.repository._canonical_key(resolved))
        self.assertEqual(item["Revision"], 4)
        self.assertEqual(acknowledged.status, AlertStatus.ACKNOWLEDGED)
        self.assertEqual(len(actioned.actions), 1)
        self.assertEqual(resolved.status, AlertStatus.RESOLVED)

    def test_concurrent_same_candidate_is_created_once(self):
        barrier = Barrier(2)

        def save():
            repository = self.new_repository(self.namespace)
            barrier.wait()
            return repository.save_alert(self.alert)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.submit(save) for _ in range(2))
            self.assertEqual(tuple(future.result() for future in results), (self.alert,) * 2)
        self.assertEqual(self.repository.list_alerts(), (self.alert,))

    def test_concurrent_conflicting_candidates_have_one_winner(self):
        candidates = (self.alert, replace(self.alert, message="Different message"))
        barrier = Barrier(2)

        def save(candidate):
            repository = self.new_repository(self.namespace)
            barrier.wait()
            try:
                return repository.save_alert(candidate)
            except AlertConflictError:
                return "CONFLICT"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(
                future.result()
                for future in (
                    executor.submit(save, candidates[0]),
                    executor.submit(save, candidates[1]),
                )
            )
        self.assertEqual(sum(value == "CONFLICT" for value in outcomes), 1)
        self.assertEqual(len(self.repository.list_alerts()), 1)

    def test_restart_recovers_evolved_alert_and_original_replay_is_safe(self):
        self.repository.save_alert(self.alert)
        evolved = self.repository.acknowledge_alert(
            self.alert.alert_id,
            actor_id="driver",
            acknowledged_at=CONTRACT_TIME + timedelta(minutes=1),
        )
        restarted = self.new_repository(self.namespace)
        self.assertEqual(restarted.get_alert(self.alert.alert_id), evolved)
        self.assertEqual(restarted.save_alert(self.alert), evolved)

    def test_locator_canonical_mismatch_fails_closed(self):
        self.repository.save_alert(self.alert)
        locator_key = self.repository._locator_key(self.alert.alert_id)
        locator = self.repository._get_item(locator_key)
        locator["CanonicalSK"] = "ALERT#wrong"
        self.client.put_item(
            TableName=self.table_name,
            Item=_marshal_item(locator),
        )
        with self.assertRaises(AlertRepositoryCorruptionError):
            self.repository.get_alert(self.alert.alert_id)

    def test_corrupt_canonical_document_fails_closed(self):
        self.repository.save_alert(self.alert)
        key = self.repository._canonical_key(self.alert)
        canonical = self.repository._get_item(key)
        canonical["Document"]["schema"] = "wrong.schema"
        self.client.put_item(
            TableName=self.table_name,
            Item=_marshal_item(canonical),
        )
        with self.assertRaises(AlertRepositoryCorruptionError):
            self.repository.get_alert(self.alert.alert_id)

    def test_reads_are_strong_and_list_uses_query_without_scan(self):
        observing_client = MagicMock(wraps=self.client)
        repository = self.new_repository(self.namespace, observing_client)
        repository.save_alert(self.alert)
        observing_client.reset_mock()
        repository.get_alert(self.alert.alert_id)
        repository.list_alerts(lot_trip_id=self.alert.lot_trip_id)
        self.assertTrue(observing_client.get_item.call_args_list)
        self.assertTrue(
            all(call.kwargs["ConsistentRead"] for call in observing_client.get_item.call_args_list)
        )
        self.assertTrue(observing_client.query.call_args_list)
        self.assertTrue(
            all(call.kwargs["ConsistentRead"] for call in observing_client.query.call_args_list)
        )
        observing_client.scan.assert_not_called()
        self.assertNotIn(".scan(", inspect.getsource(DynamoAlertRepository))

    def test_query_follows_last_evaluated_key(self):
        client = MagicMock()
        client.query.side_effect = (
            {"Items": [], "LastEvaluatedKey": _marshal_item({"PK": "p", "SK": "s"})},
            {"Items": []},
        )
        repository = DynamoAlertRepository(client, "table")
        self.assertEqual(repository._query("partition", "ALERT#"), ())
        self.assertEqual(client.query.call_count, 2)
        self.assertIn("ExclusiveStartKey", client.query.call_args_list[1].kwargs)
        self.assertTrue(
            all(call.kwargs["ConsistentRead"] for call in client.query.call_args_list)
        )

    def test_pending_outbox_retry_accepts_acknowledged_durable_alert(self):
        identity = DynamoIdentityAccessRepository(
            self.client,
            self.table_name,
            key_namespace=self.namespace,
        )
        identity.register_trip_and_assignment(
            contract_trip(status=TripStatus.ACTIVE),
            contract_assignment(active=True),
        )
        processing = DynamoTelemetryStateRepository(
            self.client,
            self.table_name,
            identity_table_name=self.table_name,
            key_namespace=self.namespace,
        )
        sample = contract_sample()
        state = contract_state(sample)
        record = telemetry_record_from_sample(state.trip_id, state.lot_trip_id, sample)
        decision = contract_decision_record(sample, state)
        outbox = contract_alert_outbox_event(decision)
        processing.commit_processing_bundle(
            record,
            state,
            decision,
            outbox,
            expected_revision=None,
        )
        self.repository.save_alert(outbox.alert_candidate)
        acknowledged = self.repository.acknowledge_alert(
            outbox.alert_candidate.alert_id,
            actor_id="driver",
            acknowledged_at=CONTRACT_TIME + timedelta(minutes=1),
        )
        service = OperationalTelemetryService(
            _ProcessingRepositoryHolder(processing),
            self.repository,
        )

        delivered = service.deliver_outbox_event(
            outbox.event_id,
            attempted_at=CONTRACT_TIME + timedelta(minutes=2),
        )

        self.assertEqual(delivered, acknowledged)
        self.assertEqual(
            processing.get_outbox_event(outbox.event_id).delivery_status,
            OutboxDeliveryStatus.DELIVERED,
        )
        self.assertEqual(
            self.repository.get_alert(outbox.alert_candidate.alert_id),
            acknowledged,
        )


if __name__ == "__main__":
    unittest.main()
