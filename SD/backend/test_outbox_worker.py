import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

try:
    from . import outbox_worker
    from .outbox_dispatcher import (
        DispatchBatchResult,
        DispatchEventOutcome,
        DispatchEventResult,
    )
    from .outbox_worker import (
        OutboxWorkerConfig,
        OutboxWorkerConfigurationError,
        OutboxWorkerInvocationError,
        OutboxWorkerRuntime,
        build_worker_runtime,
        lambda_handler,
    )
except ImportError:
    import outbox_worker
    from outbox_dispatcher import (
        DispatchBatchResult,
        DispatchEventOutcome,
        DispatchEventResult,
    )
    from outbox_worker import (
        OutboxWorkerConfig,
        OutboxWorkerConfigurationError,
        OutboxWorkerInvocationError,
        OutboxWorkerRuntime,
        build_worker_runtime,
        lambda_handler,
    )


CONTRACT_TIME = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


class FakeLambdaContext:
    def __init__(self, request_id="request-123", remaining_time_ms=60_000):
        self.aws_request_id = request_id
        self._remaining_time_ms = remaining_time_ms

    def get_remaining_time_in_millis(self):
        return self._remaining_time_ms


def worker_environment(**overrides):
    values = {
        "VITAE_AWS_REGION": "us-east-1",
        "VITAE_TELEMETRY_TABLE": "telemetry-dev",
        "VITAE_ALERT_TABLE": "alerts-dev",
    }
    values.update(overrides)
    return values


def batch_result(*, worker_id="lambda:request-123", system_failures=0):
    event_results = ()
    if system_failures:
        event_results = (
            DispatchEventResult(
                event_id="event-1",
                outcome=DispatchEventOutcome.SYSTEM_FAILURE,
                attempt_count=1,
                error_code="DISPATCH_SYSTEM_FAILURE",
            ),
        )
    return DispatchBatchResult(
        worker_id=worker_id,
        started_at=CONTRACT_TIME,
        finished_at=CONTRACT_TIME,
        discovered_count=len(event_results),
        corrupt_quarantined_count=0,
        claimed_count=0,
        delivered_count=0,
        already_delivered_count=0,
        claim_conflict_count=0,
        retry_scheduled_count=0,
        dead_lettered_count=0,
        max_attempts_exceeded_count=0,
        system_failure_count=system_failures,
        event_results=event_results,
    )


class OutboxWorkerConfigTests(unittest.TestCase):
    def test_required_settings_and_defaults(self):
        config = OutboxWorkerConfig.from_environment(worker_environment())
        self.assertEqual(config.aws_region, "us-east-1")
        self.assertEqual(config.telemetry_table, "telemetry-dev")
        self.assertEqual(config.alert_table, "alerts-dev")
        self.assertEqual(config.batch_size, 25)
        self.assertEqual(config.lease_seconds, 120)
        self.assertEqual(config.max_attempts, 96)

    def test_worker_does_not_require_identity_or_profile_configuration(self):
        config = OutboxWorkerConfig.from_environment(worker_environment())
        self.assertFalse(hasattr(config, "identity_table"))
        self.assertFalse(hasattr(config, "aws_profile"))
        self.assertFalse(hasattr(config, "dynamodb_endpoint_url"))
        self.assertFalse(hasattr(config, "minimum_remaining_time_ms"))

    def test_missing_required_setting_fails_closed(self):
        for missing in (
            "VITAE_AWS_REGION",
            "VITAE_TELEMETRY_TABLE",
            "VITAE_ALERT_TABLE",
        ):
            values = worker_environment()
            del values[missing]
            with self.subTest(missing=missing):
                with self.assertRaisesRegex(
                    OutboxWorkerConfigurationError,
                    f"{missing} is required",
                ):
                    OutboxWorkerConfig.from_environment(values)

    def test_numeric_settings_are_bounded(self):
        cases = {
            "VITAE_OUTBOX_BATCH_SIZE": ("0", "101", "not-an-int"),
            "VITAE_OUTBOX_LEASE_SECONDS": ("0", "901"),
            "VITAE_OUTBOX_MAX_ATTEMPTS": ("0", "10001"),
        }
        for name, values in cases.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(OutboxWorkerConfigurationError):
                        OutboxWorkerConfig.from_environment(
                            worker_environment(**{name: value})
                        )

    def test_max_retry_delay_cannot_be_less_than_base_delay(self):
        with self.assertRaisesRegex(
            OutboxWorkerConfigurationError,
            "max_delay_seconds cannot be less",
        ):
            OutboxWorkerConfig.from_environment(
                worker_environment(
                    VITAE_OUTBOX_BASE_DELAY_SECONDS="10",
                    VITAE_OUTBOX_MAX_DELAY_SECONDS="9",
                )
            )


class OutboxWorkerRuntimeTests(unittest.TestCase):
    def setUp(self):
        outbox_worker._reset_runtime_cache_for_tests()

    def tearDown(self):
        outbox_worker._reset_runtime_cache_for_tests()

    def test_runtime_composes_only_telemetry_and_alert_repositories(self):
        client = Mock()
        config = OutboxWorkerConfig.from_environment(worker_environment())
        runtime = build_worker_runtime(config, dynamodb_client=client)
        self.assertEqual(
            client.describe_table.call_args_list,
            [
                unittest.mock.call(TableName="telemetry-dev"),
                unittest.mock.call(TableName="alerts-dev"),
            ],
        )
        self.assertEqual(runtime.telemetry_repository.table_name, "telemetry-dev")
        self.assertEqual(runtime.alert_repository.table_name, "alerts-dev")
        self.assertFalse(hasattr(runtime, "identity_repository"))

    def test_missing_table_fails_without_memory_fallback(self):
        client = Mock()
        client.describe_table.side_effect = RuntimeError("missing")
        with self.assertRaisesRegex(
            OutboxWorkerConfigurationError,
            "telemetry table is unavailable",
        ):
            build_worker_runtime(
                OutboxWorkerConfig.from_environment(worker_environment()),
                dynamodb_client=client,
            )

    def test_warm_runtime_is_created_lazily_and_reused(self):
        config = OutboxWorkerConfig.from_environment(worker_environment())
        runtime = Mock(config=config)
        with patch.object(
            outbox_worker,
            "build_worker_runtime",
            return_value=runtime,
        ) as build:
            self.assertIs(outbox_worker._get_runtime(config), runtime)
            self.assertIs(outbox_worker._get_runtime(config), runtime)
        build.assert_called_once_with(config)

    def test_configuration_change_replaces_warm_runtime(self):
        first_config = OutboxWorkerConfig.from_environment(worker_environment())
        second_config = OutboxWorkerConfig.from_environment(
            worker_environment(VITAE_DYNAMODB_KEY_NAMESPACE="other")
        )
        runtimes = (Mock(config=first_config), Mock(config=second_config))
        with patch.object(
            outbox_worker,
            "build_worker_runtime",
            side_effect=runtimes,
        ) as build:
            self.assertIs(outbox_worker._get_runtime(first_config), runtimes[0])
            self.assertIs(outbox_worker._get_runtime(second_config), runtimes[1])
        self.assertEqual(build.call_count, 2)


class OutboxLambdaHandlerTests(unittest.TestCase):
    def setUp(self):
        self.config = OutboxWorkerConfig.from_environment(worker_environment())
        self.runtime = OutboxWorkerRuntime(self.config, Mock(), Mock())

    def invoke(self, result=None, *, event=None, context=None):
        dispatcher = Mock()
        dispatcher.run_once.return_value = result or batch_result()
        with patch.object(
            outbox_worker.OutboxWorkerConfig,
            "from_environment",
            return_value=self.config,
        ), patch.object(
            outbox_worker,
            "_get_runtime",
            return_value=self.runtime,
        ) as runtime, patch.object(
            outbox_worker,
            "OutboxDispatcher",
            return_value=dispatcher,
        ) as dispatcher_type, patch("builtins.print"):
            response = lambda_handler(
                event or {"schemaVersion": 1, "trigger": "test"},
                context or FakeLambdaContext(),
            )
        return response, runtime, dispatcher_type, dispatcher

    def test_handler_runs_one_bounded_dispatch_and_returns_json(self):
        response, runtime, dispatcher_type, dispatcher = self.invoke()
        runtime.assert_called_once_with(self.config)
        dispatcher_type.assert_called_once_with(
            self.runtime.telemetry_repository,
            self.runtime.alert_repository,
            worker_id="lambda:request-123",
            lease_duration=self.config.lease_duration,
            retry_policy=self.config.retry_policy,
        )
        dispatcher.run_once.assert_called_once_with(batch_size=25)
        self.assertEqual(response["schemaVersion"], 1)
        self.assertEqual(response["requestId"], "request-123")
        self.assertEqual(response["systemFailureCount"], 0)
        json.dumps(response)

    def test_event_cannot_override_operational_configuration(self):
        _, _, _, dispatcher = self.invoke(
            event={
                "batchSize": 100,
                "telemetryTable": "attacker-table",
                "maxAttempts": 1,
            }
        )
        dispatcher.run_once.assert_called_once_with(batch_size=25)

    def test_handler_raises_when_batch_contains_system_failure(self):
        with self.assertRaises(OutboxWorkerInvocationError) as raised:
            self.invoke(batch_result(system_failures=1))
        self.assertEqual(raised.exception.result["systemFailureCount"], 1)

    def test_low_remaining_time_fails_before_repository_composition(self):
        with patch.object(
            outbox_worker.OutboxWorkerConfig,
            "from_environment",
            return_value=self.config,
        ), patch.object(outbox_worker, "_get_runtime") as runtime:
            with self.assertRaisesRegex(
                OutboxWorkerInvocationError,
                "Insufficient Lambda execution time",
            ):
                lambda_handler({}, FakeLambdaContext(remaining_time_ms=9_999))
        runtime.assert_not_called()

    def test_invalid_event_or_context_fails_clearly(self):
        with self.assertRaisesRegex(
            OutboxWorkerInvocationError,
            "event must be a mapping",
        ):
            lambda_handler([], FakeLambdaContext())
        with patch.object(
            outbox_worker.OutboxWorkerConfig,
            "from_environment",
            return_value=self.config,
        ):
            with self.assertRaisesRegex(
                OutboxWorkerInvocationError,
                "aws_request_id is required",
            ):
                lambda_handler({}, FakeLambdaContext(request_id=""))


class OutboxWorkerInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2]
        cls.template_path = (
            cls.root / "infrastructure" / "outbox-worker" / "template.json"
        )
        cls.template = json.loads(cls.template_path.read_text(encoding="ascii"))
        cls.resources = cls.template["Resources"]

    def test_template_is_valid_json_sam_and_schedule_defaults_disabled(self):
        self.assertEqual(
            self.template["Transform"],
            "AWS::Serverless-2016-10-31",
        )
        self.assertEqual(
            self.template["Parameters"]["ScheduleState"]["Default"],
            "DISABLED",
        )
        function = self.resources["OutboxWorkerFunction"]["Properties"]
        schedule = function["Events"]["OutboxRecoverySchedule"]
        self.assertEqual(schedule["Type"], "ScheduleV2")
        self.assertNotIn("OutboxRecoverySchedule", self.resources)
        schedule = schedule["Properties"]
        self.assertEqual(schedule["FlexibleTimeWindow"], {"Mode": "OFF"})
        self.assertEqual(
            self.template["Parameters"]["ScheduleExpression"]["Default"],
            "rate(1 minute)",
        )

    def test_scheduler_and_lambda_execution_failures_use_separate_queues(self):
        function = self.resources["OutboxWorkerFunction"]["Properties"]
        execution_destination = function["EventInvokeConfig"][
            "DestinationConfig"
        ]["OnFailure"]
        schedule_dlq = function["Events"]["OutboxRecoverySchedule"][
            "Properties"
        ]["DeadLetterConfig"]
        self.assertEqual(execution_destination["Type"], "SQS")
        self.assertEqual(
            execution_destination["Destination"]["Fn::GetAtt"][0],
            "LambdaExecutionFailureQueue",
        )
        self.assertEqual(
            schedule_dlq["Arn"]["Fn::GetAtt"][0],
            "SchedulerInvocationFailureQueue",
        )
        self.assertNotEqual(
            execution_destination["Destination"], schedule_dlq["Arn"]
        )

    def test_lambda_role_can_send_only_to_execution_failure_queue(self):
        statements = self.resources["OutboxWorkerFunction"]["Properties"][
            "Policies"
        ][0]["Statement"]
        sqs = [
            statement
            for statement in statements
            if statement["Action"] == "sqs:SendMessage"
        ]
        self.assertEqual(len(sqs), 1)
        self.assertEqual(
            sqs[0]["Resource"]["Fn::GetAtt"][0],
            "LambdaExecutionFailureQueue",
        )

    def test_worker_dynamodb_permissions_exclude_identity_and_alert_indexes(self):
        statements = self.resources["OutboxWorkerFunction"]["Properties"][
            "Policies"
        ][0]["Statement"]
        alert_statement = next(
            statement
            for statement in statements
            if statement.get("Sid") == "PersistExactAlertCandidate"
        )
        self.assertEqual(
            set(alert_statement["Action"]),
            {
                "dynamodb:DescribeTable",
                "dynamodb:GetItem",
                "dynamodb:TransactWriteItems",
            },
        )
        resources = json.dumps(alert_statement["Resource"])
        self.assertNotIn("index/*", resources)
        self.assertNotIn("Identity", resources)

    def test_scheduler_role_is_narrowly_scoped_to_invoke_and_its_dlq(self):
        statements = self.resources["OutboxSchedulerRole"]["Properties"][
            "Policies"
        ][0]["PolicyDocument"]["Statement"]
        self.assertEqual(
            {statement["Action"] for statement in statements},
            {"lambda:InvokeFunction", "sqs:SendMessage"},
        )
        queue_statement = next(
            statement
            for statement in statements
            if statement["Action"] == "sqs:SendMessage"
        )
        self.assertEqual(
            queue_statement["Resource"]["Fn::GetAtt"][0],
            "SchedulerInvocationFailureQueue",
        )

    def test_worker_environment_contains_no_credentials_or_identity_access(self):
        variables = self.resources["OutboxWorkerFunction"]["Properties"][
            "Environment"
        ]["Variables"]
        joined = " ".join(variables).upper()
        self.assertNotIn("ACCESS_KEY", joined)
        self.assertNotIn("SECRET", joined)
        self.assertNotIn("PROFILE", joined)
        self.assertNotIn("IDENTITY", joined)
        self.assertNotIn("ENDPOINT", joined)
        self.assertNotIn("MINIMUM_REMAINING", joined)

    def test_run_budget_is_code_owned_and_endpoint_redirect_is_absent(self):
        worker_source = (self.root / "SD" / "backend" / "outbox_worker.py").read_text(
            encoding="ascii"
        )
        self.assertIn("MIN_RUN_BUDGET_MS = 10_000", worker_source)
        self.assertNotIn("VITAE_OUTBOX_MINIMUM_REMAINING_TIME_MS", worker_source)
        self.assertNotIn("dynamodb_endpoint_url", worker_source)
        self.assertNotIn("endpoint_url=", worker_source)

    def test_worker_package_is_explicit_and_excludes_unrelated_application_code(self):
        makefile = (self.root / "SD" / "backend" / "Makefile").read_text(
            encoding="ascii"
        )
        for required in (
            "outbox_worker.py",
            "outbox_dispatcher.py",
            "dynamo_telemetry_repository.py",
            "dynamo_alert_repository.py",
        ):
            self.assertIn(required, makefile)
        for excluded in (
            "test_",
            "app.py",
            "sensor_processor.py",
            "ml_client.py",
            "simulator.py",
        ):
            self.assertNotIn(excluded, makefile)
        requirements = (
            self.root / "SD" / "backend" / "requirements-worker.txt"
        ).read_text(encoding="ascii")
        self.assertRegex(requirements.strip(), r"^boto3==\d+\.\d+\.\d+$")

    def test_explicit_worker_package_has_a_closed_import_graph(self):
        modules = (
            "alerting.py",
            "decision_outbox.py",
            "dynamo_alert_repository.py",
            "dynamo_telemetry_repository.py",
            "outbox_dispatcher.py",
            "outbox_worker.py",
            "product_rules.py",
            "repository_serialization.py",
            "risk_rules.py",
            "shipment_access.py",
            "state_repository.py",
            "telemetry.py",
            "telemetry_processor.py",
            "trip_identity.py",
        )
        source = self.root / "SD" / "backend"
        with tempfile.TemporaryDirectory(prefix="vitae-worker-package-") as directory:
            target = Path(directory)
            for module in modules:
                shutil.copy2(source / module, target / module)
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(target)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import outbox_worker; print(outbox_worker.lambda_handler.__module__)",
                ],
                cwd=target,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.stdout.strip(), "outbox_worker")


if __name__ == "__main__":
    unittest.main()
