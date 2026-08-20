import os
import unittest
import uuid
from unittest.mock import Mock, patch

import boto3

try:
    from .alert_lifecycle_service import (
        AlertActor,
        AlertActorRole,
        AlertLifecycleAccessDeniedError,
        AlertLifecycleService,
    )
    from .dynamo_identity_repository import DynamoIdentityAccessRepository
    from .dynamo_telemetry_repository import DynamoTelemetryStateRepository
    from .monitoring_service import MonitoringService
    from .repository_config import (
        RepositoryConfig,
        RepositoryConfigurationError,
        RepositoryMode,
        compose_repositories,
        shipment_access_resolver,
    )
    from .repository_contract_suite import (
        contract_alert_outbox_event,
        contract_decision_record,
        contract_sample,
        contract_state,
        contract_alert,
        contract_shipment_access,
    )
    from .product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_STATE,
    )
    from .shipment_access import (
        IdentityAccessRepository,
        InMemoryIdentityAccessRepository,
        ShipmentAccessRepository,
    )
    from .shipment_lifecycle import V2ShipmentLifecycleService
    from .shipment_registration import V2ShipmentRegistrationService
    from .state_repository import (
        TelemetryStateRepository,
        telemetry_record_from_sample,
    )
    from .telemetry_processor import TelemetryProcessor
except ImportError:
    from alert_lifecycle_service import (
        AlertActor,
        AlertActorRole,
        AlertLifecycleAccessDeniedError,
        AlertLifecycleService,
    )
    from dynamo_identity_repository import DynamoIdentityAccessRepository
    from dynamo_telemetry_repository import DynamoTelemetryStateRepository
    from monitoring_service import MonitoringService
    from repository_config import (
        RepositoryConfig,
        RepositoryConfigurationError,
        RepositoryMode,
        compose_repositories,
        shipment_access_resolver,
    )
    from repository_contract_suite import (
        contract_alert_outbox_event,
        contract_decision_record,
        contract_sample,
        contract_state,
        contract_alert,
        contract_shipment_access,
    )
    from product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_STATE,
    )
    from shipment_access import (
        IdentityAccessRepository,
        InMemoryIdentityAccessRepository,
        ShipmentAccessRepository,
    )
    from shipment_lifecycle import V2ShipmentLifecycleService
    from shipment_registration import V2ShipmentRegistrationService
    from state_repository import (
        TelemetryStateRepository,
        telemetry_record_from_sample,
    )
    from telemetry_processor import TelemetryProcessor


class RepositoryConfigTests(unittest.TestCase):
    def test_memory_is_the_default_without_aws_settings(self):
        config = RepositoryConfig.from_environment({})
        self.assertEqual(config, RepositoryConfig(mode=RepositoryMode.MEMORY))

    def test_explicit_memory_does_not_require_aws_settings(self):
        config = RepositoryConfig.from_environment(
            {"VITAE_REPOSITORY_MODE": "memory"}
        )
        composition = compose_repositories(config)
        self.assertIsInstance(
            composition.identity_repository,
            InMemoryIdentityAccessRepository,
        )
        self.assertIs(
            composition.identity_repository,
            composition.shipment_access_repository,
        )
        self.assertIs(
            composition.identity_repository,
            composition.telemetry_state_repository,
        )
        self.assertFalse(composition.identity_is_persistent)
        self.assertFalse(composition.telemetry_is_persistent)
        self.assertFalse(composition.alerts_are_persistent)

    def test_unknown_mode_fails_closed(self):
        with self.assertRaisesRegex(
            RepositoryConfigurationError, "must be memory or dynamodb"
        ):
            RepositoryConfig.from_environment(
                {"VITAE_REPOSITORY_MODE": "sqlite-by-mistake"}
            )

    def test_dynamodb_requires_region_and_both_tables(self):
        cases = (
            {"VITAE_REPOSITORY_MODE": "dynamodb"},
            {
                "VITAE_REPOSITORY_MODE": "dynamodb",
                "VITAE_AWS_REGION": "us-east-1",
            },
            {
                "VITAE_REPOSITORY_MODE": "dynamodb",
                "VITAE_AWS_REGION": "us-east-1",
                "VITAE_IDENTITY_TABLE": "identity-dev",
            },
        )
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(RepositoryConfigurationError):
                    RepositoryConfig.from_environment(values)

    def test_dynamodb_environment_is_parsed_without_credentials(self):
        config = RepositoryConfig.from_environment(
            {
                "VITAE_REPOSITORY_MODE": "DYNAMODB",
                "VITAE_AWS_REGION": "us-east-1",
                "VITAE_IDENTITY_TABLE": "identity-dev",
                "VITAE_TELEMETRY_TABLE": "telemetry-dev",
                "VITAE_AWS_PROFILE": "vitae-dev",
                "VITAE_DYNAMODB_ENDPOINT_URL": "http://127.0.0.1:8000",
                "VITAE_DYNAMODB_KEY_NAMESPACE": "test-scope",
            }
        )
        self.assertEqual(config.mode, RepositoryMode.DYNAMODB)
        self.assertEqual(config.aws_region, "us-east-1")
        self.assertEqual(config.identity_table, "identity-dev")
        self.assertEqual(config.telemetry_table, "telemetry-dev")
        self.assertEqual(config.aws_profile, "vitae-dev")
        self.assertEqual(config.key_namespace, "test-scope")

    def test_dynamodb_composition_uses_injected_client_and_persistent_telemetry(self):
        client = Mock()
        config = RepositoryConfig(
            mode=RepositoryMode.DYNAMODB,
            aws_region="us-east-1",
            identity_table="identity-dev",
            telemetry_table="telemetry-dev",
        )
        composition = compose_repositories(config, dynamodb_client=client)
        client.describe_table.assert_called_once_with(TableName="telemetry-dev")
        self.assertIsInstance(
            composition.identity_repository,
            DynamoIdentityAccessRepository,
        )
        self.assertIs(
            composition.identity_repository,
            composition.shipment_access_repository,
        )
        self.assertIsInstance(
            composition.identity_repository,
            IdentityAccessRepository,
        )
        self.assertIsInstance(
            composition.telemetry_state_repository,
            DynamoTelemetryStateRepository,
        )
        self.assertEqual(
            composition.telemetry_state_repository.table_name,
            "telemetry-dev",
        )
        self.assertIsInstance(
            composition.shipment_access_repository,
            ShipmentAccessRepository,
        )
        self.assertTrue(composition.identity_is_persistent)
        self.assertTrue(composition.telemetry_is_persistent)
        self.assertFalse(composition.alerts_are_persistent)

    def test_dynamodb_initialization_failure_does_not_fallback_to_memory(self):
        config = RepositoryConfig(
            mode=RepositoryMode.DYNAMODB,
            aws_region="us-east-1",
            identity_table="identity-dev",
            telemetry_table="telemetry-dev",
        )
        with patch(
            f"{RepositoryConfig.__module__}._build_dynamodb_client",
            side_effect=RepositoryConfigurationError("AWS unavailable"),
        ):
            with self.assertRaisesRegex(
                RepositoryConfigurationError, "AWS unavailable"
            ):
                compose_repositories(config)

    def test_unavailable_telemetry_table_fails_without_memory_fallback(self):
        client = Mock()
        client.describe_table.side_effect = RuntimeError("table not found")
        config = RepositoryConfig(
            mode=RepositoryMode.DYNAMODB,
            aws_region="us-east-1",
            identity_table="identity-dev",
            telemetry_table="missing-telemetry-dev",
        )

        with self.assertRaisesRegex(
            RepositoryConfigurationError,
            "telemetry table is unavailable",
        ):
            compose_repositories(config, dynamodb_client=client)

    def test_shipment_access_resolver_translates_repository_domain_model(self):
        repository = InMemoryIdentityAccessRepository()
        access = contract_shipment_access()
        repository.register_shipment_access(access)
        resolved = shipment_access_resolver(repository)(access.lot_trip_id)
        self.assertEqual(
            resolved,
            {
                "shipmentId": access.shipment_id,
                "lotTripId": access.lot_trip_id,
                "organizationId": access.organization_id,
                "driverId": access.driver_id,
            },
        )


class CompositionServiceContractMixin:
    def make_composition(self):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.composition = self.make_composition()

    def test_composed_services_share_identity_and_access_authority(self):
        repositories = self.composition
        registration = V2ShipmentRegistrationService(
            repositories.identity_repository
        ).register_for_shipment(
            {
                "enabled": True,
                "productId": GARDASIL_9_PRODUCT_ID,
                "presentation": GARDASIL_9_PRESENTATION,
                "state": GARDASIL_9_STATE,
                "lotId": "contract-lot",
                "deviceId": "contract-device",
            },
            {
                "shipmentId": "contract-shipment",
                "productName": "GARDASIL 9",
                "sensorId": "contract-device",
                "organizationId": "contract-organization",
                "driverId": "contract-driver",
                "origin": "Origin",
                "destination": "Destination",
                "departureAt": "2026-08-19T12:00:00Z",
                "lastUpdated": "2026-08-19T12:00:00Z",
            },
        )
        trip = registration.trip_identity
        assignment = registration.device_assignment
        access = registration.shipment_access

        lifecycle = V2ShipmentLifecycleService(
            repositories.identity_repository
        )
        lifecycle.activate_for_shipment(
            {
                "tripId": trip.trip_id,
                "lotTripId": trip.lot_trip_id,
                "v2DeviceAssignmentId": assignment.assignment_id,
            }
        )
        processor = TelemetryProcessor(
            repositories.identity_repository,
            repositories.telemetry_state_repository,
        )
        result = processor.process(
            {
                "sample_id": "composition-safe-sample",
                "device_id": assignment.device_id,
                "timestamp": "2026-08-19T12:01:00Z",
                "temperature": 6.0,
            }
        )
        monitoring = MonitoringService(
            repositories.identity_repository,
            repositories.telemetry_state_repository,
            repositories.alert_repository,
        )
        snapshot = monitoring.get_live_snapshot(trip.lot_trip_id)
        self.assertEqual(snapshot.trip_identity.status.value, "ACTIVE")
        self.assertEqual(snapshot.live_state, result.live_state)

        alert = contract_alert(
            trip_id=trip.trip_id,
            lot_trip_id=trip.lot_trip_id,
            device_id=trip.device_id,
            sample_id=result.telemetry_record.sample_id,
        )
        repositories.alert_repository.save_alert(alert)
        lifecycle_alerts = AlertLifecycleService(
            repositories.alert_repository,
            shipment_access_resolver(repositories.shipment_access_repository),
        )
        organization = AlertActor(
            actor_id="organization-user",
            role=AlertActorRole.ORGANIZATION,
            organization_id=access.organization_id,
        )
        old_driver = AlertActor(
            actor_id="old-driver-user",
            role=AlertActorRole.DRIVER,
            organization_id=access.organization_id,
            driver_id=access.driver_id,
        )
        self.assertEqual(
            lifecycle_alerts.get_alert(access.lot_trip_id, alert.alert_id, organization),
            alert,
        )
        self.assertEqual(
            lifecycle_alerts.get_alert(access.lot_trip_id, alert.alert_id, old_driver),
            alert,
        )

        repositories.shipment_access_repository.transition_shipment_access_driver(
            access.lot_trip_id,
            access.driver_id,
            "replacement-driver",
        )
        with self.assertRaises(AlertLifecycleAccessDeniedError):
            lifecycle_alerts.get_alert(
                access.lot_trip_id,
                alert.alert_id,
                old_driver,
            )
        replacement = AlertActor(
            actor_id="replacement-driver-user",
            role=AlertActorRole.DRIVER,
            organization_id=access.organization_id,
            driver_id="replacement-driver",
        )
        self.assertEqual(
            lifecycle_alerts.get_alert(
                access.lot_trip_id,
                alert.alert_id,
                replacement,
            ),
            alert,
        )


class MemoryCompositionServiceTests(
    CompositionServiceContractMixin,
    unittest.TestCase,
):
    def make_composition(self):
        return compose_repositories(RepositoryConfig(mode=RepositoryMode.MEMORY))


class DynamoLocalCompositionServiceTests(
    CompositionServiceContractMixin,
    unittest.TestCase,
):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        endpoint = os.environ.get("VITAE_DYNAMODB_LOCAL_ENDPOINT")
        if not endpoint:
            raise unittest.SkipTest(
                "Set VITAE_DYNAMODB_LOCAL_ENDPOINT for composition integration"
            )
        cls.client = boto3.client(
            "dynamodb",
            endpoint_url=endpoint,
            region_name="us-east-1",
            aws_access_key_id="local",
            aws_secret_access_key="local",
        )
        suffix = uuid.uuid4().hex
        cls.identity_table_name = f"vitae-composition-identity-{suffix}"
        cls.telemetry_table_name = f"vitae-composition-telemetry-{suffix}"
        for table_name in (cls.identity_table_name, cls.telemetry_table_name):
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
            for table_name in (cls.identity_table_name, cls.telemetry_table_name):
                cls.client.delete_table(TableName=table_name)
        finally:
            super().tearDownClass()

    def make_composition(self):
        return compose_repositories(
            RepositoryConfig(
                mode=RepositoryMode.DYNAMODB,
                aws_region="us-east-1",
                identity_table=self.identity_table_name,
                telemetry_table=self.telemetry_table_name,
                key_namespace=uuid.uuid4().hex,
            ),
            dynamodb_client=self.client,
        )

    def test_restart_preserves_bundle_but_not_memory_alert_repository(self):
        namespace = uuid.uuid4().hex
        config = RepositoryConfig(
            mode=RepositoryMode.DYNAMODB,
            aws_region="us-east-1",
            identity_table=self.identity_table_name,
            telemetry_table=self.telemetry_table_name,
            key_namespace=namespace,
        )
        first = compose_repositories(config, dynamodb_client=self.client)
        sample = contract_sample()
        state = contract_state(sample)
        record = telemetry_record_from_sample(
            state.trip_id,
            state.lot_trip_id,
            sample,
        )
        decision = contract_decision_record(sample, state)
        outbox = contract_alert_outbox_event(decision)
        first.telemetry_state_repository.commit_processing_bundle(
            record,
            state,
            decision,
            outbox,
            expected_revision=None,
        )
        first.alert_repository.save_alert(outbox.alert_candidate)

        restarted = compose_repositories(config, dynamodb_client=self.client)

        self.assertEqual(
            restarted.telemetry_state_repository.get_live_state(state.lot_trip_id),
            state,
        )
        self.assertEqual(
            restarted.telemetry_state_repository.get_decision(decision.decision_id),
            decision,
        )
        self.assertEqual(
            restarted.telemetry_state_repository.get_outbox_event(outbox.event_id),
            outbox,
        )
        self.assertIsNone(
            restarted.alert_repository.get_alert(outbox.alert_candidate.alert_id)
        )


if __name__ == "__main__":
    unittest.main()
