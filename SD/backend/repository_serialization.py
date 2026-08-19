from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from typing import Any, Mapping, Optional

try:
    from .alerting import Alert, AlertAction, AlertSeverity, AlertStatus, AlertType
    from .risk_rules import ApplicationStatus
    from .state_repository import LiveState, TelemetryRecord
    from .shipment_access import ShipmentAccess
    from .trip_identity import DeviceAssignment, TripIdentity, TripStatus
except ImportError:
    from alerting import Alert, AlertAction, AlertSeverity, AlertStatus, AlertType
    from risk_rules import ApplicationStatus
    from state_repository import LiveState, TelemetryRecord
    from shipment_access import ShipmentAccess
    from trip_identity import DeviceAssignment, TripIdentity, TripStatus


SCHEMA_VERSION = 1


class RepositorySerializationError(ValueError):
    pass


def serialize_trip_identity(value: TripIdentity) -> dict[str, Any]:
    return _document(
        "vitae.trip_identity",
        {
            "trip_id": value.trip_id,
            "lot_trip_id": value.lot_trip_id,
            "lot_id": value.lot_id,
            "device_id": value.device_id,
            "product_id": value.product_id,
            "presentation": value.presentation,
            "state": value.state,
            "product_rule_version": value.product_rule_version,
            "origin": value.origin,
            "destination": value.destination,
            "start_time": _serialize_datetime(value.start_time, "start_time"),
            "status": _enum_value(value.status, TripStatus, "status"),
        },
    )


def deserialize_trip_identity(payload: Mapping[str, Any]) -> TripIdentity:
    fields = _fields(payload, "vitae.trip_identity", _TRIP_FIELDS)
    return TripIdentity(
        trip_id=_text(fields, "trip_id"),
        lot_trip_id=_text(fields, "lot_trip_id"),
        lot_id=_text(fields, "lot_id"),
        device_id=_text(fields, "device_id"),
        product_id=_text(fields, "product_id"),
        presentation=_text(fields, "presentation"),
        state=_text(fields, "state"),
        product_rule_version=_text(fields, "product_rule_version"),
        origin=_text(fields, "origin"),
        destination=_text(fields, "destination"),
        start_time=_deserialize_datetime(fields["start_time"], "start_time"),
        status=_enum(fields["status"], TripStatus, "status"),
    )


def serialize_device_assignment(value: DeviceAssignment) -> dict[str, Any]:
    return _document(
        "vitae.device_assignment",
        {
            "assignment_id": value.assignment_id,
            "device_id": value.device_id,
            "trip_id": value.trip_id,
            "lot_trip_id": value.lot_trip_id,
            "assigned_at": _serialize_datetime(value.assigned_at, "assigned_at"),
            "active": _boolean(value.active, "active"),
        },
    )


def deserialize_device_assignment(payload: Mapping[str, Any]) -> DeviceAssignment:
    fields = _fields(payload, "vitae.device_assignment", _ASSIGNMENT_FIELDS)
    return DeviceAssignment(
        assignment_id=_text(fields, "assignment_id"),
        device_id=_text(fields, "device_id"),
        trip_id=_text(fields, "trip_id"),
        lot_trip_id=_text(fields, "lot_trip_id"),
        assigned_at=_deserialize_datetime(fields["assigned_at"], "assigned_at"),
        active=_boolean(fields["active"], "active"),
    )


def serialize_shipment_access(value: ShipmentAccess) -> dict[str, Any]:
    return _document(
        "vitae.shipment_access",
        {
            "shipment_id": value.shipment_id,
            "lot_trip_id": value.lot_trip_id,
            "organization_id": value.organization_id,
            "driver_id": value.driver_id,
        },
    )


def deserialize_shipment_access(payload: Mapping[str, Any]) -> ShipmentAccess:
    fields = _fields(payload, "vitae.shipment_access", _SHIPMENT_ACCESS_FIELDS)
    return ShipmentAccess(
        shipment_id=_text(fields, "shipment_id"),
        lot_trip_id=_text(fields, "lot_trip_id"),
        organization_id=_text(fields, "organization_id"),
        driver_id=_text(fields, "driver_id"),
    )


def serialize_telemetry_record(value: TelemetryRecord) -> dict[str, Any]:
    return _document(
        "vitae.telemetry_record",
        {
            "trip_id": value.trip_id,
            "lot_trip_id": value.lot_trip_id,
            "sample_id": value.sample_id,
            "device_id": value.device_id,
            "timestamp": _serialize_datetime(value.timestamp, "timestamp"),
            "temperature": _number(value.temperature, "temperature"),
            "battery_level": _optional_number(value.battery_level, "battery_level"),
            "latitude": _optional_number(value.latitude, "latitude"),
            "longitude": _optional_number(value.longitude, "longitude"),
            "device_health": _optional_text_value(value.device_health, "device_health"),
        },
    )


def deserialize_telemetry_record(payload: Mapping[str, Any]) -> TelemetryRecord:
    fields = _fields(payload, "vitae.telemetry_record", _TELEMETRY_FIELDS)
    return TelemetryRecord(
        trip_id=_text(fields, "trip_id"),
        lot_trip_id=_text(fields, "lot_trip_id"),
        sample_id=_text(fields, "sample_id"),
        device_id=_text(fields, "device_id"),
        timestamp=_deserialize_datetime(fields["timestamp"], "timestamp"),
        temperature=_number(fields["temperature"], "temperature"),
        battery_level=_optional_number(fields["battery_level"], "battery_level"),
        latitude=_optional_number(fields["latitude"], "latitude"),
        longitude=_optional_number(fields["longitude"], "longitude"),
        device_health=_optional_text_value(fields["device_health"], "device_health"),
    )


def serialize_live_state(value: LiveState) -> dict[str, Any]:
    return _document(
        "vitae.live_state",
        {
            "lot_trip_id": value.lot_trip_id,
            "trip_id": value.trip_id,
            "device_id": value.device_id,
            "product_id": value.product_id,
            "product_rule_version": value.product_rule_version,
            "status": _enum_value(value.status, ApplicationStatus, "status"),
            "reason_code": value.reason_code,
            "active_rule_id": _optional_text_value(value.active_rule_id, "active_rule_id"),
            "last_sample_id": value.last_sample_id,
            "last_sample_timestamp": _serialize_datetime(value.last_sample_timestamp, "last_sample_timestamp"),
            "latest_temperature": _number(value.latest_temperature, "latest_temperature"),
            "last_updated": _serialize_datetime(value.last_updated, "last_updated"),
            "excursion_started_at": _optional_datetime(value.excursion_started_at, "excursion_started_at"),
            "excursion_episode_duration_minutes": _number(value.excursion_episode_duration_minutes, "excursion_episode_duration_minutes"),
            "cumulative_excursion_duration_minutes": _number(value.cumulative_excursion_duration_minutes, "cumulative_excursion_duration_minutes"),
            "excursion_utilization": _optional_number(value.excursion_utilization, "excursion_utilization"),
            "revision": _positive_integer(value.revision, "revision"),
        },
    )


def deserialize_live_state(payload: Mapping[str, Any]) -> LiveState:
    fields = _fields(payload, "vitae.live_state", _LIVE_STATE_FIELDS)
    return LiveState(
        lot_trip_id=_text(fields, "lot_trip_id"),
        trip_id=_text(fields, "trip_id"),
        device_id=_text(fields, "device_id"),
        product_id=_text(fields, "product_id"),
        product_rule_version=_text(fields, "product_rule_version"),
        status=_enum(fields["status"], ApplicationStatus, "status"),
        reason_code=_text(fields, "reason_code"),
        active_rule_id=_optional_text_value(fields["active_rule_id"], "active_rule_id"),
        last_sample_id=_text(fields, "last_sample_id"),
        last_sample_timestamp=_deserialize_datetime(fields["last_sample_timestamp"], "last_sample_timestamp"),
        latest_temperature=_number(fields["latest_temperature"], "latest_temperature"),
        last_updated=_deserialize_datetime(fields["last_updated"], "last_updated"),
        excursion_started_at=_deserialize_optional_datetime(fields["excursion_started_at"], "excursion_started_at"),
        excursion_episode_duration_minutes=_number(fields["excursion_episode_duration_minutes"], "excursion_episode_duration_minutes"),
        cumulative_excursion_duration_minutes=_number(fields["cumulative_excursion_duration_minutes"], "cumulative_excursion_duration_minutes"),
        excursion_utilization=_optional_number(fields["excursion_utilization"], "excursion_utilization"),
        revision=_positive_integer(fields["revision"], "revision"),
    )


def serialize_alert_action(value: AlertAction) -> dict[str, Any]:
    return _document(
        "vitae.alert_action",
        {
            "action_id": value.action_id,
            "description": value.description,
            "actor_id": value.actor_id,
            "recorded_at": _serialize_datetime(value.recorded_at, "recorded_at"),
        },
    )


def deserialize_alert_action(payload: Mapping[str, Any]) -> AlertAction:
    fields = _fields(payload, "vitae.alert_action", _ALERT_ACTION_FIELDS)
    return AlertAction(
        action_id=_text(fields, "action_id"),
        description=_text(fields, "description"),
        actor_id=_text(fields, "actor_id"),
        recorded_at=_deserialize_datetime(fields["recorded_at"], "recorded_at"),
    )


def serialize_alert(value: Alert) -> dict[str, Any]:
    return _document(
        "vitae.alert",
        {
            "alert_id": value.alert_id,
            "alert_type": _enum_value(value.alert_type, AlertType, "alert_type"),
            "severity": _enum_value(value.severity, AlertSeverity, "severity"),
            "status": _enum_value(value.status, AlertStatus, "status"),
            "trip_id": value.trip_id,
            "lot_trip_id": value.lot_trip_id,
            "device_id": value.device_id,
            "sample_id": value.sample_id,
            "source_status": _enum_value(value.source_status, ApplicationStatus, "source_status"),
            "reason_code": value.reason_code,
            "active_rule_id": _optional_text_value(value.active_rule_id, "active_rule_id"),
            "message": value.message,
            "recommended_action": value.recommended_action,
            "detected_at": _serialize_datetime(value.detected_at, "detected_at"),
            "updated_at": _serialize_datetime(value.updated_at, "updated_at"),
            "acknowledged_by": _optional_text_value(value.acknowledged_by, "acknowledged_by"),
            "acknowledged_at": _optional_datetime(value.acknowledged_at, "acknowledged_at"),
            "actions": [serialize_alert_action(action) for action in value.actions],
            "resolved_by": _optional_text_value(value.resolved_by, "resolved_by"),
            "resolved_at": _optional_datetime(value.resolved_at, "resolved_at"),
            "resolution_note": _optional_text_value(value.resolution_note, "resolution_note"),
        },
    )


def deserialize_alert(payload: Mapping[str, Any]) -> Alert:
    fields = _fields(payload, "vitae.alert", _ALERT_FIELDS)
    actions = fields["actions"]
    if not isinstance(actions, list):
        raise RepositorySerializationError("actions must be a list")
    return Alert(
        alert_id=_text(fields, "alert_id"),
        alert_type=_enum(fields["alert_type"], AlertType, "alert_type"),
        severity=_enum(fields["severity"], AlertSeverity, "severity"),
        status=_enum(fields["status"], AlertStatus, "status"),
        trip_id=_text(fields, "trip_id"),
        lot_trip_id=_text(fields, "lot_trip_id"),
        device_id=_text(fields, "device_id"),
        sample_id=_text(fields, "sample_id"),
        source_status=_enum(fields["source_status"], ApplicationStatus, "source_status"),
        reason_code=_text(fields, "reason_code"),
        active_rule_id=_optional_text_value(fields["active_rule_id"], "active_rule_id"),
        message=_text(fields, "message"),
        recommended_action=_text(fields, "recommended_action"),
        detected_at=_deserialize_datetime(fields["detected_at"], "detected_at"),
        updated_at=_deserialize_datetime(fields["updated_at"], "updated_at"),
        acknowledged_by=_optional_text_value(fields["acknowledged_by"], "acknowledged_by"),
        acknowledged_at=_deserialize_optional_datetime(fields["acknowledged_at"], "acknowledged_at"),
        actions=tuple(deserialize_alert_action(action) for action in actions),
        resolved_by=_optional_text_value(fields["resolved_by"], "resolved_by"),
        resolved_at=_deserialize_optional_datetime(fields["resolved_at"], "resolved_at"),
        resolution_note=_optional_text_value(fields["resolution_note"], "resolution_note"),
    )


_TRIP_FIELDS = frozenset(("trip_id", "lot_trip_id", "lot_id", "device_id", "product_id", "presentation", "state", "product_rule_version", "origin", "destination", "start_time", "status"))
_ASSIGNMENT_FIELDS = frozenset(("assignment_id", "device_id", "trip_id", "lot_trip_id", "assigned_at", "active"))
_SHIPMENT_ACCESS_FIELDS = frozenset(("shipment_id", "lot_trip_id", "organization_id", "driver_id"))
_TELEMETRY_FIELDS = frozenset(("trip_id", "lot_trip_id", "sample_id", "device_id", "timestamp", "temperature", "battery_level", "latitude", "longitude", "device_health"))
_LIVE_STATE_FIELDS = frozenset(("lot_trip_id", "trip_id", "device_id", "product_id", "product_rule_version", "status", "reason_code", "active_rule_id", "last_sample_id", "last_sample_timestamp", "latest_temperature", "last_updated", "excursion_started_at", "excursion_episode_duration_minutes", "cumulative_excursion_duration_minutes", "excursion_utilization", "revision"))
_ALERT_ACTION_FIELDS = frozenset(("action_id", "description", "actor_id", "recorded_at"))
_ALERT_FIELDS = frozenset(("alert_id", "alert_type", "severity", "status", "trip_id", "lot_trip_id", "device_id", "sample_id", "source_status", "reason_code", "active_rule_id", "message", "recommended_action", "detected_at", "updated_at", "acknowledged_by", "acknowledged_at", "actions", "resolved_by", "resolved_at", "resolution_note"))


def _document(schema: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {"schema": schema, "schema_version": SCHEMA_VERSION, **fields}


def _fields(payload, schema: str, expected_fields) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise RepositorySerializationError("Serialized value must be a mapping")
    if payload.get("schema") != schema:
        raise RepositorySerializationError(f"Expected schema {schema}")
    version = payload.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        raise RepositorySerializationError("Unsupported schema version")
    actual = set(payload) - {"schema", "schema_version"}
    if actual != set(expected_fields):
        missing = sorted(set(expected_fields) - actual)
        unexpected = sorted(actual - set(expected_fields))
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        raise RepositorySerializationError("Invalid schema fields (" + "; ".join(details) + ")")
    return payload


def _text(fields, field: str) -> str:
    value = fields[field]
    if not isinstance(value, str) or not value.strip():
        raise RepositorySerializationError(f"{field} must be a non-empty string")
    return value


def _optional_text_value(value, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RepositorySerializationError(f"{field} must be null or a non-empty string")
    return value


def _number(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise RepositorySerializationError(f"{field} must be a finite number")
    return float(value)


def _optional_number(value, field: str) -> Optional[float]:
    return None if value is None else _number(value, field)


def _positive_integer(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RepositorySerializationError(f"{field} must be a positive integer")
    return value


def _boolean(value, field: str) -> bool:
    if not isinstance(value, bool):
        raise RepositorySerializationError(f"{field} must be a boolean")
    return value


def _enum_value(value, enum_type, field: str) -> str:
    if not isinstance(value, enum_type):
        raise RepositorySerializationError(f"{field} must be a {enum_type.__name__}")
    return value.value


def _enum(value, enum_type, field: str) -> Enum:
    if not isinstance(value, str):
        raise RepositorySerializationError(f"{field} is invalid")
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        raise RepositorySerializationError(f"{field} is invalid") from error


def _serialize_datetime(value, field: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RepositorySerializationError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_datetime(value, field: str) -> Optional[str]:
    return None if value is None else _serialize_datetime(value, field)


def _deserialize_datetime(value, field: str) -> datetime:
    if not isinstance(value, str):
        raise RepositorySerializationError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RepositorySerializationError(f"{field} must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RepositorySerializationError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _deserialize_optional_datetime(value, field: str) -> Optional[datetime]:
    return None if value is None else _deserialize_datetime(value, field)
