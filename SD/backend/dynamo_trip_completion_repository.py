from dataclasses import replace

try:
    from botocore.exceptions import ClientError
except ImportError as error:  # pragma: no cover - only without AWS extras
    raise RuntimeError(
        "boto3 is required to use DynamoTripCompletionRepository"
    ) from error

try:
    from .completed_trip_outcome import completed_trip_outcome_from_state
    from .dynamo_identity_repository import (
        DynamoIdentityAccessRepository,
        _marshal,
        _unmarshal,
    )
    from .dynamo_telemetry_repository import (
        DynamoTelemetryStateRepository,
        _marshal as _telemetry_marshal,
    )
    from .repository_serialization import (
        RepositorySerializationError,
        deserialize_completed_trip_outcome,
        deserialize_live_state,
        serialize_completed_trip_outcome,
    )
    from .trip_completion import (
        TripCompletionConflictError,
        TripCompletionIntegrityError,
        TripCompletionRepository,
        TripCompletionResult,
        completed_trip_replay_result,
    )
    from .trip_identity import TripStatus, trip_identity_with_status
except ImportError:
    from completed_trip_outcome import completed_trip_outcome_from_state
    from dynamo_identity_repository import (
        DynamoIdentityAccessRepository,
        _marshal,
        _unmarshal,
    )
    from dynamo_telemetry_repository import (
        DynamoTelemetryStateRepository,
        _marshal as _telemetry_marshal,
    )
    from repository_serialization import (
        RepositorySerializationError,
        deserialize_completed_trip_outcome,
        deserialize_live_state,
        serialize_completed_trip_outcome,
    )
    from trip_completion import (
        TripCompletionConflictError,
        TripCompletionIntegrityError,
        TripCompletionRepository,
        TripCompletionResult,
        completed_trip_replay_result,
    )
    from trip_identity import TripStatus, trip_identity_with_status


class DynamoTripCompletionRepository(
    DynamoIdentityAccessRepository,
    TripCompletionRepository,
):
    """Cross-table DynamoDB unit of work for one terminal trip transition."""

    def __init__(
        self,
        dynamodb_client,
        identity_table_name: str,
        telemetry_table_name: str,
        *,
        key_namespace="",
    ):
        super().__init__(
            dynamodb_client, identity_table_name, key_namespace=key_namespace
        )
        if not isinstance(telemetry_table_name, str) or not telemetry_table_name.strip():
            raise ValueError("telemetry_table_name must be a non-empty string")
        self.telemetry_table_name = telemetry_table_name.strip()
        self._telemetry_repository = DynamoTelemetryStateRepository(
            dynamodb_client,
            self.telemetry_table_name,
            identity_table_name=self.table_name,
            key_namespace=key_namespace,
        )

    @property
    def telemetry_repository(self) -> DynamoTelemetryStateRepository:
        return self._telemetry_repository

    def commit_sample_and_state(self, record, new_state, expected_revision):
        return self._telemetry_repository.commit_sample_and_state(
            record, new_state, expected_revision
        )

    def get_live_state(self, lot_trip_id):
        return self._telemetry_repository.get_live_state(lot_trip_id)

    def get_telemetry_history(self, lot_trip_id):
        return self._telemetry_repository.get_telemetry_history(lot_trip_id)

    def has_sample(self, device_id, sample_id):
        return self._telemetry_repository.has_sample(device_id, sample_id)

    def complete_trip(
        self,
        trip_id: str,
        assignment_id: str,
        *,
        completed_at,
    ) -> TripCompletionResult:
        trip, assignment, state, existing = self._load_completion_snapshot(
            trip_id, assignment_id
        )
        if trip is None or assignment is None:
            raise TripCompletionIntegrityError(
                "Trip completion identity does not exist"
            )
        if (
            assignment.trip_id != trip.trip_id
            or assignment.lot_trip_id != trip.lot_trip_id
            or assignment.device_id != trip.device_id
        ):
            raise TripCompletionIntegrityError(
                "Trip completion identity is inconsistent"
            )
        if trip.status == TripStatus.COMPLETED:
            return completed_trip_replay_result(
                trip, assignment, state, existing, completed_at
            )
        if trip.status != TripStatus.ACTIVE or not assignment.active:
            raise TripCompletionConflictError(
                "Only an ACTIVE trip with an active assignment can complete"
            )
        if existing is not None:
            raise TripCompletionIntegrityError(
                "An outcome exists before the trip is completed"
            )

        next_trip = trip_identity_with_status(
            trip, TripStatus.COMPLETED, completed_at=completed_at
        )
        next_assignment = replace(assignment, active=False)
        outcome = completed_trip_outcome_from_state(
            next_trip, next_trip.completed_at, state
        )
        result = TripCompletionResult(
            trip=next_trip,
            assignment=next_assignment,
            final_live_state=state,
            outcome=outcome,
        )
        actions = self._lifecycle_actions(
            trip, assignment, next_trip, next_assignment
        )
        actions.append(self._live_state_condition(trip.lot_trip_id, state))
        actions.append(self._put_outcome(outcome))
        try:
            self._transact_write(actions)
        except ClientError as error:
            if self._is_transaction_cancel(error):
                replay = self._try_completed_replay(
                    trip_id, assignment_id, completed_at
                )
                if replay is not None:
                    return replay
                raise TripCompletionConflictError(
                    "Trip, assignment, or final LiveState changed before completion"
                ) from error
            raise
        return result

    def get_completed_trip_outcome(self, lot_trip_id):
        item = self._get_item(self._outcome_key(_required_text(lot_trip_id, "lot_trip_id")))
        return None if item is None else self._outcome_from_item(item)

    def _try_completed_replay(self, trip_id, assignment_id, completed_at):
        trip, assignment, state, outcome = self._load_completion_snapshot(
            trip_id, assignment_id
        )
        if trip is None or assignment is None or trip.status != TripStatus.COMPLETED:
            return None
        return completed_trip_replay_result(
            trip, assignment, state, outcome, completed_at
        )

    def _load_completion_snapshot(self, trip_id, assignment_id):
        trip_key = self._trip_key(_required_text(trip_id, "trip_id"))
        assignment_key = self._assignment_key(
            _required_text(assignment_id, "assignment_id")
        )
        # The lot-trip identity is learned from the trip, then all mutable completion
        # inputs are reread together in one transactionally consistent snapshot.
        trip_item = self._get_item(trip_key)
        if trip_item is None:
            return None, None, None, None
        trip = self._trip_from_item(trip_item)
        keys = (
            (self.table_name, trip_key),
            (self.table_name, assignment_key),
            (self.telemetry_table_name, self._live_state_key(trip.lot_trip_id)),
            (self.table_name, self._outcome_key(trip.lot_trip_id)),
        )
        response = self._client.transact_get_items(
            TransactItems=[
                {"Get": {"TableName": table, "Key": _marshal(key)}}
                for table, key in keys
            ]
        )
        items = [
            None if not entry.get("Item") else _unmarshal(entry["Item"])
            for entry in response["Responses"]
        ]
        consistent_trip = None if items[0] is None else self._trip_from_item(items[0])
        assignment = (
            None if items[1] is None else self._assignment_from_item(items[1])
        )
        state = None if items[2] is None else self._live_state_from_item(items[2])
        outcome = None if items[3] is None else self._outcome_from_item(items[3])
        return consistent_trip, assignment, state, outcome

    def _live_state_condition(self, lot_trip_id, state):
        operation = {
            "TableName": self.telemetry_table_name,
            "Key": _marshal(self._live_state_key(lot_trip_id)),
        }
        if state is None:
            operation["ConditionExpression"] = (
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            )
        else:
            operation["ConditionExpression"] = (
                "entityType = :entityType AND lotTripId = :lotTripId "
                "AND revision = :revision AND document = :document"
            )
            operation["ExpressionAttributeValues"] = _telemetry_marshal(
                {
                    ":entityType": "LIVE_STATE",
                    ":lotTripId": state.lot_trip_id,
                    ":revision": state.revision,
                    ":document": self._serialized_live_state(state),
                }
            )
        return {"ConditionCheck": operation}

    def _put_outcome(self, outcome):
        item = {
            **self._outcome_key(outcome.lot_trip_id),
            "entityType": "COMPLETED_TRIP_OUTCOME",
            "lotTripId": outcome.lot_trip_id,
            "tripId": outcome.trip_id,
            "document": serialize_completed_trip_outcome(outcome),
        }
        return {
            "Put": {
                "TableName": self.table_name,
                "Item": _telemetry_marshal(item),
                "ConditionExpression": (
                    "attribute_not_exists(PK) AND attribute_not_exists(SK)"
                ),
            }
        }

    def _live_state_from_item(self, item):
        if item.get("entityType") != "LIVE_STATE":
            raise TripCompletionIntegrityError(
                "Persisted LiveState item type is invalid"
            )
        try:
            state = deserialize_live_state(item["document"])
        except (KeyError, RepositorySerializationError) as error:
            raise TripCompletionIntegrityError("Persisted LiveState is invalid") from error
        expected = (
            state.lot_trip_id,
            state.trip_id,
            state.device_id,
            state.revision,
        )
        actual = (
            item.get("lotTripId"),
            item.get("tripId"),
            item.get("deviceId"),
            item.get("revision"),
        )
        if actual != expected:
            raise TripCompletionIntegrityError(
                "Persisted LiveState index attributes disagree with document"
            )
        return state

    def _outcome_from_item(self, item):
        if item.get("entityType") != "COMPLETED_TRIP_OUTCOME":
            raise TripCompletionIntegrityError(
                "Persisted completed outcome item type is invalid"
            )
        try:
            outcome = deserialize_completed_trip_outcome(item["document"])
        except (KeyError, RepositorySerializationError) as error:
            raise TripCompletionIntegrityError(
                "Persisted completed-trip outcome is invalid"
            ) from error
        if (
            item.get("lotTripId") != outcome.lot_trip_id
            or item.get("tripId") != outcome.trip_id
        ):
            raise TripCompletionIntegrityError(
                "Persisted completed outcome identity is inconsistent"
            )
        return outcome

    @staticmethod
    def _serialized_live_state(state):
        try:
            from .repository_serialization import serialize_live_state
        except ImportError:
            from repository_serialization import serialize_live_state
        return serialize_live_state(state)

    def _live_state_key(self, lot_trip_id):
        return {"PK": self._pk(f"LOTTRIP#{lot_trip_id}"), "SK": "LIVE_STATE"}

    def _outcome_key(self, lot_trip_id):
        return {"PK": self._pk(f"LOTTRIP#{lot_trip_id}"), "SK": "OUTCOME"}


def _required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()
