from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier

try:
    from .completed_trip_outcome import (
        CompletedTripOutcomeConflictError,
        CompletedTripOutcomeRepository,
        completed_trip_outcome_from_state,
    )
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
    from .decision_outbox import (
        AlertOutboxEvent,
        DecisionOutboxError,
        OutboxClaimError,
        OutboxDeliveryStatus,
        OutboxTransitionError,
        ProcessingBundleRepository,
        StatusDecisionRecord,
        alert_outbox_event_from_candidate,
        decision_id_for,
    )
    from .state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        IdentityRepository,
        StateIntegrityError,
        TelemetryStateRepository,
        TripNotActiveAtCommitError,
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
    from completed_trip_outcome import (
        CompletedTripOutcomeConflictError,
        CompletedTripOutcomeRepository,
        completed_trip_outcome_from_state,
    )
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
    from decision_outbox import (
        AlertOutboxEvent,
        DecisionOutboxError,
        OutboxClaimError,
        OutboxDeliveryStatus,
        OutboxTransitionError,
        ProcessingBundleRepository,
        StatusDecisionRecord,
        alert_outbox_event_from_candidate,
        decision_id_for,
    )
    from state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        IdentityRepository,
        StateIntegrityError,
        TelemetryStateRepository,
        TripNotActiveAtCommitError,
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


def contract_completed_trip_outcome(**changes):
    sample = contract_sample()
    state = contract_state(sample)
    completed_at = CONTRACT_TIME + timedelta(minutes=30)
    trip = contract_trip(
        status=TripStatus.COMPLETED,
        completed_at=completed_at,
    )
    value = completed_trip_outcome_from_state(
        trip,
        completed_at,
        state,
    )
    return replace(value, **changes)


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


def contract_decision_record(sample=None, state=None, **changes):
    sample = sample or contract_sample()
    state = state or contract_state(sample)
    value = StatusDecisionRecord(
        decision_id=decision_id_for(
            state.lot_trip_id,
            state.device_id,
            sample.sample_id,
        ),
        trip_id=state.trip_id,
        lot_trip_id=state.lot_trip_id,
        device_id=state.device_id,
        sample_id=sample.sample_id,
        sample_timestamp=sample.timestamp,
        product_id=state.product_id,
        product_rule_version=state.product_rule_version,
        engine_version="deterministic-status-v1",
        previous_live_state_revision=(
            None if state.revision == 1 else state.revision - 1
        ),
        resulting_live_state_revision=state.revision,
        status=state.status,
        reason_code=state.reason_code,
        active_rule_id=state.active_rule_id,
        excursion_started_at=state.excursion_started_at,
        excursion_episode_duration_minutes=(
            state.excursion_episode_duration_minutes
        ),
        cumulative_excursion_duration_minutes=(
            state.cumulative_excursion_duration_minutes
        ),
        excursion_utilization=state.excursion_utilization,
    )
    return replace(value, **changes)


def contract_alert_outbox_event(decision=None, **changes):
    decision = decision or contract_decision_record()
    alert = contract_alert(
        trip_id=decision.trip_id,
        lot_trip_id=decision.lot_trip_id,
        device_id=decision.device_id,
        sample_id=decision.sample_id,
        source_status=decision.status,
        reason_code=decision.reason_code,
        active_rule_id=decision.active_rule_id,
        detected_at=decision.sample_timestamp,
        updated_at=decision.sample_timestamp,
    )
    value = alert_outbox_event_from_candidate(decision, alert)
    return replace(value, **changes)


class CompletedTripOutcomeRepositoryContractMixin:
    """Reusable behavior suite for every completed-outcome repository adapter."""

    def make_completed_trip_outcome_repository(self):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.repository = self.make_completed_trip_outcome_repository()
        self.outcome = contract_completed_trip_outcome()

    def test_contract_runtime_protocol(self):
        self.assertIsInstance(self.repository, CompletedTripOutcomeRepository)

    def test_contract_save_and_read_by_lot_trip_id(self):
        self.assertEqual(self.repository.save_outcome(self.outcome), self.outcome)
        self.assertEqual(
            self.repository.get_outcome(self.outcome.lot_trip_id),
            self.outcome,
        )
        self.assertIsNone(self.repository.get_outcome("unknown-lot-trip"))

    def test_contract_identical_save_is_idempotent(self):
        first = self.repository.save_outcome(self.outcome)
        second = self.repository.save_outcome(self.outcome)
        self.assertEqual(first, second)

    def test_contract_conflicting_rewrite_is_rejected(self):
        self.repository.save_outcome(self.outcome)
        with self.assertRaises(CompletedTripOutcomeConflictError):
            self.repository.save_outcome(
                replace(self.outcome, completed_at=self.outcome.completed_at + timedelta(seconds=1))
            )
        self.assertEqual(
            self.repository.get_outcome(self.outcome.lot_trip_id),
            self.outcome,
        )


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

    def test_contract_completion_persists_authoritative_timestamp(self):
        trip = contract_trip()
        assignment = contract_assignment()
        self.repository.register_trip_and_assignment(trip, assignment)
        self.repository.transition_trip_and_assignment(
            trip.trip_id,
            assignment.assignment_id,
            TripStatus.PLANNED,
            TripStatus.ACTIVE,
            False,
            True,
        )
        completed_at = CONTRACT_TIME + timedelta(hours=1)
        completed_trip, completed_assignment = (
            self.repository.transition_trip_and_assignment(
                trip.trip_id,
                assignment.assignment_id,
                TripStatus.ACTIVE,
                TripStatus.COMPLETED,
                True,
                False,
                completed_at,
            )
        )
        self.assertEqual(completed_trip.completed_at, completed_at)
        self.assertFalse(completed_assignment.active)
        self.assertEqual(
            self.repository.get_trip_by_id(trip.trip_id),
            completed_trip,
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
        self.prepare_active_contract_trip(self.repository)
        self.sample = contract_sample()
        self.record = telemetry_record_from_sample(
            "contract-trip", "contract-lot-trip", self.sample
        )
        self.state = contract_state(self.sample)

    def prepare_active_contract_trip(self, repository):
        repository.register_trip_and_assignment(
            contract_trip(status=TripStatus.ACTIVE),
            contract_assignment(active=True),
        )

    def complete_contract_trip(self, repository):
        repository.transition_trip_and_assignment(
            "contract-trip",
            "contract-assignment",
            TripStatus.ACTIVE,
            TripStatus.COMPLETED,
            True,
            False,
            completed_at=CONTRACT_TIME + timedelta(minutes=30),
        )

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

    def test_contract_rejects_commit_after_trip_completion_without_partial_write(self):
        repository = self.repository
        self.complete_contract_trip(repository)
        with self.assertRaises(TripNotActiveAtCommitError):
            self.commit_first()
        self.assertFalse(repository.has_sample("contract-device", "contract-sample-1"))
        self.assertIsNone(repository.get_live_state("contract-lot-trip"))


class ProcessingBundleRepositoryContractMixin:
    """Reusable decision/outbox behavior for atomic processing repositories."""

    def make_processing_bundle_repository(self):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.repository = self.make_processing_bundle_repository()
        self.prepare_active_contract_trip(self.repository)
        self.sample = contract_sample()
        self.record = telemetry_record_from_sample(
            "contract-trip", "contract-lot-trip", self.sample
        )
        self.state = contract_state(self.sample)
        self.decision = contract_decision_record(self.sample, self.state)

    def prepare_active_contract_trip(self, repository):
        repository.register_trip_and_assignment(
            contract_trip(status=TripStatus.ACTIVE),
            contract_assignment(active=True),
        )

    def complete_contract_trip(self, repository):
        repository.transition_trip_and_assignment(
            "contract-trip",
            "contract-assignment",
            TripStatus.ACTIVE,
            TripStatus.COMPLETED,
            True,
            False,
            completed_at=CONTRACT_TIME + timedelta(minutes=30),
        )

    def commit_bundle(self, outbox_event=None):
        self.repository.commit_processing_bundle(
            self.record,
            self.state,
            self.decision,
            outbox_event,
            expected_revision=None,
        )

    def commit_first(self):
        self.repository.commit_sample_and_state(
            self.record, self.state, expected_revision=None
        )

    def test_contract_runtime_protocol(self):
        self.assertIsInstance(self.repository, ProcessingBundleRepository)

    def test_contract_no_alert_bundle_is_atomic(self):
        self.commit_bundle()
        self.assertEqual(
            self.repository.get_telemetry_history("contract-lot-trip"),
            (self.record,),
        )
        self.assertEqual(
            self.repository.get_live_state("contract-lot-trip"), self.state
        )
        self.assertEqual(
            self.repository.get_decision(self.decision.decision_id),
            self.decision,
        )
        self.assertEqual(
            self.repository.list_dispatchable_outbox_events(CONTRACT_TIME), ()
        )

    def test_contract_alert_bundle_stores_exact_candidate(self):
        event = contract_alert_outbox_event(self.decision)
        self.commit_bundle(event)
        stored = self.repository.get_outbox_event(event.event_id)
        self.assertEqual(stored, event)
        self.assertEqual(stored.alert_candidate, event.alert_candidate)

    def test_contract_discovery_is_bounded_and_returns_due_events(self):
        event = contract_alert_outbox_event(self.decision)
        self.commit_bundle(event)
        discovery = self.repository.discover_dispatchable_outbox_events(
            CONTRACT_TIME,
            limit=1,
        )
        self.assertEqual(discovery.events, (event,))
        self.assertEqual(discovery.corrupt_quarantined_count, 0)

    def test_contract_decision_history_preserves_acceptance_order(self):
        self.commit_bundle()
        sample2 = contract_sample(sample_id="contract-sample-2", minutes=5)
        record2 = telemetry_record_from_sample(
            "contract-trip", "contract-lot-trip", sample2
        )
        state2 = contract_state(sample2, self.state)
        decision2 = contract_decision_record(sample2, state2)
        self.repository.commit_processing_bundle(
            record2,
            state2,
            decision2,
            None,
            expected_revision=1,
        )
        self.assertEqual(
            self.repository.get_decision_history("contract-lot-trip"),
            (self.decision, decision2),
        )

    def test_contract_invalid_decision_leaves_bundle_unchanged(self):
        invalid = replace(self.decision, trip_id="wrong-trip")
        with self.assertRaises(DecisionOutboxError):
            self.repository.commit_processing_bundle(
                self.record,
                self.state,
                invalid,
                None,
                expected_revision=None,
            )
        self.assertFalse(
            self.repository.has_sample(self.record.device_id, self.record.sample_id)
        )
        self.assertIsNone(self.repository.get_live_state("contract-lot-trip"))
        self.assertEqual(
            self.repository.get_decision_history("contract-lot-trip"), ()
        )

    def test_contract_completion_fence_rejects_entire_bundle(self):
        self.complete_contract_trip(self.repository)
        event = contract_alert_outbox_event(self.decision)
        with self.assertRaises(TripNotActiveAtCommitError):
            self.commit_bundle(event)
        self.assertFalse(
            self.repository.has_sample("contract-device", "contract-sample-1")
        )
        self.assertIsNone(self.repository.get_live_state("contract-lot-trip"))
        self.assertIsNone(self.repository.get_decision(self.decision.decision_id))
        self.assertIsNone(self.repository.get_outbox_event(event.event_id))

    def test_contract_concurrent_claim_has_one_winner(self):
        event = contract_alert_outbox_event(self.decision)
        self.commit_bundle(event)
        barrier = Barrier(2)

        def claim(worker):
            barrier.wait()
            try:
                self.repository.claim_outbox_event(
                    event.event_id,
                    worker_id=worker,
                    claimed_at=CONTRACT_TIME,
                    lease_duration=timedelta(minutes=5),
                )
                return "claimed"
            except OutboxClaimError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(claim, ("worker-a", "worker-b")))
        self.assertCountEqual(outcomes, ("claimed", "rejected"))
        self.assertEqual(
            self.repository.get_outbox_event(event.event_id).attempt_count, 1
        )

    def test_contract_release_and_retry_increment_attempts_on_claim_only(self):
        event = contract_alert_outbox_event(self.decision)
        self.commit_bundle(event)
        first = self.repository.claim_outbox_event(
            event.event_id,
            worker_id="worker-a",
            claimed_at=CONTRACT_TIME,
            lease_duration=timedelta(minutes=5),
        )
        pending = self.repository.release_outbox_event(
            event.event_id,
            worker_id="worker-a",
            released_at=CONTRACT_TIME + timedelta(minutes=1),
            retry_at=CONTRACT_TIME + timedelta(minutes=2),
            error_code="ALERT_STORE_UNAVAILABLE",
        )
        self.assertEqual(first.attempt_count, 1)
        self.assertEqual(pending.attempt_count, 1)
        self.assertEqual(pending.delivery_status, OutboxDeliveryStatus.PENDING)
        second = self.repository.claim_outbox_event(
            event.event_id,
            worker_id="worker-b",
            claimed_at=CONTRACT_TIME + timedelta(minutes=2),
            lease_duration=timedelta(minutes=5),
        )
        self.assertEqual(second.attempt_count, 2)

    def test_contract_expired_lease_can_be_reclaimed(self):
        event = contract_alert_outbox_event(self.decision)
        self.commit_bundle(event)
        self.repository.claim_outbox_event(
            event.event_id,
            worker_id="worker-a",
            claimed_at=CONTRACT_TIME,
            lease_duration=timedelta(minutes=1),
        )
        as_of = CONTRACT_TIME + timedelta(minutes=1)
        self.assertEqual(
            self.repository.list_dispatchable_outbox_events(as_of)[0].event_id,
            event.event_id,
        )
        reclaimed = self.repository.claim_outbox_event(
            event.event_id,
            worker_id="worker-b",
            claimed_at=as_of,
            lease_duration=timedelta(minutes=1),
        )
        self.assertEqual(reclaimed.lease_owner, "worker-b")
        self.assertEqual(reclaimed.attempt_count, 2)

    def test_contract_delivery_is_terminal(self):
        event = contract_alert_outbox_event(self.decision)
        self.commit_bundle(event)
        self.repository.claim_outbox_event(
            event.event_id,
            worker_id="worker-a",
            claimed_at=CONTRACT_TIME,
            lease_duration=timedelta(minutes=5),
        )
        delivered = self.repository.mark_outbox_delivered(
            event.event_id,
            worker_id="worker-a",
            delivered_at=CONTRACT_TIME + timedelta(minutes=1),
        )
        self.assertEqual(delivered.delivery_status, OutboxDeliveryStatus.DELIVERED)
        self.assertEqual(delivered.attempt_count, 1)
        self.assertEqual(
            self.repository.list_dispatchable_outbox_events(
                CONTRACT_TIME + timedelta(hours=1)
            ),
            (),
        )
        with self.assertRaises(OutboxClaimError):
            self.repository.claim_outbox_event(
                event.event_id,
                worker_id="worker-b",
                claimed_at=CONTRACT_TIME + timedelta(hours=1),
                lease_duration=timedelta(minutes=1),
            )
        self.assertEqual(
            self.repository.get_live_state("contract-lot-trip"), self.state
        )

    def test_contract_dead_letter_is_terminal_and_preserves_candidate(self):
        event = contract_alert_outbox_event(self.decision)
        self.commit_bundle(event)
        claimed = self.repository.claim_outbox_event(
            event.event_id,
            worker_id="worker-a",
            claimed_at=CONTRACT_TIME,
            lease_duration=timedelta(minutes=5),
        )
        dead = self.repository.mark_outbox_dead_letter(
            event.event_id,
            worker_id="worker-a",
            failed_at=CONTRACT_TIME + timedelta(minutes=1),
            error_code="ALERT_CREATION_CONFLICT",
        )
        self.assertEqual(dead.delivery_status, OutboxDeliveryStatus.DEAD_LETTER)
        self.assertEqual(dead.alert_candidate, event.alert_candidate)
        self.assertEqual(dead.attempt_count, claimed.attempt_count)
        self.assertEqual(dead.dead_lettered_by, "worker-a")
        self.assertEqual(
            self.repository.discover_dispatchable_outbox_events(
                CONTRACT_TIME + timedelta(hours=1),
                limit=10,
            ).events,
            (),
        )
        with self.assertRaises(OutboxClaimError):
            self.repository.claim_outbox_event(
                event.event_id,
                worker_id="worker-b",
                claimed_at=CONTRACT_TIME + timedelta(hours=1),
                lease_duration=timedelta(minutes=1),
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

    def test_contract_original_candidate_replay_preserves_evolved_lifecycle(self):
        cases = ("acknowledged", "actioned", "resolved")
        for index, lifecycle in enumerate(cases):
            with self.subTest(lifecycle=lifecycle):
                candidate = replace(
                    self.alert,
                    alert_id=f"replay-{index}",
                    sample_id=f"replay-sample-{index}",
                )
                self.repository.save_alert(candidate)
                timestamp = CONTRACT_TIME + timedelta(minutes=1)
                if lifecycle == "acknowledged":
                    evolved = self.repository.acknowledge_alert(
                        candidate.alert_id,
                        actor_id="contract-driver",
                        acknowledged_at=timestamp,
                    )
                elif lifecycle == "actioned":
                    evolved = self.repository.record_action(
                        candidate.alert_id,
                        description="Inspected cooling unit",
                        actor_id="contract-driver",
                        recorded_at=timestamp,
                    )
                else:
                    evolved = self.repository.resolve_alert(
                        candidate.alert_id,
                        actor_id="contract-organization",
                        resolved_at=timestamp,
                        resolution_note="Disposition recorded",
                    )
                self.assertEqual(self.repository.save_alert(candidate), evolved)
                self.assertEqual(
                    self.repository.get_alert(candidate.alert_id),
                    evolved,
                )
                with self.assertRaises(AlertConflictError):
                    self.repository.save_alert(
                        replace(candidate, message="Conflicting creation content")
                    )

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

    def test_contract_action_retry_is_idempotent(self):
        self.repository.save_alert(self.alert)
        timestamp = CONTRACT_TIME + timedelta(minutes=1)
        first = self.repository.record_action(
            self.alert.alert_id,
            description="Inspected cooling unit",
            actor_id="contract-driver",
            recorded_at=timestamp,
        )
        retried = self.repository.record_action(
            self.alert.alert_id,
            description="Inspected cooling unit",
            actor_id="contract-driver",
            recorded_at=timestamp,
        )
        self.assertEqual(retried, first)
        self.assertEqual(len(retried.actions), 1)

    def test_contract_concurrent_distinct_actions_are_both_preserved(self):
        self.repository.save_alert(self.alert)
        barrier = Barrier(2)
        timestamp = CONTRACT_TIME + timedelta(minutes=1)

        def record(description):
            barrier.wait()
            return self.repository.record_action(
                self.alert.alert_id,
                description=description,
                actor_id="contract-driver",
                recorded_at=timestamp,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = tuple(
                executor.submit(record, description)
                for description in ("Checked compressor", "Checked door seal")
            )
            for future in futures:
                future.result()

        stored = self.repository.get_alert(self.alert.alert_id)
        self.assertCountEqual(
            (action.description for action in stored.actions),
            ("Checked compressor", "Checked door seal"),
        )

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
