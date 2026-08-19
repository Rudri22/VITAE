from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping, Optional, Tuple


@dataclass(frozen=True)
class ValidatedTelemetrySample:
    sample_id: str
    device_id: str
    timestamp: datetime
    temperature: float
    battery_level: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    device_health: Optional[str] = None


class TelemetryValidationError(ValueError):
    def __init__(self, reason_code: str, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.reason_code = reason_code
        self.field = field


def validate_and_normalize_telemetry(
    raw_sample: Mapping[str, Any],
) -> ValidatedTelemetrySample:
    """Validate raw sensor facts and return one immutable normalized sample."""
    if not isinstance(raw_sample, Mapping):
        raise TelemetryValidationError(
            "INVALID_PAYLOAD",
            "Telemetry payload must be a mapping",
        )

    sample_id = _required_identifier(raw_sample, "sample_id")
    device_id = _required_identifier(raw_sample, "device_id")
    timestamp = _required_timestamp(raw_sample)
    temperature = _required_number(raw_sample, "temperature")
    battery_level = _optional_battery(raw_sample)
    latitude, longitude = _optional_gps(raw_sample)
    device_health = _optional_device_health(raw_sample)

    return ValidatedTelemetrySample(
        sample_id=sample_id,
        device_id=device_id,
        timestamp=timestamp,
        temperature=temperature,
        battery_level=battery_level,
        latitude=latitude,
        longitude=longitude,
        device_health=device_health,
    )


def sample_identity(sample: ValidatedTelemetrySample) -> Tuple[str, str]:
    """Return the device-scoped idempotency key for a validated sample."""
    return sample.device_id, sample.sample_id


def is_newer_sample(
    candidate: ValidatedTelemetrySample,
    previous: ValidatedTelemetrySample,
) -> bool:
    """Return True only when the candidate timestamp is strictly newer."""
    return candidate.timestamp > previous.timestamp


def _required_identifier(raw_sample, field):
    if field not in raw_sample:
        raise TelemetryValidationError(
            f"MISSING_{field.upper()}",
            f"Telemetry {field} is required",
            field,
        )
    value = raw_sample[field]
    if not isinstance(value, str) or not value.strip():
        raise TelemetryValidationError(
            f"INVALID_{field.upper()}",
            f"Telemetry {field} must be a non-empty string",
            field,
        )
    return value.strip()


def _required_timestamp(raw_sample):
    if "timestamp" not in raw_sample or raw_sample["timestamp"] is None:
        raise TelemetryValidationError(
            "MISSING_TIMESTAMP",
            "Telemetry timestamp is required",
            "timestamp",
        )
    value = raw_sample["timestamp"]
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            parsed = None
    else:
        parsed = None
    if parsed is None:
        raise TelemetryValidationError(
            "INVALID_TIMESTAMP",
            "Telemetry timestamp must be a valid ISO 8601 timestamp",
            "timestamp",
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TelemetryValidationError(
            "NAIVE_TIMESTAMP",
            "Telemetry timestamp must include a timezone offset",
            "timestamp",
        )
    return parsed.astimezone(timezone.utc)


def _required_number(raw_sample, field):
    if field not in raw_sample or raw_sample[field] is None:
        raise TelemetryValidationError(
            f"MISSING_{field.upper()}",
            f"Telemetry {field} is required",
            field,
        )
    value = raw_sample[field]
    if not _is_finite_number(value):
        raise TelemetryValidationError(
            f"INVALID_{field.upper()}",
            f"Telemetry {field} must be a finite number",
            field,
        )
    return float(value)


def _optional_battery(raw_sample):
    if "battery_level" not in raw_sample:
        return None
    value = raw_sample["battery_level"]
    if not _is_finite_number(value) or not 0 <= float(value) <= 100:
        raise TelemetryValidationError(
            "INVALID_BATTERY_LEVEL",
            "Telemetry battery_level must be between 0 and 100",
            "battery_level",
        )
    return float(value)


def _optional_gps(raw_sample):
    has_latitude = "latitude" in raw_sample
    has_longitude = "longitude" in raw_sample
    if has_latitude != has_longitude:
        raise TelemetryValidationError(
            "INCOMPLETE_GPS",
            "Telemetry latitude and longitude must be supplied together",
            "latitude" if has_latitude else "longitude",
        )
    if not has_latitude:
        return None, None

    latitude = raw_sample["latitude"]
    longitude = raw_sample["longitude"]
    if not _is_finite_number(latitude) or not -90 <= float(latitude) <= 90:
        raise TelemetryValidationError(
            "INVALID_LATITUDE",
            "Telemetry latitude must be between -90 and 90",
            "latitude",
        )
    if not _is_finite_number(longitude) or not -180 <= float(longitude) <= 180:
        raise TelemetryValidationError(
            "INVALID_LONGITUDE",
            "Telemetry longitude must be between -180 and 180",
            "longitude",
        )
    return float(latitude), float(longitude)


def _optional_device_health(raw_sample):
    if "device_health" not in raw_sample:
        return None
    value = raw_sample["device_health"]
    if not isinstance(value, str) or not value.strip():
        raise TelemetryValidationError(
            "INVALID_DEVICE_HEALTH",
            "Telemetry device_health must be a non-empty string when supplied",
            "device_health",
        )
    return value.strip()


def _is_finite_number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value)
