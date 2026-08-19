from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from .alerting import Alert
    from .operational_service import (
        AlertProcessingError,
        OperationalProcessingResult,
        OperationalTelemetryService,
    )
    from .product_rules import ProductRulesError
    from .state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        OutOfOrderTelemetryError,
        StateIntegrityError,
    )
    from .telemetry import TelemetryValidationError
    from .telemetry_processor import ProcessingResult
    from .trip_identity import (
        MultipleActiveAssignmentsError,
        NoActiveAssignmentError,
        TripIdentityError,
        TripNotActiveError,
        UnknownDeviceError,
    )
except ImportError:
    from alerting import Alert
    from operational_service import (
        AlertProcessingError,
        OperationalProcessingResult,
        OperationalTelemetryService,
    )
    from product_rules import ProductRulesError
    from state_repository import (
        ConcurrentStateUpdateError,
        DuplicateTelemetrySampleError,
        OutOfOrderTelemetryError,
        StateIntegrityError,
    )
    from telemetry import TelemetryValidationError
    from telemetry_processor import ProcessingResult
    from trip_identity import (
        MultipleActiveAssignmentsError,
        NoActiveAssignmentError,
        TripIdentityError,
        TripNotActiveError,
        UnknownDeviceError,
    )


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: Dict[str, Any]


class TelemetryHttpAdapter:
    def __init__(self, operational_service: OperationalTelemetryService):
        self._operational_service = operational_service

    def handle_post(self, payload: Any) -> HttpResponse:
        try:
            result = self._operational_service.process(payload)
        except AlertProcessingError as error:
            return _alert_failure_response(error.processing_result)
        except TelemetryValidationError as error:
            return _error_response(
                400,
                error.reason_code,
                "Telemetry payload is invalid",
                field=error.field,
            )
        except UnknownDeviceError:
            return _error_response(404, "UNKNOWN_DEVICE", "Device is not registered")
        except NoActiveAssignmentError:
            return _error_response(
                409,
                "NO_ACTIVE_ASSIGNMENT",
                "Device has no active trip assignment",
            )
        except MultipleActiveAssignmentsError:
            return _error_response(
                409,
                "MULTIPLE_ACTIVE_ASSIGNMENTS",
                "Device has conflicting active trip assignments",
            )
        except TripNotActiveError:
            return _error_response(
                409,
                "TRIP_NOT_ACTIVE",
                "Resolved trip is not active",
            )
        except DuplicateTelemetrySampleError:
            return _error_response(
                409,
                "DUPLICATE_TELEMETRY_SAMPLE",
                "Telemetry sample was already accepted",
            )
        except OutOfOrderTelemetryError:
            return _error_response(
                409,
                "OUT_OF_ORDER_TELEMETRY",
                "Telemetry timestamp is not newer than current state",
            )
        except ConcurrentStateUpdateError:
            return _error_response(
                409,
                "CONCURRENT_STATE_UPDATE",
                "Telemetry state changed during processing",
            )
        except ProductRulesError:
            return _error_response(
                422,
                "PRODUCT_RULES_UNAVAILABLE",
                "Verified rules are unavailable for the resolved trip context",
            )
        except TripIdentityError:
            return _error_response(
                422,
                "TRIP_IDENTITY_INVALID",
                "Trip or device assignment identity is invalid",
            )
        except StateIntegrityError:
            return _error_response(
                500,
                "STATE_INTEGRITY_ERROR",
                "Telemetry state could not be persisted safely",
            )
        except Exception:
            return _error_response(
                500,
                "INTERNAL_ERROR",
                "Telemetry processing failed",
            )
        return HttpResponse(status_code=200, body=_success_body(result))


def serialize_operational_result(
    result: OperationalProcessingResult,
) -> Dict[str, Any]:
    alert_required = result.alert is not None
    return {
        "success": True,
        "telemetryAccepted": True,
        "alertRequired": alert_required,
        "alertPersisted": alert_required,
        "processingResult": serialize_processing_result(result.processing_result),
        "alert": _serialize_alert(result.alert),
    }


def serialize_processing_result(result: ProcessingResult) -> Dict[str, Any]:
    return {
        "previousLiveState": _serialize_live_state(result.previous_live_state),
        "telemetryRecord": _serialize_telemetry_record(result.telemetry_record),
        "decision": _serialize_decision(result.decision),
        "liveState": _serialize_live_state(result.live_state),
    }


def _success_body(result):
    return serialize_operational_result(result)


def _alert_failure_response(result):
    record = result.telemetry_record
    return HttpResponse(
        status_code=503,
        body={
            "success": False,
            "telemetryAccepted": True,
            "alertRequired": True,
            "alertPersisted": False,
            "sampleId": record.sample_id,
            "deviceId": record.device_id,
            "tripId": record.trip_id,
            "lotTripId": record.lot_trip_id,
            "processingResult": serialize_processing_result(result),
            "error": {
                "code": "ALERT_PERSISTENCE_FAILED",
                "message": "Telemetry was accepted, but its alert was not persisted",
            },
        },
    )


def _error_response(status_code, code, message, *, field=None):
    error = {"code": code, "message": message}
    if field is not None:
        error["field"] = field
    return HttpResponse(
        status_code=status_code,
        body={
            "success": False,
            "telemetryAccepted": False,
            "alertPersisted": False,
            "error": error,
        },
    )


def _serialize_telemetry_record(record):
    return {
        "tripId": record.trip_id,
        "lotTripId": record.lot_trip_id,
        "sampleId": record.sample_id,
        "deviceId": record.device_id,
        "timestamp": _iso_timestamp(record.timestamp),
        "temperature": record.temperature,
        "batteryLevel": record.battery_level,
        "latitude": record.latitude,
        "longitude": record.longitude,
        "deviceHealth": record.device_health,
    }


def _serialize_decision(decision):
    return {
        "status": decision.status.value,
        "reasonCode": decision.reason_code,
        "activeRuleId": decision.active_rule_id,
        "excursionEpisodeDurationMinutes": (
            decision.excursion_episode_duration_minutes
        ),
        "cumulativeExcursionDurationMinutes": (
            decision.cumulative_excursion_duration_minutes
        ),
        "excursionUtilization": decision.excursion_utilization,
        "excursionStartedAt": _iso_timestamp(decision.excursion_started_at),
    }


def _serialize_live_state(state):
    if state is None:
        return None
    return {
        "lotTripId": state.lot_trip_id,
        "tripId": state.trip_id,
        "deviceId": state.device_id,
        "productId": state.product_id,
        "productRuleVersion": state.product_rule_version,
        "status": state.status.value,
        "reasonCode": state.reason_code,
        "activeRuleId": state.active_rule_id,
        "lastSampleId": state.last_sample_id,
        "lastSampleTimestamp": _iso_timestamp(state.last_sample_timestamp),
        "latestTemperature": state.latest_temperature,
        "lastUpdated": _iso_timestamp(state.last_updated),
        "excursionStartedAt": _iso_timestamp(state.excursion_started_at),
        "excursionEpisodeDurationMinutes": (
            state.excursion_episode_duration_minutes
        ),
        "cumulativeExcursionDurationMinutes": (
            state.cumulative_excursion_duration_minutes
        ),
        "excursionUtilization": state.excursion_utilization,
        "revision": state.revision,
    }


def _serialize_alert(alert: Optional[Alert]):
    if alert is None:
        return None
    return {
        "alertId": alert.alert_id,
        "alertType": alert.alert_type.value,
        "severity": alert.severity.value,
        "status": alert.status.value,
        "tripId": alert.trip_id,
        "lotTripId": alert.lot_trip_id,
        "deviceId": alert.device_id,
        "sampleId": alert.sample_id,
        "sourceStatus": alert.source_status.value,
        "reasonCode": alert.reason_code,
        "activeRuleId": alert.active_rule_id,
        "message": alert.message,
        "recommendedAction": alert.recommended_action,
        "detectedAt": _iso_timestamp(alert.detected_at),
        "updatedAt": _iso_timestamp(alert.updated_at),
        "acknowledgedBy": alert.acknowledged_by,
        "acknowledgedAt": _iso_timestamp(alert.acknowledged_at),
        "actions": [
            {
                "actionId": action.action_id,
                "description": action.description,
                "actorId": action.actor_id,
                "recordedAt": _iso_timestamp(action.recorded_at),
            }
            for action in alert.actions
        ],
        "resolvedBy": alert.resolved_by,
        "resolvedAt": _iso_timestamp(alert.resolved_at),
        "resolutionNote": alert.resolution_note,
    }


def _iso_timestamp(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.astimezone(timezone.utc).isoformat()
    return normalized.replace("+00:00", "Z")
