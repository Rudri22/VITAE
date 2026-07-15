try:
    from .alerts import build_alerts
    from .hospital_lookup import find_nearest_hospital
    from .ml_client import get_spoilage_prediction
    from .risk_rules import calculate_risk
    from .storage import SHIPMENTS, create_or_update_shipment, save_sensor_reading, update_shipment_result
except ImportError:
    from alerts import build_alerts
    from hospital_lookup import find_nearest_hospital
    from ml_client import get_spoilage_prediction
    from risk_rules import calculate_risk
    from storage import SHIPMENTS, create_or_update_shipment, save_sensor_reading, update_shipment_result


REQUIRED_FIELDS = ["shipmentId", "temperature", "batteryLevel", "gps", "timestamp"]


def process_sensor_data(payload):
    """Main workflow for POST /api/sensor-data.

    This function links all backend parts together:
    validation -> storage -> risk rules -> hospital lookup -> ML -> alerts.
    """
    validate_sensor_payload(payload)

    shipment_id = payload["shipmentId"]
    shipment = create_or_update_shipment(payload)
    reading = normalize_reading(payload)

    save_sensor_reading(shipment_id, reading)

    risk = calculate_risk(shipment, reading)
    nearest_hospital = find_nearest_hospital(reading["gps"]["lat"], reading["gps"]["lng"])
    ml_prediction = get_spoilage_prediction(shipment, reading)
    alerts = build_alerts(shipment, reading, risk, ml_prediction, nearest_hospital)

    update_shipment_result(shipment_id, risk, ml_prediction, nearest_hospital, alerts)

    return {
        "message": "Sensor data received",
        "shipmentId": shipment_id,
        "latestReading": reading,
        "risk": risk,
        "mlPrediction": ml_prediction,
        "nearestHospital": nearest_hospital,
        "alerts": alerts,
    }


def validate_sensor_payload(payload):
    for field in REQUIRED_FIELDS:
        if field not in payload:
            raise ValueError(f"Missing required field: {field}")

    if not isinstance(payload["temperature"], (int, float)):
        raise ValueError("temperature must be a number")

    if not isinstance(payload["batteryLevel"], (int, float)):
        raise ValueError("batteryLevel must be a number")

    if payload["batteryLevel"] < 0 or payload["batteryLevel"] > 100:
        raise ValueError("batteryLevel must be between 0 and 100")

    gps = payload["gps"]
    if not isinstance(gps, dict) or "lat" not in gps or "lng" not in gps:
        raise ValueError("gps must include lat and lng")


def normalize_reading(payload):
    return {
        "temperature": float(payload["temperature"]),
        "batteryLevel": float(payload["batteryLevel"]),
        "gps": {
            "lat": float(payload["gps"]["lat"]),
            "lng": float(payload["gps"]["lng"]),
        },
        "timestamp": payload["timestamp"],
        "source": payload.get("source"),
    }
