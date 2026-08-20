from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from typing import Optional, Tuple

try:
    from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
    from botocore.exceptions import ClientError
except ImportError as error:  # pragma: no cover - only without AWS extras
    raise RuntimeError("boto3 is required to use DynamoAlertRepository") from error

try:
    from .alerting import (
        Alert,
        AlertConflictError,
        AlertError,
        AlertNotFoundError,
        AlertRepository,
        AlertStatus,
        AlertTransitionError,
        alert_creation_identity,
        build_alert_action,
        validate_new_alert_candidate,
        validate_persisted_alert,
    )
    from .repository_serialization import (
        RepositorySerializationError,
        deserialize_alert,
        serialize_alert,
    )
except ImportError:
    from alerting import (
        Alert,
        AlertConflictError,
        AlertError,
        AlertNotFoundError,
        AlertRepository,
        AlertStatus,
        AlertTransitionError,
        alert_creation_identity,
        build_alert_action,
        validate_new_alert_candidate,
        validate_persisted_alert,
    )
    from repository_serialization import (
        RepositorySerializationError,
        deserialize_alert,
        serialize_alert,
    )


_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()
_ABSENT = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
_MAX_UPDATE_ATTEMPTS = 8


class AlertRepositoryPersistenceError(AlertError):
    pass


class AlertRepositoryCorruptionError(AlertRepositoryPersistenceError):
    pass


class DynamoAlertRepository(AlertRepository):
    """Strongly consistent DynamoDB AlertRepository with immutable locators."""

    def __init__(self, dynamodb_client, table_name: str, *, key_namespace=""):
        if dynamodb_client is None:
            raise ValueError("dynamodb_client is required")
        self.table_name = _required_text(table_name, "table_name")
        namespace = str(key_namespace or "").strip()
        self._key_prefix = f"{namespace}#" if namespace else ""
        self._client = dynamodb_client

    def save_alert(self, alert: Alert) -> Alert:
        validate_new_alert_candidate(alert)
        fingerprint = _creation_fingerprint(alert)
        existing = self.get_alert(alert.alert_id)
        if existing is not None:
            return self._idempotent_existing(existing, alert, fingerprint)

        canonical = self._canonical_item(alert, fingerprint, revision=1)
        locator = self._locator_item(alert, canonical, fingerprint)
        catalog = self._catalog_item(alert, canonical, fingerprint)
        try:
            self._client.transact_write_items(
                TransactItems=[
                    self._conditional_put(canonical),
                    self._conditional_put(locator),
                    self._conditional_put(catalog),
                ]
            )
            return alert
        except ClientError as error:
            if not _is_transaction_cancel(error):
                raise
            existing = self.get_alert(alert.alert_id)
            if existing is not None:
                return self._idempotent_existing(existing, alert, fingerprint)
            if self._get_item(self._canonical_key(alert)) is not None:
                raise AlertRepositoryCorruptionError(
                    "Canonical alert exists without a valid authoritative locator"
                ) from error
            raise AlertConflictError(
                "Alert creation keys are already associated with other content"
            ) from error

    def get_alert(self, alert_id: str) -> Optional[Alert]:
        locator_key = self._locator_key(_required_text(alert_id, "alert_id"))
        locator = self._get_item(locator_key)
        if locator is None:
            return None
        alert, _, _, _ = self._record_from_locator(locator, locator_key)
        return alert

    def list_alerts(
        self,
        *,
        lot_trip_id: Optional[str] = None,
        status: Optional[AlertStatus] = None,
    ) -> Tuple[Alert, ...]:
        if status is not None and not isinstance(status, AlertStatus):
            raise ValueError("status must be an AlertStatus")
        if lot_trip_id is not None:
            partition = self._lot_partition(
                _required_text(lot_trip_id, "lot_trip_id")
            )
            items = self._query(partition, "ALERT#")
            alerts = tuple(self._alert_from_canonical(item) for item in items)
        else:
            pointers = self._query(self._catalog_partition(), "ALERT#")
            alerts = tuple(self._alert_from_catalog(item) for item in pointers)
        if status is not None:
            alerts = tuple(alert for alert in alerts if alert.status == status)
        return alerts

    def acknowledge_alert(
        self,
        alert_id: str,
        *,
        actor_id: str,
        acknowledged_at: datetime,
    ) -> Alert:
        actor = _required_text(actor_id, "actor_id")
        timestamp = _aware_timestamp(acknowledged_at, "acknowledged_at")
        for _ in range(_MAX_UPDATE_ATTEMPTS):
            alert, revision, key, fingerprint = self._require_record(alert_id)
            if alert.status != AlertStatus.OPEN:
                raise AlertTransitionError("Only an OPEN alert can be acknowledged")
            _not_before(alert, timestamp, "Acknowledgement")
            if timestamp < alert.updated_at:
                raise AlertTransitionError(
                    "Acknowledgement cannot predate alert activity"
                )
            updated = replace(
                alert,
                status=AlertStatus.ACKNOWLEDGED,
                acknowledged_by=actor,
                acknowledged_at=timestamp,
                updated_at=timestamp,
            )
            if self._put_revision(updated, revision, key, fingerprint):
                return updated
        raise AlertConflictError("Alert changed concurrently during acknowledgement")

    def record_action(
        self,
        alert_id: str,
        *,
        description: str,
        actor_id: str,
        recorded_at: datetime,
    ) -> Alert:
        action = build_alert_action(
            alert_id,
            description=description,
            actor_id=actor_id,
            recorded_at=recorded_at,
        )
        for _ in range(_MAX_UPDATE_ATTEMPTS):
            alert, revision, key, fingerprint = self._require_record(alert_id)
            if alert.status == AlertStatus.RESOLVED:
                raise AlertTransitionError("A RESOLVED alert cannot receive actions")
            for existing in alert.actions:
                if existing.action_id == action.action_id:
                    if existing != action:
                        raise AlertConflictError(
                            "Alert action ID is associated with different content"
                        )
                    return alert
            if action.recorded_at < alert.updated_at:
                raise AlertTransitionError("Alert action cannot predate its current state")
            updated = replace(
                alert,
                actions=alert.actions + (action,),
                updated_at=action.recorded_at,
            )
            if self._put_revision(updated, revision, key, fingerprint):
                return updated
        raise AlertConflictError("Alert changed concurrently while recording action")

    def resolve_alert(
        self,
        alert_id: str,
        *,
        actor_id: str,
        resolved_at: datetime,
        resolution_note: str,
    ) -> Alert:
        actor = _required_text(actor_id, "actor_id")
        timestamp = _aware_timestamp(resolved_at, "resolved_at")
        note = _required_text(resolution_note, "resolution_note")
        for _ in range(_MAX_UPDATE_ATTEMPTS):
            alert, revision, key, fingerprint = self._require_record(alert_id)
            if alert.status == AlertStatus.RESOLVED:
                raise AlertTransitionError("Alert is already RESOLVED")
            if timestamp < alert.updated_at:
                raise AlertTransitionError("Resolution cannot predate alert activity")
            updated = replace(
                alert,
                status=AlertStatus.RESOLVED,
                resolved_by=actor,
                resolved_at=timestamp,
                resolution_note=note,
                updated_at=timestamp,
            )
            if self._put_revision(updated, revision, key, fingerprint):
                return updated
        raise AlertConflictError("Alert changed concurrently during resolution")

    def _idempotent_existing(self, existing, candidate, fingerprint):
        if (
            alert_creation_identity(existing) != alert_creation_identity(candidate)
            or _creation_fingerprint(existing) != fingerprint
        ):
            raise AlertConflictError(
                "Alert ID is already associated with different creation content"
            )
        return existing

    def _require_record(self, alert_id):
        locator_key = self._locator_key(_required_text(alert_id, "alert_id"))
        locator = self._get_item(locator_key)
        if locator is None:
            raise AlertNotFoundError("Alert does not exist")
        return self._record_from_locator(locator, locator_key)

    def _record_from_locator(self, locator, expected_locator_key):
        self._require_type(locator, "ALERT_LOCATOR")
        if _item_key(locator) != expected_locator_key:
            raise AlertRepositoryCorruptionError("Alert locator key mismatch")
        alert_id = _item_text(locator, "AlertId")
        if expected_locator_key != self._locator_key(alert_id):
            raise AlertRepositoryCorruptionError("Alert locator identity mismatch")
        canonical_key = {
            "PK": _item_text(locator, "CanonicalPK"),
            "SK": _item_text(locator, "CanonicalSK"),
        }
        canonical = self._get_item(canonical_key)
        if canonical is None:
            raise AlertRepositoryCorruptionError(
                "Alert locator references a missing canonical record"
            )
        alert, revision, fingerprint = self._decode_canonical(canonical)
        if (
            alert.alert_id != alert_id
            or alert.lot_trip_id != _item_text(locator, "LotTripId")
            or canonical_key != self._canonical_key(alert)
            or fingerprint != _item_text(locator, "CreationFingerprint")
        ):
            raise AlertRepositoryCorruptionError(
                "Alert locator and canonical record do not agree"
            )
        return alert, revision, canonical_key, fingerprint

    def _alert_from_canonical(self, item):
        alert, _, fingerprint = self._decode_canonical(item)
        locator_key = self._locator_key(alert.alert_id)
        locator = self._get_item(locator_key)
        if locator is None:
            raise AlertRepositoryCorruptionError(
                "Canonical alert is missing its authoritative locator"
            )
        located, _, _, located_fingerprint = self._record_from_locator(
            locator,
            locator_key,
        )
        if located != alert or located_fingerprint != fingerprint:
            raise AlertRepositoryCorruptionError(
                "Canonical alert and authoritative locator do not agree"
            )
        return alert

    def _alert_from_catalog(self, item):
        self._require_type(item, "ALERT_CATALOG")
        alert_id = _item_text(item, "AlertId")
        alert = self.get_alert(alert_id)
        if alert is None:
            raise AlertRepositoryCorruptionError(
                "Alert catalog references a missing locator"
            )
        if (
            _item_text(item, "CanonicalPK") != self._canonical_key(alert)["PK"]
            or _item_text(item, "CanonicalSK") != self._canonical_key(alert)["SK"]
            or _item_text(item, "CreationFingerprint")
            != _creation_fingerprint(alert)
        ):
            raise AlertRepositoryCorruptionError(
                "Alert catalog and canonical record do not agree"
            )
        return alert

    def _decode_canonical(self, item):
        self._require_type(item, "ALERT_RECORD")
        revision = item.get("Revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise AlertRepositoryCorruptionError("Alert revision is invalid")
        document = item.get("Document")
        try:
            alert = deserialize_alert(document)
            validate_persisted_alert(alert)
        except (RepositorySerializationError, AlertError, TypeError) as error:
            raise AlertRepositoryCorruptionError(
                "Canonical alert document is invalid"
            ) from error
        fingerprint = _item_text(item, "CreationFingerprint")
        if (
            _item_key(item) != self._canonical_key(alert)
            or fingerprint != _creation_fingerprint(alert)
        ):
            raise AlertRepositoryCorruptionError(
                "Canonical alert identity or creation fingerprint mismatch"
            )
        return alert, revision, fingerprint

    def _put_revision(self, alert, revision, key, fingerprint):
        validate_persisted_alert(alert)
        if _creation_fingerprint(alert) != fingerprint:
            raise AlertConflictError("Alert creation identity cannot change")
        item = self._canonical_item(alert, fingerprint, revision=revision + 1)
        if _item_key(item) != key:
            raise AlertConflictError("Alert canonical key cannot change")
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=_marshal_item(item),
                ConditionExpression=(
                    "Revision = :expected_revision "
                    "AND CreationFingerprint = :creation_fingerprint"
                ),
                ExpressionAttributeValues=_marshal_item(
                    {
                        ":expected_revision": Decimal(revision),
                        ":creation_fingerprint": fingerprint,
                    }
                ),
            )
            return True
        except ClientError as error:
            if _is_conditional_failure(error):
                return False
            raise

    def _canonical_item(self, alert, fingerprint, *, revision):
        return {
            **self._canonical_key(alert),
            "ItemType": "ALERT_RECORD",
            "Revision": Decimal(revision),
            "CreationFingerprint": fingerprint,
            "Document": serialize_alert(alert),
        }

    def _locator_item(self, alert, canonical, fingerprint):
        return {
            **self._locator_key(alert.alert_id),
            "ItemType": "ALERT_LOCATOR",
            "AlertId": alert.alert_id,
            "LotTripId": alert.lot_trip_id,
            "CanonicalPK": canonical["PK"],
            "CanonicalSK": canonical["SK"],
            "CreationFingerprint": fingerprint,
        }

    def _catalog_item(self, alert, canonical, fingerprint):
        return {
            "PK": self._catalog_partition(),
            "SK": self._alert_sort_key(alert),
            "ItemType": "ALERT_CATALOG",
            "AlertId": alert.alert_id,
            "CanonicalPK": canonical["PK"],
            "CanonicalSK": canonical["SK"],
            "CreationFingerprint": fingerprint,
        }

    def _canonical_key(self, alert):
        return {
            "PK": self._lot_partition(alert.lot_trip_id),
            "SK": self._alert_sort_key(alert),
        }

    def _locator_key(self, alert_id):
        return {"PK": f"{self._key_prefix}ALERT#{alert_id}", "SK": "LOCATOR"}

    def _lot_partition(self, lot_trip_id):
        return f"{self._key_prefix}LOTTRIP#{lot_trip_id}"

    def _catalog_partition(self):
        return f"{self._key_prefix}ALERTS"

    def _alert_sort_key(self, alert):
        normalized = alert.detected_at.astimezone(
            timezone.utc
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        return f"ALERT#{normalized}#{alert.alert_id}"

    def _conditional_put(self, item):
        return {
            "Put": {
                "TableName": self.table_name,
                "Item": _marshal_item(item),
                "ConditionExpression": _ABSENT,
            }
        }

    def _get_item(self, key):
        response = self._client.get_item(
            TableName=self.table_name,
            Key=_marshal_item(key),
            ConsistentRead=True,
        )
        item = response.get("Item")
        return None if item is None else _unmarshal_item(item)

    def _query(self, partition_key, sort_prefix):
        items = []
        request = {
            "TableName": self.table_name,
            "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
            "ExpressionAttributeValues": _marshal_item(
                {":pk": partition_key, ":sk": sort_prefix}
            ),
            "ConsistentRead": True,
        }
        while True:
            response = self._client.query(**request)
            items.extend(_unmarshal_item(item) for item in response.get("Items", ()))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return tuple(items)
            request["ExclusiveStartKey"] = last_key

    @staticmethod
    def _require_type(item, expected):
        if item.get("ItemType") != expected:
            raise AlertRepositoryCorruptionError(
                f"Expected {expected} DynamoDB item"
            )


def _creation_fingerprint(alert):
    values = []
    for value in alert_creation_identity(alert):
        if isinstance(value, Enum):
            values.append(value.value)
        elif isinstance(value, datetime):
            values.append(value.astimezone(timezone.utc).isoformat())
        else:
            values.append(value)
    payload = json.dumps(values, separators=(",", ":"), ensure_ascii=True)
    return sha256(payload.encode("utf-8")).hexdigest()


def _item_key(item):
    return {"PK": item.get("PK"), "SK": item.get("SK")}


def _item_text(item, name):
    value = item.get(name)
    if not isinstance(value, str) or not value:
        raise AlertRepositoryCorruptionError(f"Alert item {name} is invalid")
    return value


def _marshal_item(item):
    return {name: _SERIALIZER.serialize(value) for name, value in item.items()}


def _unmarshal_item(item):
    return {
        name: _normalize_numbers(_DESERIALIZER.deserialize(value))
        for name, value in item.items()
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
        raise AlertTransitionError(f"{field} must be a non-empty string")
    return value.strip()


def _aware_timestamp(value, field):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AlertTransitionError(f"{field} must be timezone-aware")
    return value


def _not_before(alert, timestamp, label):
    if timestamp < alert.detected_at:
        raise AlertTransitionError(f"{label} cannot predate alert detection")


def _is_transaction_cancel(error):
    return error.response.get("Error", {}).get("Code") == "TransactionCanceledException"


def _is_conditional_failure(error):
    return error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
