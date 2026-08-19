import inspect
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta

try:
    from . import alerting
    from .alerting import (
        AlertConflictError,
        AlertNotFoundError,
        AlertPolicyError,
        AlertSeverity,
        AlertStatus,
        AlertTransitionError,
        AlertType,
        InMemoryAlertRepository,
        evaluate_alert_policy,
    )
    from .risk_rules import ApplicationStatus
    from .simulator import (
        SIMULATION_LOT_TRIP_ID,
        STATUS_LADDER_SCENARIO,
        build_local_environment,
        run_scenario,
    )
except ImportError:
    import alerting
    from alerting import (
        AlertConflictError,
        AlertNotFoundError,
        AlertPolicyError,
        AlertSeverity,
        AlertStatus,
        AlertTransitionError,
        AlertType,
        InMemoryAlertRepository,
        evaluate_alert_policy,
    )
    from risk_rules import ApplicationStatus
    from simulator import (
        SIMULATION_LOT_TRIP_ID,
        STATUS_LADDER_SCENARIO,
        build_local_environment,
        run_scenario,
    )


def ladder_results():
    environment = build_local_environment()
    steps = run_scenario(
        environment.processor,
        STATUS_LADDER_SCENARIO,
        device_id=environment.device_id,
        start_time=environment.start_time,
    )
    return tuple(step.result for step in steps)


def result_with_status(source, status, *, sequence):
    timestamp = source.telemetry_record.timestamp + timedelta(minutes=sequence)
    sample_id = f"transition-{sequence}-{status.value.lower()}"
    decision = replace(
        source.decision,
        status=status,
        reason_code=f"TEST_{status.value}",
        active_rule_id=(
            None
            if status in (ApplicationStatus.SAFE, ApplicationStatus.DATA_ERROR)
            else "test-rule"
        ),
    )
    record = replace(
        source.telemetry_record,
        sample_id=sample_id,
        timestamp=timestamp,
    )
    state = replace(
        source.live_state,
        status=status,
        reason_code=decision.reason_code,
        active_rule_id=decision.active_rule_id,
        last_sample_id=sample_id,
        last_sample_timestamp=timestamp,
        last_updated=timestamp,
        revision=sequence + 1,
    )
    return replace(source, telemetry_record=record, decision=decision, live_state=state)


class AlertPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = ladder_results()

    def result_for(self, status):
        return next(result for result in self.results if result.decision.status == status)

    def test_safe_creates_no_alert(self):
        self.assertIsNone(
            evaluate_alert_policy(None, self.result_for(ApplicationStatus.SAFE))
        )

    def test_monitor_creates_info_excursion_alert(self):
        alert = evaluate_alert_policy(None, self.result_for(ApplicationStatus.MONITOR))
        self.assertEqual(alert.alert_type, AlertType.EXCURSION_MONITOR)
        self.assertEqual(alert.severity, AlertSeverity.INFO)

    def test_at_risk_creates_warning_excursion_alert(self):
        alert = evaluate_alert_policy(None, self.result_for(ApplicationStatus.AT_RISK))
        self.assertEqual(alert.alert_type, AlertType.EXCURSION_AT_RISK)
        self.assertEqual(alert.severity, AlertSeverity.WARNING)

    def test_critical_creates_critical_temperature_alert(self):
        alert = evaluate_alert_policy(None, self.result_for(ApplicationStatus.CRITICAL))
        self.assertEqual(alert.alert_type, AlertType.TEMPERATURE_CRITICAL)
        self.assertEqual(alert.severity, AlertSeverity.CRITICAL)

    def test_rule_violation_creates_critical_rule_alert(self):
        alert = evaluate_alert_policy(
            None,
            self.result_for(ApplicationStatus.RULE_VIOLATION)
        )
        self.assertEqual(alert.alert_type, AlertType.PRODUCT_RULE_VIOLATION)
        self.assertEqual(alert.severity, AlertSeverity.CRITICAL)

    def test_data_error_creates_warning_data_alert(self):
        source = self.result_for(ApplicationStatus.MONITOR)
        decision = replace(
            source.decision,
            status=ApplicationStatus.DATA_ERROR,
            reason_code="INVALID_PREVIOUS_STATE",
            active_rule_id=None,
        )
        state = replace(
            source.live_state,
            status=decision.status,
            reason_code=decision.reason_code,
            active_rule_id=None,
        )
        alert = evaluate_alert_policy(
            None,
            replace(source, decision=decision, live_state=state)
        )
        self.assertEqual(alert.alert_type, AlertType.TELEMETRY_DATA_ERROR)
        self.assertEqual(alert.severity, AlertSeverity.WARNING)

    def test_alert_copies_only_authoritative_result_identity_and_decision(self):
        result = self.result_for(ApplicationStatus.CRITICAL)
        alert = evaluate_alert_policy(None, result)
        self.assertEqual(alert.trip_id, result.telemetry_record.trip_id)
        self.assertEqual(alert.lot_trip_id, result.telemetry_record.lot_trip_id)
        self.assertEqual(alert.device_id, result.telemetry_record.device_id)
        self.assertEqual(alert.sample_id, result.telemetry_record.sample_id)
        self.assertEqual(alert.source_status, result.decision.status)
        self.assertEqual(alert.reason_code, result.decision.reason_code)
        self.assertEqual(alert.active_rule_id, result.decision.active_rule_id)
        self.assertEqual(alert.detected_at, result.telemetry_record.timestamp)

    def test_alert_id_is_deterministic_for_same_result(self):
        result = self.result_for(ApplicationStatus.CRITICAL)
        first = evaluate_alert_policy(None, result)
        second = evaluate_alert_policy(None, result)
        self.assertEqual(first.alert_id, second.alert_id)
        self.assertEqual(first, second)

    def test_different_samples_have_different_alert_ids(self):
        monitor = evaluate_alert_policy(
            None, self.result_for(ApplicationStatus.MONITOR)
        )
        critical = evaluate_alert_policy(
            None, self.result_for(ApplicationStatus.CRITICAL)
        )
        self.assertNotEqual(monitor.alert_id, critical.alert_id)

    def test_inconsistent_decision_and_state_is_rejected(self):
        result = self.result_for(ApplicationStatus.CRITICAL)
        with self.assertRaises(AlertPolicyError):
            evaluate_alert_policy(
                None,
                replace(
                    result,
                    live_state=replace(result.live_state, status=ApplicationStatus.SAFE),
                )
            )

    def test_inconsistent_record_and_state_is_rejected(self):
        result = self.result_for(ApplicationStatus.CRITICAL)
        with self.assertRaises(AlertPolicyError):
            evaluate_alert_policy(
                None,
                replace(
                    result,
                    telemetry_record=replace(
                        result.telemetry_record, sample_id="different-sample"
                    ),
                )
            )

    def test_status_transition_table(self):
        source = self.result_for(ApplicationStatus.CRITICAL)
        cases = (
            (ApplicationStatus.SAFE, ApplicationStatus.MONITOR, True),
            (ApplicationStatus.MONITOR, ApplicationStatus.MONITOR, False),
            (ApplicationStatus.MONITOR, ApplicationStatus.AT_RISK, True),
            (ApplicationStatus.AT_RISK, ApplicationStatus.AT_RISK, False),
            (ApplicationStatus.AT_RISK, ApplicationStatus.CRITICAL, True),
            (ApplicationStatus.CRITICAL, ApplicationStatus.CRITICAL, False),
            (ApplicationStatus.CRITICAL, ApplicationStatus.RULE_VIOLATION, True),
            (
                ApplicationStatus.RULE_VIOLATION,
                ApplicationStatus.RULE_VIOLATION,
                False,
            ),
            (ApplicationStatus.MONITOR, ApplicationStatus.SAFE, False),
            (ApplicationStatus.CRITICAL, ApplicationStatus.SAFE, False),
            (ApplicationStatus.SAFE, ApplicationStatus.DATA_ERROR, True),
            (ApplicationStatus.DATA_ERROR, ApplicationStatus.DATA_ERROR, False),
            (ApplicationStatus.DATA_ERROR, ApplicationStatus.SAFE, False),
        )
        for previous_status, current_status, expected_alert in cases:
            with self.subTest(
                previous=previous_status.value,
                current=current_status.value,
            ):
                previous = result_with_status(
                    source, previous_status, sequence=10
                ).live_state
                current = result_with_status(source, current_status, sequence=11)
                alert = evaluate_alert_policy(previous, current)
                self.assertEqual(alert is not None, expected_alert)

    def test_previous_state_identity_mismatches_fail(self):
        current = self.result_for(ApplicationStatus.CRITICAL)
        changes = {
            "trip_id": "different-trip",
            "lot_trip_id": "different-lot-trip",
            "device_id": "different-device",
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                previous = replace(current.live_state, **{field: value})
                with self.assertRaises(AlertPolicyError):
                    evaluate_alert_policy(previous, current)

    def test_return_to_safe_does_not_resolve_existing_open_alert(self):
        source = self.result_for(ApplicationStatus.CRITICAL)
        previous_result = result_with_status(
            source, ApplicationStatus.CRITICAL, sequence=20
        )
        alert = evaluate_alert_policy(None, previous_result)
        repository = InMemoryAlertRepository()
        repository.save_alert(alert)
        current = result_with_status(source, ApplicationStatus.SAFE, sequence=21)

        self.assertIsNone(
            evaluate_alert_policy(previous_result.live_state, current)
        )
        self.assertEqual(
            repository.get_alert(alert.alert_id).status,
            AlertStatus.OPEN,
        )

    def test_alert_policy_contains_no_rule_threshold_calculation(self):
        source = inspect.getsource(alerting)
        self.assertNotIn("min_temperature", source)
        self.assertNotIn("max_temperature", source)
        self.assertNotIn("maximum_duration_minutes", source)

    def test_alerting_does_not_call_status_engine(self):
        self.assertNotIn("evaluate_status", inspect.getsource(alerting))


class AlertRepositoryTests(unittest.TestCase):
    def setUp(self):
        result = next(
            result
            for result in ladder_results()
            if result.decision.status == ApplicationStatus.CRITICAL
        )
        self.alert = evaluate_alert_policy(None, result)
        self.repository = InMemoryAlertRepository()
        self.base_time = self.alert.detected_at

    def save(self):
        return self.repository.save_alert(self.alert)

    def test_alert_is_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            self.alert.status = AlertStatus.RESOLVED

    def test_save_and_get_alert(self):
        saved = self.save()
        self.assertIs(self.repository.get_alert(self.alert.alert_id), saved)

    def test_saving_identical_alert_is_idempotent(self):
        first = self.save()
        second = self.repository.save_alert(self.alert)
        self.assertIs(first, second)
        self.assertEqual(len(self.repository.list_alerts()), 1)

    def test_conflicting_alert_id_is_rejected(self):
        self.save()
        with self.assertRaises(AlertConflictError):
            self.repository.save_alert(replace(self.alert, message="Different"))

    def test_list_alerts_filters_by_lot_trip_and_status(self):
        self.save()
        self.assertEqual(
            self.repository.list_alerts(lot_trip_id=SIMULATION_LOT_TRIP_ID),
            (self.alert,),
        )
        self.assertEqual(
            self.repository.list_alerts(status=AlertStatus.RESOLVED), ()
        )

    def test_acknowledgement_records_actor_and_time(self):
        self.save()
        timestamp = self.base_time + timedelta(minutes=1)
        updated = self.repository.acknowledge_alert(
            self.alert.alert_id,
            actor_id="operator-1",
            acknowledged_at=timestamp,
        )
        self.assertEqual(updated.status, AlertStatus.ACKNOWLEDGED)
        self.assertEqual(updated.acknowledged_by, "operator-1")
        self.assertEqual(updated.acknowledged_at, timestamp)
        self.assertEqual(updated.updated_at, timestamp)

    def test_unknown_alert_acknowledgement_fails(self):
        with self.assertRaises(AlertNotFoundError):
            self.repository.acknowledge_alert(
                "missing-alert",
                actor_id="operator-1",
                acknowledged_at=self.base_time,
            )

    def test_acknowledgement_requires_actor_and_aware_time(self):
        self.save()
        with self.assertRaises(AlertTransitionError):
            self.repository.acknowledge_alert(
                self.alert.alert_id,
                actor_id="",
                acknowledged_at=self.base_time,
            )
        with self.assertRaises(AlertTransitionError):
            self.repository.acknowledge_alert(
                self.alert.alert_id,
                actor_id="operator-1",
                acknowledged_at=self.base_time.replace(tzinfo=None),
            )

    def test_acknowledgement_cannot_predate_detection(self):
        self.save()
        with self.assertRaises(AlertTransitionError):
            self.repository.acknowledge_alert(
                self.alert.alert_id,
                actor_id="operator-1",
                acknowledged_at=self.base_time - timedelta(seconds=1),
            )

    def test_action_records_actor_time_and_description(self):
        self.save()
        timestamp = self.base_time + timedelta(minutes=2)
        updated = self.repository.record_action(
            self.alert.alert_id,
            description="Moved lot to backup refrigeration",
            actor_id="operator-2",
            recorded_at=timestamp,
        )
        action = updated.actions[0]
        self.assertEqual(action.description, "Moved lot to backup refrigeration")
        self.assertEqual(action.actor_id, "operator-2")
        self.assertEqual(action.recorded_at, timestamp)
        self.assertTrue(action.action_id.startswith("action-"))

    def test_resolution_records_actor_time_and_note(self):
        self.save()
        timestamp = self.base_time + timedelta(minutes=3)
        updated = self.repository.resolve_alert(
            self.alert.alert_id,
            actor_id="quality-manager",
            resolved_at=timestamp,
            resolution_note="Lot quarantined for review",
        )
        self.assertEqual(updated.status, AlertStatus.RESOLVED)
        self.assertEqual(updated.resolved_by, "quality-manager")
        self.assertEqual(updated.resolved_at, timestamp)
        self.assertEqual(updated.resolution_note, "Lot quarantined for review")

    def test_unknown_action_and_resolution_fail(self):
        with self.assertRaises(AlertNotFoundError):
            self.repository.record_action(
                "missing-alert",
                description="Inspect",
                actor_id="operator-1",
                recorded_at=self.base_time,
            )
        with self.assertRaises(AlertNotFoundError):
            self.repository.resolve_alert(
                "missing-alert",
                actor_id="operator-1",
                resolved_at=self.base_time,
                resolution_note="Done",
            )

    def test_resolved_alert_rejects_further_lifecycle_changes(self):
        self.save()
        resolved = self.repository.resolve_alert(
            self.alert.alert_id,
            actor_id="operator-1",
            resolved_at=self.base_time + timedelta(minutes=1),
            resolution_note="Resolved",
        )
        with self.assertRaises(AlertTransitionError):
            self.repository.acknowledge_alert(
                resolved.alert_id,
                actor_id="operator-2",
                acknowledged_at=self.base_time + timedelta(minutes=2),
            )
        with self.assertRaises(AlertTransitionError):
            self.repository.record_action(
                resolved.alert_id,
                description="Late action",
                actor_id="operator-2",
                recorded_at=self.base_time + timedelta(minutes=2),
            )


if __name__ == "__main__":
    unittest.main()
