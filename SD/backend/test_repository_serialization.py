import json
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta, timezone

try:
    from .alerting import InMemoryAlertRepository
    from .decision_outbox import OutboxDeliveryStatus
    from .repository_contract_suite import (
        CONTRACT_TIME,
        contract_alert,
        contract_alert_outbox_event,
        contract_assignment,
        contract_completed_trip_outcome,
        contract_decision_record,
        contract_sample,
        contract_shipment_access,
        contract_state,
        contract_trip,
    )
    from .repository_serialization import (
        RepositorySerializationError,
        deserialize_completed_trip_outcome,
        deserialize_alert_outbox_event,
        deserialize_alert,
        deserialize_alert_action,
        deserialize_device_assignment,
        deserialize_live_state,
        deserialize_shipment_access,
        deserialize_telemetry_record,
        deserialize_status_decision_record,
        deserialize_trip_identity,
        serialize_alert,
        serialize_alert_outbox_event,
        serialize_alert_action,
        serialize_completed_trip_outcome,
        serialize_device_assignment,
        serialize_live_state,
        serialize_shipment_access,
        serialize_telemetry_record,
        serialize_status_decision_record,
        serialize_trip_identity,
    )
    from .state_repository import telemetry_record_from_sample
    from .trip_identity import TripStatus
except ImportError:
    from alerting import InMemoryAlertRepository
    from decision_outbox import OutboxDeliveryStatus
    from repository_contract_suite import (
        CONTRACT_TIME,
        contract_alert,
        contract_alert_outbox_event,
        contract_assignment,
        contract_completed_trip_outcome,
        contract_decision_record,
        contract_sample,
        contract_shipment_access,
        contract_state,
        contract_trip,
    )
    from repository_serialization import (
        RepositorySerializationError,
        deserialize_completed_trip_outcome,
        deserialize_alert_outbox_event,
        deserialize_alert,
        deserialize_alert_action,
        deserialize_device_assignment,
        deserialize_live_state,
        deserialize_shipment_access,
        deserialize_telemetry_record,
        deserialize_status_decision_record,
        deserialize_trip_identity,
        serialize_alert,
        serialize_alert_outbox_event,
        serialize_alert_action,
        serialize_completed_trip_outcome,
        serialize_device_assignment,
        serialize_live_state,
        serialize_shipment_access,
        serialize_telemetry_record,
        serialize_status_decision_record,
        serialize_trip_identity,
    )
    from state_repository import telemetry_record_from_sample
    from trip_identity import TripStatus


class RepositorySerializationTests(unittest.TestCase):
    def setUp(self):
        self.trip = contract_trip()
        self.assignment = contract_assignment()
        self.sample = contract_sample()
        self.record = telemetry_record_from_sample(
            self.trip.trip_id,
            self.trip.lot_trip_id,
            self.sample,
        )
        self.state = contract_state(self.sample)

    def test_trip_identity_round_trip(self):
        self.assertEqual(
            deserialize_trip_identity(serialize_trip_identity(self.trip)),
            self.trip,
        )

    def test_completed_trip_identity_round_trip(self):
        completed = replace(
            self.trip,
            status=TripStatus.COMPLETED,
            completed_at=CONTRACT_TIME + timedelta(hours=1),
        )
        document = serialize_trip_identity(completed)
        self.assertEqual(document["schema_version"], 2)
        self.assertEqual(
            deserialize_trip_identity(document),
            completed,
        )

    def test_trip_identity_v1_non_completed_document_remains_readable(self):
        document = serialize_trip_identity(self.trip)
        document["schema_version"] = 1
        document.pop("completed_at")
        self.assertEqual(deserialize_trip_identity(document), self.trip)

    def test_trip_identity_v1_completed_document_is_rejected(self):
        document = serialize_trip_identity(self.trip)
        document["schema_version"] = 1
        document.pop("completed_at")
        document["status"] = TripStatus.COMPLETED.value
        with self.assertRaises(RepositorySerializationError):
            deserialize_trip_identity(document)

    def test_device_assignment_round_trip(self):
        self.assertEqual(
            deserialize_device_assignment(
                serialize_device_assignment(self.assignment)
            ),
            self.assignment,
        )

    def test_telemetry_record_round_trip(self):
        self.assertEqual(
            deserialize_telemetry_record(serialize_telemetry_record(self.record)),
            self.record,
        )

    def test_shipment_access_round_trip(self):
        access = contract_shipment_access()
        self.assertEqual(
            deserialize_shipment_access(serialize_shipment_access(access)),
            access,
        )

    def test_live_state_round_trip(self):
        excursion_state = replace(
            self.state,
            active_rule_id="excursion-rule",
            excursion_started_at=CONTRACT_TIME,
            excursion_episode_duration_minutes=12.5,
            cumulative_excursion_duration_minutes=22.5,
            excursion_utilization=0.25,
        )
        self.assertEqual(
            deserialize_live_state(serialize_live_state(excursion_state)),
            excursion_state,
        )

    def test_completed_trip_outcome_round_trip(self):
        outcome = contract_completed_trip_outcome()
        self.assertEqual(
            deserialize_completed_trip_outcome(
                serialize_completed_trip_outcome(outcome)
            ),
            outcome,
        )

    def test_status_decision_record_round_trip(self):
        decision = contract_decision_record()
        self.assertEqual(
            deserialize_status_decision_record(
                serialize_status_decision_record(decision)
            ),
            decision,
        )

    def test_alert_outbox_event_round_trip_preserves_exact_candidate(self):
        event = contract_alert_outbox_event()
        restored = deserialize_alert_outbox_event(
            serialize_alert_outbox_event(event)
        )
        self.assertEqual(restored, event)
        self.assertEqual(restored.alert_candidate, event.alert_candidate)

    def test_alert_action_round_trip(self):
        repository = InMemoryAlertRepository()
        alert = repository.save_alert(contract_alert())
        updated = repository.record_action(
            alert.alert_id,
            description="Inspected cooling unit",
            actor_id="contract-driver",
            recorded_at=CONTRACT_TIME + timedelta(minutes=1),
        )
        action = updated.actions[0]
        self.assertEqual(
            deserialize_alert_action(serialize_alert_action(action)), action
        )

    def test_full_alert_lifecycle_round_trip(self):
        repository = InMemoryAlertRepository()
        alert = repository.save_alert(contract_alert())
        alert = repository.acknowledge_alert(
            alert.alert_id,
            actor_id="contract-driver",
            acknowledged_at=CONTRACT_TIME + timedelta(minutes=1),
        )
        alert = repository.record_action(
            alert.alert_id,
            description="Inspected cooling unit",
            actor_id="contract-driver",
            recorded_at=CONTRACT_TIME + timedelta(minutes=2),
        )
        alert = repository.resolve_alert(
            alert.alert_id,
            actor_id="contract-organization",
            resolved_at=CONTRACT_TIME + timedelta(minutes=3),
            resolution_note="Disposition recorded",
        )
        self.assertEqual(deserialize_alert(serialize_alert(alert)), alert)

    def test_outbox_v1_document_remains_readable(self):
        event = contract_alert_outbox_event()
        document = serialize_alert_outbox_event(event)
        document["schema_version"] = 1
        document.pop("dead_lettered_at")
        document.pop("dead_lettered_by")
        self.assertEqual(deserialize_alert_outbox_event(document), event)

    def test_dead_letter_outbox_v2_round_trip(self):
        event = replace(
            contract_alert_outbox_event(),
            delivery_status=OutboxDeliveryStatus.DEAD_LETTER,
            attempt_count=1,
            last_error_code="ALERT_CREATION_CONFLICT",
            dead_lettered_at=CONTRACT_TIME + timedelta(minutes=1),
            dead_lettered_by="worker-a",
        )
        document = serialize_alert_outbox_event(event)
        self.assertEqual(document["schema_version"], 2)
        self.assertEqual(deserialize_alert_outbox_event(document), event)

    def test_every_schema_is_json_serializable_and_versioned(self):
        documents = (
            serialize_trip_identity(self.trip),
            serialize_device_assignment(self.assignment),
            serialize_shipment_access(contract_shipment_access()),
            serialize_telemetry_record(self.record),
            serialize_live_state(self.state),
            serialize_status_decision_record(contract_decision_record()),
            serialize_alert_outbox_event(contract_alert_outbox_event()),
            serialize_alert(contract_alert()),
            serialize_completed_trip_outcome(contract_completed_trip_outcome()),
        )
        for document in documents:
            with self.subTest(schema=document["schema"]):
                expected_version = (
                    2
                    if document["schema"]
                    in {"vitae.trip_identity", "vitae.alert_outbox_event"}
                    else 1
                )
                self.assertEqual(document["schema_version"], expected_version)
                self.assertIsInstance(json.dumps(document), str)

    def test_timestamps_are_canonical_utc(self):
        offset_trip = replace(
            self.trip,
            start_time=CONTRACT_TIME.astimezone(timezone(timedelta(hours=3))),
        )
        document = serialize_trip_identity(offset_trip)
        self.assertTrue(document["start_time"].endswith("Z"))
        self.assertEqual(
            deserialize_trip_identity(document).start_time,
            self.trip.start_time,
        )
        completed = replace(
            self.trip,
            status=TripStatus.COMPLETED,
            completed_at=(CONTRACT_TIME + timedelta(hours=1)).astimezone(
                timezone(timedelta(hours=3))
            ),
        )
        completed_document = serialize_trip_identity(completed)
        self.assertTrue(completed_document["completed_at"].endswith("Z"))

    def test_unknown_schema_version_is_rejected(self):
        for version in (3, True, "2"):
            document = serialize_trip_identity(self.trip)
            document["schema_version"] = version
            with self.subTest(version=version):
                with self.assertRaises(RepositorySerializationError):
                    deserialize_trip_identity(document)

    def test_missing_or_unexpected_fields_are_rejected(self):
        missing = serialize_live_state(self.state)
        missing.pop("revision")
        unexpected = serialize_live_state(self.state)
        unexpected["new_field"] = "not-versioned"
        for document in (missing, unexpected):
            with self.subTest(keys=tuple(document)):
                with self.assertRaises(RepositorySerializationError):
                    deserialize_live_state(document)

    def test_invalid_enum_and_timestamp_are_rejected(self):
        invalid_status = serialize_trip_identity(self.trip)
        invalid_status["status"] = "NOT_A_STATUS"
        naive_time = serialize_trip_identity(self.trip)
        naive_time["start_time"] = "2026-08-19T12:00:00"
        for document in (invalid_status, naive_time):
            with self.assertRaises(RepositorySerializationError):
                deserialize_trip_identity(deepcopy(document))

    def test_optional_values_round_trip_as_null(self):
        document = serialize_telemetry_record(
            replace(
                self.record,
                battery_level=None,
                latitude=None,
                longitude=None,
                device_health=None,
            )
        )
        self.assertIsNone(document["battery_level"])
        self.assertIsNone(
            deserialize_telemetry_record(document).battery_level
        )


if __name__ == "__main__":
    unittest.main()
