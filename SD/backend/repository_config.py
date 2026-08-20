from dataclasses import dataclass
from enum import Enum
import os
from typing import Mapping, Optional

try:
    from .alerting import AlertRepository, InMemoryAlertRepository
    from .decision_outbox import (
        InMemoryProcessingBundleRepository,
        ProcessingBundleRepository,
    )
    from .shipment_access import (
        IdentityAccessRepository,
        InMemoryIdentityAccessRepository,
        ShipmentAccessRepository,
    )
except ImportError:
    from alerting import AlertRepository, InMemoryAlertRepository
    from decision_outbox import (
        InMemoryProcessingBundleRepository,
        ProcessingBundleRepository,
    )
    from shipment_access import (
        IdentityAccessRepository,
        InMemoryIdentityAccessRepository,
        ShipmentAccessRepository,
    )


class RepositoryMode(str, Enum):
    MEMORY = "memory"
    DYNAMODB = "dynamodb"


class RepositoryConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class RepositoryConfig:
    mode: RepositoryMode
    aws_region: Optional[str] = None
    identity_table: Optional[str] = None
    telemetry_table: Optional[str] = None
    aws_profile: Optional[str] = None
    dynamodb_endpoint_url: Optional[str] = None
    key_namespace: str = ""

    @classmethod
    def from_environment(
        cls,
        environment: Optional[Mapping[str, str]] = None,
    ) -> "RepositoryConfig":
        values = os.environ if environment is None else environment
        raw_mode = str(values.get("VITAE_REPOSITORY_MODE", "memory")).strip().lower()
        try:
            mode = RepositoryMode(raw_mode)
        except ValueError as error:
            raise RepositoryConfigurationError(
                "VITAE_REPOSITORY_MODE must be memory or dynamodb"
            ) from error
        if mode == RepositoryMode.MEMORY:
            return cls(mode=mode)

        region = _required_setting(values, "VITAE_AWS_REGION")
        identity_table = _required_setting(values, "VITAE_IDENTITY_TABLE")
        telemetry_table = _required_setting(values, "VITAE_TELEMETRY_TABLE")
        return cls(
            mode=mode,
            aws_region=region,
            identity_table=identity_table,
            telemetry_table=telemetry_table,
            aws_profile=_optional_setting(values, "VITAE_AWS_PROFILE"),
            dynamodb_endpoint_url=_optional_setting(
                values, "VITAE_DYNAMODB_ENDPOINT_URL"
            ),
            key_namespace=str(
                values.get("VITAE_DYNAMODB_KEY_NAMESPACE", "")
            ).strip(),
        )


@dataclass(frozen=True)
class RepositoryComposition:
    config: RepositoryConfig
    identity_repository: IdentityAccessRepository
    shipment_access_repository: ShipmentAccessRepository
    telemetry_state_repository: ProcessingBundleRepository
    alert_repository: AlertRepository

    @property
    def identity_is_persistent(self) -> bool:
        return self.config.mode == RepositoryMode.DYNAMODB

    @property
    def telemetry_is_persistent(self) -> bool:
        return self.config.mode == RepositoryMode.DYNAMODB

    @property
    def alerts_are_persistent(self) -> bool:
        return False


def compose_repositories(
    config: RepositoryConfig,
    *,
    dynamodb_client=None,
) -> RepositoryComposition:
    if not isinstance(config, RepositoryConfig):
        raise RepositoryConfigurationError("config must be a RepositoryConfig")
    if config.mode == RepositoryMode.MEMORY:
        identity = InMemoryIdentityAccessRepository()
        return RepositoryComposition(
            config=config,
            identity_repository=identity,
            shipment_access_repository=identity,
            telemetry_state_repository=identity,
            alert_repository=InMemoryAlertRepository(),
        )
    if config.mode != RepositoryMode.DYNAMODB:
        raise RepositoryConfigurationError("Unsupported repository mode")
    if not config.aws_region or not config.identity_table or not config.telemetry_table:
        raise RepositoryConfigurationError(
            "DynamoDB mode requires aws_region, identity_table, and telemetry_table"
        )
    try:
        from .dynamo_identity_repository import DynamoIdentityAccessRepository
        from .dynamo_telemetry_repository import DynamoTelemetryStateRepository
    except ImportError:
        from dynamo_identity_repository import DynamoIdentityAccessRepository
        from dynamo_telemetry_repository import DynamoTelemetryStateRepository
    client = dynamodb_client or _build_dynamodb_client(config)
    _require_dynamodb_table(client, config.telemetry_table)
    identity = DynamoIdentityAccessRepository(
        client,
        config.identity_table,
        key_namespace=config.key_namespace,
    )
    telemetry = DynamoTelemetryStateRepository(
        client,
        config.telemetry_table,
        key_namespace=config.key_namespace,
    )
    return RepositoryComposition(
        config=config,
        identity_repository=identity,
        shipment_access_repository=identity,
        telemetry_state_repository=telemetry,
        alert_repository=InMemoryAlertRepository(),
    )


def shipment_access_resolver(repository: ShipmentAccessRepository):
    def resolve(lot_trip_id):
        access = repository.get_shipment_access(lot_trip_id)
        if access is None:
            return None
        return {
            "shipmentId": access.shipment_id,
            "lotTripId": access.lot_trip_id,
            "organizationId": access.organization_id,
            "driverId": access.driver_id,
        }

    return resolve


def _build_dynamodb_client(config):
    try:
        import boto3
    except ImportError as error:
        raise RepositoryConfigurationError(
            "boto3 is required when VITAE_REPOSITORY_MODE=dynamodb"
        ) from error
    try:
        session = boto3.Session(
            profile_name=config.aws_profile,
            region_name=config.aws_region,
        )
        return session.client(
            "dynamodb",
            endpoint_url=config.dynamodb_endpoint_url,
        )
    except Exception as error:
        raise RepositoryConfigurationError(
            "Unable to initialize the configured DynamoDB repository"
        ) from error


def _require_dynamodb_table(client, table_name):
    try:
        client.describe_table(TableName=table_name)
    except Exception as error:
        raise RepositoryConfigurationError(
            f"Configured DynamoDB telemetry table is unavailable: {table_name}"
        ) from error


def _required_setting(values, name):
    value = _optional_setting(values, name)
    if value is None:
        raise RepositoryConfigurationError(f"{name} is required in dynamodb mode")
    return value


def _optional_setting(values, name):
    value = values.get(name)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
