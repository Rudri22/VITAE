from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier

try:
    from .alerting import (
        Alert,
        AlertConflictError,
        AlertRepository,
        AlertSeverity,
        AlertStatus,
        AlertTransitionError,
        AlertType,
    )
    from .risk_rules import ApplicationStatus, StatusDecision
    from .state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        IdentityRepository,
        StateIntegrityError,
        TelemetryStateRepository,
        live_state_from_decision,
        telemetry_record_from_sample,
    )
    from .telemetry import ValidatedTelemetrySample
    from .shipment_access import (
        ShipmentAccess,
        ShipmentAccessConflictError,
        ShipmentAccessNotFoundError,
        ShipmentAccessRepository,
    )
    from .trip_identity import DeviceAssignment, TripIdentity, TripStatus
except ImportError:
    from alerting import (
        Alert,
        AlertConflictError,
        AlertRepository,
        AlertSeverity,
        AlertStatus,
        AlertTransitionError,
        AlertType,
    )
    from risk_rules import ApplicationStatus, StatusDecision
    from state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        IdentityRepository,
        StateIntegrityError,
        TelemetryStateRepository,
        live_state_from_decision,
        telemetry_record_from_sample,
    )
    from telemetry import ValidatedTelemetrySample
    from shipment_access import (
        ShipmentAccess,
        ShipmentAccessConflictError,
        ShipmentAccessNotFoundError,
        ShipmentAccessRepository,
    )
    from trip_identity import DeviceAssignment, TripIdentity, TripStatus


CONTRACT_TIME = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def contract_trip(**changes):
    value = TripIdentity(
        trip_id="contract-trip",
        lot_trip_id="contract-lot-trip",
        lot_id="contract-lot",
        device_id="contract-device",
        product_id="gardasil-9",
        presentation="single-dose-prefilled-syringe-0.5-ml",
        state="unopened",
        product_rule_version="uspi-v503-i-2503r017",
        origin="Origin",
        destination="Destination",
        start_time=CONTRACT_TIME,
        status=TripStatus.PLANNED,
    )
    return replace(value, **changes)


def contract_assignment(**changes):
    value = DeviceAssignment(
        assignment_id="contract-assignment",
        device_id="contract-device",
        trip_id="contract-trip",
        lot_trip_id="contract-lot-trip",
        assigned_at=CONTRACT_TIME,
        active=False,
    )
    return replace(value, **changes)


def contract_shipment_access(**changes):
    value = ShipmentAccess(
        shipment_id="contract-shipment",
        lot_trip_id="contract-lot-trip",
        organization_id="contract-organization",
        driver_id="contract-driver",
    )
    return replace(value, **changes)


def contract_sample(*, sample_id="contract-sample-1", minutes=0):
    return ValidatedTelemetrySample(
        sample_id=sample_id,
        device_id="contract-device",
        timestamp=CONTRACT_TIME + timedelta(minutes=minutes),
        temperature=6.0,
        battery_level=90.0,
        latitude=33.8938,
        longitude=35.5018,
        device_health="OK",
    )


def contract_state(sample, previous=None):
    decision = StatusDecision(
        status=ApplicationStatus.SAFE,
        reason_code="TEMPERATURE_WITHIN_NORMAL_RANGE",
        active_rule_id=None,
        excursion_episode_duration_minutes=0.0,
        cumulative_excursion_duration_minutes=0.0,
        excursion_utilization=None,
    )
    return live_state_from_decision(
        lot_trip_id="contract-lot-trip",
        trip_id="contract-trip",
        product_id="gardasil-9",
        product_rule_version="uspi-v503-i-2503r017",
        sample=sample,
        decision=decision,
        previous_live_state=previous,
    )


def contract_alert(**changes):
    value = Alert(
        alert_id="contract-alert",
        alert_type=AlertType.EXCURSION_MONITOR,
        severity=AlertSeverity.INFO,
        status=AlertStatus.OPEN,
        trip_id="contract-trip",
        lot_trip_id="contract-lot-trip",
        device_id="contract-device",
        sample_id="contract-sample-1",
        source_status=ApplicationStatus.MONITOR,
        reason_code="PERMITTED_EXCURSION_BELOW_50_PERCENT",
        active_rule_id="gardasil-9-high-temperature-excursion",
        message="Product is in a verified permitted excursion",
        recommended_action="Continue monitoring",
        detected_at=CONTRACT_TIME,
        updated_at=CONTRACT_TIME,
    )
    return replace(value, **changes)


class IdentityRepositoryContractMixin:
    """Reusable behavior suite for every IdentityRepository adapter."""

    def make_identity_repository(self):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.repository = self.make_identity_repository()

    def test_contract_runtime_protocol(self):
        self.assertIsInstance(self.repository, IdentityRepository)

    def test_contract_registers_pair_and_supports_all_required_reads(self):
        trip = contract_trip()
        assignment = contract_assignment()
        self.repository.register_trip_and_assignment(trip, assignment)
        self.assertEqual(self.repository.get_trip_by_id(trip.trip_id), trip)
        self.assertEqual(
            self.repository.get_trip_by_lot_trip_id(trip.lot_trip_id), trip
        )
        self.assertEqual(
            self.repository.get_device_assignments(assignment.device_id),
            (assignment,),
        )

    def test_contract_registration_is_idempotent_for_identical_pair(self):
        trip = contract_trip()
        assignment = contract_assignment()
        self.repository.register_trip_and_assignment(trip, assignment)
        self.repository.register_trip_and_assignment(trip, assignment)
        self.assertEqual(
            self.repository.get_device_assignments(assignment.device_id),
            (assignment,),
        )

    def test_contract_registration_conflict_has_no_partial_write(self):
        trip = contract_trip()
        assignment = contract_assignment()
        self.repository.register_trip_and_assignment(trip, assignment)
        conflicting_trip = replace(
            trip,
            trip_id="second-trip",
            lot_trip_id="second-lot-trip",
        )
        conflicting_assignment = replace(
            assignment,
            assignment_id="second-assignment",
            trip_id="second-trip",
            lot_trip_id="second-lot-trip",
        )
        with self.assertRaises(StateIntegrityError):
            self.repository.register_trip_and_assignment(
                conflicting_trip,
                conflicting_assignment,
            )
        self.assertIsNone(self.repository.get_trip_by_id("second-trip"))

    def test_contract_lifecycle_transition_updates_pair_atomically(self):
        self.repository.register_trip_and_assignment(
            contract_trip(), contract_assignment()
        )
        trip, assignment = self.repository.transition_trip_and_assignment(
            "contract-trip",
            "contract-assignment",
            TripStatus.PLANNED,
            TripStatus.ACTIVE,
            False,
            True,
        )
        self.assertEqual(trip.status, TripStatus.ACTIVE)
        self.assertTrue(assignment.active)
        self.assertEqual(self.repository.get_trip_by_id(trip.trip_id), trip)
        self.assertEqual(
            self.repository.get_device_assignments(assignment.device_id),
            (assignment,),
        )

    def test_contract_stale_lifecycle_transition_changes_nothing(self):
        trip = contract_trip()
        assignment = contract_assignment()
        self.repository.register_trip_and_assignment(trip, assignment)
        with self.assertRaises(StateIntegrityError):
            self.repository.transition_trip_and_assignment(
                trip.trip_id,
                assignment.assignment_id,
                TripStatus.ACTIVE,
                TripStatus.COMPLETED,
                True,
                False,
            )
        self.assertEqual(self.repository.get_trip_by_id(trip.trip_id), trip)
        self.assertEqual(
            self.repository.get_device_assignments(assignment.device_id),
            (assignment,),
        )

    def test_contract_planned_registration_can_be_compensated(self):
        trip = contract_trip()
        assignment = contract_assignment()
        self.repository.register_trip_and_assignment(trip, assignment)
        self.repository.unregister_planned_trip_and_assignment(
            trip.trip_id,
            assignment.assignment_id,
        )
        self.assertIsNone(self.repository.get_trip_by_id(trip.trip_id))
        self.assertEqual(
            self.repository.get_device_assignments(assignment.device_id), ()
        )


class ShipmentAccessRepositoryContractMixin:
    """Reusable behavior suite for every ShipmentAccessRepository adapter."""

    def make_shipment_access_repository(self):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.repository = self.make_shipment_access_repository()
        self.access = contract_shipment_access()

    def test_contract_runtime_protocol(self):
        self.assertIsInstance(self.repository, ShipmentAccessRepository)

    def test_contract_register_get_and_list(self):
        self.assertEqual(
            self.repository.register_shipment_access(self.access), self.access
        )
        self.assertEqual(
            self.repository.get_shipment_access(self.access.lot_trip_id),
            self.access,
        )
        self.assertEqual(
            self.repository.list_shipment_accesses(), (self.access,)
        )

    def test_contract_identical_registration_is_idempotent(self):
        self.repository.register_shipment_access(self.access)
        self.assertEqual(
            self.repository.register_shipment_access(self.access), self.access
        )
        self.assertEqual(
            self.repository.list_shipment_accesses(), (self.access,)
        )

    def test_contract_lot_trip_conflict_preserves_original(self):
        self.repository.register_shipment_access(self.access)
        with self.assertRaises(ShipmentAccessConflictError):
            self.repository.register_shipment_access(
                replace(self.access, shipment_id="different-shipment")
            )
        self.assertEqual(
            self.repository.get_shipment_access(self.access.lot_trip_id),
            self.access,
        )

    def test_contract_shipment_id_is_unique(self):
        self.repository.register_shipment_access(self.access)
        with self.assertRaises(ShipmentAccessConflictError):
            self.repository.register_shipment_access(
                replace(self.access, lot_trip_id="different-lot-trip")
            )
        self.assertIsNone(
            self.repository.get_shipment_access("different-lot-trip")
        )

    def test_contract_filters_organization_and_driver(self):
        second = replace(
            self.access,
            shipment_id="second-shipment",
            lot_trip_id="second-lot-trip",
            organization_id="second-organization",
            driver_id="second-driver",
        )
        self.repository.register_shipment_access(self.access)
        self.repository.register_shipment_access(second)
        self.assertEqual(
            self.repository.list_shipment_accesses(
                organization_id=self.access.organization_id
            ),
            (self.access,),
        )
        self.assertEqual(
            self.repository.list_shipment_accesses(driver_id=second.driver_id),
            (second,),
        )

    def test_contract_unregister_requires_both_identities(self):
        self.repository.register_shipment_access(self.access)
        with self.assertRaises(ShipmentAccessNotFoundError):
            self.repository.unregister_shipment_access(
                self.access.lot_trip_id,
                "wrong-shipment",
            )
        self.assertEqual(
            self.repository.get_shipment_access(self.access.lot_trip_id),
            self.access,
        )
        self.repository.unregister_shipment_access(
            self.access.lot_trip_id,
            self.access.shipment_id,
        )
        self.assertIsNone(
            self.repository.get_shipment_access(self.access.lot_trip_id)
        )

    def test_contract_driver_transition_is_conditional(self):
        self.repository.register_shipment_access(self.access)
        updated = self.repository.transition_shipment_access_driver(
            self.access.lot_trip_id,
            self.access.driver_id,
            "replacement-driver",
        )
        self.assertEqual(updated.driver_id, "replacement-driver")
        with self.assertRaises(ShipmentAccessConflictError):
            self.repository.transition_shipment_access_driver(
                self.access.lot_trip_id,
                self.access.driver_id,
                "stale-replacement",
            )
        self.assertEqual(
            self.repository.get_shipment_access(self.access.lot_trip_id),
            updated,
        )


class TelemetryStateRepositoryContractMixin:
    """Reusable behavior suite for every TelemetryStateRepository adapter."""

    def make_telemetry_state_repository(self):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.repository = self.make_telemetry_state_repository()
        self.sample = contract_sample()
        self.record = telemetry_record_from_sample(
            "contract-trip", "contract-lot-trip", self.sample
        )
        self.state = contract_state(self.sample)

    def commit_first(self):
        self.repository.commit_sample_and_state(
            self.record, self.state, expected_revision=None
        )

    def test_contract_runtime_protocol(self):
        self.assertIsInstance(self.repository, TelemetryStateRepository)

    def test_contract_commits_record_and_state_together(self):
        self.commit_first()
        self.assertEqual(
            self.repository.get_telemetry_history("contract-lot-trip"),
            (self.record,),
        )
        self.assertEqual(
            self.repository.get_live_state("contract-lot-trip"), self.state
        )

    def test_contract_duplicate_identity_has_no_partial_write(self):
        self.commit_first()
        with self.assertRaises(DuplicateTelemetrySampleError):
            self.repository.commit_sample_and_state(
                self.record,
                replace(self.state, revision=2),
                expected_revision=1,
            )
        self.assertEqual(
            self.repository.get_telemetry_history("contract-lot-trip"),
            (self.record,),
        )
        self.assertEqual(
            self.repository.get_live_state("contract-lot-trip"), self.state
        )

    def test_contract_stale_revision_has_no_partial_write(self):
        self.commit_first()
        next_sample = contract_sample(sample_id="contract-sample-2", minutes=5)
        with self.assertRaises(ConcurrentStateUpdateError):
            self.repository.commit_sample_and_state(
                telemetry_record_from_sample(
                    "contract-trip", "contract-lot-trip", next_sample
                ),
                contract_state(next_sample, self.state),
                expected_revision=0,
            )
        self.assertEqual(
            self.repository.get_telemetry_history("contract-lot-trip"),
            (self.record,),
        )

    def test_contract_successive_samples_preserve_history_and_revision(self):
        self.commit_first()
        next_sample = contract_sample(sample_id="contract-sample-2", minutes=5)
        next_record = telemetry_record_from_sample(
            "contract-trip", "contract-lot-trip", next_sample
        )
        next_state = contract_state(next_sample, self.state)
        self.repository.commit_sample_and_state(
            next_record, next_state, expected_revision=1
        )
        self.assertEqual(
            self.repository.get_telemetry_history("contract-lot-trip"),
            (self.record, next_record),
        )
        self.assertEqual(next_state.revision, 2)

    def test_contract_reads_are_side_effect_free(self):
        self.assertIsNone(self.repository.get_live_state("missing-lot-trip"))
        self.assertEqual(
            self.repository.get_telemetry_history("missing-lot-trip"), ()
        )
        self.assertFalse(
            self.repository.has_sample("contract-device", "missing-sample")
        )

    def test_contract_concurrent_duplicate_has_one_commit(self):
        barrier = Barrier(2)

        def commit_once():
            barrier.wait()
            try:
                self.commit_first()
                return "committed"
            except DuplicateTelemetrySampleError:
                return "duplicate"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: commit_once(), range(2)))
        self.assertCountEqual(outcomes, ("committed", "duplicate"))
        self.assertEqual(
            self.repository.get_telemetry_history("contract-lot-trip"),
            (self.record,),
        )


class AlertRepositoryContractMixin:
    """Reusable behavior suite for every AlertRepository adapter."""

    def make_alert_repository(self):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.repository = self.make_alert_repository()
        self.alert = contract_alert()

    def test_contract_runtime_protocol(self):
        self.assertIsInstance(self.repository, AlertRepository)

    def test_contract_save_get_and_list(self):
        saved = self.repository.save_alert(self.alert)
        self.assertEqual(saved, self.alert)
        self.assertEqual(self.repository.get_alert(self.alert.alert_id), self.alert)
        self.assertEqual(
            self.repository.list_alerts(lot_trip_id=self.alert.lot_trip_id),
            (self.alert,),
        )

    def test_contract_save_is_idempotent_and_conflicts_are_rejected(self):
        self.repository.save_alert(self.alert)
        self.assertEqual(self.repository.save_alert(self.alert), self.alert)
        with self.assertRaises(AlertConflictError):
            self.repository.save_alert(replace(self.alert, message="Different"))
        self.assertEqual(self.repository.get_alert(self.alert.alert_id), self.alert)

    def test_contract_acknowledgement_records_actor_and_time(self):
        self.repository.save_alert(self.alert)
        timestamp = CONTRACT_TIME + timedelta(minutes=1)
        updated = self.repository.acknowledge_alert(
            self.alert.alert_id,
            actor_id="contract-driver",
            acknowledged_at=timestamp,
        )
        self.assertEqual(updated.status, AlertStatus.ACKNOWLEDGED)
        self.assertEqual(updated.acknowledged_by, "contract-driver")
        self.assertEqual(updated.acknowledged_at, timestamp)

    def test_contract_action_is_append_only(self):
        self.repository.save_alert(self.alert)
        updated = self.repository.record_action(
            self.alert.alert_id,
            description="Inspected cooling unit",
            actor_id="contract-driver",
            recorded_at=CONTRACT_TIME + timedelta(minutes=1),
        )
        self.assertEqual(len(updated.actions), 1)
        self.assertEqual(updated.actions[0].description, "Inspected cooling unit")

    def test_contract_resolution_retains_history_and_blocks_changes(self):
        self.repository.save_alert(self.alert)
        resolved = self.repository.resolve_alert(
            self.alert.alert_id,
            actor_id="contract-organization",
            resolved_at=CONTRACT_TIME + timedelta(minutes=1),
            resolution_note="Disposition recorded",
        )
        self.assertEqual(resolved.status, AlertStatus.RESOLVED)
        self.assertEqual(
            self.repository.list_alerts(status=AlertStatus.RESOLVED),
            (resolved,),
        )
        with self.assertRaises(AlertTransitionError):
            self.repository.record_action(
                resolved.alert_id,
                description="Late action",
                actor_id="contract-driver",
                recorded_at=CONTRACT_TIME + timedelta(minutes=2),
            )

    def test_contract_list_filters_by_lot_trip_and_status(self):
        acknowledged_source = replace(
            self.alert,
            alert_id="acknowledged-alert",
        )
        other = replace(
            self.alert,
            alert_id="other-alert",
            lot_trip_id="other-lot-trip",
        )
        for alert in (self.alert, acknowledged_source, other):
            self.repository.save_alert(alert)
        acknowledged = self.repository.acknowledge_alert(
            acknowledged_source.alert_id,
            actor_id="contract-driver",
            acknowledged_at=CONTRACT_TIME + timedelta(minutes=1),
        )
        self.assertCountEqual(
            self.repository.list_alerts(lot_trip_id="contract-lot-trip"),
            (self.alert, acknowledged),
        )
        self.assertEqual(
            self.repository.list_alerts(status=AlertStatus.ACKNOWLEDGED),
            (acknowledged,),
        )
