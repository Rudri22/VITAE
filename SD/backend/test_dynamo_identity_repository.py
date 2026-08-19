import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta, timezone
from threading import Barrier

import boto3

try:
    from .dynamo_identity_repository import DynamoIdentityAccessRepository
    from .repository_contract_suite import (
        IdentityRepositoryContractMixin,
        ShipmentAccessRepositoryContractMixin,
        contract_assignment,
        contract_shipment_access,
        contract_trip,
    )
    from .shipment_access import (
        IdentityAccessRepository,
        ShipmentAccessConflictError,
    )
    from .state_repository import StateIntegrityError
    from .trip_identity import TripStatus
except ImportError:
    from dynamo_identity_repository import DynamoIdentityAccessRepository
    from repository_contract_suite import (
        IdentityRepositoryContractMixin,
        ShipmentAccessRepositoryContractMixin,
        contract_assignment,
        contract_shipment_access,
        contract_trip,
    )
    from shipment_access import IdentityAccessRepository, ShipmentAccessConflictError
    from state_repository import StateIntegrityError
    from trip_identity import TripStatus


LOCAL_ENDPOINT_ENV = "VITAE_DYNAMODB_LOCAL_ENDPOINT"


class DynamoLocalTestMixin:
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
        cls.table_name = f"vitae-identity-test-{uuid.uuid4().hex}"
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
        return DynamoIdentityAccessRepository(
            self.client,
            self.table_name,
            key_namespace=namespace or uuid.uuid4().hex,
        )


class DynamoIdentityRepositoryContractTests(
    DynamoLocalTestMixin,
    IdentityRepositoryContractMixin,
    unittest.TestCase,
):
    def make_identity_repository(self):
        return self.new_repository()


class DynamoShipmentAccessRepositoryContractTests(
    DynamoLocalTestMixin,
    ShipmentAccessRepositoryContractMixin,
    unittest.TestCase,
):
    def make_shipment_access_repository(self):
        return self.new_repository()


class DynamoIdentityPersistenceTests(DynamoLocalTestMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.namespace = uuid.uuid4().hex
        self.repository = self.new_repository(self.namespace)

    def test_adapter_satisfies_combined_identity_access_protocol(self):
        self.assertIsInstance(self.repository, IdentityAccessRepository)

    def test_registration_persists_all_ten_items_atomically(self):
        trip = contract_trip()
        assignment = contract_assignment()
        access = contract_shipment_access()
        self.repository.register_trip_assignment_and_access(
            trip, assignment, access
        )
        response = self.client.scan(
            TableName=self.table_name,
            FilterExpression="begins_with(PK, :namespace)",
            ExpressionAttributeValues={
                ":namespace": {"S": f"{self.namespace}#"}
            },
            ConsistentRead=True,
            Select="COUNT",
        )
        self.assertEqual(response["Count"], 10)
        self.assertEqual(self.repository.get_trip_by_id(trip.trip_id), trip)
        self.assertEqual(
            self.repository.get_shipment_access(access.lot_trip_id), access
        )

    def test_access_conflict_rolls_back_identity_and_reservation(self):
        existing = replace(
            contract_shipment_access(), lot_trip_id="existing-lot-trip"
        )
        self.repository.register_shipment_access(existing)
        trip = contract_trip()
        assignment = contract_assignment()
        with self.assertRaises(ShipmentAccessConflictError):
            self.repository.register_trip_assignment_and_access(
                trip,
                assignment,
                contract_shipment_access(),
            )
        self.assertIsNone(self.repository.get_trip_by_id(trip.trip_id))
        self.assertEqual(
            self.repository.get_device_assignments(assignment.device_id), ()
        )
        self.assertEqual(
            self.repository.get_shipment_access(existing.lot_trip_id), existing
        )

    def test_restart_preserves_complete_registration(self):
        trip = contract_trip()
        assignment = contract_assignment()
        access = contract_shipment_access()
        self.repository.register_trip_assignment_and_access(
            trip, assignment, access
        )
        restarted = self.new_repository(self.namespace)
        self.assertEqual(restarted.get_trip_by_id(trip.trip_id), trip)
        self.assertEqual(
            restarted.get_trip_by_lot_trip_id(trip.lot_trip_id), trip
        )
        self.assertEqual(
            restarted.get_device_assignments(assignment.device_id),
            (assignment,),
        )
        self.assertEqual(
            restarted.get_shipment_access(access.lot_trip_id), access
        )

    def test_combined_registration_accepts_identical_preexisting_access(self):
        trip = contract_trip()
        assignment = contract_assignment()
        access = contract_shipment_access()
        self.repository.register_shipment_access(access)
        self.repository.register_trip_assignment_and_access(
            trip, assignment, access
        )
        self.assertEqual(self.repository.get_trip_by_id(trip.trip_id), trip)
        self.assertEqual(
            self.repository.get_device_assignments(assignment.device_id),
            (assignment,),
        )

    def test_pair_registration_accepts_identical_preexisting_trip(self):
        trip = contract_trip()
        assignment = contract_assignment()
        self.repository.register_trip(trip)
        self.repository.register_trip_and_assignment(trip, assignment)
        self.assertEqual(
            self.repository.get_device_assignments(assignment.device_id),
            (assignment,),
        )

    def test_non_utc_assignment_key_survives_restart_and_lifecycle(self):
        trip = contract_trip()
        assignment = replace(
            contract_assignment(),
            assigned_at=contract_assignment().assigned_at.astimezone(
                timezone(timedelta(hours=3))
            ),
        )
        self.repository.register_trip_and_assignment(trip, assignment)
        restarted = self.new_repository(self.namespace)
        active_trip, active_assignment = restarted.transition_trip_and_assignment(
            trip.trip_id,
            assignment.assignment_id,
            TripStatus.PLANNED,
            TripStatus.ACTIVE,
            False,
            True,
        )
        self.assertEqual(active_trip.status, TripStatus.ACTIVE)
        self.assertTrue(active_assignment.active)

    def test_two_repositories_cannot_reserve_same_device(self):
        first = self.new_repository(self.namespace)
        second = self.new_repository(self.namespace)
        barrier = Barrier(2)

        def register(repository, suffix):
            trip = replace(
                contract_trip(),
                trip_id=f"trip-{suffix}",
                lot_trip_id=f"lot-trip-{suffix}",
                lot_id=f"lot-{suffix}",
            )
            assignment = replace(
                contract_assignment(),
                assignment_id=f"assignment-{suffix}",
                trip_id=trip.trip_id,
                lot_trip_id=trip.lot_trip_id,
            )
            barrier.wait()
            try:
                repository.register_trip_and_assignment(trip, assignment)
                return "registered", trip
            except StateIntegrityError:
                return "conflict", trip

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(register, first, "one"),
                executor.submit(register, second, "two"),
            )
            outcomes = tuple(future.result() for future in futures)

        self.assertCountEqual(
            (outcome for outcome, _ in outcomes),
            ("registered", "conflict"),
        )
        winner = next(trip for outcome, trip in outcomes if outcome == "registered")
        loser = next(trip for outcome, trip in outcomes if outcome == "conflict")
        restarted = self.new_repository(self.namespace)
        self.assertEqual(restarted.get_trip_by_id(winner.trip_id), winner)
        self.assertIsNone(restarted.get_trip_by_id(loser.trip_id))

    def test_concurrent_stale_lifecycle_transition_has_one_winner(self):
        trip = contract_trip()
        assignment = contract_assignment()
        self.repository.register_trip_and_assignment(trip, assignment)
        first = self.new_repository(self.namespace)
        second = self.new_repository(self.namespace)
        barrier = Barrier(2)

        def activate(repository):
            barrier.wait()
            try:
                repository.transition_trip_and_assignment(
                    trip.trip_id,
                    assignment.assignment_id,
                    TripStatus.PLANNED,
                    TripStatus.ACTIVE,
                    False,
                    True,
                )
                return "activated"
            except StateIntegrityError:
                return "stale"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(activate, (first, second)))
        self.assertCountEqual(outcomes, ("activated", "stale"))
        self.assertEqual(
            self.repository.get_trip_by_id(trip.trip_id).status,
            TripStatus.ACTIVE,
        )

    def test_activation_completion_and_device_reuse(self):
        trip = contract_trip()
        assignment = contract_assignment()
        self.repository.register_trip_and_assignment(trip, assignment)
        self.repository.transition_trip_and_assignment(
            trip.trip_id,
            assignment.assignment_id,
            TripStatus.PLANNED,
            TripStatus.ACTIVE,
            False,
            True,
        )
        completed_trip, completed_assignment = (
            self.repository.transition_trip_and_assignment(
                trip.trip_id,
                assignment.assignment_id,
                TripStatus.ACTIVE,
                TripStatus.COMPLETED,
                True,
                False,
            )
        )
        self.assertEqual(completed_trip.status, TripStatus.COMPLETED)
        self.assertFalse(completed_assignment.active)

        next_trip = replace(
            contract_trip(),
            trip_id="replacement-trip",
            lot_trip_id="replacement-lot-trip",
            lot_id="replacement-lot",
        )
        next_assignment = replace(
            contract_assignment(),
            assignment_id="replacement-assignment",
            trip_id=next_trip.trip_id,
            lot_trip_id=next_trip.lot_trip_id,
        )
        self.repository.register_trip_and_assignment(next_trip, next_assignment)
        self.assertEqual(
            self.repository.get_trip_by_id(next_trip.trip_id), next_trip
        )

    def test_compensation_removes_access_and_identity_together(self):
        trip = contract_trip()
        assignment = contract_assignment()
        access = contract_shipment_access()
        self.repository.register_trip_assignment_and_access(
            trip, assignment, access
        )
        self.repository.unregister_planned_trip_assignment_and_access(
            trip.trip_id,
            assignment.assignment_id,
            access.lot_trip_id,
            access.shipment_id,
        )
        self.assertIsNone(self.repository.get_trip_by_id(trip.trip_id))
        self.assertIsNone(
            self.repository.get_shipment_access(access.lot_trip_id)
        )

    def test_corrupt_serialized_document_is_rejected(self):
        trip = contract_trip()
        self.repository.register_trip(trip)
        key = {
            "PK": {"S": f"{self.namespace}#TRIP#{trip.trip_id}"},
            "SK": {"S": "META"},
        }
        item = self.client.get_item(
            TableName=self.table_name,
            Key=key,
            ConsistentRead=True,
        )["Item"]
        item["document"]["M"]["schema_version"] = {"N": "99"}
        self.client.put_item(TableName=self.table_name, Item=item)
        with self.assertRaisesRegex(
            StateIntegrityError, "Persisted TripIdentity document is invalid"
        ):
            self.repository.get_trip_by_id(trip.trip_id)


if __name__ == "__main__":
    unittest.main()
