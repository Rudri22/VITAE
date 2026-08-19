import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import boto3

try:
    from .dynamo_telemetry_repository import (
        DynamoTelemetryStateRepository,
        _marshal,
    )
    from .repository_contract_suite import (
        TelemetryStateRepositoryContractMixin,
        contract_sample,
        contract_state,
    )
    from .repository_serialization import serialize_live_state
    from .state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        StateIntegrityError,
        telemetry_record_from_sample,
    )
except ImportError:
    from dynamo_telemetry_repository import DynamoTelemetryStateRepository, _marshal
    from repository_contract_suite import (
        TelemetryStateRepositoryContractMixin,
        contract_sample,
        contract_state,
    )
    from repository_serialization import serialize_live_state
    from state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
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


if __name__ == "__main__":
    unittest.main()
