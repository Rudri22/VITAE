import unittest

try:
    from .alerting import InMemoryAlertRepository
    from .repository_contract_suite import (
        AlertRepositoryContractMixin,
        IdentityRepositoryContractMixin,
        TelemetryStateRepositoryContractMixin,
    )
    from .state_repository import InMemoryTelemetryStateRepository
except ImportError:
    from alerting import InMemoryAlertRepository
    from repository_contract_suite import (
        AlertRepositoryContractMixin,
        IdentityRepositoryContractMixin,
        TelemetryStateRepositoryContractMixin,
    )
    from state_repository import InMemoryTelemetryStateRepository


class InMemoryIdentityRepositoryContractTests(
    IdentityRepositoryContractMixin,
    unittest.TestCase,
):
    def make_identity_repository(self):
        return InMemoryTelemetryStateRepository()


class InMemoryTelemetryStateRepositoryContractTests(
    TelemetryStateRepositoryContractMixin,
    unittest.TestCase,
):
    def make_telemetry_state_repository(self):
        return InMemoryTelemetryStateRepository()


class InMemoryAlertRepositoryContractTests(
    AlertRepositoryContractMixin,
    unittest.TestCase,
):
    def make_alert_repository(self):
        return InMemoryAlertRepository()


if __name__ == "__main__":
    unittest.main()
