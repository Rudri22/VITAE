import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import MagicMock, patch

import boto3

try:
    from .dynamo_trip_completion_repository import DynamoTripCompletionRepository
    from .repository_contract_suite import (
        CONTRACT_TIME,
        TripCompletionRepositoryContractMixin,
        contract_assignment,
        contract_sample,
        contract_state,
        contract_trip,
    )
    from .state_repository import TripNotActiveAtCommitError, telemetry_record_from_sample
    from .trip_completion import TripCompletionConflictError
    from .trip_identity import TripStatus
except ImportError:
    from dynamo_trip_completion_repository import DynamoTripCompletionRepository
    from repository_contract_suite import (
        CONTRACT_TIME,
        TripCompletionRepositoryContractMixin,
        contract_assignment,
        contract_sample,
        contract_state,
        contract_trip,
    )
    from state_repository import TripNotActiveAtCommitError, telemetry_record_from_sample
    from trip_completion import TripCompletionConflictError
    from trip_identity import TripStatus


LOCAL_ENDPOINT_ENV = "VITAE_DYNAMODB_LOCAL_ENDPOINT"


class DynamoTripCompletionLocalMixin:
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
        suffix = uuid.uuid4().hex
        cls.identity_table = f"vitae-completion-identity-{suffix}"
        cls.telemetry_table = f"vitae-completion-telemetry-{suffix}"
        for table_name in (cls.identity_table, cls.telemetry_table):
            cls.client.create_table(
                TableName=table_name,
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
            cls.client.get_waiter("table_exists").wait(TableName=table_name)

    @classmethod
    def tearDownClass(cls):
        try:
            for table_name in (cls.identity_table, cls.telemetry_table):
                cls.client.delete_table(TableName=table_name)
        finally:
            super().tearDownClass()

    def new_repository(self, namespace=None):
        return DynamoTripCompletionRepository(
            self.client,
            self.identity_table,
            self.telemetry_table,
            key_namespace=namespace or uuid.uuid4().hex,
        )


class DynamoTripCompletionTransactionShapeTests(unittest.TestCase):
    def test_completion_is_one_six_action_cross_table_transaction(self):
        client = MagicMock()
        repository = DynamoTripCompletionRepository(
            client,
            "identity-table",
            "telemetry-table",
            key_namespace="shape",
        )
        trip = contract_trip(status=TripStatus.ACTIVE)
        assignment = contract_assignment(active=True)
        sample = contract_sample(minutes=5)
        state = contract_state(sample)
        with patch.object(
            repository,
            "_load_completion_snapshot",
            return_value=(trip, assignment, state, None),
        ):
            result = repository.complete_trip(
                trip.trip_id,
                assignment.assignment_id,
                completed_at=CONTRACT_TIME + timedelta(hours=1),
            )
        actions = client.transact_write_items.call_args.kwargs["TransactItems"]
        self.assertEqual(len(actions), 6)
        self.assertEqual(actions[-2]["ConditionCheck"]["TableName"], "telemetry-table")
        self.assertIn("document = :document", actions[-2]["ConditionCheck"]["ConditionExpression"])
        self.assertEqual(actions[-1]["Put"]["TableName"], "identity-table")
        self.assertIn("attribute_not_exists", actions[-1]["Put"]["ConditionExpression"])
        self.assertEqual(result.final_live_state, state)

    def test_no_telemetry_completion_conditions_on_live_state_absence(self):
        client = MagicMock()
        repository = DynamoTripCompletionRepository(
            client, "identity-table", "telemetry-table"
        )
        trip = contract_trip(status=TripStatus.ACTIVE)
        assignment = contract_assignment(active=True)
        with patch.object(
            repository,
            "_load_completion_snapshot",
            return_value=(trip, assignment, None, None),
        ):
            repository.complete_trip(
                trip.trip_id,
                assignment.assignment_id,
                completed_at=CONTRACT_TIME + timedelta(hours=1),
            )
        condition = client.transact_write_items.call_args.kwargs["TransactItems"][-2]
        self.assertEqual(
            condition["ConditionCheck"]["ConditionExpression"],
            "attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )


class DynamoTripCompletionContractTests(
    DynamoTripCompletionLocalMixin,
    TripCompletionRepositoryContractMixin,
    unittest.TestCase,
):
    def make_trip_completion_repository(self):
        return self.new_repository()


class DynamoTripCompletionRaceTests(
    DynamoTripCompletionLocalMixin,
    unittest.TestCase,
):
    def test_telemetry_and_completion_have_one_serializable_winner(self):
        repository = self.new_repository()
        trip = contract_trip()
        assignment = contract_assignment()
        repository.register_trip_and_assignment(trip, assignment)
        active_trip, _ = repository.transition_trip_and_assignment(
            trip.trip_id,
            assignment.assignment_id,
            TripStatus.PLANNED,
            TripStatus.ACTIVE,
            False,
            True,
        )
        first = contract_sample(minutes=1)
        first_state = contract_state(first)
        repository.commit_sample_and_state(
            telemetry_record_from_sample(
                active_trip.trip_id, active_trip.lot_trip_id, first
            ), first_state, None
        )
        second = contract_sample(sample_id="dynamo-race-sample", minutes=2)
        second_state = contract_state(second, first_state)
        barrier = Barrier(2)

        def complete():
            barrier.wait()
            try:
                return repository.complete_trip(
                    trip.trip_id,
                    assignment.assignment_id,
                    completed_at=CONTRACT_TIME + timedelta(hours=1),
                )
            except TripCompletionConflictError:
                return repository.complete_trip(
                    trip.trip_id,
                    assignment.assignment_id,
                    completed_at=CONTRACT_TIME + timedelta(hours=1),
                )

        def commit():
            barrier.wait()
            try:
                repository.commit_sample_and_state(
                    telemetry_record_from_sample(
                        active_trip.trip_id, active_trip.lot_trip_id, second
                    ), second_state, 1
                )
                return "committed"
            except TripNotActiveAtCommitError:
                return "blocked"

        with ThreadPoolExecutor(max_workers=2) as executor:
            completion_future = executor.submit(complete)
            telemetry_future = executor.submit(commit)
            result = completion_future.result()
            telemetry_result = telemetry_future.result()
        expected = second_state if telemetry_result == "committed" else first_state
        self.assertEqual(result.final_live_state, expected)
        self.assertEqual(repository.get_live_state(trip.lot_trip_id), expected)


if __name__ == "__main__":
    unittest.main()
