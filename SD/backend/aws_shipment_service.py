import os
from copy import deepcopy

try:
    import boto3
except ImportError:  # Keep local demo usable until boto3 is installed.
    boto3 = None

try:
    from .storage import ORGANIZATIONS, SHIPMENTS, shipment_monitor_record
except ImportError:
    from storage import ORGANIZATIONS, SHIPMENTS, shipment_monitor_record


ACTIVE_STATUSES = {"active", "in_transit", "at_risk", "delayed", "arrived"}


class ShipmentServiceError(RuntimeError):
    pass


def get_live_shipments_for_user(user):
    """Read live shipment rows from DynamoDB and scope them by role."""
    role = (user or {}).get("role")
    organization_id = (user or {}).get("organizationId")
    driver_id = (user or {}).get("driverId")

    if role not in ["hospital", "organization_user", "driver", "support"]:
        raise PermissionError("Shipment monitoring is not available for this role")

    records = read_shipments_from_dynamodb()
    if records is None:
        records = read_local_shipments()

    live_records = [normalize_live_shipment(record) for record in records]
    active_records = [
        record for record in live_records
        if str(record.get("shipmentStatus") or "").lower() in ACTIVE_STATUSES
        or str(record.get("alertLevel") or "").lower() in ["warning", "high", "critical"]
    ]

    if role in ["hospital", "organization_user"]:
        active_records = [record for record in active_records if record.get("hospitalId") == organization_id]
    if role == "driver":
        active_records = [record for record in active_records if record.get("driverId") == driver_id]

    return {
        "shipments": active_records,
        "alerts": [record for record in active_records if str(record.get("alertLevel") or "").lower() in ["warning", "high", "critical"]],
        "source": "aws-dynamodb" if has_aws_config() and boto3 else "local-fallback",
    }


def read_shipments_from_dynamodb():
    if not has_aws_config():
        return None
    if boto3 is None:
        raise ShipmentServiceError("boto3 is not installed. Install boto3 to read shipments from DynamoDB.")

    region = os.environ["AWS_REGION"]
    table_name = os.environ["SHIPMENTS_TABLE"]
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    response = table.scan()
    items = list(response.get("Items", []))

    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    return items


def has_aws_config():
    return bool(os.environ.get("AWS_REGION") and os.environ.get("SHIPMENTS_TABLE"))


def read_local_shipments():
    return [shipment_monitor_record(deepcopy(shipment)) for shipment in SHIPMENTS.values()]


def normalize_live_shipment(record):
    hospital_id = (
        record.get("hospitalId")
        or record.get("destinationHospitalId")
        or record.get("organizationId")
    )
    status = record.get("shipmentStatus") or record.get("status") or "in_transit"
    alert_level = record.get("alertLevel") or record.get("riskLevel") or "low"
    latitude = as_float(record.get("latitude") or (record.get("currentGps") or {}).get("lat"))
    longitude = as_float(record.get("longitude") or (record.get("currentGps") or {}).get("lng"))
    destination_gps = destination_coordinates(record, hospital_id)

    return {
        "shipmentId": record.get("shipmentId") or record.get("id"),
        "driverId": record.get("driverId"),
        "driverName": record.get("driverName"),
        "hospitalId": hospital_id,
        "hospitalName": record.get("hospitalName") or record.get("destinationHospitalName"),
        "origin": record.get("origin"),
        "destinationHospitalName": record.get("destinationHospitalName") or record.get("hospitalName"),
        "currentLocation": record.get("currentLocation") or "Location unavailable",
        "latitude": latitude,
        "longitude": longitude,
        "currentGps": {"lat": latitude, "lng": longitude} if latitude is not None and longitude is not None else None,
        "destinationLatitude": as_float(record.get("destinationLatitude") or destination_gps.get("lat")),
        "destinationLongitude": as_float(record.get("destinationLongitude") or destination_gps.get("lng")),
        "containerTemperature": as_float(record.get("containerTemperature") or record.get("temperature")),
        "temperature": as_float(record.get("containerTemperature") or record.get("temperature")),
        "safeTemperatureMin": as_float(record.get("safeTemperatureMin")),
        "safeTemperatureMax": as_float(record.get("safeTemperatureMax")),
        "coolingBatteryHealth": as_float(record.get("coolingBatteryHealth") or record.get("batteryLevel")),
        "batteryLevel": as_float(record.get("coolingBatteryHealth") or record.get("batteryLevel")),
        "sensorStatus": record.get("sensorStatus") or record.get("coolingUnitStatus"),
        "shipmentStatus": status,
        "status": status,
        "alertLevel": alert_level,
        "riskLevel": alert_level,
        "expectedArrival": record.get("expectedArrival"),
        "lastUpdated": record.get("lastUpdated") or record.get("updatedAt"),
    }


def destination_coordinates(record, hospital_id):
    explicit = {
        "lat": record.get("destinationLatitude") or (record.get("destinationGps") or {}).get("lat"),
        "lng": record.get("destinationLongitude") or (record.get("destinationGps") or {}).get("lng"),
    }
    if explicit.get("lat") is not None and explicit.get("lng") is not None:
        return explicit
    return (ORGANIZATIONS.get(hospital_id) or {}).get("gps") or {}


def as_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
