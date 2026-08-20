from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Optional, Tuple

try:
    from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
    from botocore.exceptions import ClientError
except ImportError as error:  # pragma: no cover - only without AWS extras
    raise RuntimeError(
        "boto3 is required to use DynamoTelemetryStateRepository"
    ) from error

try:
    from .decision_outbox import (
        AlertOutboxEvent,
        DecisionOutboxError,
        OutboxClaimError,
        OutboxDiscoveryBatch,
        OutboxDeliveryStatus,
        OutboxTransitionError,
        ProcessingBundleRepository,
        StatusDecisionRecord,
        lot_trip_id_from_decision_id,
        validate_alert_outbox_event,
        validate_processing_bundle_commit,
    )
    from .repository_serialization import (
        RepositorySerializationError,
        deserialize_alert_outbox_event,
        deserialize_live_state,
        deserialize_status_decision_record,
        deserialize_telemetry_record,
        deserialize_trip_identity,
        serialize_alert_outbox_event,
        serialize_live_state,
        serialize_status_decision_record,
        serialize_telemetry_record,
    )
    from .state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        LiveState,
        OutOfOrderTelemetryError,
        StateIntegrityError,
        TelemetryRecord,
        TripNotActiveAtCommitError,
        validate_telemetry_state_commit,
    )
except ImportError:
    from decision_outbox import (
        AlertOutboxEvent,
        DecisionOutboxError,
        OutboxClaimError,
        OutboxDiscoveryBatch,
        OutboxDeliveryStatus,
        OutboxTransitionError,
        ProcessingBundleRepository,
        StatusDecisionRecord,
        lot_trip_id_from_decision_id,
        validate_alert_outbox_event,
        validate_processing_bundle_commit,
    )
    from repository_serialization import (
        RepositorySerializationError,
        deserialize_alert_outbox_event,
        deserialize_live_state,
        deserialize_status_decision_record,
        deserialize_telemetry_record,
        deserialize_trip_identity,
        serialize_alert_outbox_event,
        serialize_live_state,
        serialize_status_decision_record,
        serialize_telemetry_record,
    )
    from state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        LiveState,
        OutOfOrderTelemetryError,
        StateIntegrityError,
        TelemetryRecord,
        TripNotActiveAtCommitError,
        validate_telemetry_state_commit,
    )


_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()
_ABSENT = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
OUTBOX_WORK_INDEX_NAME = "OutboxWorkIndex"
OUTBOX_WORK_PARTITION_ATTRIBUTE = "outboxWorkPartition"
OUTBOX_WORK_SORT_ATTRIBUTE = "outboxWorkSort"
OUTBOX_WORK_SHARD_COUNT = 16
_OUTBOX_RECORD_VERSION = "recordVersion"
_MAX_DISCOVERY_OVERREAD = 4


class DynamoTelemetryStateRepository(ProcessingBundleRepository):
    """DynamoDB adapter for atomic telemetry, state, decision, and outbox data."""

    def __init__(
        self,
        dynamodb_client,
        table_name: str,
        *,
        identity_table_name=None,
        key_namespace="",
        outbox_work_index_name=OUTBOX_WORK_INDEX_NAME,
    ):
        if dynamodb_client is None:
            raise ValueError("dynamodb_client is required")
        self.table_name = _required_text(table_name, "table_name")
        self.identity_table_name = (
            None
            if identity_table_name is None
            else _required_text(identity_table_name, "identity_table_name")
        )
        namespace = str(key_namespace or "").strip()
        self._key_prefix = f"{namespace}#" if namespace else ""
        self._client = dynamodb_client
        self._outbox_work_index_name = _required_text(
            outbox_work_index_name,
            "outbox_work_index_name",
        )
        self._outbox_work_index_validated = False

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
        self._require_identity_table()
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
            self._active_trip_condition(record),
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

    def commit_processing_bundle(
        self,
        record: TelemetryRecord,
        new_state: LiveState,
        decision_record: StatusDecisionRecord,
        alert_outbox_event: Optional[AlertOutboxEvent],
        expected_revision: Optional[int],
    ) -> None:
        self._require_identity_table()
        current_state = self.get_live_state(record.lot_trip_id)
        sample_exists = self.has_sample(record.device_id, record.sample_id)
        validate_processing_bundle_commit(
            record=record,
            new_state=new_state,
            decision_record=decision_record,
            alert_outbox_event=alert_outbox_event,
            current_state=current_state,
            expected_revision=expected_revision,
            sample_exists=sample_exists,
        )
        if self.get_decision(decision_record.decision_id) is not None:
            raise DecisionOutboxError("Decision ID is already committed")
        if (
            alert_outbox_event is not None
            and self.get_outbox_event(alert_outbox_event.event_id) is not None
        ):
            raise DecisionOutboxError("Outbox event ID is already committed")

        actions = [
            self._active_trip_condition(record),
            self._put(self._sample_guard_item(record)),
            self._put(self._record_item(record)),
            self._live_state_write(new_state, expected_revision),
            self._put(self._decision_item(decision_record)),
        ]
        if alert_outbox_event is not None:
            actions.append(self._put(self._outbox_item(alert_outbox_event)))
        try:
            self._client.transact_write_items(TransactItems=actions)
        except ClientError as error:
            if not self._is_transaction_cancel(error):
                raise
            self._raise_bundle_conflict(
                record,
                new_state,
                decision_record,
                alert_outbox_event,
                expected_revision,
                error,
            )

    def get_decision(self, decision_id: str) -> Optional[StatusDecisionRecord]:
        try:
            lot_trip_id = lot_trip_id_from_decision_id(decision_id)
        except DecisionOutboxError:
            return None
        item = self._get_item(self._decision_key(lot_trip_id, decision_id))
        if item is None:
            return None
        self._require_item_type(item, "STATUS_DECISION_RECORD")
        return self._deserialize_decision(item)

    def get_decision_history(
        self, lot_trip_id: str
    ) -> Tuple[StatusDecisionRecord, ...]:
        lot_trip = _required_text(lot_trip_id, "lot_trip_id")
        decisions = tuple(
            self._deserialize_decision(item)
            for item in self._query(self._lot_partition(lot_trip), "DECISION#")
        )
        return tuple(
            sorted(
                decisions,
                key=lambda value: (value.sample_timestamp, value.decision_id),
            )
        )

    def get_outbox_event(self, event_id: str) -> Optional[AlertOutboxEvent]:
        item = self._get_item(self._outbox_key(event_id))
        if item is None:
            return None
        self._require_item_type(item, "ALERT_OUTBOX_EVENT")
        self._record_version(item)
        return self._deserialize_outbox(item)

    def list_dispatchable_outbox_events(
        self, as_of: datetime
    ) -> Tuple[AlertOutboxEvent, ...]:
        return self.discover_dispatchable_outbox_events(
            as_of,
            limit=1000,
        ).events

    def discover_dispatchable_outbox_events(
        self,
        as_of: datetime,
        *,
        limit: int,
    ) -> OutboxDiscoveryBatch:
        timestamp = _aware_timestamp(as_of, "as_of")
        bounded_limit = _positive_integer(limit, "limit")
        self.validate_outbox_work_index()
        candidate_keys = self._query_due_outbox_keys(timestamp, bounded_limit)
        events = []
        quarantined = 0
        for key in candidate_keys:
            item = self._get_item(key)
            if item is None or item.get("persistenceState") == "QUARANTINED":
                continue
            try:
                self._require_item_type(item, "ALERT_OUTBOX_EVENT")
                event = self._deserialize_outbox(item)
            except (StateIntegrityError, TypeError, ValueError):
                if self._quarantine_corrupt_outbox_item(
                    item,
                    quarantined_at=timestamp,
                    error_code="OUTBOX_RECORD_CORRUPTION",
                ):
                    quarantined += 1
                continue
            if _is_dispatchable(event, timestamp):
                events.append(event)
        events.sort(key=lambda event: (_effective_due_at(event), event.event_id))
        return OutboxDiscoveryBatch(
            events=tuple(events[:bounded_limit]),
            corrupt_quarantined_count=quarantined,
        )

    def validate_outbox_work_index(self):
        if self._outbox_work_index_validated:
            return
        response = self._client.describe_table(TableName=self.table_name)
        indexes = response.get("Table", {}).get("GlobalSecondaryIndexes", ())
        index = next(
            (
                value
                for value in indexes
                if value.get("IndexName") == self._outbox_work_index_name
            ),
            None,
        )
        expected_keys = [
            {"AttributeName": OUTBOX_WORK_PARTITION_ATTRIBUTE, "KeyType": "HASH"},
            {"AttributeName": OUTBOX_WORK_SORT_ATTRIBUTE, "KeyType": "RANGE"},
        ]
        if (
            index is None
            or index.get("IndexStatus") != "ACTIVE"
            or index.get("KeySchema") != expected_keys
            or index.get("Projection", {}).get("ProjectionType") != "KEYS_ONLY"
        ):
            raise StateIntegrityError(
                "Configured DynamoDB OutboxWorkIndex is unavailable or invalid"
            )
        self._outbox_work_index_validated = True

    def claim_outbox_event(
        self,
        event_id: str,
        *,
        worker_id: str,
        claimed_at: datetime,
        lease_duration: timedelta,
    ) -> AlertOutboxEvent:
        worker = _required_text(worker_id, "worker_id")
        timestamp = _aware_timestamp(claimed_at, "claimed_at")
        if not isinstance(lease_duration, timedelta) or lease_duration <= timedelta(0):
            raise OutboxClaimError("lease_duration must be positive")
        event, record_version = self._require_outbox_event_with_version(event_id)
        available = (
            event.delivery_status == OutboxDeliveryStatus.PENDING
            and event.available_at <= timestamp
        ) or (
            event.delivery_status == OutboxDeliveryStatus.IN_FLIGHT
            and event.lease_expires_at is not None
            and event.lease_expires_at <= timestamp
        )
        if not available:
            raise OutboxClaimError("Outbox event is not available for claim")
        claimed = replace(
            event,
            delivery_status=OutboxDeliveryStatus.IN_FLIGHT,
            attempt_count=event.attempt_count + 1,
            lease_owner=worker,
            lease_expires_at=timestamp + lease_duration,
        )
        condition = (
            "attemptCount = :expectedAttempts AND ((deliveryStatus = :pending "
            "AND availableAt <= :timestamp) OR (deliveryStatus = :inFlight "
            "AND leaseExpiresAt <= :timestamp))"
        )
        try:
            return self._update_outbox(
                claimed,
                condition,
                {
                    ":expectedAttempts": event.attempt_count,
                    ":pending": OutboxDeliveryStatus.PENDING.value,
                    ":inFlight": OutboxDeliveryStatus.IN_FLIGHT.value,
                    ":timestamp": _iso(timestamp),
                },
                expected_record_version=record_version,
            )
        except ClientError as error:
            if self._is_conditional_failure(error):
                raise OutboxClaimError(
                    "Outbox event is not available for claim"
                ) from error
            raise

    def release_outbox_event(
        self,
        event_id: str,
        *,
        worker_id: str,
        released_at: datetime,
        retry_at: datetime,
        error_code: str,
    ) -> AlertOutboxEvent:
        worker = _required_text(worker_id, "worker_id")
        released = _aware_timestamp(released_at, "released_at")
        retry = _aware_timestamp(retry_at, "retry_at")
        error = _required_text(error_code, "error_code")
        if retry < released:
            raise OutboxTransitionError("retry_at cannot predate released_at")
        event, record_version = self._require_owned_lease(
            event_id,
            worker,
            released,
        )
        pending = replace(
            event,
            delivery_status=OutboxDeliveryStatus.PENDING,
            available_at=retry,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=error,
        )
        return self._conditional_outbox_transition(
            pending,
            event,
            worker,
            released,
            record_version,
        )

    def mark_outbox_delivered(
        self,
        event_id: str,
        *,
        worker_id: str,
        delivered_at: datetime,
    ) -> AlertOutboxEvent:
        worker = _required_text(worker_id, "worker_id")
        timestamp = _aware_timestamp(delivered_at, "delivered_at")
        event, record_version = self._require_owned_lease(
            event_id,
            worker,
            timestamp,
        )
        delivered = replace(
            event,
            delivery_status=OutboxDeliveryStatus.DELIVERED,
            lease_owner=None,
            lease_expires_at=None,
            delivered_at=timestamp,
            last_error_code=None,
        )
        return self._conditional_outbox_transition(
            delivered,
            event,
            worker,
            timestamp,
            record_version,
        )

    def mark_outbox_dead_letter(
        self,
        event_id: str,
        *,
        worker_id: str,
        failed_at: datetime,
        error_code: str,
    ) -> AlertOutboxEvent:
        worker = _required_text(worker_id, "worker_id")
        timestamp = _aware_timestamp(failed_at, "failed_at")
        error = _required_text(error_code, "error_code")
        event, record_version = self._require_owned_lease(
            event_id,
            worker,
            timestamp,
        )
        dead_letter = replace(
            event,
            delivery_status=OutboxDeliveryStatus.DEAD_LETTER,
            lease_owner=None,
            lease_expires_at=None,
            delivered_at=None,
            last_error_code=error,
            dead_lettered_at=timestamp,
            dead_lettered_by=worker,
        )
        return self._conditional_outbox_transition(
            dead_letter,
            event,
            worker,
            timestamp,
            record_version,
        )

    def _raise_commit_conflict(self, record, new_state, expected_revision, error):
        self._require_active_trip_after_rejection(record, error)
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

    def _raise_bundle_conflict(
        self,
        record,
        new_state,
        decision_record,
        alert_outbox_event,
        expected_revision,
        error,
    ):
        self._require_active_trip_after_rejection(record, error)
        try:
            validate_processing_bundle_commit(
                record=record,
                new_state=new_state,
                decision_record=decision_record,
                alert_outbox_event=alert_outbox_event,
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
        if self.get_decision(decision_record.decision_id) is not None:
            raise DecisionOutboxError("Decision ID is already committed") from error
        if (
            alert_outbox_event is not None
            and self.get_outbox_event(alert_outbox_event.event_id) is not None
        ):
            raise DecisionOutboxError("Outbox event ID is already committed") from error
        raise StateIntegrityError("Processing bundle was conditionally rejected") from error

    def _require_identity_table(self):
        if self.identity_table_name is None:
            raise StateIntegrityError(
                "Dynamo telemetry commits require an identity table"
            )

    def _active_trip_condition(self, record):
        return {
            "ConditionCheck": {
                "TableName": self.identity_table_name,
                "Key": _marshal(self._trip_key(record.trip_id)),
                "ConditionExpression": (
                    "entityType = :tripType AND #status = :active "
                    "AND tripId = :tripId AND lotTripId = :lotTripId "
                    "AND deviceId = :deviceId "
                    "AND document.fields.#status = :active "
                    "AND (attribute_not_exists(document.fields.#completedAt) "
                    "OR document.fields.#completedAt = :noCompletion)"
                ),
                "ExpressionAttributeNames": {
                    "#status": "status",
                    "#completedAt": "completed_at",
                },
                "ExpressionAttributeValues": _marshal(
                    {
                        ":tripType": "TRIP",
                        ":active": "ACTIVE",
                        ":tripId": record.trip_id,
                        ":lotTripId": record.lot_trip_id,
                        ":deviceId": record.device_id,
                        ":noCompletion": None,
                    }
                ),
            }
        }

    def _require_active_trip_after_rejection(self, record, error):
        response = self._client.get_item(
            TableName=self.identity_table_name,
            Key=_marshal(self._trip_key(record.trip_id)),
            ConsistentRead=True,
        )
        item = response.get("Item")
        trip = None if item is None else _unmarshal(item)
        trip_document = None
        if trip is not None:
            try:
                trip_document = deserialize_trip_identity(trip["document"])
            except (KeyError, RepositorySerializationError) as corruption:
                raise StateIntegrityError(
                    "Persisted TripIdentity is invalid"
                ) from corruption
        if (
            trip is None
            or trip.get("entityType") != "TRIP"
            or trip.get("status") != "ACTIVE"
            or trip.get("tripId") != record.trip_id
            or trip.get("lotTripId") != record.lot_trip_id
            or trip.get("deviceId") != record.device_id
            or trip_document.trip_id != record.trip_id
            or trip_document.lot_trip_id != record.lot_trip_id
            or trip_document.device_id != record.device_id
            or trip_document.status.value != "ACTIVE"
            or trip_document.completed_at is not None
        ):
            raise TripNotActiveAtCommitError(
                "Trip was not ACTIVE when telemetry was committed"
            ) from error

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

    def _decision_item(self, decision):
        document = serialize_status_decision_record(decision)
        return {
            **self._decision_key(decision.lot_trip_id, decision.decision_id),
            "entityType": "STATUS_DECISION_RECORD",
            "decisionId": decision.decision_id,
            "tripId": decision.trip_id,
            "lotTripId": decision.lot_trip_id,
            "deviceId": decision.device_id,
            "sampleId": decision.sample_id,
            "sampleTimestamp": document["sample_timestamp"],
            "document": document,
        }

    def _outbox_item(self, event, *, record_version=1):
        document = serialize_alert_outbox_event(event)
        item = {
            **self._outbox_key(event.event_id),
            "entityType": "ALERT_OUTBOX_EVENT",
            _OUTBOX_RECORD_VERSION: record_version,
            "eventId": event.event_id,
            "decisionId": event.decision_id,
            "lotTripId": event.lot_trip_id,
            "deviceId": event.device_id,
            "sampleId": event.sample_id,
            "deliveryStatus": event.delivery_status.value,
            "attemptCount": event.attempt_count,
            "availableAt": document["available_at"],
            "leaseOwner": event.lease_owner,
            "leaseExpiresAt": document["lease_expires_at"],
            "deliveredAt": document["delivered_at"],
            "lastErrorCode": event.last_error_code,
            "deadLetteredAt": document["dead_lettered_at"],
            "deadLetteredBy": event.dead_lettered_by,
            "document": document,
        }
        item.update(self._outbox_work_attributes(event))
        return item

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

    def _deserialize_decision(self, item):
        try:
            decision = deserialize_status_decision_record(item["document"])
        except (KeyError, RepositorySerializationError) as error:
            raise StateIntegrityError(
                "Persisted StatusDecisionRecord is invalid"
            ) from error
        if (
            decision.decision_id != item.get("decisionId")
            or decision.trip_id != item.get("tripId")
            or decision.lot_trip_id != item.get("lotTripId")
            or decision.device_id != item.get("deviceId")
            or decision.sample_id != item.get("sampleId")
        ):
            raise StateIntegrityError(
                "Persisted StatusDecisionRecord identity is inconsistent"
            )
        return decision

    def _deserialize_outbox(self, item):
        try:
            event = deserialize_alert_outbox_event(item["document"])
        except (KeyError, RepositorySerializationError) as error:
            raise StateIntegrityError("Persisted AlertOutboxEvent is invalid") from error
        if (
            event.event_id != item.get("eventId")
            or event.decision_id != item.get("decisionId")
            or event.lot_trip_id != item.get("lotTripId")
            or event.device_id != item.get("deviceId")
            or event.sample_id != item.get("sampleId")
            or event.delivery_status.value != item.get("deliveryStatus")
            or event.attempt_count != item.get("attemptCount")
            or serialize_alert_outbox_event(event)["available_at"]
            != item.get("availableAt")
            or event.lease_owner != item.get("leaseOwner")
            or serialize_alert_outbox_event(event)["lease_expires_at"]
            != item.get("leaseExpiresAt")
            or serialize_alert_outbox_event(event)["delivered_at"]
            != item.get("deliveredAt")
            or event.last_error_code != item.get("lastErrorCode")
            or serialize_alert_outbox_event(event)["dead_lettered_at"]
            != item.get("deadLetteredAt")
            or event.dead_lettered_by != item.get("deadLetteredBy")
        ):
            raise StateIntegrityError(
                "Persisted AlertOutboxEvent identity is inconsistent"
            )
        return event

    def _update_outbox(
        self,
        event,
        condition,
        condition_values,
        *,
        expected_record_version,
    ):
        validate_alert_outbox_event(event)
        next_record_version = expected_record_version + 1
        item = self._outbox_item(event, record_version=next_record_version)
        values = {
            ":document": item["document"],
            ":deliveryStatus": item["deliveryStatus"],
            ":attemptCount": item["attemptCount"],
            ":availableAt": item["availableAt"],
            ":leaseOwner": item["leaseOwner"],
            ":leaseExpiresAt": item["leaseExpiresAt"],
            ":deliveredAt": item["deliveredAt"],
            ":lastErrorCode": item["lastErrorCode"],
            ":deadLetteredAt": item["deadLetteredAt"],
            ":deadLetteredBy": item["deadLetteredBy"],
            ":nextRecordVersion": next_record_version,
            ":expectedRecordVersion": expected_record_version,
            **condition_values,
        }
        set_expression = (
            "document = :document, deliveryStatus = :deliveryStatus, "
            "attemptCount = :attemptCount, availableAt = :availableAt, "
            "leaseOwner = :leaseOwner, leaseExpiresAt = :leaseExpiresAt, "
            "deliveredAt = :deliveredAt, lastErrorCode = :lastErrorCode, "
            "deadLetteredAt = :deadLetteredAt, "
            "deadLetteredBy = :deadLetteredBy, "
            "recordVersion = :nextRecordVersion"
        )
        work_attributes = self._outbox_work_attributes(event)
        if work_attributes:
            values[":outboxWorkPartition"] = work_attributes[
                OUTBOX_WORK_PARTITION_ATTRIBUTE
            ]
            values[":outboxWorkSort"] = work_attributes[
                OUTBOX_WORK_SORT_ATTRIBUTE
            ]
            set_expression += (
                ", outboxWorkPartition = :outboxWorkPartition, "
                "outboxWorkSort = :outboxWorkSort"
            )
            remove_expression = ""
        else:
            remove_expression = (
                " REMOVE outboxWorkPartition, outboxWorkSort"
            )
        response = self._client.update_item(
            TableName=self.table_name,
            Key=_marshal(self._outbox_key(event.event_id)),
            UpdateExpression="SET " + set_expression + remove_expression,
            ConditionExpression=(
                f"({condition}) AND recordVersion = :expectedRecordVersion"
            ),
            ExpressionAttributeValues=_marshal(values),
            ReturnValues="ALL_NEW",
        )
        return self._deserialize_outbox(_unmarshal(response["Attributes"]))

    def _conditional_outbox_transition(
        self,
        next_event,
        current_event,
        worker,
        at,
        record_version,
    ):
        condition = (
            "deliveryStatus = :inFlight AND leaseOwner = :worker "
            "AND leaseExpiresAt > :timestamp AND attemptCount = :expectedAttempts"
        )
        try:
            return self._update_outbox(
                next_event,
                condition,
                {
                    ":inFlight": OutboxDeliveryStatus.IN_FLIGHT.value,
                    ":worker": worker,
                    ":timestamp": _iso(at),
                    ":expectedAttempts": current_event.attempt_count,
                },
                expected_record_version=record_version,
            )
        except ClientError as error:
            if self._is_conditional_failure(error):
                raise OutboxTransitionError(
                    "Outbox event lease is not owned and active"
                ) from error
            raise

    def _require_outbox_event(self, event_id):
        event_and_version = self._get_outbox_event_with_version(event_id)
        if event_and_version is None:
            raise OutboxTransitionError("Outbox event does not exist")
        return event_and_version[0]

    def _require_outbox_event_with_version(self, event_id):
        event_and_version = self._get_outbox_event_with_version(event_id)
        if event_and_version is None:
            raise OutboxTransitionError("Outbox event does not exist")
        return event_and_version

    def _get_outbox_event_with_version(self, event_id):
        item = self._get_item(self._outbox_key(event_id))
        if item is None:
            return None
        self._require_item_type(item, "ALERT_OUTBOX_EVENT")
        return self._deserialize_outbox(item), self._record_version(item)

    def _require_owned_lease(self, event_id, worker_id, timestamp):
        event, record_version = self._require_outbox_event_with_version(event_id)
        if (
            event.delivery_status != OutboxDeliveryStatus.IN_FLIGHT
            or event.lease_owner != worker_id
            or event.lease_expires_at is None
            or timestamp >= event.lease_expires_at
        ):
            raise OutboxTransitionError(
                "Outbox event lease is not owned and active"
            )
        return event, record_version

    def _record_version(self, item):
        value = item.get(_OUTBOX_RECORD_VERSION)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise StateIntegrityError(
                "Persisted AlertOutboxEvent record version is invalid"
            )
        return value

    def _outbox_work_attributes(self, event):
        if event.delivery_status == OutboxDeliveryStatus.PENDING:
            category = "OUTBOX_DUE"
            timestamp = event.available_at
        elif event.delivery_status == OutboxDeliveryStatus.IN_FLIGHT:
            category = "OUTBOX_DUE"
            timestamp = event.lease_expires_at
        elif event.delivery_status == OutboxDeliveryStatus.DEAD_LETTER:
            category = "OUTBOX_DEAD"
            timestamp = event.dead_lettered_at
        else:
            return {}
        shard = _outbox_work_shard(event.event_id)
        return {
            OUTBOX_WORK_PARTITION_ATTRIBUTE: self._pk(
                f"{category}#v1#{shard:02d}"
            ),
            OUTBOX_WORK_SORT_ATTRIBUTE: (
                f"{_work_timestamp(timestamp)}#EVENT#{event.event_id}"
            ),
        }

    def _query_due_outbox_keys(self, as_of, limit):
        cutoff = f"{_work_timestamp(as_of)}#\uffff"
        max_inspected_per_shard = limit * _MAX_DISCOVERY_OVERREAD
        candidates = []
        seen = set()
        for shard in range(OUTBOX_WORK_SHARD_COUNT):
            request = {
                "TableName": self.table_name,
                "IndexName": self._outbox_work_index_name,
                "KeyConditionExpression": (
                    "outboxWorkPartition = :partition "
                    "AND outboxWorkSort <= :cutoff"
                ),
                "ExpressionAttributeValues": _marshal(
                    {
                        ":partition": self._pk(
                            f"OUTBOX_DUE#v1#{shard:02d}"
                        ),
                        ":cutoff": cutoff,
                    }
                ),
                "ScanIndexForward": True,
                "Limit": min(limit, max_inspected_per_shard),
            }
            inspected = 0
            while inspected < max_inspected_per_shard:
                response = self._client.query(**request)
                items = tuple(
                    _unmarshal(item) for item in response.get("Items", ())
                )
                inspected += len(items)
                for item in items:
                    key_identity = (item.get("PK"), item.get("SK"))
                    if None in key_identity or key_identity in seen:
                        continue
                    seen.add(key_identity)
                    candidates.append(
                        (
                            item.get(OUTBOX_WORK_SORT_ATTRIBUTE, ""),
                            {"PK": key_identity[0], "SK": key_identity[1]},
                        )
                    )
                last_key = response.get("LastEvaluatedKey")
                if not last_key:
                    break
                request["ExclusiveStartKey"] = last_key
                request["Limit"] = min(
                    limit,
                    max_inspected_per_shard - inspected,
                )
        candidates.sort(key=lambda value: (value[0], value[1]["SK"]))
        return tuple(
            key for _, key in candidates[: limit * _MAX_DISCOVERY_OVERREAD]
        )

    def _quarantine_corrupt_outbox_item(
        self,
        item,
        *,
        quarantined_at,
        error_code,
    ):
        event_id = item.get("eventId")
        if not isinstance(event_id, str) or not event_id.strip():
            event_id = str(item.get("SK") or "unknown").removeprefix("EVENT#")
        shard = _outbox_work_shard(event_id)
        values = {
            ":quarantined": "QUARANTINED",
            ":quarantinedAt": _iso(quarantined_at),
            ":errorCode": _required_text(error_code, "error_code"),
            ":workPartition": self._pk(
                f"OUTBOX_QUARANTINE#v1#{shard:02d}"
            ),
            ":workSort": (
                f"{_work_timestamp(quarantined_at)}#EVENT#{event_id}"
            ),
        }
        conditions = []
        for attribute, placeholder in (
            ("entityType", ":observedEntityType"),
            ("document", ":observedDocument"),
            (_OUTBOX_RECORD_VERSION, ":observedRecordVersion"),
        ):
            if attribute in item:
                conditions.append(f"{attribute} = {placeholder}")
                values[placeholder] = item[attribute]
            else:
                conditions.append(f"attribute_not_exists({attribute})")
        record_version = item.get(_OUTBOX_RECORD_VERSION)
        if (
            isinstance(record_version, int)
            and not isinstance(record_version, bool)
            and record_version > 0
        ):
            values[":nextVersion"] = record_version + 1
        else:
            values[":nextVersion"] = 1
        condition = " AND ".join(conditions)
        try:
            self._client.update_item(
                TableName=self.table_name,
                Key=_marshal({"PK": item["PK"], "SK": item["SK"]}),
                UpdateExpression=(
                    "SET persistenceState = :quarantined, "
                    "quarantinedAt = :quarantinedAt, "
                    "quarantineErrorCode = :errorCode, "
                    "recordVersion = :nextVersion, "
                    "outboxWorkPartition = :workPartition, "
                    "outboxWorkSort = :workSort"
                ),
                ConditionExpression=condition,
                ExpressionAttributeValues=_marshal(values),
            )
            return True
        except ClientError as error:
            if self._is_conditional_failure(error):
                return False
            raise

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
    def _is_conditional_failure(error):
        return error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"

    @staticmethod
    def _require_item_type(item, expected):
        if item.get("entityType") != expected:
            raise StateIntegrityError(f"Expected DynamoDB item type {expected}")

    def _pk(self, value):
        return f"{self._key_prefix}{value}"

    def _lot_partition(self, lot_trip_id):
        return self._pk(f"LOTTRIP#{_required_text(lot_trip_id, 'lot_trip_id')}")

    def _trip_key(self, trip_id):
        return {
            "PK": self._pk(f"TRIP#{_required_text(trip_id, 'trip_id')}"),
            "SK": "META",
        }

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

    def _decision_key(self, lot_trip_id, decision_id):
        return {
            "PK": self._lot_partition(lot_trip_id),
            "SK": f"DECISION#{_required_text(decision_id, 'decision_id')}",
        }

    def _outbox_partition(self):
        return self._pk("ALERT_OUTBOX")

    def _outbox_key(self, event_id):
        return {
            "PK": self._outbox_partition(),
            "SK": f"EVENT#{_required_text(event_id, 'event_id')}",
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


def _aware_timestamp(value, field):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise OutboxTransitionError(f"{field} must be timezone-aware")
    return value


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _work_timestamp(value):
    timestamp = _aware_timestamp(value, "outbox work timestamp")
    return timestamp.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )


def _outbox_work_shard(event_id):
    value = _required_text(event_id, "event_id")
    return int.from_bytes(sha256(value.encode("utf-8")).digest()[:4], "big") % (
        OUTBOX_WORK_SHARD_COUNT
    )


def _effective_due_at(event):
    if event.delivery_status == OutboxDeliveryStatus.IN_FLIGHT:
        return event.lease_expires_at
    return event.available_at


def _is_dispatchable(event, as_of):
    return (
        event.delivery_status == OutboxDeliveryStatus.PENDING
        and event.available_at <= as_of
    ) or (
        event.delivery_status == OutboxDeliveryStatus.IN_FLIGHT
        and event.lease_expires_at is not None
        and event.lease_expires_at <= as_of
    )


def _positive_integer(value, field):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value
