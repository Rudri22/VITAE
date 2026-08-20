import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from unittest.mock import MagicMock, patch

import boto3

try:
    from .dynamo_telemetry_repository import (
        DynamoTelemetryStateRepository,
        _marshal,
    )
    from .repository_contract_suite import (
        ProcessingBundleRepositoryContractMixin,
        TelemetryStateRepositoryContractMixin,
        contract_alert_outbox_event,
        contract_decision_record,
        contract_sample,
        contract_state,
    )
    from .repository_serialization import (
        serialize_alert_outbox_event,
        serialize_live_state,
        serialize_status_decision_record,
    )
    from .decision_outbox import DecisionOutboxError
    from .state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        OutOfOrderTelemetryError,
        StateIntegrityError,
        telemetry_record_from_sample,
    )
except ImportError:
    from dynamo_telemetry_repository import DynamoTelemetryStateRepository, _marshal
    from repository_contract_suite import (
        ProcessingBundleRepositoryContractMixin,
        TelemetryStateRepositoryContractMixin,
        contract_alert_outbox_event,
        contract_decision_record,
        contract_sample,
        contract_state,
    )
    from repository_serialization import (
        serialize_alert_outbox_event,
        serialize_live_state,
        serialize_status_decision_record,
    )
    from decision_outbox import DecisionOutboxError
    from state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        OutOfOrderTelemetryError,
        StateIntegrityError,
        telemetry_record_from_sample,
    )


LOCAL_ENDPOINT_ENV = "VITAE_DYNAMODB_LOCAL_ENDPOINT"


class DynamoTelemetryLocalMixin:
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
        cls.table_name = f"vitae-telemetry-test-{uuid.uuid4().hex}"
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

    def new_repository(self, namespace=None):
        return DynamoTelemetryStateRepository(
            self.client,
            self.table_name,
            key_namespace=namespace or uuid.uuid4().hex,
        )


class DynamoTelemetryRepositoryContractTests(
    DynamoTelemetryLocalMixin,
    TelemetryStateRepositoryContractMixin,
    unittest.TestCase,
):
    def make_telemetry_state_repository(self):
        return self.new_repository()


class DynamoProcessingBundleRepositoryContractTests(
    DynamoTelemetryLocalMixin,
    ProcessingBundleRepositoryContractMixin,
    unittest.TestCase,
):
    def make_processing_bundle_repository(self):
        return self.new_repository()


class DynamoTelemetryPersistenceTests(DynamoTelemetryLocalMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.namespace = uuid.uuid4().hex
        self.repository = self.new_repository(self.namespace)
        self.sample = contract_sample()
        self.record = telemetry_record_from_sample(
            "contract-trip", "contract-lot-trip", self.sample
        )
        self.state = contract_state(self.sample)

    def test_first_commit_persists_exactly_three_items(self):
        self.repository.commit_sample_and_state(self.record, self.state, None)
        response = self.client.scan(
            TableName=self.table_name,
            FilterExpression="begins_with(PK, :namespace)",
            ExpressionAttributeValues={
                ":namespace": {"S": f"{self.namespace}#"}
            },
            ConsistentRead=True,
            Select="COUNT",
        )
        self.assertEqual(response["Count"], 3)

    def test_repository_restart_recovers_history_and_state(self):
        self.repository.commit_sample_and_state(self.record, self.state, None)
        restarted = self.new_repository(self.namespace)
        self.assertEqual(restarted.get_live_state(self.state.lot_trip_id), self.state)
        self.assertEqual(
            restarted.get_telemetry_history(self.record.lot_trip_id),
            (self.record,),
        )
        self.assertTrue(restarted.has_sample(self.record.device_id, self.record.sample_id))

    def test_concurrent_next_revision_has_one_winner(self):
        self.repository.commit_sample_and_state(self.record, self.state, None)
        samples = (
            contract_sample(sample_id="revision-a", minutes=5),
            contract_sample(sample_id="revision-b", minutes=6),
        )
        barrier = Barrier(2)

        def commit(sample):
            record = telemetry_record_from_sample(
                "contract-trip", "contract-lot-trip", sample
            )
            state = contract_state(sample, self.state)
            barrier.wait()
            try:
                self.repository.commit_sample_and_state(record, state, 1)
                return "committed"
            except ConcurrentStateUpdateError:
                return "concurrent"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(commit, samples))
        self.assertCountEqual(results, ("committed", "concurrent"))
        self.assertEqual(
            len(self.repository.get_telemetry_history("contract-lot-trip")), 2
        )

    def test_concurrent_duplicate_has_one_winner(self):
        barrier = Barrier(2)

        def commit():
            barrier.wait()
            try:
                self.repository.commit_sample_and_state(
                    self.record, self.state, None
                )
                return "committed"
            except DuplicateTelemetrySampleError:
                return "duplicate"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: commit(), range(2)))
        self.assertCountEqual(results, ("committed", "duplicate"))
        self.assertEqual(
            self.repository.get_telemetry_history("contract-lot-trip"),
            (self.record,),
        )

    def test_corrupt_live_state_document_fails_closed(self):
        document = serialize_live_state(self.state)
        document["schema_version"] = 999
        item = {
            "PK": f"{self.namespace}#LOTTRIP#contract-lot-trip",
            "SK": "LIVE_STATE",
            "entityType": "LIVE_STATE",
            "lotTripId": self.state.lot_trip_id,
            "tripId": self.state.trip_id,
            "deviceId": self.state.device_id,
            "productId": self.state.product_id,
            "productRuleVersion": self.state.product_rule_version,
            "revision": self.state.revision,
            "lastSampleTimestamp": document["last_sample_timestamp"],
            "document": document,
        }
        self.client.put_item(TableName=self.table_name, Item=_marshal(item))
        with self.assertRaises(StateIntegrityError):
            self.repository.get_live_state("contract-lot-trip")

    def test_corrupt_record_identity_fails_closed(self):
        self.repository.commit_sample_and_state(self.record, self.state, None)
        key = self.repository._record_key(self.record)
        item = self.client.get_item(
            TableName=self.table_name,
            Key=_marshal(key),
            ConsistentRead=True,
        )["Item"]
        item["sampleId"] = {"S": "corrupt-sample"}
        self.client.put_item(TableName=self.table_name, Item=item)
        with self.assertRaises(StateIntegrityError):
            self.repository.get_telemetry_history("contract-lot-trip")

    def test_no_alert_bundle_persists_exactly_four_items(self):
        decision = contract_decision_record(self.sample, self.state)
        self.repository.commit_processing_bundle(
            self.record,
            self.state,
            decision,
            None,
            None,
        )
        self.assertEqual(self._namespace_item_count(), 4)

    def test_alert_bundle_persists_exactly_five_items(self):
        decision = contract_decision_record(self.sample, self.state)
        event = contract_alert_outbox_event(decision)
        self.repository.commit_processing_bundle(
            self.record,
            self.state,
            decision,
            event,
            None,
        )
        self.assertEqual(self._namespace_item_count(), 5)

    def test_bundle_restart_recovers_decision_and_outbox(self):
        decision = contract_decision_record(self.sample, self.state)
        event = contract_alert_outbox_event(decision)
        self.repository.commit_processing_bundle(
            self.record,
            self.state,
            decision,
            event,
            None,
        )
        restarted = self.new_repository(self.namespace)
        self.assertEqual(restarted.get_decision(decision.decision_id), decision)
        self.assertEqual(
            restarted.get_decision_history(decision.lot_trip_id),
            (decision,),
        )
        self.assertEqual(restarted.get_outbox_event(event.event_id), event)

    def test_decision_key_is_lot_trip_partition_scoped(self):
        decision = contract_decision_record(self.sample, self.state)
        self.assertEqual(
            self.repository._decision_key(
                decision.lot_trip_id,
                decision.decision_id,
            ),
            {
                "PK": f"{self.namespace}#LOTTRIP#{decision.lot_trip_id}",
                "SK": f"DECISION#{decision.decision_id}",
            },
        )

    def test_decision_history_uses_strong_query_and_isolates_lot_trips(self):
        decision = contract_decision_record(self.sample, self.state)
        self.repository.commit_processing_bundle(
            self.record,
            self.state,
            decision,
            None,
            None,
        )
        other_sample = contract_sample(sample_id="other-lot-sample", minutes=1)
        other_state = replace(
            contract_state(other_sample),
            trip_id="other-trip",
            lot_trip_id="other-lot-trip",
        )
        other_record = telemetry_record_from_sample(
            other_state.trip_id,
            other_state.lot_trip_id,
            other_sample,
        )
        other_decision = contract_decision_record(other_sample, other_state)
        self.repository.commit_processing_bundle(
            other_record,
            other_state,
            other_decision,
            None,
            None,
        )

        with patch.object(
            self.client,
            "scan",
            side_effect=AssertionError("decision history must not scan"),
        ), patch.object(
            self.client,
            "query",
            wraps=self.client.query,
        ) as query:
            history = self.repository.get_decision_history(
                decision.lot_trip_id
            )

        self.assertEqual(history, (decision,))
        request = query.call_args.kwargs
        self.assertTrue(request["ConsistentRead"])
        self.assertEqual(
            request["KeyConditionExpression"],
            "PK = :pk AND begins_with(SK, :prefix)",
        )
        self.assertNotIn("IndexName", request)

    def test_concurrent_bundle_revision_has_one_winner(self):
        first_decision = contract_decision_record(self.sample, self.state)
        self.repository.commit_processing_bundle(
            self.record,
            self.state,
            first_decision,
            None,
            None,
        )
        samples = (
            contract_sample(sample_id="bundle-revision-a", minutes=5),
            contract_sample(sample_id="bundle-revision-b", minutes=6),
        )
        barrier = Barrier(2)

        def commit(sample):
            record = telemetry_record_from_sample(
                "contract-trip", "contract-lot-trip", sample
            )
            state = contract_state(sample, self.state)
            decision = contract_decision_record(sample, state)
            barrier.wait()
            try:
                self.repository.commit_processing_bundle(
                    record,
                    state,
                    decision,
                    None,
                    1,
                )
                return "committed"
            except ConcurrentStateUpdateError:
                return "concurrent"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(commit, samples))
        self.assertCountEqual(results, ("committed", "concurrent"))
        self.assertEqual(
            len(self.repository.get_decision_history("contract-lot-trip")),
            2,
        )

    def test_concurrent_duplicate_bundle_has_one_winner(self):
        decision = contract_decision_record(self.sample, self.state)
        event = contract_alert_outbox_event(decision)
        barrier = Barrier(2)

        def commit():
            barrier.wait()
            try:
                self.repository.commit_processing_bundle(
                    self.record,
                    self.state,
                    decision,
                    event,
                    None,
                )
                return "committed"
            except DuplicateTelemetrySampleError:
                return "duplicate"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: commit(), range(2)))
        self.assertCountEqual(results, ("committed", "duplicate"))
        self.assertEqual(
            self.repository.get_decision_history("contract-lot-trip"),
            (decision,),
        )
        self.assertEqual(self.repository.get_outbox_event(event.event_id), event)

    def test_bundle_rejects_older_timestamp_without_partial_writes(self):
        first_decision = contract_decision_record(self.sample, self.state)
        self.repository.commit_processing_bundle(
            self.record,
            self.state,
            first_decision,
            None,
            None,
        )
        older = contract_sample(sample_id="older-bundle", minutes=-1)
        older_record = telemetry_record_from_sample(
            "contract-trip", "contract-lot-trip", older
        )
        older_state = contract_state(older, self.state)
        older_decision = contract_decision_record(older, older_state)
        with self.assertRaises(OutOfOrderTelemetryError):
            self.repository.commit_processing_bundle(
                older_record,
                older_state,
                older_decision,
                None,
                1,
            )
        self.assertFalse(
            self.repository.has_sample(older.device_id, older.sample_id)
        )
        self.assertIsNone(
            self.repository.get_decision(older_decision.decision_id)
        )

    def test_existing_decision_rejects_entire_bundle(self):
        decision = contract_decision_record(self.sample, self.state)
        conflicting = replace(decision, engine_version="conflicting-engine")
        self.client.put_item(
            TableName=self.table_name,
            Item=_marshal(self.repository._decision_item(conflicting)),
        )
        with self.assertRaises(DecisionOutboxError):
            self.repository.commit_processing_bundle(
                self.record,
                self.state,
                decision,
                None,
                None,
            )
        self.assertFalse(
            self.repository.has_sample(self.record.device_id, self.record.sample_id)
        )
        self.assertIsNone(self.repository.get_live_state(self.record.lot_trip_id))
        self.assertEqual(
            self.repository.get_telemetry_history(self.record.lot_trip_id), ()
        )

    def test_existing_outbox_rejects_entire_bundle(self):
        decision = contract_decision_record(self.sample, self.state)
        event = contract_alert_outbox_event(decision)
        conflicting = replace(event, alert_policy_version="conflicting-policy")
        self.client.put_item(
            TableName=self.table_name,
            Item=_marshal(self.repository._outbox_item(conflicting)),
        )
        with self.assertRaises(DecisionOutboxError):
            self.repository.commit_processing_bundle(
                self.record,
                self.state,
                decision,
                event,
                None,
            )
        self.assertFalse(
            self.repository.has_sample(self.record.device_id, self.record.sample_id)
        )
        self.assertIsNone(self.repository.get_decision(decision.decision_id))
        self.assertIsNone(self.repository.get_live_state(self.record.lot_trip_id))

    def test_corrupt_decision_document_fails_closed(self):
        decision = contract_decision_record(self.sample, self.state)
        item = self.repository._decision_item(decision)
        document = serialize_status_decision_record(decision)
        document["schema_version"] = 999
        item["document"] = document
        self.client.put_item(TableName=self.table_name, Item=_marshal(item))
        with self.assertRaises(StateIntegrityError):
            self.repository.get_decision(decision.decision_id)

    def test_corrupt_outbox_document_fails_closed(self):
        decision = contract_decision_record(self.sample, self.state)
        event = contract_alert_outbox_event(decision)
        item = self.repository._outbox_item(event)
        document = serialize_alert_outbox_event(event)
        document["schema_version"] = 999
        item["document"] = document
        self.client.put_item(TableName=self.table_name, Item=_marshal(item))
        with self.assertRaises(StateIntegrityError):
            self.repository.get_outbox_event(event.event_id)

    def _namespace_item_count(self):
        response = self.client.scan(
            TableName=self.table_name,
            FilterExpression="begins_with(PK, :namespace)",
            ExpressionAttributeValues={
                ":namespace": {"S": f"{self.namespace}#"}
            },
            ConsistentRead=True,
            Select="COUNT",
        )
        return response["Count"]


class DynamoDecisionQueryPaginationTests(unittest.TestCase):
    def test_partition_query_follows_last_evaluated_key(self):
        client = MagicMock()
        first_key = _marshal({"PK": "lot", "SK": "DECISION#one"})
        client.query.side_effect = (
            {
                "Items": (_marshal({"PK": "lot", "SK": "DECISION#one"}),),
                "LastEvaluatedKey": first_key,
            },
            {
                "Items": (_marshal({"PK": "lot", "SK": "DECISION#two"}),),
            },
        )
        repository = DynamoTelemetryStateRepository(client, "table")

        items = repository._query("lot", "DECISION#")

        self.assertEqual(
            tuple(item["SK"] for item in items),
            ("DECISION#one", "DECISION#two"),
        )
        self.assertEqual(client.query.call_count, 2)
        self.assertEqual(
            client.query.call_args_list[1].kwargs["ExclusiveStartKey"],
            first_key,
        )
        self.assertTrue(
            all(call.kwargs["ConsistentRead"] for call in client.query.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
