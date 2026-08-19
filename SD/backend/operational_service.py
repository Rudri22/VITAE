from dataclasses import dataclass
from typing import Any, Mapping, Optional

try:
    from .alerting import Alert, AlertRepository, evaluate_alert_policy
    from .telemetry_processor import ProcessingResult, TelemetryProcessor
except ImportError:
    from alerting import Alert, AlertRepository, evaluate_alert_policy
    from telemetry_processor import ProcessingResult, TelemetryProcessor


@dataclass(frozen=True)
class OperationalProcessingResult:
    processing_result: ProcessingResult
    alert: Optional[Alert]


class AlertProcessingError(RuntimeError):
    def __init__(self, processing_result: ProcessingResult):
        super().__init__(
            "Telemetry was committed, but downstream alert processing failed"
        )
        self.processing_result = processing_result


class OperationalTelemetryService:
    def __init__(
        self,
        telemetry_processor: TelemetryProcessor,
        alert_repository: AlertRepository,
    ):
        self._telemetry_processor = telemetry_processor
        self._alert_repository = alert_repository

    def process(self, raw_sample: Mapping[str, Any]) -> OperationalProcessingResult:
        processing_result = self._telemetry_processor.process(raw_sample)
        try:
            alert = self.persist_alert_for_result(processing_result)
        except Exception as error:
            raise AlertProcessingError(processing_result) from error
        return OperationalProcessingResult(
            processing_result=processing_result,
            alert=alert,
        )

    def persist_alert_for_result(
        self,
        result: ProcessingResult,
    ) -> Optional[Alert]:
        alert = evaluate_alert_policy(result.previous_live_state, result)
        if alert is None:
            return None
        return self._alert_repository.save_alert(alert)
