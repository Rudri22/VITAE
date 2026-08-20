import inspect
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
from threading import Barrier
from types import SimpleNamespace

try:
    from .decision_outbox import (
        DETERMINISTIC_ALERT_POLICY_VERSION,
        DETERMINISTIC_ENGINE_VERSION,
        DecisionOutboxError,
        InMemoryProcessingBundleRepository,
        alert_outbox_event_from_candidate,
        decision_id_for,
        decision_record_from_processing_result,
        lot_trip_id_from_decision_id,
        outbox_event_id_for,
    )
    from .repository_contract_suite import (
        contract_alert,
        contract_alert_outbox_event,
        contract_decision_record,
        contract_sample,
        contract_state,
    )
    from .risk_rules import ApplicationStatus, StatusDecision
    from .state_repository import telemetry_record_from_sample
    from .state_repository import (
        DuplicateTelemetrySampleError,
        InMemoryTelemetryStateRepository,
    )
except ImportError:
    from decision_outbox import (
        DETERMINISTIC_ALERT_POLICY_VERSION,
        DETERMINISTIC_ENGINE_VERSION,
        DecisionOutboxError,
        InMemoryProcessingBundleRepository,
        alert_outbox_event_from_candidate,
        decision_id_for,
        decision_record_from_processing_result,
        lot_trip_id_from_decision_id,
        outbox_event_id_for,
    )
    from repository_contract_suite import (
        contract_alert,
        contract_alert_outbox_event,
        contract_decision_record,
        contract_sample,
        contract_state,
    )
    from risk_rules import ApplicationStatus, StatusDecision
    from state_repository import telemetry_record_from_sample
    from state_repository import (
        DuplicateTelemetrySampleError,
        InMemoryTelemetryStateRepository,
    )


class DecisionOutboxModelTests(unittest.TestCase):
    def test_identifiers_are_deterministic_and_domain_scoped(self):
        decision = decision_id_for(
            "contract-lot-trip", "contract-device", "contract-sample-1"
        )
        self.assertEqual(
            decision,
            decision_id_for(
                "contract-lot-trip", "contract-device", "contract-sample-1"
            ),
        )
        self.assertNotEqual(
            decision,
            decision_id_for(
                "other-lot-trip", "contract-device", "contract-sample-1"
            ),
        )
        self.assertEqual(
            lot_trip_id_from_decision_id(decision),
            "contract-lot-trip",
        )
        event = outbox_event_id_for(decision, "contract-alert")
        self.assertEqual(event, outbox_event_id_for(decision, "contract-alert"))

    def test_models_are_immutable(self):
        decision = contract_decision_record()
        event = contract_alert_outbox_event(decision)
        with self.assertRaises(FrozenInstanceError):
            decision.reason_code = "CHANGED"
        with self.assertRaises(FrozenInstanceError):
            event.attempt_count = 99

    def test_outbox_domain_model_has_no_persistence_record_version(self):
        self.assertNotIn(
            "record_version",
            {field.name for field in fields(contract_alert_outbox_event())},
        )

    def test_processing_result_factory_preserves_authoritative_decision(self):
        sample = contract_sample()
        record = telemetry_record_from_sample(
            "contract-trip", "contract-lot-trip", sample
        )
        state = contract_state(sample)
        decision = StatusDecision(
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
        result = SimpleNamespace(
            previous_live_state=None,
            telemetry_record=record,
            decision=decision,
            live_state=state,
        )
        stored = decision_record_from_processing_result(result)
        self.assertEqual(stored.engine_version, DETERMINISTIC_ENGINE_VERSION)
        self.assertEqual(stored.status, decision.status)
        self.assertEqual(stored.reason_code, decision.reason_code)
        self.assertEqual(stored.resulting_live_state_revision, 1)
        self.assertIsNone(stored.previous_live_state_revision)

    def test_outbox_factory_embeds_exact_alert_candidate(self):
        decision = contract_decision_record()
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
        event = alert_outbox_event_from_candidate(decision, alert)
        self.assertIs(event.alert_candidate, alert)
        self.assertEqual(
            event.alert_policy_version, DETERMINISTIC_ALERT_POLICY_VERSION
        )

    def test_outbox_identity_mismatch_rejects_entire_bundle(self):
        repository = InMemoryProcessingBundleRepository()
        sample = contract_sample()
        record = telemetry_record_from_sample(
            "contract-trip", "contract-lot-trip", sample
        )
        state = contract_state(sample)
        decision = contract_decision_record(sample, state)
        event = replace(
            contract_alert_outbox_event(decision),
            sample_id="different-sample",
        )
        with self.assertRaises(DecisionOutboxError):
            repository.commit_processing_bundle(
                record, state, decision, event, expected_revision=None
            )
        self.assertFalse(repository.has_sample(record.device_id, record.sample_id))
        self.assertIsNone(repository.get_decision(decision.decision_id))

    def test_module_does_not_evaluate_status_or_alert_policy(self):
        try:
            from . import decision_outbox
        except ImportError:
            import decision_outbox
        source = inspect.getsource(decision_outbox)
        self.assertNotIn("evaluate_status", source)
        self.assertNotIn("evaluate_alert_policy", source)


class InMemoryProcessingBundleStorageBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryProcessingBundleRepository()
        self.sample = contract_sample()
        self.record = telemetry_record_from_sample(
            "contract-trip", "contract-lot-trip", self.sample
        )
        self.state = contract_state(self.sample)
        self.decision = contract_decision_record(self.sample, self.state)

    def test_bundle_repository_extends_one_authoritative_state_store(self):
        self.assertTrue(
            issubclass(
                InMemoryProcessingBundleRepository,
                InMemoryTelemetryStateRepository,
            )
        )
        self.assertNotIn("_telemetry_repository", vars(self.repository))
        self.assertNotIn("_state_repository", vars(self.repository))
        for inherited_store in (
            "_history",
            "_live_states",
            "_sample_identities",
            "_lock",
        ):
            self.assertIn(inherited_store, vars(self.repository))

    def test_legacy_commit_then_bundle_commit_share_history_and_state(self):
        self.repository.commit_sample_and_state(self.record, self.state, None)
        sample2 = contract_sample(sample_id="shared-sample-2", minutes=5)
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
            self.repository.get_telemetry_history("contract-lot-trip"),
            (self.record, record2),
        )
        self.assertEqual(
            self.repository.get_live_state("contract-lot-trip"), state2
        )
        self.assertEqual(
            self.repository.get_decision_history("contract-lot-trip"),
            (decision2,),
        )

    def test_bundle_commit_then_legacy_commit_share_history_and_state(self):
        self.repository.commit_processing_bundle(
            self.record,
            self.state,
            self.decision,
            None,
            expected_revision=None,
        )
        sample2 = contract_sample(sample_id="shared-sample-2", minutes=5)
        record2 = telemetry_record_from_sample(
            "contract-trip", "contract-lot-trip", sample2
        )
        state2 = contract_state(sample2, self.state)
        self.repository.commit_sample_and_state(record2, state2, 1)
        self.assertEqual(
            self.repository.get_telemetry_history("contract-lot-trip"),
            (self.record, record2),
        )
        self.assertEqual(
            self.repository.get_live_state("contract-lot-trip"), state2
        )
        self.assertEqual(
            self.repository.get_decision_history("contract-lot-trip"),
            (self.decision,),
        )

    def test_legacy_and_bundle_race_share_sample_guard_and_lock(self):
        barrier = Barrier(2)

        def legacy_commit():
            barrier.wait()
            try:
                self.repository.commit_sample_and_state(
                    self.record, self.state, None
                )
                return "legacy"
            except DuplicateTelemetrySampleError:
                return "duplicate"

        def bundle_commit():
            barrier.wait()
            try:
                self.repository.commit_processing_bundle(
                    self.record,
                    self.state,
                    self.decision,
                    None,
                    expected_revision=None,
                )
                return "bundle"
            except DuplicateTelemetrySampleError:
                return "duplicate"

        with ThreadPoolExecutor(max_workers=2) as executor:
            legacy_future = executor.submit(legacy_commit)
            bundle_future = executor.submit(bundle_commit)
            results = (legacy_future.result(), bundle_future.result())
        self.assertIn("duplicate", results)
        self.assertEqual(results.count("duplicate"), 1)
        self.assertEqual(
            self.repository.get_telemetry_history("contract-lot-trip"),
            (self.record,),
        )
        self.assertEqual(
            self.repository.get_live_state("contract-lot-trip"), self.state
        )


if __name__ == "__main__":
    unittest.main()
