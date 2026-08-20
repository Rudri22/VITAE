import inspect
import unittest

try:
    from . import (
        alert_lifecycle_service,
        completed_trip_outcome,
        decision_outbox,
        monitoring_service,
        operational_service,
        repository_contract_suite,
        repository_serialization,
        shipment_access,
        sqlite_completed_trip_outcome_repository,
        sqlite_identity_repository,
        shipment_lifecycle,
        shipment_registration,
        telemetry_processor,
    )
    from .alerting import AlertRepository, InMemoryAlertRepository
    from .completed_trip_outcome import (
        CompletedTripOutcomeRepository,
        InMemoryCompletedTripOutcomeRepository,
    )
    from .decision_outbox import (
        InMemoryProcessingBundleRepository,
        ProcessingBundleRepository,
    )
    from .state_repository import (
        IdentityRepository,
        InMemoryTelemetryStateRepository,
        TelemetryStateRepository,
    )
except ImportError:
    import alert_lifecycle_service
    import completed_trip_outcome
    import decision_outbox
    import monitoring_service
    import operational_service
    import repository_contract_suite
    import repository_serialization
    import shipment_access
    import sqlite_completed_trip_outcome_repository
    import sqlite_identity_repository
    import shipment_lifecycle
    import shipment_registration
    import telemetry_processor
    from alerting import AlertRepository, InMemoryAlertRepository
    from completed_trip_outcome import (
        CompletedTripOutcomeRepository,
        InMemoryCompletedTripOutcomeRepository,
    )
    from decision_outbox import (
        InMemoryProcessingBundleRepository,
        ProcessingBundleRepository,
    )
    from state_repository import (
        IdentityRepository,
        InMemoryTelemetryStateRepository,
        TelemetryStateRepository,
    )


class RepositoryBoundaryTests(unittest.TestCase):
    def test_memory_adapters_explicitly_implement_protocols(self):
        state_repository = InMemoryTelemetryStateRepository()
        combined_repository = shipment_access.InMemoryIdentityAccessRepository()
        self.assertIsInstance(state_repository, IdentityRepository)
        self.assertIsInstance(state_repository, TelemetryStateRepository)
        self.assertIsInstance(InMemoryAlertRepository(), AlertRepository)
        self.assertIsInstance(
            InMemoryCompletedTripOutcomeRepository(),
            CompletedTripOutcomeRepository,
        )
        self.assertIsInstance(
            InMemoryProcessingBundleRepository(), ProcessingBundleRepository
        )
        self.assertIsInstance(
            shipment_access.InMemoryShipmentAccessRepository(),
            shipment_access.ShipmentAccessRepository,
        )
        self.assertIsInstance(
            combined_repository,
            shipment_access.IdentityAccessRepository,
        )
        self.assertIsInstance(combined_repository, TelemetryStateRepository)
        self.assertIsInstance(combined_repository, ProcessingBundleRepository)

    def test_services_depend_on_protocol_annotations(self):
        expected = {
            telemetry_processor.TelemetryProcessor: {
                "identity_repository": IdentityRepository,
                "state_repository": TelemetryStateRepository,
            },
            operational_service.OperationalTelemetryService: {
                "alert_repository": AlertRepository,
            },
            monitoring_service.MonitoringService: {
                "identity_repository": IdentityRepository,
                "state_repository": TelemetryStateRepository,
                "alert_repository": AlertRepository,
            },
            shipment_registration.V2ShipmentRegistrationService: {
                "identity_repository": shipment_access.IdentityAccessRepository,
            },
            shipment_lifecycle.V2ShipmentLifecycleService: {
                "identity_repository": IdentityRepository,
            },
            alert_lifecycle_service.AlertLifecycleService: {
                "alert_repository": AlertRepository,
            },
        }
        for service, annotations in expected.items():
            parameters = inspect.signature(service.__init__).parameters
            for name, protocol in annotations.items():
                with self.subTest(service=service.__name__, parameter=name):
                    self.assertIs(parameters[name].annotation, protocol)

    def test_new_contract_and_serialization_modules_have_no_aws_dependency(self):
        forbidden = ("boto3", "botocore", "dynamodb", "aws_sdk")
        for module in (
            repository_contract_suite,
            repository_serialization,
            decision_outbox,
            completed_trip_outcome,
            shipment_access,
            sqlite_completed_trip_outcome_repository,
            sqlite_identity_repository,
        ):
            source = inspect.getsource(module).lower()
            with self.subTest(module=module.__name__):
                self.assertFalse(any(name in source for name in forbidden))


if __name__ == "__main__":
    unittest.main()
