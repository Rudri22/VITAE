from decimal import Decimal
from typing import Optional, Tuple

try:
    from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
    from botocore.exceptions import ClientError
except ImportError as error:  # pragma: no cover - only without AWS extras
    raise RuntimeError(
        "boto3 is required to use DynamoTelemetryStateRepository"
    ) from error

try:
    from .repository_serialization import (
        RepositorySerializationError,
        deserialize_live_state,
        deserialize_telemetry_record,
        serialize_live_state,
        serialize_telemetry_record,
    )
    from .state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        LiveState,
        OutOfOrderTelemetryError,
        StateIntegrityError,
        TelemetryRecord,
        TelemetryStateRepository,
        validate_telemetry_state_commit,
    )
except ImportError:
    from repository_serialization import (
        RepositorySerializationError,
        deserialize_live_state,
        deserialize_telemetry_record,
        serialize_live_state,
        serialize_telemetry_record,
    )
    from state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        LiveState,
        OutOfOrderTelemetryError,
        StateIntegrityError,
        TelemetryRecord,
        TelemetryStateRepository,
        validate_telemetry_state_commit,
    )


_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()
_ABSENT = "attribute_not_exists(PK) AND attribute_not_exists(SK)"


class DynamoTelemetryStateRepository(TelemetryStateRepository):
    """DynamoDB adapter for atomic telemetry history and LiveState."""

    def __init__(self, dynamodb_client, table_name: str, *, key_namespace=""):
        if dynamodb_client is None:
            raise ValueError("dynamodb_client is required")
        self.table_name = _required_text(table_name, "table_name")
        namespace = str(key_namespace or "").strip()
        self._key_prefix = f"{namespace}#" if namespace else ""
        self._client = dynamodb_client

    def get_live_state(self, lot_trip_id: str) -> Optional[LiveState]:
        item = self._get_item(self._live_state_key(lot_trip_id))
        if item is None:
            return None
        self._require_item_type(item, "LIVE_STATE")
        return self._deserialize_live_state(item)

    def has_sample(self, device_id: str, sample_id: str) -> bool:
        return self._get_item(self._sample_guard_key(device_id, sample_id)) is not None

    def get_telemetry_history(
        self, lot_trip_id: str
    ) -> Tuple[TelemetryRecord, ...]:
        items = self._query(self._lot_partition(lot_trip_id), "TELEMETRY#")
        records = []
        for item in items:
            self._require_item_type(item, "TELEMETRY_RECORD")
            records.append(self._deserialize_record(item))
        return tuple(records)

    def commit_sample_and_state(
        self,
        record: TelemetryRecord,
        new_state: LiveState,
        expected_revision: Optional[int],
    ) -> None:
        current_state = self.get_live_state(record.lot_trip_id)
        sample_exists = self.has_sample(record.device_id, record.sample_id)
        validate_telemetry_state_commit(
            record=record,
            new_state=new_state,
            current_state=current_state,
            expected_revision=expected_revision,
            sample_exists=sample_exists,
        )

        actions = [
            self._put(self._sample_guard_item(record)),
            self._put(self._record_item(record)),
            self._live_state_write(new_state, expected_revision),
        ]
        try:
            self._client.transact_write_items(TransactItems=actions)
        except ClientError as error:
            if not self._is_transaction_cancel(error):
                raise
            self._raise_commit_conflict(record, new_state, expected_revision, error)

    def _raise_commit_conflict(self, record, new_state, expected_revision, error):
        try:
            validate_telemetry_state_commit(
                record=record,
                new_state=new_state,
                current_state=self.get_live_state(record.lot_trip_id),
                expected_revision=expected_revision,
                sample_exists=self.has_sample(record.device_id, record.sample_id),
            )
        except (
            DuplicateTelemetrySampleError,
            ConcurrentStateUpdateError,
            OutOfOrderTelemetryError,
            StateIntegrityError,
        ) as domain_error:
            raise domain_error from error
        raise StateIntegrityError("Telemetry transaction was conditionally rejected") from error

    def _sample_guard_item(self, record):
        return {
            **self._sample_guard_key(record.device_id, record.sample_id),
            "entityType": "TELEMETRY_SAMPLE_GUARD",
            "deviceId": record.device_id,
            "sampleId": record.sample_id,
            "lotTripId": record.lot_trip_id,
        }

    def _record_item(self, record):
        document = serialize_telemetry_record(record)
        return {
            **self._record_key(record),
            "entityType": "TELEMETRY_RECORD",
            "tripId": record.trip_id,
            "lotTripId": record.lot_trip_id,
            "deviceId": record.device_id,
            "sampleId": record.sample_id,
            "sampleTimestamp": document["timestamp"],
            "document": document,
        }

    def _live_state_item(self, state):
        document = serialize_live_state(state)
        return {
            **self._live_state_key(state.lot_trip_id),
            "entityType": "LIVE_STATE",
            "lotTripId": state.lot_trip_id,
            "tripId": state.trip_id,
            "deviceId": state.device_id,
            "productId": state.product_id,
            "productRuleVersion": state.product_rule_version,
            "revision": state.revision,
            "lastSampleTimestamp": document["last_sample_timestamp"],
            "document": document,
        }

    def _live_state_write(self, state, expected_revision):
        item = self._live_state_item(state)
        if expected_revision is None:
            return self._put(item)
        values = {
            ":expectedRevision": expected_revision,
            ":timestamp": item["lastSampleTimestamp"],
            ":lotTripId": state.lot_trip_id,
            ":tripId": state.trip_id,
            ":deviceId": state.device_id,
            ":productId": state.product_id,
            ":productRuleVersion": state.product_rule_version,
            ":document": item["document"],
            ":nextRevision": state.revision,
        }
        return {
            "Update": {
                "TableName": self.table_name,
                "Key": _marshal(self._live_state_key(state.lot_trip_id)),
                "UpdateExpression": (
                    "SET document = :document, revision = :nextRevision, "
                    "lastSampleTimestamp = :timestamp"
                ),
                "ConditionExpression": (
                    "revision = :expectedRevision "
                    "AND lastSampleTimestamp < :timestamp "
                    "AND lotTripId = :lotTripId AND tripId = :tripId "
                    "AND deviceId = :deviceId AND productId = :productId "
                    "AND productRuleVersion = :productRuleVersion"
                ),
                "ExpressionAttributeValues": _marshal(values),
            }
        }

    def _deserialize_record(self, item):
        try:
            record = deserialize_telemetry_record(item["document"])
        except (KeyError, RepositorySerializationError) as error:
            raise StateIntegrityError("Persisted TelemetryRecord is invalid") from error
        if (
            record.trip_id != item.get("tripId")
            or record.lot_trip_id != item.get("lotTripId")
            or record.device_id != item.get("deviceId")
            or record.sample_id != item.get("sampleId")
        ):
            raise StateIntegrityError("Persisted TelemetryRecord identity is inconsistent")
        return record

    def _deserialize_live_state(self, item):
        try:
            state = deserialize_live_state(item["document"])
        except (KeyError, RepositorySerializationError) as error:
            raise StateIntegrityError("Persisted LiveState is invalid") from error
        if (
            state.lot_trip_id != item.get("lotTripId")
            or state.trip_id != item.get("tripId")
            or state.device_id != item.get("deviceId")
            or state.product_id != item.get("productId")
            or state.product_rule_version != item.get("productRuleVersion")
            or state.revision != item.get("revision")
        ):
            raise StateIntegrityError("Persisted LiveState identity is inconsistent")
        return state

    def _put(self, item):
        return {
            "Put": {
                "TableName": self.table_name,
                "Item": _marshal(item),
                "ConditionExpression": _ABSENT,
            }
        }

    def _get_item(self, key):
        response = self._client.get_item(
            TableName=self.table_name,
            Key=_marshal(key),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return None if item is None else _unmarshal(item)

    def _query(self, partition_key, sort_prefix):
        request = {
            "TableName": self.table_name,
            "KeyConditionExpression": "PK = :pk AND begins_with(SK, :prefix)",
            "ExpressionAttributeValues": _marshal(
                {":pk": partition_key, ":prefix": sort_prefix}
            ),
            "ConsistentRead": True,
        }
        items = []
        while True:
            response = self._client.query(**request)
            items.extend(_unmarshal(item) for item in response.get("Items", ()))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return tuple(items)
            request["ExclusiveStartKey"] = last_key

    @staticmethod
    def _is_transaction_cancel(error):
        return error.response.get("Error", {}).get("Code") == "TransactionCanceledException"

    @staticmethod
    def _require_item_type(item, expected):
        if item.get("entityType") != expected:
            raise StateIntegrityError(f"Expected DynamoDB item type {expected}")

    def _pk(self, value):
        return f"{self._key_prefix}{value}"

    def _lot_partition(self, lot_trip_id):
        return self._pk(f"LOTTRIP#{_required_text(lot_trip_id, 'lot_trip_id')}")

    def _live_state_key(self, lot_trip_id):
        return {"PK": self._lot_partition(lot_trip_id), "SK": "LIVE_STATE"}

    def _record_key(self, record):
        timestamp = serialize_telemetry_record(record)["timestamp"]
        return {
            "PK": self._lot_partition(record.lot_trip_id),
            "SK": f"TELEMETRY#{timestamp}#{record.device_id}#{record.sample_id}",
        }

    def _sample_guard_key(self, device_id, sample_id):
        return {
            "PK": self._pk(f"SAMPLE#{_required_text(device_id, 'device_id')}"),
            "SK": f"SAMPLE#{_required_text(sample_id, 'sample_id')}",
        }


def _marshal(values):
    return {
        key: _SERIALIZER.serialize(_dynamodb_numbers(value))
        for key, value in values.items()
    }


def _dynamodb_numbers(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _dynamodb_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_dynamodb_numbers(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_dynamodb_numbers(item) for item in value)
    if isinstance(value, set):
        return {_dynamodb_numbers(item) for item in value}
    return value


def _unmarshal(values):
    return {
        key: _normalize_numbers(_DESERIALIZER.deserialize(value))
        for key, value in values.items()
    }


def _normalize_numbers(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _normalize_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_numbers(item) for item in value]
    if isinstance(value, set):
        return {_normalize_numbers(item) for item in value}
    return value


def _required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()
