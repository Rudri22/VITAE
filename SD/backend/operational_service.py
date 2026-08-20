from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional

try:
    from .alerting import Alert, AlertRepository, evaluate_alert_policy
    from .decision_outbox import (
        alert_outbox_event_from_candidate,
        decision_record_from_processing_result,
    )
    from .outbox_dispatcher import (
        AlertOutboxDeliveryService,
        OutboxDeliveryOutcome,
    )
    from .telemetry_processor import ProcessingResult, TelemetryProcessor
except ImportError:
    from alerting import Alert, AlertRepository, evaluate_alert_policy
    from decision_outbox import (
        alert_outbox_event_from_candidate,
        decision_record_from_processing_result,
    )
    from outbox_dispatcher import AlertOutboxDeliveryService, OutboxDeliveryOutcome
    from telemetry_processor import ProcessingResult, TelemetryProcessor


@dataclass(frozen=True)
class OperationalProcessingResult:
    processing_result: ProcessingResult
    alert: Optional[Alert]


class AlertProcessingError(RuntimeError):
    def __init__(
        self,
        processing_result: ProcessingResult,
        *,
        outbox_event_id: str,
        alert_candidate: Alert,
    ):
        super().__init__(
            "Telemetry was committed, but downstream alert processing failed"
        )
        self.processing_result = processing_result
        self.outbox_event_id = outbox_event_id
        self.alert_candidate = alert_candidate


class OperationalTelemetryService:
    _SYNC_WORKER_ID = "operational-sync"
    _SYNC_LEASE = timedelta(minutes=5)

    def __init__(
        self,
        telemetry_processor: TelemetryProcessor,
        alert_repository: AlertRepository,
    ):
        self._telemetry_processor = telemetry_processor
        self._alert_repository = alert_repository
        self._outbox_delivery = AlertOutboxDeliveryService(
            telemetry_processor.processing_repository,
            alert_repository,
        )

    def process(self, raw_sample: Mapping[str, Any]) -> OperationalProcessingResult:
        processing_result = self._telemetry_processor.prepare(raw_sample)
        alert = evaluate_alert_policy(
            processing_result.previous_live_state,
            processing_result,
        )
        decision_record = decision_record_from_processing_result(processing_result)
        outbox_event = (
            None
            if alert is None
            else alert_outbox_event_from_candidate(decision_record, alert)
        )
        self._telemetry_processor.commit_processing_bundle(
            processing_result,
            decision_record,
            outbox_event,
        )
        if outbox_event is None:
            return OperationalProcessingResult(
                processing_result=processing_result,
                alert=None,
            )
        try:
            persisted_alert = self.deliver_outbox_event(
                outbox_event.event_id,
                attempted_at=processing_result.telemetry_record.timestamp,
            )
        except Exception as error:
            raise AlertProcessingError(
                processing_result,
                outbox_event_id=outbox_event.event_id,
                alert_candidate=outbox_event.alert_candidate,
            ) from error
        return OperationalProcessingResult(
            processing_result=processing_result,
            alert=persisted_alert,
        )

    def deliver_outbox_event(
        self,
        event_id: str,
        *,
        attempted_at: datetime,
    ) -> Alert:
        """Deliver the exact stored candidate; never recalculate alert policy."""
        result = self._outbox_delivery.deliver(
            event_id,
            worker_id=self._SYNC_WORKER_ID,
            attempted_at=attempted_at,
            lease_duration=self._SYNC_LEASE,
        )
        if result.outcome in {
            OutboxDeliveryOutcome.DELIVERED,
            OutboxDeliveryOutcome.ALREADY_DELIVERED,
        }:
            return result.alert
        raise RuntimeError(
            f"Alert delivery deferred with {result.error_code}"
        )
