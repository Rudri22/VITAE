import unittest
from datetime import datetime, timezone

try:
    from .real_device_gateway import DeviceTelemetryGateway, GatewayDeliveryError, SensorReadError
except ImportError:
    from real_device_gateway import DeviceTelemetryGateway, GatewayDeliveryError, SensorReadError


class _Sensor:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.calls = 0

    def read_temperature_celsius(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.value


class _Transport:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def post_json(self, payload, bearer_token):
        self.calls.append((payload, bearer_token))
        if self.error:
            raise self.error
        return {"telemetryAccepted": True}


class RealDeviceGatewayTests(unittest.TestCase):
    def test_fake_sensor_posts_official_minimal_real_device_payload(self):
        sensor = _Sensor(6.25)
        transport = _Transport()
        gateway = DeviceTelemetryGateway(
            "device-1", sensor, transport, "secret-token",
            clock=lambda: datetime(2026, 8, 23, 12, tzinfo=timezone.utc),
            sample_id_factory=lambda: "sample-physical-1",
        )
        payload, response = gateway.read_and_send()
        self.assertEqual(response, {"telemetryAccepted": True})
        self.assertEqual(payload, {
            "sample_id": "sample-physical-1", "device_id": "device-1",
            "timestamp": "2026-08-23T12:00:00Z", "temperature": 6.25,
            "source": "REAL_DEVICE",
        })
        self.assertEqual(transport.calls, [(payload, "secret-token")])

    def test_sensor_failure_sends_nothing_and_invents_no_measurement(self):
        sensor = _Sensor(error=OSError("disconnected"))
        transport = _Transport()
        gateway = DeviceTelemetryGateway("device-1", sensor, transport, "token")
        with self.assertRaises(SensorReadError):
            gateway.read_and_send()
        self.assertEqual(transport.calls, [])

    def test_nonfinite_sensor_value_is_rejected_before_transport(self):
        transport = _Transport()
        gateway = DeviceTelemetryGateway("device-1", _Sensor(float("nan")), transport, "token")
        with self.assertRaises(SensorReadError):
            gateway.read_and_send()
        self.assertEqual(transport.calls, [])

    def test_connection_failure_does_not_retry_with_changed_data(self):
        transport = _Transport(GatewayDeliveryError("offline"))
        sensor = _Sensor(5.5)
        gateway = DeviceTelemetryGateway("device-1", sensor, transport, "token")
        with self.assertRaises(GatewayDeliveryError):
            gateway.read_and_send()
        self.assertEqual(sensor.calls, 1)
        self.assertEqual(len(transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
