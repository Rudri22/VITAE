"""Hardware-neutral temperature sensor to VITAE HTTP gateway."""

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Callable, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEVICE_GATEWAY_URL_ENV = "VITAE_DEVICE_INGEST_URL"
DEVICE_GATEWAY_TOKEN_ENV = "VITAE_DEVICE_INGEST_TOKEN"


class SensorReadError(RuntimeError):
    pass


class GatewayDeliveryError(RuntimeError):
    pass


@runtime_checkable
class TemperatureSensor(Protocol):
    def read_temperature_celsius(self) -> float:
        ...


@runtime_checkable
class TelemetryTransport(Protocol):
    def post_json(self, payload: dict, bearer_token: str) -> dict:
        ...


@dataclass(frozen=True)
class HttpJsonTelemetryTransport:
    endpoint_url: str
    timeout_seconds: float = 5.0

    def post_json(self, payload, bearer_token):
        request = Request(
            self.endpoint_url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                document = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError) as error:
            raise GatewayDeliveryError("VITAE telemetry delivery failed") from error
        if not isinstance(document, dict) or document.get("telemetryAccepted") is not True:
            raise GatewayDeliveryError("VITAE did not accept the telemetry sample")
        return document


class DeviceTelemetryGateway:
    """Read exactly once, then deliver that measurement without substitution."""

    def __init__(
        self,
        device_id: str,
        sensor: TemperatureSensor,
        transport: TelemetryTransport,
        bearer_token: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sample_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ):
        self._device_id = _required_text(device_id, "device_id")
        if not isinstance(sensor, TemperatureSensor):
            raise TypeError("sensor must support TemperatureSensor")
        if not isinstance(transport, TelemetryTransport):
            raise TypeError("transport must support TelemetryTransport")
        self._sensor = sensor
        self._transport = transport
        self._token = _required_text(bearer_token, "bearer_token")
        self._clock = clock
        self._sample_id_factory = sample_id_factory

    def read_and_send(self):
        try:
            temperature = self._sensor.read_temperature_celsius()
        except Exception as error:
            raise SensorReadError("Temperature sensor read failed") from error
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not isfinite(temperature):
            raise SensorReadError("Temperature sensor returned a non-finite measurement")
        measured_at = self._clock()
        if not isinstance(measured_at, datetime) or measured_at.tzinfo is None or measured_at.utcoffset() is None:
            raise SensorReadError("Gateway clock must return a timezone-aware timestamp")
        payload = {
            "sample_id": _required_text(self._sample_id_factory(), "sample_id"),
            "device_id": self._device_id,
            "timestamp": measured_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "temperature": float(temperature),
            "source": "REAL_DEVICE",
        }
        response = self._transport.post_json(payload, self._token)
        return payload, response


def gateway_from_environment(sensor):
    endpoint = _required_text(os.environ.get(DEVICE_GATEWAY_URL_ENV), DEVICE_GATEWAY_URL_ENV)
    token = _required_text(os.environ.get(DEVICE_GATEWAY_TOKEN_ENV), DEVICE_GATEWAY_TOKEN_ENV)
    device_id = _required_text(os.environ.get("VITAE_DEVICE_ID"), "VITAE_DEVICE_ID")
    return DeviceTelemetryGateway(device_id, sensor, HttpJsonTelemetryTransport(endpoint), token)


def _required_text(value, field):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized
