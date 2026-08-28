from copy import deepcopy
from datetime import datetime, timedelta, timezone
from secrets import randbelow
from urllib.parse import urlencode
from uuid import uuid4


# In-memory storage is seeded with role-specific demo records so the UI clearly
# shows private vs. shared data. These dictionaries can later be replaced by AWS
# DynamoDB, RDS, IoT Core, API Gateway payloads, or another persistence layer.
SHIPMENTS = {
    "ship-a-100": {
        "shipmentId": "ship-a-100",
        "id": "ship-a-100",
        "origin": "Central Cold Storage",
        "destinationHospitalId": "hospital-a",
        "destinationHospitalName": "Hospital A",
        "medicineType": "Insulin",
        "safeTemperatureMin": 2,
        "safeTemperatureMax": 8,
        "destination": "Hospital A",
        "originFacilityId": "facility-a-central",
        "destinationFacilityId": "facility-a-receiving",
        "currentLocation": "Central Cold Storage",
        "routeProgress": 0,
        "status": "pending",
        "temperature": 5.0,
        "batteryLevel": 95,
        "coolingUnitStatus": "normal",
        "departureAt": "2026-07-15T10:30:00Z",
        "expectedArrival": "2026-07-15T11:15:00Z",
        "lastUpdated": "2026-07-15T10:30:00Z",
        "riskLevel": "low",
        "riskClassification": "safe",
        "supplies": ["Insulin - 10 boxes"],
        "timeline": [{"timestamp": "2026-07-15T10:30:00Z", "label": "Delivery request sent to Aya Mansour"}],
        "originGps": {"lat": 33.8797, "lng": 35.5018},
        "destinationGps": {"lat": 33.8938, "lng": 35.5018},
        "organizationId": "hospital-a",
        "driverId": "driver-aya",
        "vehicleId": "van-12",
        "containerId": "container-12",
        "sensorId": "sensor-cold-12",
        "productCategory": "Pharmaceuticals",
        "latestReading": {"temperature": 5.0, "batteryLevel": 95, "gps": {"lat": 33.8797, "lng": 35.5018}, "timestamp": "2026-07-15T10:30:00Z", "source": "demo_seed"},
        "readings": [],
        "risk": {"score": 0, "level": "low", "reasons": ["Shipment is currently within safe rule limits"]},
        "mlPrediction": None,
        "nearestHospital": None,
        "alerts": [],
    },
    "ship-b-220": {
        "shipmentId": "ship-b-220",
        "id": "ship-b-220",
        "origin": "Regional Supply Hub",
        "destinationHospitalId": "hospital-b",
        "destinationHospitalName": "Hospital B",
        "medicineType": "Syringes",
        "safeTemperatureMin": 15,
        "safeTemperatureMax": 25,
        "destination": "Hospital B",
        "currentLocation": "Mount Lebanon route - 6 km from destination",
        "routeProgress": 82,
        "status": "in_transit",
        "temperature": 22.1,
        "batteryLevel": 81,
        "coolingUnitStatus": "normal",
        "lastUpdated": "2026-07-09T09:05:00Z",
        "riskLevel": "low",
        "supplies": ["Syringes - 12 packs"],
        "timeline": [
            {"timestamp": "2026-07-09T08:15:00Z", "label": "Shipment loaded"},
            {"timestamp": "2026-07-09T09:05:00Z", "label": "On route, telemetry normal"},
        ],
        "destinationGps": None,
        "organizationId": "hospital-b",
        "driverId": "driver-samir",
        "vehicleId": "van-22",
        "containerId": "container-22",
        "sensorId": "sensor-cold-22",
        "productCategory": "Medical supplies",
        "latestReading": {"temperature": 22.1, "batteryLevel": 81, "gps": {"lat": 33.86, "lng": 35.52}, "timestamp": "2026-07-09T09:05:00Z"},
        "readings": [],
        "risk": {"score": 0, "level": "low", "reasons": ["Shipment is currently within safe rule limits"]},
        "mlPrediction": None,
        "nearestHospital": None,
        "alerts": [],
    },
    "ship-a-145": {
        "shipmentId": "ship-a-145",
        "id": "ship-a-145",
        "origin": "Blood Bank North",
        "destinationHospitalId": "hospital-a",
        "destinationHospitalName": "Hospital A",
        "medicineType": "O negative blood",
        "safeTemperatureMin": 2,
        "safeTemperatureMax": 6,
        "destination": "Hospital A",
        "currentLocation": "Hospital A loading bay",
        "routeProgress": 100,
        "status": "delivered",
        "temperature": 4.1,
        "batteryLevel": 63,
        "coolingUnitStatus": "normal",
        "lastUpdated": "2026-07-09T09:20:00Z",
        "destinationGps": None,
        "organizationId": "hospital-a",
        "driverId": "driver-aya",
        "vehicleId": "van-12",
        "containerId": "container-12",
        "sensorId": "sensor-cold-12",
        "productCategory": "Blood products",
        "latestReading": {"temperature": 4.1, "batteryLevel": 63, "gps": {"lat": 33.8938, "lng": 35.5018}, "timestamp": "2026-07-09T09:20:00Z"},
        "readings": [],
        "risk": {"score": 0, "level": "low", "reasons": ["Shipment arrived within safe range"]},
        "riskLevel": "low",
        "mlPrediction": None,
        "nearestHospital": None,
        "alerts": [],
        "supplies": ["O negative blood - 6 units"],
        "timeline": [
            {"timestamp": "2026-07-09T08:10:00Z", "label": "Departed Blood Bank North"},
            {"timestamp": "2026-07-09T09:20:00Z", "label": "Arrived at Hospital A"},
        ],
    },
    "ship-a-190": {
        "shipmentId": "ship-a-190",
        "id": "ship-a-190",
        "origin": "Bekaa Fresh Distribution",
        "destinationHospitalId": "hospital-a",
        "destinationHospitalName": "Hospital A",
        "destination": "Hospital A",
        "organizationId": "hospital-a",
        "productCategory": "Fresh food",
        "medicineType": "Fresh dairy",
        "safeTemperatureMin": 1,
        "safeTemperatureMax": 4,
        "currentLocation": "Beirut east corridor - service area",
        "routeProgress": 44,
        "status": "at_risk",
        "temperature": 11.6,
        "batteryLevel": 12,
        "coolingUnitStatus": "critical",
        "lastUpdated": "2026-07-15T09:35:00Z",
        "riskLevel": "critical",
        "driverId": "driver-aya",
        "vehicleId": "van-12",
        "containerId": "container-12",
        "sensorId": "sensor-cold-12",
        "latestReading": {"temperature": 11.6, "batteryLevel": 12, "gps": {"lat": 33.875, "lng": 35.535}, "timestamp": "2026-07-15T09:35:00Z"},
        "readings": [],
        "risk": {"score": 90, "level": "critical", "reasons": ["Temperature is above the product storage range", "Cooling battery is critically low"]},
        "mlPrediction": {"riskLevel": "critical", "confidence": 0.91},
        "nearestHospital": None,
        "alerts": [{"severity": "critical", "type": "temperature-excursion", "message": "Fresh dairy has exceeded its safe storage range"}],
        "supplies": ["Fresh dairy - 48 crates"],
        "timeline": [
            {"timestamp": "2026-07-15T08:50:00Z", "label": "Pickup confirmed"},
            {"timestamp": "2026-07-15T09:35:00Z", "label": "Critical temperature excursion detected"},
        ],
    },
    "ship-a-175": {
        "shipmentId": "ship-a-175", "id": "ship-a-175", "organizationId": "hospital-a",
        "origin": "Central Cold Storage", "destination": "Hospital A", "destinationHospitalId": "hospital-a",
        "destinationHospitalName": "Hospital A", "medicineType": "Laboratory samples", "productName": "Diagnostic samples",
        "productCategory": "Laboratory samples", "quantity": 18, "unit": "cases", "estimatedValue": 9200,
        "safeTemperatureMin": 2, "safeTemperatureMax": 8, "status": "awaiting_verification", "riskLevel": "low",
        "temperature": 4.5, "batteryLevel": 71, "coolingUnitStatus": "normal", "routeProgress": 100,
        "currentLocation": "Hospital A receiving bay", "driverId": "driver-aya", "vehicleId": "van-12",
        "containerId": "container-12", "sensorId": "sensor-cold-12", "expectedArrival": "2026-07-15T10:15:00Z",
        "arrivalTime": "2026-07-15T10:09:00Z", "receiverName": "Maya Nasser", "deliveryNotes": "Seal intact on arrival.",
        "latestReading": {"temperature": 4.5, "batteryLevel": 71, "gps": {"lat": 33.8938, "lng": 35.5018}, "timestamp": "2026-07-15T10:09:00Z"},
        "readings": [{"temperature": 4.2, "batteryLevel": 78, "timestamp": "2026-07-15T09:30:00Z"}, {"temperature": 4.5, "batteryLevel": 71, "timestamp": "2026-07-15T10:09:00Z"}],
        "risk": {"score": 0, "level": "low", "reasons": ["Temperature remained within the required range"]},
        "alerts": [], "driverActions": ["Confirmed receiver identity", "Recorded delivery note"],
        "timeline": [{"timestamp": "2026-07-15T09:20:00Z", "label": "Shipment departed"}, {"timestamp": "2026-07-15T10:09:00Z", "label": "Delivery submitted for organization verification"}],
    },
    "ship-a-205": {
        "shipmentId": "ship-a-205", "id": "ship-a-205", "organizationId": "hospital-a",
        "origin": "Central Cold Storage", "destination": "Hospital A Receiving", "destinationHospitalName": "Hospital A Receiving",
        "destinationHospitalId": "hospital-a", "medicineType": "Temperature-sensitive vaccines", "productName": "Routine vaccines",
        "productCategory": "Vaccines", "safeTemperatureMin": 2, "safeTemperatureMax": 8, "status": "planned", "riskLevel": "low",
        "currentLocation": "Central Cold Storage", "routeProgress": 0, "driverId": "driver-aya", "vehicleId": "van-12",
        "containerId": "container-12", "sensorId": "sensor-cold-12", "expectedArrival": "2026-07-16T09:30:00Z",
        "departureAt": "2026-07-16T08:15:00Z", "deliveryInstructions": "Keep the container closed and hand it directly to receiving staff.",
        "handlingNotes": "Protect from direct light. Do not freeze.", "latestReading": {"temperature": 4.2, "batteryLevel": 88, "timestamp": "2026-07-15T10:20:00Z"},
        "temperature": 4.2, "batteryLevel": 88, "coolingUnitStatus": "normal", "alerts": [], "readings": [],
        "risk": {"score": 0, "level": "low", "reasons": ["Shipment is ready for pickup"]},
        "timeline": [{"timestamp": "2026-07-15T10:20:00Z", "label": "Assigned to Aya Mansour"}],
    },
}

# Start the focused local demo with one shipment linked to the v2 prototype.
# The Organization creates additional delivery requests through the normal workflow.
SHIPMENTS = {
    "ship-a-v2-001": {
        "shipmentId": "ship-a-v2-001",
        "id": "ship-a-v2-001",
        "lotTripId": "lot-trip-sim-001",
        "organizationId": "hospital-a",
        "origin": "Beirut Distribution Center",
        "destination": "AUB Medical Center",
        "destinationHospitalId": "hospital-a",
        "destinationHospitalName": "AUB Medical Center",
        "productName": "GARDASIL 9",
        "medicineType": "GARDASIL 9",
        "productCategory": "Vaccines",
        "quantity": 24,
        "unit": "doses",
        "safeTemperatureMin": 2,
        "safeTemperatureMax": 8,
        "status": "in_transit",
        "riskLevel": "low",
        "riskClassification": "safe",
        "currentLocation": "Beirut Distribution Center",
        "routeProgress": 15,
        "driverId": "driver-aya",
        "vehicleId": "van-12",
        "containerId": "container-v2-001",
        "sensorId": "device-sim-001",
        "tripId": "trip-sim-001",
        "v2DeviceAssignmentId": "assignment-sim-001",
        "tripStatus": "ACTIVE",
        "destinationVerificationCode": "246810",
        "destinationVerificationStatus": "pending",
        "temperature": None,
        "batteryLevel": None,
        "coolingUnitStatus": "normal",
        "lastUpdated": "2026-08-19T00:00:00Z",
        "departureAt": "2026-08-19T00:00:00Z",
        "expectedArrival": "2026-08-19T22:00:00Z",
        "readings": [],
        "alerts": [],
        "risk": {
            "score": 0,
            "level": "low",
            "reasons": ["Awaiting authoritative v2 telemetry"],
        },
        "timeline": [
            {
                "timestamp": "2026-08-19T00:00:00Z",
                "label": "Prototype shipment entered transit",
            }
        ],
    }
}

ORGANIZATIONS = {
    "hospital-a": {
        "organizationId": "hospital-a",
        "name": "Hospital A",
        "type": "hospital",
        "gps": {"lat": 33.8938, "lng": 35.5018},
        "status": "online",
        "contact": "ops-a@example.org",
        "region": "Beirut",
    },
    "hospital-b": {
        "organizationId": "hospital-b",
        "name": "Hospital B",
        "type": "hospital",
        "gps": {"lat": 33.8710, "lng": 35.5300},
        "status": "online",
        "contact": "ops-b@example.org",
        "region": "Mount Lebanon",
    },
    "warehouse-north": {
        "organizationId": "warehouse-north",
        "name": "NorthLine Refrigerated Warehouse",
        "type": "refrigerated_warehouse",
        "gps": {"lat": 34.122, "lng": 35.651},
        "status": "online",
        "contact": "operations@northline.example",
        "region": "North Lebanon",
    },
}
VANS = {
    "van-12": {
        "vanId": "van-12",
        "driver": "Central Dispatch",
        "status": "on-road",
        "gps": {"lat": 33.88, "lng": 35.51},
        "route": {"from": "Hospital B", "to": "Hospital A"},
        "payload": ["Syringes"],
        "latestTemperature": 21.4,
        "batteryLevel": 86,
        "etaMinutes": 28,
    }
}
ORIGIN_LOCATIONS = {
    "Central Cold Storage": {"lat": 33.8797, "lng": 35.5018},
    "Regional Supply Hub": {"lat": 33.8332, "lng": 35.5466},
    "Blood Bank North": {"lat": 33.9142, "lng": 35.5894},
}
AWS_EVENTS = [
    {
        "source": "AWS IoT Core",
        "eventType": "alert",
        "timestamp": "2026-07-09T09:10:00Z",
        "payload": {"name": "Cold-chain alert on shipment ship-a-100"},
    }
]
INVENTORY_ITEMS = {
    "inv-a-private": {
        "inventoryId": "inv-a-private",
        "organizationId": "hospital-a",
        "itemName": "Ventilator filters",
        "category": "Equipment",
        "quantity": 14,
        "unit": "packs",
        "status": "available",
        "expiryDate": "2026-12-15",
        "storageLocation": "ICU reserve cabinet A",
        "minThreshold": 8,
        "shareLevel": "private",
        "shareExactQuantity": False,
        "publicNote": None,
        "internalNotes": "ICU reserve cabinet A. Do not publish.",
        "updatedAt": "2026-07-09T08:00:00Z",
    },
    "inv-a-shared": {
        "inventoryId": "inv-a-shared",
        "organizationId": "hospital-a",
        "itemName": "Insulin",
        "category": "Medicine",
        "quantity": 64,
        "unit": "boxes",
        "status": "surplus",
        "expiryDate": "2026-08-04",
        "storageLocation": "Cold room 1",
        "minThreshold": 20,
        "shareLevel": "support",
        "shareExactQuantity": False,
        "publicNote": "Available for support-coordinated transfer.",
        "internalNotes": "Exact stock in cold room 1 is private.",
        "updatedAt": "2026-07-09T08:15:00Z",
    },
    "inv-b-private": {
        "inventoryId": "inv-b-private",
        "organizationId": "hospital-b",
        "itemName": "N95 masks",
        "category": "PPE",
        "quantity": 320,
        "unit": "boxes",
        "status": "available",
        "expiryDate": "2027-01-20",
        "storageLocation": "ER storage",
        "minThreshold": 100,
        "shareLevel": "private",
        "shareExactQuantity": False,
        "publicNote": None,
        "internalNotes": "Reserved for ER surge plan.",
        "updatedAt": "2026-07-09T08:20:00Z",
    },
    "inv-b-shared": {
        "inventoryId": "inv-b-shared",
        "organizationId": "hospital-b",
        "itemName": "Syringes",
        "category": "Consumables",
        "quantity": 45,
        "unit": "packs",
        "status": "surplus",
        "expiryDate": "2026-11-10",
        "storageLocation": "Main supply room",
        "minThreshold": 15,
        "shareLevel": "network",
        "shareExactQuantity": True,
        "publicNote": "Ready for same-day pickup.",
        "internalNotes": "Loading dock pickup instructions are private.",
        "updatedAt": "2026-07-09T08:30:00Z",
    },
}
SUPPLY_REQUESTS = {
    "req-a-shared": {
        "requestId": "req-a-shared",
        "organizationId": "hospital-a",
        "itemName": "Syringes",
        "category": "Consumables",
        "quantityNeeded": 18,
        "unit": "packs",
        "urgency": "high",
        "status": "pending",
        "shareLevel": "network",
        "shareExactQuantity": False,
        "publicNote": "Needed for outpatient vaccination clinic.",
        "internalNotes": "Clinic opens at 07:00 tomorrow.",
        "updatedAt": "2026-07-09T08:40:00Z",
    },
    "req-a-private": {
        "requestId": "req-a-private",
        "organizationId": "hospital-a",
        "itemName": "O negative blood",
        "category": "Blood bank",
        "quantityNeeded": 6,
        "unit": "units",
        "urgency": "critical",
        "status": "pending",
        "shareLevel": "emergency",
        "shareExactQuantity": False,
        "publicNote": None,
        "internalNotes": "Private trauma case details.",
        "updatedAt": "2026-07-09T08:42:00Z",
    },
    "req-b-shared": {
        "requestId": "req-b-shared",
        "organizationId": "hospital-b",
        "itemName": "Insulin",
        "category": "Medicine",
        "quantityNeeded": 25,
        "unit": "boxes",
        "urgency": "critical",
        "status": "matched",
        "shareLevel": "network",
        "shareExactQuantity": False,
        "publicNote": "Cold-chain transfer required.",
        "internalNotes": "Exact ward allocation is private.",
        "updatedAt": "2026-07-09T08:50:00Z",
    },
    "req-b-private": {
        "requestId": "req-b-private",
        "organizationId": "hospital-b",
        "itemName": "Portable oxygen concentrator",
        "category": "Equipment",
        "quantityNeeded": 2,
        "unit": "devices",
        "urgency": "medium",
        "status": "pending",
        "shareLevel": "private",
        "shareExactQuantity": False,
        "publicNote": None,
        "internalNotes": "Home-care discharge plan.",
        "updatedAt": "2026-07-09T08:55:00Z",
    },
}

USERS = {
    "admin-token": {
        "userId": "central-admin",
        "username": "admin",
        "email": "admin@vitae.local",
        "password": "admin123",
        "name": "Platform Admin",
        "role": "admin",
        "organizationId": None,
        "permissions": ["monitor", "manage_hospitals", "manage_users", "coordinate_requests"],
    },
    "support-token": {
        "userId": "central-support",
        "username": "support",
        "email": "support@vitae.local",
        "password": "support123",
        "name": "Monitoring Center Support",
        "role": "support",
        "organizationId": None,
        "permissions": ["monitor", "coordinate_requests"],
    },
    "organization-token": {
        "userId": "organization-operations-user",
        "username": "organization",
        "email": "organization@vitae.local",
        "password": "organization123",
        "name": "Organization Operations",
        "role": "organization_user",
        "organizationId": "hospital-a",
        "permissions": ["manage_shipments", "monitor_shipments", "manage_drivers", "request_support"],
    },
    "driver-token": {
        "userId": "driver-aya-user",
        "username": "driver",
        "email": "driver@vitae.local",
        "password": "driver123",
        "name": "Aya Mansour",
        "role": "driver",
        "organizationId": "hospital-a",
        "driverId": "driver-aya",
        "permissions": ["view_assigned_deliveries", "report_incident", "complete_delivery", "request_support"],
    },
    "driver-rami-token": {
        "userId": "driver-rami-user",
        "username": "driver.rami",
        "email": "rami.driver@vitae.local",
        "password": "driver123",
        "name": "Rami Haddad",
        "role": "driver",
        "organizationId": "hospital-a",
        "driverId": "driver-rami",
        "permissions": ["view_assigned_deliveries", "report_incident", "complete_delivery", "request_support"],
    },
    "hospital-a-token": {
        "userId": "hospital-a-manager",
        "username": "hospitalA",
        "email": "hospital-a@vitae.local",
        "password": "hospitalA123",
        "name": "Hospital A Manager",
        "role": "hospital",
        "organizationId": "hospital-a",
        "permissions": ["manage_inventory", "manage_requests", "manage_transfers", "manage_staff"],
    },
    "hospital-b-token": {
        "userId": "hospital-b-manager",
        "username": "hospitalB",
        "email": "hospital-b@vitae.local",
        "password": "hospitalB123",
        "name": "Hospital B Manager",
        "role": "hospital",
        "organizationId": "hospital-b",
        "permissions": ["manage_inventory", "manage_requests", "manage_transfers", "manage_staff"],
    },
}

TRANSFERS = {
    "tr-a-in-1": {
        "transferId": "tr-a-in-1",
        "fromOrganizationId": "hospital-b",
        "toOrganizationId": "hospital-a",
        "items": ["Syringes"],
        "quantity": 12,
        "status": "in_transit",
        "eta": "Today 15:30",
        "timeline": ["Pending", "Matched", "Approved", "In Transit"],
    },
    "tr-a-out-1": {
        "transferId": "tr-a-out-1",
        "fromOrganizationId": "hospital-a",
        "toOrganizationId": "hospital-b",
        "items": ["Insulin"],
        "quantity": 10,
        "status": "approved",
        "eta": "Tomorrow 09:00",
        "timeline": ["Pending", "Matched", "Approved"],
    },
}

NOTIFICATIONS = {
    "hospital-a": [
        {"title": "Transfer arriving", "detail": "Syringes from Hospital B arrive today.", "type": "transfer", "unread": True},
        {"title": "Low inventory", "detail": "O negative blood is marked critical.", "type": "inventory", "unread": True},
        {"title": "New hospital match", "detail": "Hospital B has shared syringes.", "type": "match", "unread": False},
    ],
    "hospital-b": [
        {"title": "Emergency broadcast", "detail": "Cold-chain transfer required for insulin.", "type": "emergency", "unread": True},
        {"title": "Transfer approved", "detail": "Outgoing syringes transfer approved.", "type": "transfer", "unread": False},
    ],
}

STAFF = {
    "hospital-a": [
        {"name": "Rana Haddad", "role": "Hospital Manager", "status": "active"},
        {"name": "Omar Karim", "role": "Inventory Staff", "status": "active"},
        {"name": "Maya Nasser", "role": "Emergency Coordinator", "status": "active"},
    ],
    "hospital-b": [
        {"name": "Lina Mansour", "role": "Hospital Manager", "status": "active"},
        {"name": "Tarek Saad", "role": "Pharmacy Staff", "status": "active"},
    ],
}

DRIVERS = {
    "driver-aya": {
        "driverId": "driver-aya",
        "organizationId": "hospital-a",
        "name": "Aya Mansour",
        "phone": "+961 70 555 014",
        "status": "assigned",
        "vehicleId": "van-12",
    },
    "driver-samir": {
        "driverId": "driver-samir",
        "organizationId": "hospital-b",
        "name": "Samir Khalil",
        "phone": "+961 71 555 022",
        "status": "available",
        "vehicleId": "van-22",
    },
    "driver-rami": {
        "driverId": "driver-rami", "organizationId": "hospital-a", "name": "Rami Haddad",
        "username": "rami.haddad", "phone": "+961 76 555 031", "status": "available", "vehicleId": "van-18",
    },
}

DRIVERS = {"driver-aya": DRIVERS["driver-aya"]}
DRIVERS["driver-aya"]["status"] = "available"

FACILITY_CAPABILITY_PROFILES = {
    "demo-product-receiving-v1": {
        "profileId": "demo-product-receiving-v1",
        "supportedProductIds": ["gardasil-9"],
        "evidenceKind": "ENGINEERING_DEMO_METADATA",
    },
}

FACILITIES = {
    "facility-a-central": {"facilityId": "facility-a-central", "organizationId": "hospital-a", "name": "Central Cold Storage", "type": "warehouse", "gps": {"lat": 33.8797, "lng": 35.5018}},
    "facility-a-receiving": {"facilityId": "facility-a-receiving", "organizationId": "hospital-a", "name": "Hospital A Receiving", "type": "receiving", "gps": {"lat": 33.8938, "lng": 35.5018}, "capabilityProfileId": "demo-product-receiving-v1"},
    "facility-b-hub": {"facilityId": "facility-b-hub", "organizationId": "hospital-b", "name": "Regional Supply Hub", "type": "warehouse", "gps": {"lat": 33.8332, "lng": 35.5466}},
    "facility-b-receiving": {"facilityId": "facility-b-receiving", "organizationId": "hospital-b", "name": "Hospital B Receiving", "type": "receiving", "gps": {"lat": 33.8710, "lng": 35.5300}, "capabilityProfileId": "demo-product-receiving-v1"},
    "facility-north": {"facilityId": "facility-north", "organizationId": "warehouse-north", "name": "NorthLine Main Facility", "type": "warehouse", "gps": {"lat": 34.122, "lng": 35.651}},
}

VEHICLES = {
    "van-12": {"vehicleId": "van-12", "organizationId": "hospital-a", "name": "Refrigerated Van 12", "type": "vehicle", "status": "available"},
    "van-18": {"vehicleId": "van-18", "organizationId": "hospital-a", "name": "Refrigerated Van 18", "type": "vehicle", "status": "available"},
    "van-22": {"vehicleId": "van-22", "organizationId": "hospital-b", "name": "Refrigerated Van 22", "type": "vehicle", "status": "available"},
}

VEHICLES = {"van-12": VEHICLES["van-12"]}

SENSORS = {
    "sensor-cold-12": {
        "sensorId": "sensor-cold-12",
        "organizationId": "hospital-a",
        "containerId": "container-12",
        "status": "healthy",
        "connectionStatus": "online",
        "batteryLevel": 95,
        "lastSeen": "2026-07-15T10:30:00Z",
        "shipmentId": "ship-a-100",
    },
    "sensor-cold-22": {
        "sensorId": "sensor-cold-22",
        "organizationId": "hospital-b",
        "containerId": "container-22",
        "status": "healthy",
        "batteryLevel": 81,
        "lastSeen": "2026-07-15T09:33:00Z",
    },
    "sensor-freezer-07": {
        "sensorId": "sensor-freezer-07",
        "organizationId": "warehouse-north",
        "containerId": "container-07",
        "status": "offline",
        "batteryLevel": 64,
        "lastSeen": "2026-07-15T07:10:00Z",
    },
}

SENSORS = {"sensor-cold-12": SENSORS["sensor-cold-12"]}
SENSORS["sensor-cold-12"].pop("shipmentId", None)

SUPPORT_TICKETS = {
    "ticket-admin-200": {
        "ticketId": "ticket-admin-200",
        "organizationId": None,
        "shipmentId": None,
        "sourceType": "admin",
        "subject": "Coordinate platform sensor investigation",
        "category": "admin_request",
        "priority": "high",
        "status": "new",
        "assignedTo": "central-support",
        "requester": "VITAE Platform Admin",
        "createdAt": "2026-07-15T09:42:00Z",
        "updatedAt": "2026-07-15T09:42:00Z",
        "summary": "Admin requested Support to coordinate diagnostics for an intermittent warehouse sensor outage.",
    },
    "ticket-1042": {
        "ticketId": "ticket-1042",
        "organizationId": "hospital-a",
        "shipmentId": "ship-a-190",
        "subject": "Critical temperature excursion",
        "category": "shipment_risk",
        "priority": "critical",
        "status": "new",
        "assignedTo": "central-support",
        "requester": "Organization Operations",
        "createdAt": "2026-07-15T09:35:00Z",
        "updatedAt": "2026-07-15T09:38:00Z",
        "summary": "Temperature remains above range and the cooling battery is low.",
    },
    "ticket-1038": {
        "ticketId": "ticket-1038",
        "organizationId": "hospital-b",
        "shipmentId": "ship-b-220",
        "subject": "Confirm revised delivery window",
        "category": "delivery",
        "priority": "medium",
        "status": "in_progress",
        "assignedTo": "central-support",
        "requester": "Hospital B Manager",
        "createdAt": "2026-07-15T08:25:00Z",
        "updatedAt": "2026-07-15T09:05:00Z",
        "summary": "Receiver requested confirmation of the updated arrival time.",
    },
    "ticket-1021": {
        "ticketId": "ticket-1021",
        "organizationId": "warehouse-north",
        "shipmentId": None,
        "subject": "Sensor stopped reporting",
        "category": "device",
        "priority": "high",
        "status": "waiting_for_response",
        "assignedTo": "central-support",
        "requester": "NorthLine Operations",
        "createdAt": "2026-07-15T07:55:00Z",
        "updatedAt": "2026-07-15T08:22:00Z",
        "summary": "Warehouse team is checking power to sensor-freezer-07.",
    },
    "ticket-0994": {
        "ticketId": "ticket-0994",
        "organizationId": "hospital-a",
        "shipmentId": "ship-a-145",
        "subject": "Delivery confirmation received",
        "category": "delivery",
        "priority": "low",
        "status": "resolved",
        "assignedTo": "central-support",
        "requester": "Hospital A Manager",
        "createdAt": "2026-07-14T12:30:00Z",
        "resolvedAt": "2026-07-14T16:45:00Z",
        "resolutionSummary": "Delivery evidence was confirmed with the receiving team.",
        "updatedAt": "2026-07-14T16:45:00Z",
        "summary": "Proof of delivery was verified and the ticket was closed.",
    },
    "ticket-driver-010": {
        "ticketId": "ticket-driver-010", "organizationId": "hospital-a", "shipmentId": "ship-a-100", "driverId": "driver-aya",
        "subject": "Cooling unit guidance", "category": "cooling_problem", "priority": "medium", "status": "in_progress",
        "assignedTo": "central-support", "requester": "Aya Mansour", "updatedAt": "2026-07-15T09:05:00Z",
        "createdAt": "2026-07-15T08:58:00Z",
        "summary": "Driver requested guidance after a temperature warning.",
        "messages": [
            {"author": "Aya Mansour", "timestamp": "2026-07-15T08:58:00Z", "body": "The temperature warning remains visible after checking the container.", "internal": False},
            {"author": "Monitoring Center Support", "timestamp": "2026-07-15T09:05:00Z", "body": "Keep the container closed and confirm that cooling power is active. Contact operations if the warning continues.", "internal": False},
        ],
    },
}

# Tickets and alerts are intentionally empty at startup. They are created by
# real Organization/Driver actions or by telemetry risk during the demo.
SUPPORT_TICKETS = {}

ORGANIZATION_ALERTS = {
    "alert-a-190": {"alertId": "alert-a-190", "organizationId": "hospital-a", "shipmentId": "ship-a-190", "severity": "critical", "type": "temperature excursion", "detectedAt": "2026-07-15T09:35:00Z", "explanation": "Fresh dairy exceeded its safe storage range while cooling power was low.", "recommendedAction": "Contact the driver and restore cooling immediately.", "status": "new", "driverResponse": "No response recorded", "updatedAt": "2026-07-15T09:35:00Z"},
    "alert-a-100": {"alertId": "alert-a-100", "organizationId": "hospital-a", "shipmentId": "ship-a-100", "severity": "high", "type": "temperature warning", "detectedAt": "2026-07-09T08:45:00Z", "explanation": "Temperature is above the required range.", "recommendedAction": "Confirm cooling operation with the assigned driver.", "status": "acknowledged", "driverResponse": "Driver is checking the cooling unit.", "updatedAt": "2026-07-09T08:50:00Z"},
}

ORGANIZATION_ALERTS = {}

DRIVER_INCIDENTS = {}

PLATFORM_SETTINGS = {
    "displayName": "VITAE",
    "temperatureWarningMargin": 1.0,
    "lowBatteryThreshold": 30,
    "criticalBatteryThreshold": 15,
    "notifyCriticalAlerts": True,
    "notifyOfflineSensors": True,
}


def get_all_shipments():
    return deepcopy(list(SHIPMENTS.values()))


def get_shipment_by_id(shipment_id):
    shipment = SHIPMENTS.get(shipment_id)
    return deepcopy(shipment) if shipment else None


def get_dashboard_data():
    """Returns one API-shaped object for the frontend dashboard.

    The frontend should not invent numbers. It reads this object and renders
    whatever real records were submitted by hospitals, NGOs, vans, sensors, or
    AWS services.
    """
    return {
        "shipments": get_all_shipments(),
        "organizations": deepcopy(list(ORGANIZATIONS.values())),
        "vans": deepcopy(list(VANS.values())),
        "awsEvents": deepcopy(AWS_EVENTS),
    }


def get_user_by_token(token):
    user = USERS.get(token)
    return deepcopy(user) if user and user.get("accountStatus", "active") == "active" else None


def authenticate_user(username, password):
    normalized_username = str(username or "").strip().lower()
    for token, user in USERS.items():
        allowed_names = [user.get("username"), user.get("email")]
        if normalized_username in [str(name or "").lower() for name in allowed_names] and user.get("password") == password and user.get("accountStatus", "active") == "active":
            return token, deepcopy(user)
    return None, None


def is_admin_user(user):
    return bool(user and user.get("role") == "admin")


def normalized_role(user_or_role):
    role = user_or_role.get("role") if isinstance(user_or_role, dict) else user_or_role
    return "organization_user" if role == "hospital" else role


def is_organization_user(user):
    organization = ORGANIZATIONS.get(user.get("organizationId")) if user else None
    return bool(user and normalized_role(user) == "organization_user" and organization and organization.get("accountStatus", "active") == "active")


def is_driver_user(user):
    if not user or normalized_role(user) != "driver" or not user.get("driverId"):
        return False
    driver = DRIVERS.get(user.get("driverId"))
    organization = ORGANIZATIONS.get(user.get("organizationId"))
    return bool(driver and organization and driver.get("organizationId") == user.get("organizationId") and organization.get("accountStatus", "active") == "active")


def is_central_user(user):
    return bool(user and user.get("role") in ["admin", "support"])


def get_user_profile(user):
    profile = sanitize_user(user)
    profile["normalizedRole"] = normalized_role(user)
    organization_id = user.get("organizationId")
    profile["organization"] = public_organization(ORGANIZATIONS.get(organization_id)) if organization_id else None
    return profile


def get_admin_foundation_dashboard_data(telemetry_state_repository=None):
    shipments = [
        admin_shipment_record(shipment, telemetry_state_repository)
        for shipment in SHIPMENTS.values()
    ]
    sensors = [admin_sensor_record(sensor) for sensor in SENSORS.values()]
    tickets = [admin_ticket_record(ticket) for ticket in SUPPORT_TICKETS.values()]
    organizations = [admin_organization_record(organization) for organization in ORGANIZATIONS.values()]
    users = [admin_user_record(token, user) for token, user in USERS.items()]
    alerts = admin_alert_records()
    reports = admin_report_data(shipments, sensors, tickets, organizations)
    return {
        "scope": "admin",
        "summary": {
            "organizations": len(organizations),
            "activeShipments": len([item for item in shipments if item.get("status") in ["active", "in_transit", "at_risk", "delayed", "arrived"]]),
            "safeShipments": len([item for item in shipments if item.get("riskLevel") == "low"]),
            "atRiskShipments": len([item for item in shipments if item.get("riskLevel") == "high"]),
            "criticalShipments": len([item for item in shipments if item.get("riskLevel") == "critical"]),
            "onlineSensors": len([item for item in sensors if item.get("connectionStatus") == "online"]),
            "offlineSensors": len([item for item in sensors if item.get("connectionStatus") == "offline"]),
            "openTickets": len([item for item in tickets if item.get("status") != "resolved"]),
            "protectedShipments": reports["protectedShipments"],
            "estimatedValueProtected": reports["estimatedValueProtected"],
        },
        "shipmentStatus": status_counts(shipments, "status"),
        "criticalIncidents": [item for item in shipments if item.get("riskLevel") in ["critical", "high"]],
        "deviceHealth": {
            "healthy": len([item for item in sensors if item.get("deviceStatus") == "healthy"]),
            "lowBattery": len([item for item in sensors if item.get("batteryCondition") == "low"]),
            "offline": len([item for item in sensors if item.get("connectionStatus") == "offline"]),
            "sensors": sensors,
        },
        "recentOrganizations": sorted(organizations, key=lambda item: item.get("createdAt") or "", reverse=True)[:4],
        "latestTickets": sorted(tickets, key=lambda item: item.get("updatedAt") or "", reverse=True)[:5],
        "organizations": organizations,
        "users": users,
        "shipments": shipments,
        "devices": sensors,
        "alerts": alerts,
        "tickets": tickets,
        "reports": reports,
        "settings": deepcopy(PLATFORM_SETTINGS),
    }


def get_organization_foundation_dashboard_data(
    organization_id, telemetry_state_repository=None
):
    shipments = get_organization_shipments(
        organization_id, telemetry_state_repository
    )
    active = [item for item in shipments if item.get("status") in {"active", "in_transit", "at_risk", "delayed", "arrived"}]
    alerts = get_organization_alerts(organization_id)
    drivers = get_organization_drivers(organization_id)
    reports = get_organization_reports(organization_id)
    return {
        "scope": "organization",
        "organization": hospital_own_organization(organization_id),
        "summary": {
            "activeShipments": len(active),
            "incomingShipments": len([item for item in active if item.get("status") in ["in_transit", "active", "at_risk"]]),
            "completedShipments": reports["completedShipments"],
            "atRiskShipments": len([
                item for item in active
                if (
                    item.get("conditionStatus")
                    in {"AT_RISK", "CRITICAL", "RULE_VIOLATION"}
                    if item.get("conditionStatus") is not None
                    else item.get("riskLevel") in {"critical", "high"}
                )
            ]),
            "criticalAlerts": len([item for item in alerts if item.get("severity") == "critical" and item.get("status") != "resolved"]),
            "availableDrivers": len([item for item in drivers if item.get("status") == "available"]),
            "recentDeliveries": len([item for item in shipments if item.get("status") in ["delivered", "awaiting_verification"]]),
            "estimatedValueProtected": reports["estimatedValueProtected"],
        },
        "shipments": shipments,
        "activeShipments": active,
        "recentActivity": [event for item in shipments for event in item.get("timeline", [])][-6:],
        "drivers": drivers,
        "facilities": deepcopy([item for item in FACILITIES.values() if item.get("organizationId") == organization_id]),
        "vehicles": deepcopy([item for item in VEHICLES.values() if item.get("organizationId") == organization_id]),
        "sensors": get_organization_sensors(organization_id),
        "alerts": alerts,
        "tickets": get_organization_tickets(organization_id),
        "reports": reports,
    }


def get_driver_dashboard_data(driver_id, telemetry_state_repository=None):
    driver = DRIVERS.get(driver_id)
    if not driver:
        return {"scope": "driver", "driver": None, "activeDelivery": None, "nextDelivery": None, "assignedDeliveries": [], "deliveryRequests": [], "acceptedDeliveries": [], "upcomingDeliveries": [], "activeDeliveries": [], "completedDeliveries": [], "alerts": [], "tickets": [], "incidents": []}
    shipments = [
        driver_shipment_record(item, telemetry_state_repository)
        for item in SHIPMENTS.values()
        if item.get("driverId") == driver_id
    ]
    requests = [item for item in shipments if item.get("status") in DRIVER_REQUEST_STATUSES]
    accepted = [item for item in shipments if item.get("status") in DRIVER_ACCEPTED_STATUSES]
    active = [item for item in shipments if item.get("status") in DRIVER_ACTIVE_STATUSES]
    completed = [item for item in shipments if item.get("status") in DRIVER_COMPLETED_STATUSES]
    active.sort(key=lambda item: ({"critical": 0, "high": 1, "medium": 2, "low": 3}.get(item.get("riskLevel"), 4), item.get("expectedArrival") or ""))
    requests.sort(key=lambda item: item.get("departureAt") or item.get("expectedArrival") or "")
    accepted.sort(key=lambda item: item.get("departureAt") or item.get("expectedArrival") or "")
    return {
        "scope": "driver",
        "driver": {**deepcopy(driver), "organizationName": organization_name(driver.get("organizationId")), "organizationContact": (ORGANIZATIONS.get(driver.get("organizationId")) or {}).get("contact")},
        "activeDelivery": active[0] if active else None,
        "nextDelivery": accepted[0] if accepted else requests[0] if requests else None,
        "assignedDeliveries": requests + accepted + active,
        "deliveryRequests": requests,
        "acceptedDeliveries": accepted,
        "upcomingDeliveries": requests + accepted,
        "activeDeliveries": active,
        "completedDeliveries": completed,
        "alerts": get_driver_alerts(driver_id),
        "tickets": get_driver_support_tickets(driver_id),
        "incidents": deepcopy([item for item in DRIVER_INCIDENTS.values() if item.get("driverId") == driver_id]),
    }


def get_support_foundation_dashboard_data(user_id, telemetry_state_repository=None):
    tickets = [support_ticket_record(item) for item in SUPPORT_TICKETS.values()]
    assigned = [item for item in tickets if item.get("assignedTo") == user_id and item.get("status") != "resolved"]
    resolved = [item for item in tickets if item.get("status") == "resolved"]
    resolution_hours = []
    for item in resolved:
        if item.get("createdAt") and item.get("resolvedAt"):
            resolution_hours.append((_parse_datetime(item["resolvedAt"], "Resolved") - _parse_datetime(item["createdAt"], "Created")).total_seconds() / 3600)
    priority_queue = sorted(
        [item for item in tickets if item.get("status") != "resolved"],
        key=lambda item: ({"critical": 0, "high": 1, "medium": 2, "low": 3}.get(item.get("priority"), 4), item.get("updatedAt") or ""),
    )
    shipments = get_support_shipment_records(telemetry_state_repository)
    active_trips = [item for item in shipments if item.get("status") in {"active", "in_transit", "at_risk", "delayed"}]
    active_trips.sort(key=lambda item: (0 if item.get("status") in {"at_risk", "delayed"} else 1, item.get("lastUpdated") or ""))
    return {
        "scope": "support",
        "summary": {
            "new": count_ticket_status(tickets, "new"),
            "critical": len([item for item in tickets if item.get("priority") == "critical" and item.get("status") != "resolved"]),
            "inProgress": count_ticket_status(tickets, "in_progress"),
            "waiting": count_ticket_status(tickets, "waiting_for_user"),
            "resolved": len(resolved),
            "averageResolutionHours": round(sum(resolution_hours) / len(resolution_hours), 1) if resolution_hours else None,
        },
        "priorityQueue": priority_queue,
        "incomingRequests": priority_queue,
        "activeTrips": active_trips,
        "recentlyUpdated": sorted(tickets, key=lambda item: item.get("updatedAt") or "", reverse=True),
        "assignedTickets": assigned,
        "tickets": tickets,
        "shipments": shipments,
        "organizations": get_support_organization_records(),
        "agents": [{"userId": user.get("userId"), "name": user.get("name")} for user in USERS.values() if normalized_role(user) == "support"],
        "knowledgeBase": support_knowledge_base(),
    }


def get_admin_dashboard_data():
    """Returns the platform-level admin dashboard."""
    organizations = list(ORGANIZATIONS.values())
    inventory = list(INVENTORY_ITEMS.values())
    requests = list(SUPPLY_REQUESTS.values())
    alerts = get_shipment_alerts()
    emergency_events = [
        event for event in AWS_EVENTS
        if str(event.get("eventType") or "").lower() == "emergency"
    ]

    return {
        "scope": "admin",
        "overview": {
            "totalRegisteredHospitals": count_by_type(organizations, "hospital"),
            "activeHospitalsToday": len([
                org for org in organizations
                if normalize_name(org.get("type")) == "hospital" and normalize_name(org.get("status")) == "online"
            ]),
            "activeRequests": len([request for request in requests if request.get("status") != "closed"]),
            "criticalShortageAlerts": len([
                request for request in requests
                if request.get("status") != "closed" and request.get("urgency") == "critical"
            ]) + len([alert for alert in alerts if alert.get("severity") == "critical"]),
            "emergencyIncidents": len(emergency_events),
            "totalInventory": sum_numeric_inventory(inventory),
            "activeTransfers": len([
                van for van in VANS.values()
                if normalize_name(van.get("status")) in ["on-road", "loading"]
            ]),
        },
        "recentActivity": build_recent_activity(),
        "hospitalMap": [admin_hospital_map_marker(org) for org in organizations if normalize_name(org.get("type")) == "hospital"],
        "moduleData": {
            "hospitals": [admin_hospital_map_marker(org) for org in organizations if normalize_name(org.get("type")) == "hospital"],
            "requests": [admin_request_summary(request) for request in sort_by_priority(requests)],
            "inventory": summarize_inventory(inventory),
            "alerts": alerts,
            "transfers": [admin_transfer_summary(van) for van in VANS.values()],
            "auditLogs": build_recent_activity(),
            "users": [sanitize_user(user) for user in USERS.values()],
        },
    }


def get_support_dashboard_data():
    """Returns coordination data without admin management surfaces."""
    shared_inventory = [item for item in INVENTORY_ITEMS.values() if is_support_visible(item)]
    shared_requests = [request for request in SUPPLY_REQUESTS.values() if is_support_visible(request)]
    alerts = get_shipment_alerts()

    return {
        "scope": "support",
        "overview": {
            "sharedOffers": len(shared_inventory),
            "sharedNeeds": len(shared_requests),
            "urgentRequests": len([request for request in shared_requests if request.get("urgency") in ["high", "critical"]]),
            "activeAlerts": len(alerts),
            "matches": len(find_network_matches()),
        },
        "sharedOffers": [support_inventory_record(item) for item in shared_inventory],
        "sharedNeeds": [admin_request_summary(request) for request in sort_by_priority(shared_requests)],
        "coordinationQueue": build_coordination_queue(),
        "alerts": alerts[:10],
        "hospitalSignals": [support_hospital_signal(org) for org in ORGANIZATIONS.values()],
        "liveShipments": [shipment_monitor_record(shipment) for shipment in SHIPMENTS.values()],
    }


def get_hospital_dashboard_data(organization_id):
    """Returns only records owned by the logged-in hospital."""
    own_inventory = [
        item for item in INVENTORY_ITEMS.values()
        if item.get("organizationId") == organization_id
    ]
    own_requests = [
        request for request in SUPPLY_REQUESTS.values()
        if request.get("organizationId") == organization_id
    ]
    shared_inventory = []
    shared_requests = []
    own_shared_items = [
        {"type": "supply", **own_inventory_record(item)}
        for item in own_inventory
        if is_support_visible(item)
    ] + [
        {"type": "need", **own_request_record(request)}
        for request in own_requests
        if is_support_visible(request)
    ]

    return {
        "scope": "hospital",
        "organization": hospital_own_organization(organization_id),
        "overview": {
            "ownInventoryItems": len(own_inventory),
            "ownRequests": len([request for request in own_requests if request.get("status") != "closed"]),
            "ownSharedItems": len(own_shared_items),
            "otherSharedItems": 0,
        },
        "inventory": [own_inventory_record(item) for item in own_inventory],
        "requests": [own_request_record(request) for request in own_requests],
        "ownSharedItems": own_shared_items,
        "sharedOffers": [shared_inventory_record(item) for item in shared_inventory],
        "sharedNeeds": [shared_request_record(request) for request in shared_requests],
        "transfers": hospital_transfers(organization_id),
        "shipments": [
            shipment_monitor_record(shipment)
            for shipment in SHIPMENTS.values()
            if shipment.get("destinationHospitalId") == organization_id or shipment.get("organizationId") == organization_id
        ],
        "notifications": deepcopy(NOTIFICATIONS.get(organization_id, [])),
        "staff": deepcopy(STAFF.get(organization_id, [])),
        "profile": hospital_profile(organization_id),
        "recommendations": hospital_recommendations(organization_id),
        "reports": ["Inventory Report", "Request History", "Transfer History"],
        "sharingSettings": {
            "privacyLevels": "Private, Support only, All hospitals, Emergency shared",
            "shareSurplusInventory": True,
            "shareUrgentRequests": True,
            "allowSupportOnlySharing": True,
            "allowEmergencyVisibility": True,
            "hideExactQuantities": True,
            "hideWarehouseLocations": True,
            "hideInternalNotes": True,
        },
    }


def create_or_update_inventory_item(payload, user):
    inventory_id = payload.get("inventoryId") or f"inv-{uuid4().hex[:8]}"
    existing = INVENTORY_ITEMS.get(inventory_id, {})
    organization_id = get_payload_organization(payload, user)

    item = {
        "inventoryId": inventory_id,
        "organizationId": organization_id,
        "itemName": existing.get("itemName"),
        "category": existing.get("category"),
        "quantity": existing.get("quantity"),
        "unit": existing.get("unit"),
        "status": existing.get("status", "available"),
        "minThreshold": existing.get("minThreshold"),
        "shareLevel": existing.get("shareLevel", "private"),
        "shareExactQuantity": existing.get("shareExactQuantity", False),
        "publicNote": existing.get("publicNote"),
        "internalNotes": existing.get("internalNotes"),
        "updatedAt": existing.get("updatedAt"),
    }

    for field in [
        "itemName",
        "category",
        "quantity",
        "unit",
        "status",
        "minThreshold",
        "shareLevel",
        "shareExactQuantity",
        "publicNote",
        "internalNotes",
        "updatedAt",
    ]:
        if field in payload:
            item[field] = payload[field]

    item["shareLevel"] = normalize_share_level(item.get("shareLevel"))
    item["shareExactQuantity"] = bool(item.get("shareExactQuantity"))
    INVENTORY_ITEMS[inventory_id] = item
    return deepcopy(item)


def create_or_update_supply_request(payload, user):
    request_id = payload.get("requestId") or f"req-{uuid4().hex[:8]}"
    existing = SUPPLY_REQUESTS.get(request_id, {})
    organization_id = get_payload_organization(payload, user)

    request = {
        "requestId": request_id,
        "organizationId": organization_id,
        "itemName": existing.get("itemName"),
        "category": existing.get("category"),
        "quantityNeeded": existing.get("quantityNeeded"),
        "unit": existing.get("unit"),
        "urgency": existing.get("urgency", "medium"),
        "status": existing.get("status", "open"),
        "shareLevel": existing.get("shareLevel", "private"),
        "shareExactQuantity": existing.get("shareExactQuantity", False),
        "publicNote": existing.get("publicNote"),
        "internalNotes": existing.get("internalNotes"),
        "updatedAt": existing.get("updatedAt"),
    }

    for field in [
        "itemName",
        "category",
        "quantityNeeded",
        "unit",
        "urgency",
        "status",
        "shareLevel",
        "shareExactQuantity",
        "publicNote",
        "internalNotes",
        "updatedAt",
    ]:
        if field in payload:
            request[field] = payload[field]

    request["shareLevel"] = normalize_share_level(request.get("shareLevel"))
    request["shareExactQuantity"] = bool(request.get("shareExactQuantity"))
    SUPPLY_REQUESTS[request_id] = request
    return deepcopy(request)


def create_or_update_shipment(payload):
    shipment_id = payload.get("shipmentId")

    if not shipment_id:
        raise ValueError("shipmentId is required")

    shipment = SHIPMENTS.get(shipment_id, create_empty_shipment(shipment_id))

    for field in [
        "origin",
        "status",
        "medicineType",
        "productCategory",
        "safeTemperatureMin",
        "safeTemperatureMax",
        "destination",
        "destinationHospitalId",
        "destinationHospitalName",
        "destinationGps",
        "organizationId",
        "driverId",
        "vehicleId",
        "containerId",
        "sensorId",
    ]:
        if field in payload:
            shipment[field] = payload[field]

    SHIPMENTS[shipment_id] = shipment
    return deepcopy(shipment)


def create_or_update_organization(payload):
    organization_id = payload.get("organizationId")

    if not organization_id:
        raise ValueError("organizationId is required")

    organization = ORGANIZATIONS.get(organization_id, {
        "organizationId": organization_id,
        "name": None,
        "type": None,
        "gps": None,
        "status": None,
        "contact": None,
    })

    for field in ["name", "type", "gps", "status", "contact", "region"]:
        if field in payload:
            organization[field] = payload[field]

    ORGANIZATIONS[organization_id] = organization
    return deepcopy(organization)


def create_or_update_van(payload):
    van_id = payload.get("vanId")

    if not van_id:
        raise ValueError("vanId is required")

    van = VANS.get(van_id, {
        "vanId": van_id,
        "driver": None,
        "status": None,
        "gps": None,
        "route": None,
        "payload": [],
        "latestTemperature": None,
        "batteryLevel": None,
        "etaMinutes": None,
    })

    for field in [
        "driver",
        "status",
        "gps",
        "route",
        "payload",
        "latestTemperature",
        "batteryLevel",
        "etaMinutes",
    ]:
        if field in payload:
            van[field] = payload[field]

    VANS[van_id] = van
    return deepcopy(van)


def add_aws_event(payload):
    event = {
        "source": payload.get("source"),
        "eventType": payload.get("eventType"),
        "timestamp": payload.get("timestamp"),
        "payload": payload.get("payload", {}),
    }
    AWS_EVENTS.append(event)
    return deepcopy(event)


def create_empty_shipment(shipment_id):
    return {
        "shipmentId": shipment_id,
        "medicineType": None,
        "productCategory": None,
        "safeTemperatureMin": None,
        "safeTemperatureMax": None,
        "destination": None,
        "destinationGps": None,
        "organizationId": None,
        "driverId": None,
        "vehicleId": None,
        "containerId": None,
        "sensorId": None,
        "latestReading": None,
        "readings": [],
        "risk": None,
        "mlPrediction": None,
        "nearestHospital": None,
        "alerts": [],
    }


def save_sensor_reading(shipment_id, reading):
    shipment = SHIPMENTS[shipment_id]
    shipment["latestReading"] = reading
    shipment.setdefault("readings", []).append(reading)
    shipment["lastUpdated"] = reading.get("timestamp") or now_iso()
    if reading.get("gps"):
        shipment["currentLocation"] = reading.get("locationLabel") or location_label(reading.get("gps"))
    if reading.get("routeProgress") is not None:
        shipment["routeProgress"] = reading.get("routeProgress")
    sensor = SENSORS.get(shipment.get("sensorId"))
    if sensor:
        sensor["batteryLevel"] = reading.get("batteryLevel")
        sensor["lastReadingTime"] = shipment["lastUpdated"]
        sensor["lastSeen"] = shipment["lastUpdated"]
        sensor["status"] = "healthy"
        sensor["connectionStatus"] = "online"


def update_shipment_result(shipment_id, risk, ml_prediction, nearest_hospital, alerts):
    shipment = SHIPMENTS[shipment_id]
    previous_classification = shipment.get("riskClassification")
    previous_alerts = {item.get("type"): item for item in shipment.get("alerts", [])}
    classification = {"low": "safe", "medium": "monitor", "high": "at_risk", "critical": "critical"}.get(risk.get("level"), "monitor")
    shipment["risk"] = risk
    shipment["riskLevel"] = risk.get("level")
    shipment["riskClassification"] = classification
    shipment["mlPrediction"] = ml_prediction
    shipment["nearestHospital"] = nearest_hospital
    normalized_alerts = []
    new_alert_ids = set()
    for alert in alerts:
        existing = next((item for item in ORGANIZATION_ALERTS.values() if item.get("shipmentId") == shipment_id and item.get("type") == alert.get("type") and item.get("status") != "resolved"), None)
        alert_id = alert.get("alertId") or (existing or previous_alerts.get(alert.get("type")) or {}).get("alertId") or f"alert-{shipment_id}-{uuid4().hex[:8]}"
        timestamp = alert.get("timestamp") or shipment.get("lastUpdated") or now_iso()
        normalized = {**deepcopy(alert), "alertId": alert_id, "status": alert.get("status") or "new"}
        normalized_alerts.append(normalized)
        if not existing and alert.get("type") not in previous_alerts:
            new_alert_ids.add(alert_id)
        organization_id = shipment.get("organizationId")
        if organization_id:
            stored = {
                "alertId": alert_id, "organizationId": organization_id, "shipmentId": shipment_id,
                "severity": alert.get("severity") or risk.get("level") or "medium",
                "type": alert.get("type") or "cold_chain_risk", "detectedAt": timestamp,
                "explanation": alert.get("message") or "; ".join(risk.get("reasons") or []) or "Cold-chain condition requires review.",
                "recommendedAction": alert.get("recommendedAction") or "Check the shipment condition immediately.",
                "status": "new", "driverResponse": "No response recorded", "updatedAt": timestamp,
            }
            if existing:
                stored["status"] = existing.get("status", "new")
                stored["driverResponse"] = existing.get("driverResponse", "No response recorded")
                stored["detectedAt"] = existing.get("detectedAt", timestamp)
            ORGANIZATION_ALERTS[alert_id] = stored
    shipment["alerts"] = normalized_alerts
    if classification in {"at_risk", "critical"} and shipment.get("status") in {"active", "in_transit"}:
        shipment["status"] = "at_risk"
    elif classification in {"safe", "monitor"} and shipment.get("status") == "at_risk":
        shipment["status"] = "in_transit"
    timestamp = shipment.get("lastUpdated") or now_iso()
    if previous_classification != classification:
        shipment.setdefault("timeline", []).append({"timestamp": timestamp, "label": f"Risk classified: {classification.replace('_', ' ').title()}"})
    for alert in normalized_alerts:
        if alert.get("alertId") in new_alert_ids:
            shipment.setdefault("timeline", []).append({"timestamp": alert.get("timestamp") or timestamp, "label": f"Alert created: {str(alert.get('type') or 'cold chain risk').replace('_', ' ').title()}"})


def get_payload_organization(payload, user):
    if is_central_user(user):
        organization_id = payload.get("organizationId")
    else:
        organization_id = user.get("organizationId")

    if not organization_id:
        raise ValueError("organizationId is required")

    return organization_id


def normalize_share_level(value):
    normalized = normalize_name(value)
    if normalized in ["support", "support_only", "support-only"]:
        return "support"
    if normalized in ["network", "public", "shared", "all_hospitals", "all-hospitals"]:
        return "network"
    if normalized in ["emergency", "emergency_shared", "emergency-shared"]:
        return "emergency"
    return "private"


def is_network_shared(record):
    return is_hospital_network_visible(record)


def is_support_visible(record):
    return record.get("shareLevel") in ["support", "network", "emergency"]


def is_hospital_network_visible(record):
    return record.get("shareLevel") in ["network", "emergency"]


def privacy_label(record):
    labels = {
        "private": "Private",
        "support": "Shared with Support only",
        "network": "Shared with all hospitals",
        "emergency": "Emergency shared",
    }
    return labels.get(record.get("shareLevel"), "Private")


def public_organization(org):
    if not org:
        return None
    return {
        "organizationId": org.get("organizationId"),
        "name": org.get("name"),
        "type": org.get("type"),
        "status": org.get("status"),
        "region": org.get("region"),
    }


def hospital_own_organization(organization_id):
    org = ORGANIZATIONS.get(organization_id)
    if org:
        return deepcopy(org)
    return {
        "organizationId": organization_id,
        "name": "Your Hospital",
        "type": "hospital",
        "status": "Not connected",
    }


def admin_organization_summary(org):
    return {
        **public_organization(org),
        "inventoryItems": len([
            item for item in INVENTORY_ITEMS.values()
            if item.get("organizationId") == org.get("organizationId")
        ]),
        "activeRequests": len([
            request for request in SUPPLY_REQUESTS.values()
            if request.get("organizationId") == org.get("organizationId") and request.get("status") != "closed"
        ]),
        "sharedRecords": len([
            record for record in [*INVENTORY_ITEMS.values(), *SUPPLY_REQUESTS.values()]
            if record.get("organizationId") == org.get("organizationId") and is_network_shared(record)
        ]),
    }


def admin_hospital_map_marker(org):
    organization_id = org.get("organizationId")
    active_requests = [
        request for request in SUPPLY_REQUESTS.values()
        if request.get("organizationId") == organization_id and request.get("status") != "closed"
    ]
    has_critical = any(request.get("urgency") == "critical" for request in active_requests)
    status = "critical" if has_critical else normalize_name(org.get("status") or "unknown")

    return {
        "organizationId": organization_id,
        "name": org.get("name"),
        "region": org.get("region"),
        "status": status,
        "gps": org.get("gps"),
        "activeRequests": len(active_requests),
        "criticalRequests": len([request for request in active_requests if request.get("urgency") == "critical"]),
    }


def admin_transfer_summary(van):
    route = van.get("route") or {}
    if isinstance(route, dict):
      route_label = f"{route.get('from') or 'Origin'} to {route.get('to') or 'Destination'}"
    else:
      route_label = route or "Route not assigned"

    return {
        "transferId": van.get("vanId"),
        "status": van.get("status"),
        "route": route_label,
        "payload": van.get("payload") or [],
        "etaMinutes": van.get("etaMinutes"),
    }


def sum_numeric_inventory(items):
    total = 0
    for item in items:
        quantity = item.get("quantity")
        if isinstance(quantity, (int, float)):
            total += quantity
    return total


def build_recent_activity():
    activity = []
    for event in AWS_EVENTS:
        activity.append({
            "label": f"{event.get('source') or 'System'} - {event.get('eventType') or 'event'}",
            "detail": (event.get("payload") or {}).get("name") or "Platform event received",
            "timestamp": event.get("timestamp"),
            "type": event.get("eventType") or "event",
        })
    for request in SUPPLY_REQUESTS.values():
        activity.append({
            "label": f"{request.get('itemName') or 'Supply'} request",
            "detail": f"{organization_name(request.get('organizationId'))} marked {request.get('urgency') or 'medium'} urgency",
            "timestamp": request.get("updatedAt"),
            "type": "request",
        })
    for item in INVENTORY_ITEMS.values():
        activity.append({
            "label": f"{item.get('itemName') or 'Inventory'} inventory",
            "detail": f"{organization_name(item.get('organizationId'))} updated {item.get('shareLevel') or 'private'} stock",
            "timestamp": item.get("updatedAt"),
            "type": "inventory",
        })
    return sorted(activity, key=lambda row: row.get("timestamp") or "", reverse=True)[:8]


def organization_name(organization_id):
    return (ORGANIZATIONS.get(organization_id) or {}).get("name") or organization_id or "Unknown hospital"


def own_inventory_record(item):
    return deepcopy(item)


def own_request_record(request):
    return deepcopy(request)


def shared_inventory_record(item):
    return {
        "inventoryId": item.get("inventoryId"),
        "organization": public_organization(ORGANIZATIONS.get(item.get("organizationId"))),
        "itemName": item.get("itemName"),
        "category": item.get("category"),
        "status": item.get("status"),
        "unit": item.get("unit"),
        "quantity": item.get("quantity") if item.get("shareExactQuantity") else None,
        "quantityRange": quantity_range(item.get("quantity"), item.get("unit")),
        "privacy": privacy_label(item),
        "publicNote": item.get("publicNote"),
        "updatedAt": item.get("updatedAt"),
    }


def shared_request_record(request):
    return {
        "requestId": request.get("requestId"),
        "organization": public_organization(ORGANIZATIONS.get(request.get("organizationId"))),
        "itemName": request.get("itemName"),
        "category": request.get("category"),
        "urgency": request.get("urgency"),
        "status": request.get("status"),
        "unit": request.get("unit"),
        "quantityNeeded": request.get("quantityNeeded") if request.get("shareExactQuantity") else None,
        "quantityRange": quantity_range(request.get("quantityNeeded"), request.get("unit")),
        "privacy": privacy_label(request),
        "flowStatus": status_label(request.get("status")),
        "publicNote": request.get("publicNote"),
        "updatedAt": request.get("updatedAt"),
    }


def hospital_transfers(organization_id):
    records = []
    for transfer in TRANSFERS.values():
        if transfer.get("fromOrganizationId") != organization_id and transfer.get("toOrganizationId") != organization_id:
            continue
        direction = "incoming" if transfer.get("toOrganizationId") == organization_id else "outgoing"
        other_id = transfer.get("fromOrganizationId") if direction == "incoming" else transfer.get("toOrganizationId")
        records.append({
            "transferId": transfer.get("transferId"),
            "direction": direction,
            "hospitalInvolved": organization_name(other_id),
            "items": transfer.get("items", []),
            "quantity": transfer.get("quantity"),
            "status": transfer.get("status"),
            "flowStatus": status_label(transfer.get("status")),
            "eta": transfer.get("eta"),
            "timeline": transfer.get("timeline", []),
        })
    return records


def hospital_profile(organization_id):
    org = ORGANIZATIONS.get(organization_id, {})
    return {
        "logo": "+",
        "name": org.get("name"),
        "address": org.get("region"),
        "contact": org.get("contact"),
        "emergencyContact": "+961 1 000 000",
        "operatingHours": "24/7",
        "emergencyLevel": "Level 2",
    }


def hospital_recommendations(organization_id):
    recommendations = []
    own_requests = [
        request for request in SUPPLY_REQUESTS.values()
        if request.get("organizationId") == organization_id and request.get("status") != "closed"
    ]
    for request in own_requests:
        if request.get("urgency") == "critical":
            recommendations.append(f"{request.get('itemName')} is critical. Review marketplace matches or create an emergency transfer.")
    for item in INVENTORY_ITEMS.values():
        if item.get("organizationId") == organization_id and item.get("shareLevel") == "network":
            recommendations.append(f"{item.get('itemName')} is shared as surplus. Monitor transfer requests for this item.")
    for transfer in hospital_transfers(organization_id):
        if transfer.get("direction") == "incoming":
            recommendations.append(f"Incoming transfer {transfer.get('transferId')} from {transfer.get('hospitalInvolved')} is scheduled for {transfer.get('eta')}.")
    return recommendations[:4]


def admin_request_summary(request):
    return {
        "requestId": request.get("requestId"),
        "organization": public_organization(ORGANIZATIONS.get(request.get("organizationId"))),
        "itemName": request.get("itemName"),
        "category": request.get("category"),
        "urgency": request.get("urgency"),
        "status": request.get("status"),
        "flowStatus": status_label(request.get("status")),
        "shareLevel": request.get("shareLevel"),
        "privacy": privacy_label(request),
        "quantityRange": quantity_range(request.get("quantityNeeded"), request.get("unit")),
        "publicNote": request.get("publicNote"),
        "updatedAt": request.get("updatedAt"),
    }


def support_inventory_record(item):
    return {
        "inventoryId": item.get("inventoryId"),
        "organization": public_organization(ORGANIZATIONS.get(item.get("organizationId"))),
        "itemName": item.get("itemName"),
        "category": item.get("category"),
        "status": item.get("status"),
        "quantity": item.get("quantity") if item.get("shareExactQuantity") else None,
        "unit": item.get("unit"),
        "quantityRange": quantity_range(item.get("quantity"), item.get("unit")),
        "privacy": privacy_label(item),
        "publicNote": item.get("publicNote"),
        "updatedAt": item.get("updatedAt"),
    }


def support_hospital_signal(org):
    organization_id = org.get("organizationId")
    shared_needs = [
        request for request in SUPPLY_REQUESTS.values()
        if request.get("organizationId") == organization_id and is_network_shared(request)
    ]
    shared_offers = [
        item for item in INVENTORY_ITEMS.values()
        if item.get("organizationId") == organization_id and is_network_shared(item)
    ]
    return {
        **public_organization(org),
        "sharedNeeds": len(shared_needs),
        "sharedOffers": len(shared_offers),
        "urgentSharedNeeds": len([request for request in shared_needs if request.get("urgency") in ["high", "critical"]]),
    }


def shipment_monitor_record(shipment, telemetry_state_repository=None):
    lot_trip_id = shipment.get("lotTripId")
    authoritative_state = None
    authoritative_history = None
    if telemetry_state_repository is not None and lot_trip_id:
        authoritative_state = telemetry_state_repository.get_live_state(lot_trip_id)
        authoritative_history = (
            telemetry_state_repository.get_telemetry_history(lot_trip_id)
            if authoritative_state is not None
            else ()
        )
        if authoritative_state is not None:
            authoritative_history = authoritative_history[
                : authoritative_state.revision
            ]
            if (
                not authoritative_history
                or authoritative_history[-1].sample_id
                != authoritative_state.last_sample_id
            ):
                raise ValueError(
                    "Authoritative telemetry history does not match LiveState"
                )

    if authoritative_history is not None:
        readings = list(authoritative_history)
        latest_record = readings[-1] if readings else None
        latest = (
            {
                "temperature": latest_record.temperature,
                "batteryLevel": latest_record.battery_level,
                "gps": (
                    {"lat": latest_record.latitude, "lng": latest_record.longitude}
                    if latest_record.latitude is not None
                    and latest_record.longitude is not None
                    else None
                ),
                "timestamp": authoritative_state.last_updated.isoformat(),
            }
            if latest_record is not None
            else {}
        )
        temperature = (
            authoritative_state.latest_temperature
            if authoritative_state is not None
            else None
        )
        battery_level = latest_record.battery_level if latest_record else None
    else:
        latest = shipment.get("latestReading") or {}
        temperature = shipment.get("temperature", latest.get("temperature"))
        battery_level = shipment.get("batteryLevel", latest.get("batteryLevel"))
        readings = shipment.get("readings") or []

    safe_min = shipment.get("safeTemperatureMin")
    safe_max = shipment.get("safeTemperatureMax")
    history = readings[-6:] if readings else [latest] if latest else []
    destination_id = shipment.get("destinationHospitalId") or shipment.get("organizationId")
    destination_gps = shipment.get("destinationGps") or (ORGANIZATIONS.get(destination_id) or {}).get("gps")
    origin_gps = shipment.get("originGps") or ORIGIN_LOCATIONS.get(shipment.get("origin"))
    current_gps = latest.get("gps")
    last_updated = (
        authoritative_state.last_updated.isoformat()
        if authoritative_history is not None and authoritative_state is not None
        else None
        if authoritative_history is not None
        else shipment.get("lastUpdated") or latest.get("timestamp")
    )
    route_points = [point for point in [
        {"label": shipment.get("origin") or "Origin", "gps": origin_gps},
        {"label": shipment.get("currentLocation") or "Current truck location", "gps": current_gps},
        {"label": shipment.get("destinationHospitalName") or shipment.get("destination") or "Destination", "gps": destination_gps},
    ] if point.get("gps")]

    return {
        "id": shipment.get("id") or shipment.get("shipmentId"),
        "shipmentId": shipment.get("shipmentId"),
        "lotTripId": shipment.get("lotTripId"),
        "tripId": shipment.get("tripId"),
        "productRuleVersion": shipment.get("productRuleVersion"),
        "tripStatus": shipment.get("tripStatus"),
        "origin": shipment.get("origin") or "Origin not assigned",
        "originGps": origin_gps,
        "destinationHospitalId": destination_id,
        "destinationHospitalName": shipment.get("destinationHospitalName") or shipment.get("destination"),
        "organizationId": shipment.get("organizationId"),
        "driverId": shipment.get("driverId"),
        "driverName": (DRIVERS.get(shipment.get("driverId")) or {}).get("name"),
        "vehicleId": shipment.get("vehicleId"),
        "containerId": shipment.get("containerId"),
        "sensorId": shipment.get("sensorId"),
        "sensorStatus": (SENSORS.get(shipment.get("sensorId")) or {}).get("status"),
        "productCategory": shipment.get("productCategory") or shipment.get("medicineType"),
        "destinationGps": destination_gps,
        "currentLocation": shipment.get("currentLocation") or location_label(latest.get("gps")),
        "currentGps": current_gps,
        "routePoints": route_points,
        "googleMapsUrl": google_maps_route_url(current_gps, destination_gps),
        "routeProgress": shipment.get("routeProgress", 0),
        "status": shipment.get("status") or shipment_status_from_risk(shipment),
        "temperature": temperature,
        "safeTemperatureMin": safe_min,
        "safeTemperatureMax": safe_max,
        "temperatureStatus": temperature_status(temperature, safe_min, safe_max),
        "batteryLevel": battery_level,
        "batteryStatus": battery_status(battery_level),
        "coolingUnitStatus": shipment.get("coolingUnitStatus") or ("warning" if battery_status(battery_level) != "normal" else "normal"),
        "lastUpdated": last_updated,
        "expectedArrival": shipment.get("expectedArrival"),
        "conditionStatus": (
            authoritative_state.status.value if authoritative_state is not None else None
        ),
        "conditionReasonCode": (
            authoritative_state.reason_code if authoritative_state is not None else None
        ),
        "riskLevel": shipment.get("riskLevel") or get_risk_level(shipment),
        "riskClassification": shipment.get("riskClassification") or {"low": "safe", "medium": "monitor", "high": "at_risk", "critical": "critical"}.get(shipment.get("riskLevel") or get_risk_level(shipment), "monitor"),
        "alerts": deepcopy(shipment.get("alerts", [])),
        "supplies": deepcopy(shipment.get("supplies") or [shipment.get("medicineType")] if shipment.get("medicineType") else []),
        "timeline": deepcopy(shipment.get("timeline", [])),
        "temperatureHistory": [
            {
                "timestamp": (
                    reading.timestamp.isoformat()
                    if authoritative_history is not None
                    else reading.get("timestamp")
                ),
                "value": (
                    reading.temperature
                    if authoritative_history is not None
                    else reading.get("temperature")
                ),
            }
            for reading in history
            if reading
        ],
        "batteryHistory": [
            {
                "timestamp": (
                    reading.timestamp.isoformat()
                    if authoritative_history is not None
                    else reading.get("timestamp")
                ),
                "value": (
                    reading.battery_level
                    if authoritative_history is not None
                    else reading.get("batteryLevel")
                ),
            }
            for reading in history
            if reading
        ],
    }


def summarize_requests(requests):
    summary = {}
    for request in requests:
        key = request.get("category") or "Uncategorized"
        row = summary.setdefault(key, {"category": key, "open": 0, "urgent": 0, "shared": 0})
        if request.get("status") != "closed":
            row["open"] += 1
        if request.get("urgency") in ["high", "critical"]:
            row["urgent"] += 1
        if is_network_shared(request):
            row["shared"] += 1
    return list(summary.values())


def summarize_inventory(items):
    summary = {}
    for item in items:
        key = item.get("category") or "Uncategorized"
        row = summary.setdefault(key, {"category": key, "items": 0, "shared": 0, "lowStock": 0})
        row["items"] += 1
        if is_network_shared(item):
            row["shared"] += 1
        quantity = item.get("quantity")
        threshold = item.get("minThreshold")
        if isinstance(quantity, (int, float)) and isinstance(threshold, (int, float)) and quantity <= threshold:
            row["lowStock"] += 1
    return list(summary.values())


def sort_by_priority(requests):
    priority = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(requests, key=lambda request: priority.get(request.get("urgency"), 4))


def find_matching_opportunities(organization_id):
    own_requests = [
        request for request in SUPPLY_REQUESTS.values()
        if request.get("organizationId") == organization_id and request.get("status") != "closed"
    ]
    shared_inventory = [
        item for item in INVENTORY_ITEMS.values()
        if item.get("organizationId") != organization_id and is_network_shared(item)
    ]

    matches = []
    for request in own_requests:
        for item in shared_inventory:
            if normalize_name(request.get("itemName")) == normalize_name(item.get("itemName")):
                matches.append({
                    "requestId": request.get("requestId"),
                    "inventoryId": item.get("inventoryId"),
                    "itemName": item.get("itemName"),
                    "urgency": request.get("urgency"),
                    "supplier": public_organization(ORGANIZATIONS.get(item.get("organizationId"))),
                    "quantityRange": quantity_range(item.get("quantity"), item.get("unit")),
                    "publicNote": item.get("publicNote"),
                })
    return matches


def find_network_matches():
    matches = []
    for request in SUPPLY_REQUESTS.values():
        if not is_network_shared(request) or request.get("status") == "closed":
            continue
        for item in INVENTORY_ITEMS.values():
            if (
                item.get("organizationId") != request.get("organizationId")
                and is_network_shared(item)
                and normalize_name(request.get("itemName")) == normalize_name(item.get("itemName"))
            ):
                matches.append({
                    "request": admin_request_summary(request),
                    "offer": support_inventory_record(item),
                })
    return matches


def build_coordination_queue():
    queue = []
    for match in find_network_matches():
        request = match["request"]
        offer = match["offer"]
        queue.append({
            "itemName": request.get("itemName"),
            "needHospital": request.get("organization"),
            "offerHospital": offer.get("organization"),
            "urgency": request.get("urgency"),
            "needRange": request.get("quantityRange"),
            "offerRange": offer.get("quantityRange"),
            "publicNote": request.get("publicNote") or offer.get("publicNote"),
            "flowStatus": "Matched",
        })
    priority = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(queue, key=lambda row: priority.get(row.get("urgency"), 4))


def quantity_range(quantity, unit):
    if not isinstance(quantity, (int, float)):
        return "Quantity not shared"
    if quantity <= 0:
        label = "None"
    elif quantity <= 10:
        label = "Low"
    elif quantity <= 50:
        label = "Moderate"
    else:
        label = "High"
    return f"{label} availability{f' ({unit})' if unit else ''}"


def status_label(value):
    labels = {
        "pending": "Pending",
        "matched": "Matched",
        "approved": "Approved",
        "in_transit": "In Transit",
        "in-transit": "In Transit",
        "delivered": "Delivered",
        "cancelled": "Cancelled",
        "open": "Pending",
        "incoming": "In Transit",
        "outgoing": "Approved",
        "on-road": "In Transit",
    }
    return labels.get(normalize_name(value), str(value or "Pending").title())


def shipment_status_from_risk(shipment):
    risk = get_risk_level(shipment)
    if risk in ["critical", "high"]:
        return "at_risk"
    return "in_transit"


def temperature_status(temperature, safe_min, safe_max):
    if not isinstance(temperature, (int, float)) or safe_min is None or safe_max is None:
        return "not_tracked"
    return "normal" if safe_min <= temperature <= safe_max else "critical"


def battery_status(level):
    if not isinstance(level, (int, float)):
        return "unknown"
    if level <= 20:
        return "critical"
    if level <= 45:
        return "warning"
    return "normal"


def location_label(gps):
    if not isinstance(gps, dict):
        return "Location unavailable"
    lat = gps.get("lat")
    lng = gps.get("lng")
    if lat is None or lng is None:
        return "Location unavailable"
    return f"{lat:.3f}, {lng:.3f}"


def google_maps_route_url(origin_gps, destination_gps):
    if not isinstance(origin_gps, dict) or not isinstance(destination_gps, dict):
        return "https://www.google.com/maps"
    origin = f"{origin_gps.get('lat')},{origin_gps.get('lng')}"
    destination = f"{destination_gps.get('lat')},{destination_gps.get('lng')}"
    return f"https://www.google.com/maps/dir/?{urlencode({'api': '1', 'origin': origin, 'destination': destination})}"


def normalize_name(value):
    return str(value or "").strip().lower()


def count_by_type(organizations, org_type):
    return len([org for org in organizations if normalize_name(org.get("type")) == org_type])


def get_risk_level(shipment):
    return (
        (shipment.get("risk") or {}).get("level")
        or (shipment.get("mlPrediction") or {}).get("riskLevel")
        or "unknown"
    )


def get_shipment_alerts():
    alerts = []
    for shipment in SHIPMENTS.values():
        for alert in shipment.get("alerts", []):
            alerts.append({
                "shipmentId": shipment.get("shipmentId"),
                "medicineType": shipment.get("medicineType"),
                "destination": shipment.get("destination"),
                "severity": alert.get("severity"),
                "type": alert.get("type"),
                "message": alert.get("message"),
            })
    return alerts


ORGANIZATION_TYPES = {"hospital", "pharmacy", "laboratory", "supermarket", "food_distributor", "refrigerated_warehouse"}
ADMIN_ROLES = {"admin", "organization_user", "driver", "support"}
ALERT_STATUSES = {"new", "acknowledged", "action_taken", "escalated", "resolved"}
TICKET_PRIORITIES = {"low", "medium", "high", "critical"}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def admin_organization_record(organization):
    organization_id = organization.get("organizationId")
    return {
        **deepcopy(organization),
        "contactPerson": organization.get("contactPerson") or "Operations Manager",
        "email": organization.get("email") or organization.get("contact"),
        "phone": organization.get("phone") or "+961 1 000 000",
        "address": organization.get("address") or organization.get("region"),
        "accountStatus": organization.get("accountStatus") or ("active" if organization.get("status") == "online" else "suspended"),
        "activeShipments": len([
            item for item in SHIPMENTS.values()
            if (item.get("organizationId") == organization_id or item.get("destinationHospitalId") == organization_id)
            and item.get("status") not in ["arrived", "delivered"]
        ]),
        "createdAt": organization.get("createdAt") or "2026-06-25T09:00:00Z",
    }


def admin_user_record(token, user):
    organization = ORGANIZATIONS.get(user.get("organizationId")) or {}
    return {
        "userId": user.get("userId"),
        "name": user.get("name"),
        "username": user.get("username"),
        "email": user.get("email"),
        "role": normalized_role(user),
        "legacyRole": user.get("role") if user.get("role") == "hospital" else None,
        "organizationId": user.get("organizationId"),
        "organizationName": organization.get("name"),
        "driverId": user.get("driverId"),
        "accountStatus": user.get("accountStatus", "active"),
        "lastActivity": user.get("lastActivity") or "2026-07-15T09:30:00Z",
    }


def admin_shipment_record(shipment, telemetry_state_repository=None):
    record = shipment_monitor_record(shipment, telemetry_state_repository)
    organization_id = shipment.get("organizationId") or shipment.get("destinationHospitalId")
    driver = DRIVERS.get(shipment.get("driverId")) or {}
    record.update({
        "organizationId": organization_id,
        "organizationName": organization_name(organization_id),
        "product": shipment.get("productCategory") or shipment.get("medicineType") or "Cold-chain product",
        "driverName": driver.get("name") or "Unassigned",
        "expectedArrival": shipment.get("expectedArrival") or "Not available",
        "estimatedValue": shipment.get("estimatedValue") or estimated_shipment_value(shipment),
    })
    return record


def admin_sensor_record(sensor):
    organization = ORGANIZATIONS.get(sensor.get("organizationId")) or {}
    battery = sensor.get("batteryLevel")
    status = sensor.get("status") or "healthy"
    connection = sensor.get("connectionStatus") or ("offline" if status == "offline" else "online")
    battery_condition = "critical" if isinstance(battery, (int, float)) and battery <= 15 else "low" if isinstance(battery, (int, float)) and battery <= 30 else "normal"
    return {
        **deepcopy(sensor),
        "deviceType": sensor.get("deviceType") or "Temperature and location sensor",
        "organizationName": organization.get("name"),
        "shipmentId": sensor.get("shipmentId") or next((item.get("shipmentId") for item in SHIPMENTS.values() if item.get("sensorId") == sensor.get("sensorId")), None),
        "assignment": sensor.get("shipmentId") or sensor.get("containerId") or "Unassigned",
        "connectionStatus": connection,
        "batteryCondition": battery_condition,
        "lastReadingTime": sensor.get("lastReadingTime") or sensor.get("lastSeen"),
        "deviceStatus": "inactive" if sensor.get("active") is False else status,
        "active": sensor.get("active", True),
    }


def admin_alert_records():
    records = []
    for shipment in SHIPMENTS.values():
        organization_id = shipment.get("organizationId") or shipment.get("destinationHospitalId")
        for index, alert in enumerate(shipment.get("alerts", [])):
            alert_id = alert.get("alertId") or f"alert-{shipment.get('shipmentId')}-{index + 1}"
            alert["alertId"] = alert_id
            alert.setdefault("status", "new")
            records.append({
                "alertId": alert_id,
                "severity": alert.get("severity") or "medium",
                "shipmentId": shipment.get("shipmentId"),
                "organizationId": organization_id,
                "organizationName": organization_name(organization_id),
                "type": alert.get("type") or "cold_chain",
                "detectedAt": alert.get("timestamp") or shipment.get("lastUpdated"),
                "explanation": alert.get("message") or "Cold-chain condition requires review.",
                "recommendedAction": alert.get("recommendedAction") or recommended_admin_action(alert, shipment),
                "status": alert.get("status"),
                "driverResponse": alert.get("driverResponse") or "No response recorded",
            })
    return records


def admin_ticket_record(ticket):
    assigned_user = next((user for user in USERS.values() if user.get("userId") == ticket.get("assignedTo")), None) or {}
    return {
        **ticket_record(ticket),
        "reportingUser": ticket.get("reportingUser") or ticket.get("requester"),
        "assignedAgent": assigned_user.get("name") or "Unassigned",
        "createdAt": ticket.get("createdAt") or ticket.get("updatedAt"),
    }


def admin_report_data(shipments, sensors, tickets, organizations):
    completed = [item for item in shipments if item.get("status") in ["arrived", "delivered"]]
    safe = [item for item in shipments if item.get("riskLevel") == "low"]
    protected_ids = {item.get("shipmentId") for item in [*completed, *safe]}
    return {
        "totalShipments": len(shipments),
        "completedShipments": len(completed),
        "safeShipments": len(safe),
        "atRiskShipments": len([item for item in shipments if item.get("riskLevel") == "high"]),
        "criticalShipments": len([item for item in shipments if item.get("riskLevel") == "critical"]),
        "organizationsByType": status_counts(organizations, "type"),
        "onlineSensors": len([item for item in sensors if item.get("connectionStatus") == "online"]),
        "offlineSensors": len([item for item in sensors if item.get("connectionStatus") == "offline"]),
        "openTickets": len([item for item in tickets if item.get("status") != "resolved"]),
        "resolvedTickets": len([item for item in tickets if item.get("status") == "resolved"]),
        "protectedShipments": len(protected_ids),
        "estimatedValueProtected": sum(item.get("estimatedValue") or 0 for item in shipments if item.get("shipmentId") in protected_ids),
    }


def create_admin_organization(payload):
    name = str(payload.get("name") or "").strip()
    org_type = normalize_name(payload.get("type")).replace(" ", "_")
    email = str(payload.get("email") or "").strip()
    if not name:
        raise ValueError("Organization name is required")
    if org_type not in ORGANIZATION_TYPES:
        raise ValueError("Select a supported organization type")
    if not email or "@" not in email:
        raise ValueError("A valid organization email is required")
    organization_id = payload.get("organizationId") or f"org-{uuid4().hex[:8]}"
    if organization_id in ORGANIZATIONS:
        raise ValueError("Organization ID already exists")
    ORGANIZATIONS[organization_id] = {
        "organizationId": organization_id,
        "name": name,
        "type": org_type,
        "contactPerson": str(payload.get("contactPerson") or "").strip(),
        "email": email,
        "contact": email,
        "phone": str(payload.get("phone") or "").strip(),
        "address": str(payload.get("address") or "").strip(),
        "region": str(payload.get("address") or "").strip(),
        "accountStatus": "active",
        "status": "online",
        "createdAt": now_iso(),
        "gps": payload.get("gps"),
    }
    return admin_organization_record(ORGANIZATIONS[organization_id])


def update_admin_organization(organization_id, payload):
    organization = ORGANIZATIONS.get(organization_id)
    if not organization:
        raise KeyError("Organization not found")
    if "type" in payload:
        org_type = normalize_name(payload.get("type")).replace(" ", "_")
        if org_type not in ORGANIZATION_TYPES:
            raise ValueError("Select a supported organization type")
        organization["type"] = org_type
    for field in ["name", "contactPerson", "email", "phone", "address"]:
        if field in payload:
            value = str(payload.get(field) or "").strip()
            if field in ["name", "email"] and not value:
                raise ValueError(f"{field.title()} is required")
            organization[field] = value
    if "email" in payload:
        if "@" not in organization["email"]:
            raise ValueError("A valid organization email is required")
        organization["contact"] = organization["email"]
    if "address" in payload:
        organization["region"] = organization["address"]
    if "accountStatus" in payload:
        status = normalize_name(payload.get("accountStatus"))
        if status not in ["active", "suspended"]:
            raise ValueError("Account status must be active or suspended")
        organization["accountStatus"] = status
        organization["status"] = "online" if status == "active" else "offline"
    return admin_organization_record(organization)


def create_admin_user(payload):
    name = str(payload.get("name") or "").strip()
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    role = normalized_role(payload.get("role"))
    if not name or not username or not password:
        raise ValueError("Name, username, and temporary password are required")
    if role not in ADMIN_ROLES:
        raise ValueError("Select a supported user role")
    if any(normalize_name(user.get("username")) == normalize_name(username) for user in USERS.values()):
        raise ValueError("Username already exists")
    organization_id = payload.get("organizationId") or None
    if organization_id and organization_id not in ORGANIZATIONS:
        raise ValueError("Assigned organization does not exist")
    if role == "organization_user" and not organization_id:
        raise ValueError("Organization Users must be assigned to an organization")
    user_id = f"user-{uuid4().hex[:8]}"
    token = f"{role}-{uuid4().hex[:12]}"
    USERS[token] = {
        "userId": user_id,
        "username": username,
        "email": str(payload.get("email") or "").strip(),
        "password": password,
        "name": name,
        "role": role,
        "organizationId": organization_id,
        "driverId": payload.get("driverId") or None,
        "accountStatus": "active",
        "lastActivity": None,
        "permissions": [],
    }
    return admin_user_record(token, USERS[token])


def update_admin_user(user_id, payload):
    token, user = find_user_record(user_id)
    if not user:
        raise KeyError("User not found")
    role = normalized_role(payload.get("role", user.get("role")))
    if role not in ADMIN_ROLES:
        raise ValueError("Select a supported user role")
    organization_id = payload.get("organizationId", user.get("organizationId")) or None
    if organization_id and organization_id not in ORGANIZATIONS:
        raise ValueError("Assigned organization does not exist")
    if role == "organization_user" and not organization_id:
        raise ValueError("Organization Users must be assigned to an organization")
    for field in ["name", "username", "email", "driverId"]:
        if field in payload:
            user[field] = str(payload.get(field) or "").strip() or None
    user["role"] = role
    user["organizationId"] = organization_id
    if "accountStatus" in payload:
        status = normalize_name(payload.get("accountStatus"))
        if status not in ["active", "inactive"]:
            raise ValueError("Account status must be active or inactive")
        user["accountStatus"] = status
    if payload.get("password"):
        user["password"] = str(payload["password"])
    return admin_user_record(token, user)


def create_admin_sensor(payload):
    sensor_id = str(payload.get("sensorId") or "").strip()
    if not sensor_id:
        raise ValueError("Sensor ID is required")
    if sensor_id in SENSORS:
        raise ValueError("Sensor ID already exists")
    organization_id = payload.get("organizationId") or None
    if organization_id and organization_id not in ORGANIZATIONS:
        raise ValueError("Assigned organization does not exist")
    SENSORS[sensor_id] = {
        "sensorId": sensor_id,
        "deviceType": str(payload.get("deviceType") or "Temperature and location sensor").strip(),
        "organizationId": organization_id,
        "shipmentId": payload.get("shipmentId") or None,
        "containerId": payload.get("containerId") or None,
        "status": "healthy",
        "connectionStatus": "online",
        "batteryLevel": float(payload.get("batteryLevel", 100)),
        "lastSeen": now_iso(),
        "active": True,
    }
    return admin_sensor_record(SENSORS[sensor_id])


def update_admin_sensor(sensor_id, payload):
    sensor = SENSORS.get(sensor_id)
    if not sensor:
        raise KeyError("Sensor not found")
    organization_id = payload.get("organizationId", sensor.get("organizationId")) or None
    if organization_id and organization_id not in ORGANIZATIONS:
        raise ValueError("Assigned organization does not exist")
    sensor["organizationId"] = organization_id
    for field in ["deviceType", "shipmentId", "containerId", "connectionStatus", "status"]:
        if field in payload:
            sensor[field] = payload.get(field) or None
    if "batteryLevel" in payload:
        battery = float(payload["batteryLevel"])
        if battery < 0 or battery > 100:
            raise ValueError("Battery level must be between 0 and 100")
        sensor["batteryLevel"] = battery
    if "active" in payload:
        sensor["active"] = bool(payload["active"])
        if not sensor["active"]:
            sensor["status"] = "inactive"
    return admin_sensor_record(sensor)


def update_admin_alert(alert_id, payload):
    status = normalize_name(payload.get("status"))
    if status not in ALERT_STATUSES:
        raise ValueError("Select a valid alert status")
    for shipment in SHIPMENTS.values():
        for index, alert in enumerate(shipment.get("alerts", [])):
            current_id = alert.get("alertId") or f"alert-{shipment.get('shipmentId')}-{index + 1}"
            if current_id == alert_id:
                alert["alertId"] = current_id
                alert["status"] = status
                return next(item for item in admin_alert_records() if item.get("alertId") == alert_id)
    raise KeyError("Alert not found")


def update_admin_ticket(ticket_id, payload):
    ticket = SUPPORT_TICKETS.get(ticket_id)
    if not ticket:
        raise KeyError("Ticket not found")
    if "priority" in payload:
        priority = normalize_name(payload.get("priority"))
        if priority not in TICKET_PRIORITIES:
            raise ValueError("Select a valid ticket priority")
        ticket["priority"] = priority
    if "assignedTo" in payload:
        assigned_to = payload.get("assignedTo") or None
        if assigned_to and not any(user.get("userId") == assigned_to and normalized_role(user) == "support" for user in USERS.values()):
            raise ValueError("Assigned Support Agent does not exist")
        ticket["assignedTo"] = assigned_to
    if "status" in payload:
        status = normalize_name(payload.get("status"))
        if status not in ["new", "in_progress", "waiting_for_response", "escalated", "resolved", "closed"]:
            raise ValueError("Select a valid ticket status")
        ticket["status"] = status
    ticket["updatedAt"] = now_iso()
    return admin_ticket_record(ticket)


def update_platform_settings(payload):
    if "displayName" in payload:
        display_name = str(payload.get("displayName") or "").strip()
        if not display_name:
            raise ValueError("Platform display name is required")
        PLATFORM_SETTINGS["displayName"] = display_name
    for field in ["temperatureWarningMargin", "lowBatteryThreshold", "criticalBatteryThreshold"]:
        if field in payload:
            value = float(payload[field])
            if value < 0 or value > 100:
                raise ValueError(f"{field} must be between 0 and 100")
            PLATFORM_SETTINGS[field] = value
    if PLATFORM_SETTINGS["criticalBatteryThreshold"] > PLATFORM_SETTINGS["lowBatteryThreshold"]:
        raise ValueError("Critical battery threshold cannot exceed the low-battery threshold")
    for field in ["notifyCriticalAlerts", "notifyOfflineSensors"]:
        if field in payload:
            PLATFORM_SETTINGS[field] = bool(payload[field])
    return deepcopy(PLATFORM_SETTINGS)


def find_user_record(user_id):
    for token, user in USERS.items():
        if user.get("userId") == user_id:
            return token, user
    return None, None


def estimated_shipment_value(shipment):
    category = normalize_name(shipment.get("productCategory") or shipment.get("medicineType"))
    if any(label in category for label in ["blood", "pharma", "insulin", "laboratory"]):
        return 18000
    if any(label in category for label in ["fresh", "food", "dairy"]):
        return 6500
    return 4000


def recommended_admin_action(alert, shipment):
    if alert.get("severity") == "critical":
        return "Escalate to operations and contact the assigned driver immediately"
    if shipment.get("batteryLevel", 100) <= 30:
        return "Confirm cooling-unit power and arrange battery service"
    return "Acknowledge and continue monitoring the shipment"


def ticket_record(ticket):
    record = deepcopy(ticket)
    record["organizationName"] = organization_name(ticket.get("organizationId"))
    return record


def count_ticket_status(tickets, status):
    return len([ticket for ticket in tickets if ticket.get("status") == status])


def status_counts(records, field):
    counts = {}
    for record in records:
        value = record.get(field) or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def sanitize_user(user):
    return {
        "userId": user.get("userId"),
        "username": user.get("username"),
        "name": user.get("name"),
        "role": user.get("role"),
        "organizationId": user.get("organizationId"),
        "driverId": user.get("driverId"),
        "permissions": list(user.get("permissions", [])),
    }


def get_permission_matrix():
    return [
        {"role": "admin", "access": "All monitoring, hospital management, user management, coordination"},
        {"role": "support", "access": "Monitoring and coordination without user administration"},
        {"role": "hospital", "access": "Own inventory, own requests, shared network records, matching opportunities"},
    ]


# Organization operations are deliberately scoped by the authenticated user's
# organization ID. Frontend-supplied organization IDs are never authoritative.
PRODUCT_CATEGORIES = {"Vaccines", "Medicine", "Blood products", "Laboratory samples", "Dairy", "Frozen food", "Meat", "Fresh produce", "Other"}
PRODUCT_HANDLING_PROFILES = {
    "Vaccines": {"min": 2, "max": 8, "hours": 2, "sensitivity": "critical", "instruction": "Keep refrigerated and protect from direct light."},
    "Medicine": {"min": 2, "max": 8, "hours": 4, "sensitivity": "standard", "instruction": "Keep sealed in the cooled container."},
    "Blood products": {"min": 2, "max": 6, "hours": 2, "sensitivity": "critical", "instruction": "Prioritize delivery and avoid unnecessary handling."},
    "Laboratory samples": {"min": 2, "max": 8, "hours": 3, "sensitivity": "high", "instruction": "Keep upright, sealed, and continuously cooled."},
    "Dairy": {"min": 1, "max": 4, "hours": 4, "sensitivity": "standard", "instruction": "Maintain refrigeration and keep the container closed."},
    "Frozen food": {"min": -20, "max": -18, "hours": 6, "sensitivity": "high", "instruction": "Keep frozen and minimize door-open time."},
    "Meat": {"min": 0, "max": 4, "hours": 4, "sensitivity": "high", "instruction": "Maintain refrigeration and prevent cross-contamination."},
    "Fresh produce": {"min": 2, "max": 8, "hours": 6, "sensitivity": "standard", "instruction": "Keep cool, dry, and protected from crushing."},
    "Other": {"min": 2, "max": 8, "hours": 4, "sensitivity": "standard", "instruction": "Follow the organization-provided handling instructions."},
}
TERMINAL_SHIPMENT_STATUSES = {"delivered", "cancelled", "rejected"}


def organization_shipment_record(shipment, telemetry_state_repository=None):
    record = shipment_monitor_record(shipment, telemetry_state_repository)
    driver = DRIVERS.get(shipment.get("driverId")) or {}
    sensor = SENSORS.get(shipment.get("sensorId")) or {}
    record.update({
        "productName": shipment.get("productName") or shipment.get("medicineType") or shipment.get("productCategory"),
        "quantity": shipment.get("quantity"), "unit": shipment.get("unit"),
        "estimatedValue": shipment.get("estimatedValue", estimated_shipment_value(shipment)),
        "expirationDate": shipment.get("expirationDate"), "sensitivity": shipment.get("sensitivity", "standard"),
        "handlingNotes": shipment.get("handlingNotes"), "deliveryInstructions": shipment.get("deliveryInstructions"),
        "originFacilityId": shipment.get("originFacilityId"), "destinationFacilityId": shipment.get("destinationFacilityId"),
        "departureAt": shipment.get("departureAt"), "expectedArrival": shipment.get("expectedArrival"), "acceptedAt": shipment.get("acceptedAt"),
        "driverName": driver.get("name") or shipment.get("driverId"), "sensorStatus": sensor.get("status"),
        "riskExplanation": (shipment.get("risk") or {}).get("reasons", []),
        "recommendedAction": "Contact the assigned driver and protect the cold chain." if get_risk_level(shipment) in ["high", "critical"] else "Continue routine monitoring.",
        "arrivalTime": shipment.get("arrivalTime"), "receiverName": shipment.get("receiverName"), "receiverSignature": shipment.get("receiverSignature"),
        "destinationVerificationCode": shipment.get("destinationVerificationCode") if shipment.get("destinationVerificationStatus") != "confirmed" else None,
        "destinationVerificationStatus": shipment.get("destinationVerificationStatus", "pending"),
        "destinationVerifiedAt": shipment.get("destinationVerifiedAt"),
        "deliveryNotes": shipment.get("deliveryNotes"), "driverActions": deepcopy(shipment.get("driverActions", [])),
        "verification": deepcopy(shipment.get("verification")),
    })
    return record


def _organization_shipments(organization_id):
    return [item for item in SHIPMENTS.values() if item.get("organizationId") == organization_id]


def _owned_record(records, record_id, organization_id, label):
    record = records.get(record_id)
    if not record or record.get("organizationId") != organization_id:
        raise KeyError(f"{label} not found")
    return record


def _required(payload, fields):
    missing = [field for field in fields if payload.get(field) is None or (isinstance(payload.get(field), str) and not payload.get(field).strip())]
    if missing:
        raise ValueError("Required fields: " + ", ".join(missing))


def _parse_datetime(value, label):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a valid date and time")


def get_organization_shipments(organization_id, telemetry_state_repository=None):
    return [
        organization_shipment_record(item, telemetry_state_repository)
        for item in _organization_shipments(organization_id)
    ]


def get_organization_shipment(
    organization_id, shipment_id, telemetry_state_repository=None
):
    return organization_shipment_record(
        _owned_record(SHIPMENTS, shipment_id, organization_id, "Shipment"),
        telemetry_state_repository,
    )


def get_v2_alert_shipment_access(lot_trip_id):
    """Return the legacy ownership link used to authorize V2 alert commands."""
    matches = [
        shipment
        for shipment in SHIPMENTS.values()
        if shipment.get("lotTripId") == lot_trip_id
    ]
    if len(matches) != 1:
        return None
    shipment = matches[0]
    return {
        "shipmentId": shipment.get("shipmentId"),
        "lotTripId": shipment.get("lotTripId"),
        "organizationId": shipment.get("organizationId"),
        "driverId": shipment.get("driverId"),
    }


def get_organization_drivers(organization_id):
    shipments = _organization_shipments(organization_id)
    result = []
    for driver in DRIVERS.values():
        if driver.get("organizationId") != organization_id:
            continue
        deliveries = [item for item in shipments if item.get("driverId") == driver.get("driverId")]
        active = [item for item in deliveries if item.get("status") not in TERMINAL_SHIPMENT_STATUSES | {"awaiting_verification"}]
        result.append({**deepcopy(driver), "currentAssignment": active[0].get("shipmentId") if active else None,
                       "completedDeliveries": len([item for item in deliveries if item.get("status") == "delivered"]),
                       "deliveryHistory": [organization_shipment_record(item) for item in deliveries if item.get("status") in TERMINAL_SHIPMENT_STATUSES | {"awaiting_verification"}]})
    return result


def get_organization_sensors(organization_id):
    return deepcopy([item for item in SENSORS.values() if item.get("organizationId") == organization_id])


def get_organization_alerts(organization_id):
    return deepcopy([item for item in ORGANIZATION_ALERTS.values() if item.get("organizationId") == organization_id])


def organization_ticket_record(ticket):
    record = ticket_record(ticket)
    if record.get("status") == "waiting_for_response":
        record["status"] = "waiting_for_user"
    record.pop("internalNotes", None)
    record["messages"] = [deepcopy(message) for message in ticket.get("messages", []) if not message.get("internal")]
    return record


def get_organization_tickets(organization_id):
    return [organization_ticket_record(item) for item in SUPPORT_TICKETS.values() if item.get("organizationId") == organization_id]


def get_organization_ticket(organization_id, ticket_id):
    return organization_ticket_record(_owned_record(SUPPORT_TICKETS, ticket_id, organization_id, "Ticket"))


def create_organization_shipment(payload, user, v2_registration_service=None):
    organization_id = user["organizationId"]
    required = ["submissionId", "productCategory", "quantity", "originFacilityId", "destinationFacilityId"]
    _required(payload, required)
    for existing in _organization_shipments(organization_id):
        if existing.get("submissionId") == payload["submissionId"]:
            return organization_shipment_record(existing), False
    if payload["productCategory"] not in PRODUCT_CATEGORIES:
        raise ValueError("Unsupported product category")
    profile = PRODUCT_HANDLING_PROFILES[payload["productCategory"]]
    try:
        quantity = float(payload["quantity"])
        value = float(payload.get("estimatedValue") or 0)
        safe_min = float(payload.get("safeTemperatureMin") if payload.get("safeTemperatureMin") not in [None, ""] else profile["min"])
        safe_max = float(payload.get("safeTemperatureMax") if payload.get("safeTemperatureMax") not in [None, ""] else profile["max"])
    except (TypeError, ValueError):
        raise ValueError("Quantity, value, and temperature limits must be numeric")
    if quantity <= 0 or value < 0 or safe_min >= safe_max:
        raise ValueError("Quantity must be positive and minimum temperature must be below maximum")
    origin = _owned_record(FACILITIES, payload["originFacilityId"], organization_id, "Origin facility")
    destination = _owned_record(FACILITIES, payload["destinationFacilityId"], organization_id, "Destination facility")
    if origin["facilityId"] == destination["facilityId"]:
        raise ValueError("Origin and destination must be different")
    departure_value = payload.get("departureAt") or (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    departure = _parse_datetime(departure_value, "Departure")
    arrival_value = payload.get("expectedArrival") or (departure + timedelta(hours=profile["hours"])).isoformat()
    arrival = _parse_datetime(arrival_value, "Expected arrival")
    if arrival <= departure:
        raise ValueError("Expected arrival must be after departure")
    driver_id = payload.get("driverId") or next((item["driverId"] for item in DRIVERS.values() if item.get("organizationId") == organization_id and item.get("status") in ["available", "assigned"]), None)
    driver = _owned_record(DRIVERS, driver_id, organization_id, "Available driver")
    if driver.get("status") not in ["available", "assigned"]:
        raise ValueError("Selected driver is not available")
    vehicle_id = payload.get("vehicleId") or driver.get("vehicleId") or next((item["vehicleId"] for item in VEHICLES.values() if item.get("organizationId") == organization_id and item.get("status") == "available"), None)
    sensor_id = payload.get("sensorId") or next((item["sensorId"] for item in SENSORS.values() if item.get("organizationId") == organization_id and item.get("status") != "offline" and not item.get("shipmentId")), None)
    vehicle = _owned_record(VEHICLES, vehicle_id, organization_id, "Available refrigerated vehicle")
    sensor = _owned_record(SENSORS, sensor_id, organization_id, "Available sensor")
    if sensor.get("status") == "offline":
        raise ValueError("Selected sensor is offline")
    shipment_id = str(payload.get("shipmentId") or f"ship-{organization_id[:3]}-{uuid4().hex[:6]}").lower()
    if shipment_id in SHIPMENTS:
        raise ValueError("Shipment ID already exists")
    timestamp = now_iso()
    initial_temperature = round((safe_min + safe_max) / 2, 1)
    initial_reading = {"timestamp": timestamp, "temperature": initial_temperature, "batteryLevel": sensor.get("batteryLevel"), "gps": origin.get("gps")}
    shipment = {
        "shipmentId": shipment_id, "id": shipment_id, "submissionId": payload["submissionId"], "organizationId": organization_id,
        "productName": str(payload.get("productName") or payload["productCategory"]).strip(), "medicineType": str(payload.get("productName") or payload["productCategory"]).strip(), "productCategory": payload["productCategory"],
        "quantity": quantity, "unit": payload.get("unit") or "units", "estimatedValue": value, "safeTemperatureMin": safe_min, "safeTemperatureMax": safe_max,
        "expirationDate": payload.get("expirationDate"), "sensitivity": payload.get("sensitivity") or profile["sensitivity"], "handlingNotes": payload.get("handlingNotes") or profile["instruction"],
        "originFacilityId": origin["facilityId"], "origin": origin["name"], "originGps": origin.get("gps"),
        "destinationFacilityId": destination["facilityId"], "destination": destination["name"], "destinationHospitalName": destination["name"], "destinationGps": destination.get("gps"),
        "departureAt": departure_value, "expectedArrival": arrival_value, "deliveryInstructions": payload.get("deliveryInstructions") or profile["instruction"],
        "driverId": driver["driverId"], "vehicleId": vehicle["vehicleId"], "containerId": vehicle["vehicleId"], "sensorId": sensor["sensorId"],
        "status": "pending", "riskLevel": "low", "risk": {"score": 0, "level": "low", "reasons": ["Waiting for Driver acceptance"]},
        "destinationVerificationCode": f"{randbelow(1000000):06d}", "destinationVerificationStatus": "pending",
        "temperature": initial_temperature, "batteryLevel": sensor.get("batteryLevel"), "coolingUnitStatus": "normal", "latestReading": initial_reading,
        "routeProgress": 0, "currentLocation": origin["name"], "lastUpdated": timestamp, "alerts": [], "readings": [initial_reading],
        "timeline": [{"timestamp": timestamp, "label": f"Delivery request sent to {driver['name']}"}],
    }
    registration = None
    v2_request = payload.get("v2Monitoring")
    if v2_request not in [None, False]:
        if not isinstance(v2_request, dict):
            raise ValueError("v2Monitoring must be an object or false")
        if v2_request.get("enabled") is not False:
            if v2_registration_service is None:
                raise ValueError("V2 shipment registration is unavailable")
            registration = v2_registration_service.register_for_shipment(
                v2_request,
                shipment,
            )
            shipment.update(
                {
                    "lotTripId": registration.trip_identity.lot_trip_id,
                    "tripId": registration.trip_identity.trip_id,
                    "productRuleVersion": (
                        registration.trip_identity.product_rule_version
                    ),
                    "tripStatus": registration.trip_identity.status.value,
                    "v2DeviceAssignmentId": (
                        registration.device_assignment.assignment_id
                    ),
                }
            )
    driver_status = driver.get("status")
    sensor_had_shipment = "shipmentId" in sensor
    sensor_shipment_id = sensor.get("shipmentId")
    try:
        _commit_organization_shipment(shipment, driver, sensor)
    except Exception:
        SHIPMENTS.pop(shipment_id, None)
        driver["status"] = driver_status
        if sensor_had_shipment:
            sensor["shipmentId"] = sensor_shipment_id
        else:
            sensor.pop("shipmentId", None)
        if registration is not None:
            v2_registration_service.rollback_registration(registration)
        raise
    return organization_shipment_record(shipment), True


def _commit_organization_shipment(shipment, driver, sensor):
    """Apply the legacy in-memory writes as one compensatable unit."""
    shipment_id = shipment["shipmentId"]
    SHIPMENTS[shipment_id] = shipment
    driver["status"] = "assigned"
    sensor["shipmentId"] = shipment_id


def assign_organization_driver(
    organization_id,
    shipment_id,
    payload,
    v2_shipment_access_repository=None,
):
    shipment = _owned_record(SHIPMENTS, shipment_id, organization_id, "Shipment")
    if shipment.get("status") not in ["planned", "pending"]:
        raise ValueError("Driver may only be changed before the trip begins")
    driver = _owned_record(DRIVERS, payload.get("driverId"), organization_id, "Driver")
    if driver.get("status") not in ["available", "assigned"]:
        raise ValueError("Selected driver is not available")
    previous_driver_id = shipment.get("driverId")
    access_updated = False
    if shipment.get("lotTripId"):
        if v2_shipment_access_repository is None:
            raise ValueError("V2 shipment access repository is unavailable")
        v2_shipment_access_repository.transition_shipment_access_driver(
            shipment["lotTripId"],
            previous_driver_id,
            driver["driverId"],
        )
        access_updated = previous_driver_id != driver["driverId"]
    shipment_before = deepcopy(shipment)
    driver_before = deepcopy(driver)
    try:
        shipment["driverId"] = driver["driverId"]
        shipment.setdefault("timeline", []).append(
            {"timestamp": now_iso(), "label": f"Driver assigned: {driver['name']}"}
        )
        driver["status"] = "assigned"
    except Exception:
        _restore_mapping(shipment, shipment_before)
        _restore_mapping(driver, driver_before)
        if access_updated:
            v2_shipment_access_repository.transition_shipment_access_driver(
                shipment["lotTripId"],
                driver["driverId"],
                previous_driver_id,
            )
        raise
    return organization_shipment_record(shipment)


def update_organization_driver(organization_id, driver_id, payload):
    driver = _owned_record(DRIVERS, driver_id, organization_id, "Driver")
    status = payload.get("status")
    if status not in ["available", "unavailable"]:
        raise ValueError("Availability must be available or unavailable")
    active = [item for item in _organization_shipments(organization_id) if item.get("driverId") == driver_id and item.get("status") in ["in_transit", "active", "at_risk"]]
    if active:
        raise ValueError("Availability cannot change during an active trip")
    driver["status"] = status
    return deepcopy(driver)


def update_organization_alert(organization_id, alert_id, payload, user):
    alert = _owned_record(ORGANIZATION_ALERTS, alert_id, organization_id, "Alert")
    status = payload.get("status")
    if status not in ["new", "acknowledged", "action_taken", "escalated", "resolved"]:
        raise ValueError("Invalid alert status")
    transitions = {
        "new": {"acknowledged", "action_taken", "escalated", "resolved"},
        "acknowledged": {"action_taken", "escalated", "resolved"},
        "action_taken": {"escalated", "resolved"},
        "escalated": {"action_taken", "resolved"},
        "resolved": set(),
    }
    if status != alert.get("status") and status not in transitions.get(alert.get("status", "new"), set()):
        raise ValueError("Invalid alert status transition")
    timestamp = now_iso()
    alert["status"], alert["updatedAt"] = status, timestamp
    if payload.get("driverResponse"):
        alert["driverResponse"] = str(payload["driverResponse"]).strip()
    ticket = None
    if status == "escalated":
        linked = next((item for item in SUPPORT_TICKETS.values() if item.get("alertId") == alert_id and item.get("organizationId") == organization_id), None)
        if not linked:
            ticket_id = f"ticket-{uuid4().hex[:6]}"
            linked = {"ticketId": ticket_id, "organizationId": organization_id, "shipmentId": alert.get("shipmentId"), "alertId": alert_id,
                      "subject": f"Escalated {alert.get('type')} alert", "category": "temperature_alert", "priority": alert.get("severity", "high"),
                      "status": "escalated", "requester": user.get("name"), "reportingUserId": user.get("userId"), "createdAt": timestamp, "updatedAt": timestamp,
                      "summary": alert.get("explanation"), "messages": [{"author": user.get("name"), "timestamp": now_iso(), "body": alert.get("explanation"), "internal": False}]}
            SUPPORT_TICKETS[ticket_id] = linked
        alert["ticketId"] = linked["ticketId"]
        ticket = organization_ticket_record(linked)
    shipment = SHIPMENTS.get(alert.get("shipmentId"))
    if shipment:
        shipment.setdefault("timeline", []).append({"timestamp": timestamp, "label": f"Alert {status.replace('_', ' ')} by {user.get('name')}"})
    return deepcopy(alert), ticket


def create_organization_ticket(organization_id, payload, user):
    _required(payload, ["category", "description", "urgency"])
    if payload.get("shipmentId"):
        _owned_record(SHIPMENTS, payload["shipmentId"], organization_id, "Shipment")
    if payload.get("alertId"):
        _owned_record(ORGANIZATION_ALERTS, payload["alertId"], organization_id, "Alert")
    if payload["urgency"] not in ["low", "medium", "high", "critical"]:
        raise ValueError("Invalid ticket urgency")
    categories = {"sensor_problem", "temperature_alert", "cooling_problem", "shipment_issue", "driver_issue", "login_or_account_issue", "incorrect_data", "other"}
    if payload["category"] not in categories:
        raise ValueError("Invalid ticket category")
    ticket_id = f"ticket-{uuid4().hex[:6]}"
    timestamp = now_iso()
    ticket = {"ticketId": ticket_id, "organizationId": organization_id, "shipmentId": payload.get("shipmentId"), "alertId": payload.get("alertId"),
              "subject": payload.get("subject") or payload["category"].replace("_", " ").title(), "category": payload["category"], "priority": payload["urgency"],
              "status": "new", "requester": user.get("name"), "createdAt": timestamp, "updatedAt": timestamp, "summary": str(payload["description"]).strip(),
              "messages": [{"author": user.get("name"), "timestamp": timestamp, "body": str(payload["description"]).strip(), "internal": False}]}
    SUPPORT_TICKETS[ticket_id] = ticket
    if payload.get("shipmentId"):
        SHIPMENTS[payload["shipmentId"]].setdefault("timeline", []).append({"timestamp": timestamp, "label": f"Support ticket created: {ticket_id}"})
    return organization_ticket_record(ticket)


def add_organization_ticket_message(organization_id, ticket_id, payload, user):
    ticket = _owned_record(SUPPORT_TICKETS, ticket_id, organization_id, "Ticket")
    _required(payload, ["message"])
    ticket.setdefault("messages", []).append({"author": user.get("name"), "timestamp": now_iso(), "body": str(payload["message"]).strip(), "internal": False})
    ticket["updatedAt"] = now_iso()
    if ticket.get("status") in ["waiting_for_user", "waiting_for_response"]:
        ticket["status"] = "in_progress"
    return organization_ticket_record(ticket)


def verify_organization_delivery(organization_id, shipment_id, payload, user):
    shipment = _owned_record(SHIPMENTS, shipment_id, organization_id, "Shipment")
    if shipment.get("status") != "awaiting_verification":
        raise ValueError("Shipment is not awaiting verification")
    decision, notes = payload.get("decision"), str(payload.get("notes") or "").strip()
    if decision not in ["accept", "flag", "reject"]:
        raise ValueError("Decision must be accept, flag, or reject")
    if decision in ["flag", "reject"] and not notes:
        raise ValueError("A note is required when flagging or rejecting a delivery")
    timestamp = now_iso()
    shipment["verification"] = {"decision": decision, "userId": user.get("userId"), "userName": user.get("name"), "timestamp": timestamp, "notes": notes}
    shipment["status"] = {"accept": "delivered", "flag": "flagged", "reject": "rejected"}[decision]
    shipment["lastUpdated"] = timestamp
    shipment.setdefault("timeline", []).append({"timestamp": timestamp, "label": f"Delivery {decision}ed by {user.get('name')}"})
    return organization_shipment_record(shipment)


def get_organization_reports(organization_id):
    shipments = get_organization_shipments(organization_id)
    completed = [item for item in shipments if item.get("status") in ["delivered", "flagged", "rejected"]]
    on_time = [item for item in completed if not item.get("arrivalTime") or not item.get("expectedArrival") or _parse_datetime(item["arrivalTime"], "Arrival") <= _parse_datetime(item["expectedArrival"], "Expected arrival")]
    alerts = get_organization_alerts(organization_id)
    return {"totalShipments": len(shipments), "activeShipments": len([item for item in shipments if item.get("status") in {"active", "in_transit", "at_risk", "delayed", "arrived"}]),
            "completedShipments": len(completed), "safeShipments": len([item for item in shipments if item.get("riskLevel") == "low"]),
            "atRiskShipments": len([item for item in shipments if item.get("riskLevel") == "high"]), "criticalShipments": len([item for item in shipments if item.get("riskLevel") == "critical"]),
            "onTimePercentage": round(len(on_time) / len(completed) * 100) if completed else None,
            "estimatedValueProtected": sum(float(item.get("estimatedValue") or 0) for item in shipments if item.get("status") != "rejected"),
            "driverSummary": [{"driverId": driver["driverId"], "name": driver["name"], "completed": driver["completedDeliveries"]} for driver in get_organization_drivers(organization_id)],
            "shipmentOutcomes": status_counts(shipments, "status"), "alertSummary": status_counts(alerts, "status")}


DRIVER_REQUEST_STATUSES = {"planned", "pending", "assigned"}
DRIVER_ACCEPTED_STATUSES = {"accepted"}
DRIVER_UPCOMING_STATUSES = DRIVER_REQUEST_STATUSES | DRIVER_ACCEPTED_STATUSES
DRIVER_ACTIVE_STATUSES = {"active", "in_transit", "at_risk", "delayed", "arrived"}
DRIVER_COMPLETED_STATUSES = {"delivered", "awaiting_verification", "flagged", "rejected"}
DRIVER_INCIDENT_CATEGORIES = {"vehicle_problem", "cooling_failure", "sensor_problem", "traffic_delay", "route_blocked", "container_problem", "other_problem"}
DRIVER_ALERT_ACTIONS = {"action_completed", "problem_continues", "contact_organization", "sensor_issue"}


def driver_shipment_record(shipment, telemetry_state_repository=None):
    monitor = shipment_monitor_record(shipment, telemetry_state_repository)
    organization = ORGANIZATIONS.get(shipment.get("organizationId")) or {}
    return {
        "shipmentId": shipment.get("shipmentId"),
        "lotTripId": shipment.get("lotTripId"),
        "tripId": shipment.get("tripId"),
        "tripStatus": shipment.get("tripStatus"),
        "productCategory": shipment.get("productCategory") or shipment.get("medicineType") or "Cold-chain goods",
        "pickup": shipment.get("origin") or "Pickup not assigned",
        "pickupGps": shipment.get("originGps"),
        "destination": shipment.get("destinationHospitalName") or shipment.get("destination") or "Destination not assigned",
        "deadline": shipment.get("expectedArrival"),
        "departureAt": shipment.get("departureAt"),
        "acceptedAt": shipment.get("acceptedAt"),
        "status": shipment.get("status") or "planned",
        "riskLevel": shipment.get("riskLevel") or get_risk_level(shipment),
        "safeTemperatureMin": shipment.get("safeTemperatureMin"),
        "safeTemperatureMax": shipment.get("safeTemperatureMax"),
        "temperature": monitor.get("temperature"),
        "conditionStatus": monitor.get("conditionStatus"),
        "conditionReasonCode": monitor.get("conditionReasonCode"),
        "batteryLevel": monitor.get("batteryLevel"),
        "sensorStatus": monitor.get("sensorStatus"),
        "coolingUnitStatus": monitor.get("coolingUnitStatus"),
        "currentLocation": monitor.get("currentLocation"),
        "currentGps": monitor.get("currentGps"),
        "destinationGps": monitor.get("destinationGps"),
        "routeProgress": monitor.get("routeProgress", 0),
        "lastUpdated": monitor.get("lastUpdated"),
        "organizationName": organization.get("name"),
        "organizationContact": organization.get("contact"),
        "specialHandlingInstructions": shipment.get("deliveryInstructions") or shipment.get("handlingNotes") or "Keep the container closed and follow the assigned route.",
        "preTripChecklist": deepcopy(shipment.get("preTripChecklist")),
        "receiverName": shipment.get("receiverName"),
        "receiverSignature": shipment.get("receiverSignature"),
        "destinationVerificationStatus": shipment.get("destinationVerificationStatus", "pending"),
        "destinationVerifiedAt": shipment.get("destinationVerifiedAt"),
        "deliveryNotes": shipment.get("deliveryNotes"),
        "arrivalTime": shipment.get("arrivalTime"),
        "timeline": deepcopy(shipment.get("timeline", [])),
    }


def _driver_shipment(driver_id, shipment_id):
    shipment = SHIPMENTS.get(shipment_id)
    if not shipment or shipment.get("driverId") != driver_id:
        raise KeyError("Delivery not found")
    return shipment


def get_driver_delivery(driver_id, shipment_id, telemetry_state_repository=None):
    return driver_shipment_record(
        _driver_shipment(driver_id, shipment_id),
        telemetry_state_repository,
    )


def get_driver_alerts(driver_id):
    assigned_ids = {item.get("shipmentId") for item in SHIPMENTS.values() if item.get("driverId") == driver_id}
    alerts = []
    for alert in ORGANIZATION_ALERTS.values():
        if alert.get("shipmentId") not in assigned_ids or alert.get("status") == "resolved":
            continue
        alerts.append({
            "alertId": alert.get("alertId"), "shipmentId": alert.get("shipmentId"), "severity": alert.get("severity"),
            "type": alert.get("type"), "message": alert.get("explanation"),
            "instruction": "Check that cooling is active and keep the container closed." if "temperature" in str(alert.get("type", "")).lower() else alert.get("recommendedAction"),
            "status": alert.get("driverStatus") or "requires_action", "driverResponse": alert.get("driverResponse"),
            "updatedAt": alert.get("updatedAt") or alert.get("detectedAt"),
        })
    return deepcopy(sorted(alerts, key=lambda item: ({"critical": 0, "high": 1, "medium": 2}.get(item.get("severity"), 3), item.get("updatedAt") or "")))


def get_driver_support_tickets(driver_id):
    return [organization_ticket_record(item) for item in SUPPORT_TICKETS.values() if item.get("driverId") == driver_id]


def start_driver_delivery(
    driver_id,
    shipment_id,
    payload,
    user,
    v2_lifecycle_service=None,
):
    shipment = _driver_shipment(driver_id, shipment_id)
    if shipment.get("status") not in DRIVER_ACCEPTED_STATUSES:
        raise ValueError("Accept the delivery request before starting the trip")
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    required = ["shipmentCollected", "containerClosed", "sensorConnected", "coolingActive", "vehicleReady"]
    if not all(checks.get(item) is True for item in required):
        raise ValueError("Complete every pre-trip check before starting the delivery")
    transition = None
    if shipment.get("lotTripId"):
        if v2_lifecycle_service is None:
            raise ValueError("V2 shipment lifecycle service is unavailable")
        transition = v2_lifecycle_service.activate_for_shipment(shipment)

    shipment_before = deepcopy(shipment)
    driver_before = deepcopy(DRIVERS.get(driver_id))
    timestamp = now_iso()
    try:
        _commit_driver_delivery_start(
            shipment,
            driver_id,
            user,
            required,
            timestamp,
            transition,
        )
    except Exception:
        _restore_mapping(shipment, shipment_before)
        if driver_before is not None:
            _restore_mapping(DRIVERS[driver_id], driver_before)
        if transition is not None:
            v2_lifecycle_service.rollback_activation(shipment_before)
        raise
    return driver_shipment_record(shipment)


def _commit_driver_delivery_start(
    shipment,
    driver_id,
    user,
    required_checks,
    timestamp,
    transition,
):
    shipment["preTripChecklist"] = {**{item: True for item in required_checks}, "confirmedBy": user.get("userId"), "confirmedAt": timestamp}
    shipment["status"] = "in_transit"
    if transition is not None:
        shipment["tripStatus"] = transition.trip_identity.status.value
    shipment["lastUpdated"] = timestamp
    shipment.setdefault("timeline", []).append({"timestamp": timestamp, "label": f"Delivery started by {user.get('name')}"})
    if driver_id in DRIVERS:
        DRIVERS[driver_id]["status"] = "on_route"


def accept_driver_delivery(driver_id, shipment_id, user):
    shipment = _driver_shipment(driver_id, shipment_id)
    if shipment.get("status") not in DRIVER_REQUEST_STATUSES:
        raise ValueError("This delivery request is no longer available")
    timestamp = now_iso()
    shipment["status"] = "accepted"
    shipment["acceptedAt"] = timestamp
    shipment["lastUpdated"] = timestamp
    shipment.setdefault("timeline", []).append({"timestamp": timestamp, "label": f"Delivery request accepted by {user.get('name')}"})
    if driver_id in DRIVERS:
        DRIVERS[driver_id]["status"] = "assigned"
    return driver_shipment_record(shipment)


def complete_driver_delivery(
    driver_id,
    shipment_id,
    payload,
    user,
    v2_lifecycle_service=None,
):
    shipment = _driver_shipment(driver_id, shipment_id)
    if shipment.get("status") not in DRIVER_ACTIVE_STATUSES:
        raise ValueError("Only an active delivery can be completed")
    if payload.get("confirmedArrival") is not True:
        raise ValueError("Confirm arrival before completing the delivery")
    receiver_name = str(payload.get("receiverName") or "").strip()
    if not receiver_name:
        raise ValueError("Receiver name is required")
    receiver_signature = str(payload.get("receiverSignature") or "").strip()
    if not receiver_signature.startswith("data:image/png;base64,"):
        raise ValueError("Receiver signature is required")
    if len(receiver_signature) > 250000:
        raise ValueError("Receiver signature is too large")
    destination_code = str(payload.get("destinationVerificationCode") or "").strip()
    if destination_code != str(shipment.get("destinationVerificationCode") or ""):
        raise ValueError("The destination handoff code is incorrect")
    shipment_before = deepcopy(shipment)
    driver_before = deepcopy(DRIVERS.get(driver_id))
    timestamp = now_iso()
    transition = None
    try:
        if shipment.get("lotTripId"):
            if v2_lifecycle_service is None:
                raise ValueError("V2 shipment lifecycle service is unavailable")
            transition = v2_lifecycle_service.complete_for_shipment(
                shipment,
                _parse_datetime(timestamp, "Completion timestamp"),
            )
            timestamp = transition.trip.completed_at.astimezone(
                timezone.utc
            ).isoformat().replace("+00:00", "Z")
        _commit_driver_delivery_completion(
            shipment,
            driver_id,
            payload,
            user,
            receiver_name,
            receiver_signature,
            timestamp,
            transition,
        )
    except Exception:
        _restore_mapping(shipment, shipment_before)
        if driver_before is not None:
            _restore_mapping(DRIVERS[driver_id], driver_before)
        raise
    return driver_shipment_record(shipment)


def _commit_driver_delivery_completion(
    shipment,
    driver_id,
    payload,
    user,
    receiver_name,
    receiver_signature,
    timestamp,
    transition,
):
    shipment["receiverName"] = receiver_name
    shipment["receiverSignature"] = receiver_signature
    shipment["destinationVerificationStatus"] = "confirmed"
    shipment["destinationVerifiedAt"] = timestamp
    shipment["destinationVerifiedBy"] = receiver_name
    shipment["deliveryNotes"] = str(payload.get("deliveryNotes") or "").strip()
    shipment["arrivalTime"] = timestamp
    shipment["status"] = "awaiting_verification"
    if transition is not None:
        shipment["tripStatus"] = transition.trip.status.value
    shipment["lastUpdated"] = timestamp
    shipment.setdefault("driverActions", []).append("Confirmed arrival and receiver")
    shipment.setdefault("timeline", []).append({"timestamp": timestamp, "label": f"Destination handoff confirmed by {receiver_name}"})
    shipment.setdefault("timeline", []).append({"timestamp": timestamp, "label": f"Delivery completed by {user.get('name')}; awaiting organization verification"})
    shipment_id = shipment.get("shipmentId")
    remaining_active = [item for item in SHIPMENTS.values() if item.get("driverId") == driver_id and item.get("shipmentId") != shipment_id and item.get("status") in DRIVER_ACTIVE_STATUSES]
    if not remaining_active and driver_id in DRIVERS:
        DRIVERS[driver_id]["status"] = "available"


def _restore_mapping(target, snapshot):
    target.clear()
    target.update(snapshot)


def respond_to_driver_alert(driver_id, alert_id, payload, user):
    alert = ORGANIZATION_ALERTS.get(alert_id)
    if not alert:
        raise KeyError("Alert not found")
    _driver_shipment(driver_id, alert.get("shipmentId"))
    action = payload.get("action")
    if action not in DRIVER_ALERT_ACTIONS:
        raise ValueError("Invalid alert response")
    labels = {
        "action_completed": "Driver completed the recommended action",
        "problem_continues": "Driver reports that the problem continues",
        "contact_organization": "Driver requested organization contact",
        "sensor_issue": "Driver reported a sensor issue",
    }
    alert["driverStatus"] = action
    alert["driverResponse"] = labels[action]
    alert["driverUserId"] = user.get("userId")
    timestamp = now_iso()
    alert["updatedAt"] = timestamp
    shipment = SHIPMENTS.get(alert.get("shipmentId"))
    if shipment:
        shipment.setdefault("driverActions", []).append(labels[action])
        shipment.setdefault("timeline", []).append({"timestamp": timestamp, "label": labels[action]})
    return next(item for item in get_driver_alerts(driver_id) if item.get("alertId") == alert_id)


def create_driver_incident(driver_id, payload, user):
    _required(payload, ["category", "description", "shipmentId"])
    if payload.get("category") not in DRIVER_INCIDENT_CATEGORIES:
        raise ValueError("Invalid incident category")
    shipment = _driver_shipment(driver_id, payload.get("shipmentId"))
    incident_id = f"incident-{uuid4().hex[:6]}"
    incident = {
        "incidentId": incident_id, "driverId": driver_id, "organizationId": shipment.get("organizationId"),
        "shipmentId": shipment.get("shipmentId"), "category": payload.get("category"),
        "description": str(payload.get("description")).strip(),
        "location": payload.get("location") or shipment.get("currentLocation") or "Location unavailable",
        "status": "reported", "reportedBy": user.get("userId"), "reportedAt": now_iso(),
    }
    DRIVER_INCIDENTS[incident_id] = incident
    shipment.setdefault("driverActions", []).append(f"Incident reported: {payload.get('category').replace('_', ' ')}")
    shipment.setdefault("timeline", []).append({"timestamp": incident["reportedAt"], "label": f"Driver incident reported: {payload.get('category').replace('_', ' ')}"})
    return deepcopy(incident)


def create_driver_support_ticket(driver_id, payload, user):
    _required(payload, ["issueType", "message"])
    issue_types = {"delivery_help", "temperature_alert", "cooling_problem", "sensor_problem", "vehicle_problem", "route_problem", "other"}
    if payload.get("issueType") not in issue_types:
        raise ValueError("Invalid support issue type")
    driver = DRIVERS.get(driver_id)
    shipment = None
    if payload.get("shipmentId"):
        shipment = _driver_shipment(driver_id, payload.get("shipmentId"))
    ticket_id = f"ticket-{uuid4().hex[:6]}"
    timestamp = now_iso()
    ticket = {
        "ticketId": ticket_id, "organizationId": (shipment or driver or {}).get("organizationId"), "shipmentId": payload.get("shipmentId") or None,
        "driverId": driver_id, "subject": payload.get("issueType").replace("_", " ").title(), "category": payload.get("issueType"),
        "priority": "high" if payload.get("issueType") in ["temperature_alert", "cooling_problem"] else "medium", "status": "new",
        "requester": user.get("name"), "createdAt": timestamp, "updatedAt": timestamp, "summary": str(payload.get("message")).strip(),
        "messages": [{"author": user.get("name"), "timestamp": timestamp, "body": str(payload.get("message")).strip(), "internal": False}],
    }
    SUPPORT_TICKETS[ticket_id] = ticket
    if shipment:
        shipment.setdefault("timeline", []).append({"timestamp": timestamp, "label": f"Driver requested Support: {ticket_id}"})
    return organization_ticket_record(ticket)


SUPPORT_TICKET_STATUSES = {"new", "in_progress", "waiting_for_user", "escalated", "resolved"}
SUPPORT_TICKET_PRIORITIES = {"low", "medium", "high", "critical"}


def support_ticket_record(ticket):
    record = deepcopy(ticket)
    if record.get("status") == "waiting_for_response":
        record["status"] = "waiting_for_user"
    source_type = ticket.get("sourceType") or ("driver" if ticket.get("driverId") else "organization")
    record["sourceType"] = source_type
    record["sourceLabel"] = {"admin": "Admin", "driver": "Driver", "organization": "Organization"}.get(source_type, "Organization")
    record["organizationName"] = "VITAE Platform" if source_type == "admin" else organization_name(ticket.get("organizationId"))
    assigned = next((user for user in USERS.values() if user.get("userId") == ticket.get("assignedTo")), None)
    record["assignedAgentName"] = assigned.get("name") if assigned else "Unassigned"
    record["createdAt"] = ticket.get("createdAt") or ticket.get("updatedAt")
    messages = [deepcopy(item) for item in ticket.get("messages", []) if not item.get("internal")]
    if not messages and ticket.get("summary"):
        messages = [{"author": ticket.get("requester") or "Reporting user", "timestamp": record["createdAt"], "body": ticket.get("summary"), "internal": False}]
    record["messages"] = messages
    record["internalNotes"] = deepcopy(ticket.get("internalNotes", []))
    record["shipmentContext"] = support_shipment_context(ticket.get("shipmentId"))
    return record


def support_shipment_context(shipment_id, telemetry_state_repository=None):
    shipment = SHIPMENTS.get(shipment_id)
    if not shipment:
        return None
    monitor = shipment_monitor_record(shipment, telemetry_state_repository)
    alerts = [deepcopy(item) for item in ORGANIZATION_ALERTS.values() if item.get("shipmentId") == shipment_id]
    return {
        "shipmentId": shipment_id, "organizationId": shipment.get("organizationId"), "organizationName": organization_name(shipment.get("organizationId")),
        "driverId": shipment.get("driverId"), "driverName": (DRIVERS.get(shipment.get("driverId")) or {}).get("name"),
        "status": shipment.get("status"), "currentLocation": monitor.get("currentLocation"),
        "temperature": monitor.get("temperature"), "safeTemperatureMin": shipment.get("safeTemperatureMin"), "safeTemperatureMax": shipment.get("safeTemperatureMax"),
        "conditionStatus": monitor.get("conditionStatus"), "conditionReasonCode": monitor.get("conditionReasonCode"),
        "sensorId": shipment.get("sensorId"), "sensorStatus": monitor.get("sensorStatus"), "batteryLevel": monitor.get("batteryLevel"),
        "lastUpdated": monitor.get("lastUpdated"), "recentAlerts": alerts[-4:], "actionsTaken": deepcopy(shipment.get("driverActions", [])),
    }


def get_support_shipment_records(telemetry_state_repository=None):
    return [
        context
        for context in [
            support_shipment_context(
                item.get("shipmentId"), telemetry_state_repository
            )
            for item in SHIPMENTS.values()
        ]
        if context
    ]


def get_support_organization_records():
    records = []
    for organization in ORGANIZATIONS.values():
        tickets = [support_ticket_record(item) for item in SUPPORT_TICKETS.values() if item.get("organizationId") == organization.get("organizationId")]
        records.append({
            "organizationId": organization.get("organizationId"), "name": organization.get("name"), "type": organization.get("type"),
            "contact": organization.get("contact"), "region": organization.get("region"),
            "openTickets": len([item for item in tickets if item.get("status") != "resolved"]),
            "ticketHistory": [{key: item.get(key) for key in ["ticketId", "subject", "summary", "priority", "status", "updatedAt", "organizationName"]} for item in tickets],
        })
    return records


def support_knowledge_base():
    return [
        {"articleId": "kb-sensor-offline", "title": "Sensor offline", "category": "Sensor", "summary": "Check power, container pairing, and last-seen time before arranging replacement.", "steps": ["Confirm sensor power", "Verify container pairing", "Check last telemetry time", "Escalate if offline for more than 15 minutes"]},
        {"articleId": "kb-low-battery", "title": "Low cooling battery", "category": "Cooling", "summary": "Keep the container closed, confirm vehicle power, and arrange a safe charging stop.", "steps": ["Confirm cooling remains active", "Check vehicle power connection", "Notify the organization", "Escalate critical battery alerts"]},
        {"articleId": "kb-missing-readings", "title": "Missing temperature readings", "category": "Sensor", "summary": "Validate connectivity and compare the sensor last-seen time with the shipment timeline.", "steps": ["Check sensor connection", "Review last-seen time", "Ask the driver to inspect the device", "Record the diagnostic result"]},
        {"articleId": "kb-gps", "title": "Incorrect GPS location", "category": "Location", "summary": "Confirm the latest timestamp and ask the driver to verify their current route position.", "steps": ["Check update timestamp", "Compare route progress", "Confirm location with driver", "Report persistent GPS drift"]},
        {"articleId": "kb-cooling", "title": "Cooling issue", "category": "Cooling", "summary": "Prioritize product safety, keep the container sealed, and coordinate a backup cooling option.", "steps": ["Confirm current temperature", "Keep container closed", "Contact driver and organization", "Escalate if temperature is outside range"]},
        {"articleId": "kb-login", "title": "Login issue", "category": "Account", "summary": "Confirm the account identifier and status without requesting or exposing the password.", "steps": ["Confirm username or email", "Check account status with Admin", "Ask user to retry sign-in", "Escalate account changes to Admin"]},
        {"articleId": "kb-lookup", "title": "Shipment lookup issue", "category": "Shipment", "summary": "Search by shipment, organization, driver, or linked ticket and verify access scope.", "steps": ["Confirm shipment ID", "Search linked organization", "Check related tickets", "Escalate missing records to Admin"]},
    ]


def get_support_ticket(ticket_id):
    ticket = SUPPORT_TICKETS.get(ticket_id)
    if not ticket:
        raise KeyError("Ticket not found")
    return support_ticket_record(ticket)


def add_support_ticket_reply(ticket_id, payload, user):
    ticket = SUPPORT_TICKETS.get(ticket_id)
    if not ticket:
        raise KeyError("Ticket not found")
    _required(payload, ["message"])
    timestamp = now_iso()
    ticket.setdefault("messages", []).append({"author": user.get("name"), "authorUserId": user.get("userId"), "timestamp": timestamp, "body": str(payload.get("message")).strip(), "internal": False})
    ticket["status"] = "waiting_for_user" if payload.get("requestMoreInfo") is True else "in_progress" if ticket.get("status") == "new" else ticket.get("status")
    ticket["updatedAt"] = timestamp
    return support_ticket_record(ticket)


def add_support_internal_note(ticket_id, payload, user):
    ticket = SUPPORT_TICKETS.get(ticket_id)
    if not ticket:
        raise KeyError("Ticket not found")
    _required(payload, ["note"])
    ticket.setdefault("internalNotes", []).append({"author": user.get("name"), "authorUserId": user.get("userId"), "timestamp": now_iso(), "body": str(payload.get("note")).strip()})
    ticket["updatedAt"] = now_iso()
    return support_ticket_record(ticket)


def update_support_ticket(ticket_id, payload, user):
    ticket = SUPPORT_TICKETS.get(ticket_id)
    if not ticket:
        raise KeyError("Ticket not found")
    if ticket.get("status") == "resolved" and payload.get("status") not in [None, "resolved"]:
        raise ValueError("Resolved tickets cannot be reopened in this MVP")
    if "priority" in payload:
        priority = normalize_name(payload.get("priority"))
        if priority not in SUPPORT_TICKET_PRIORITIES:
            raise ValueError("Invalid ticket priority")
        ticket["priority"] = priority
    if "status" in payload:
        status = normalize_name(payload.get("status"))
        if status == "waiting_for_response":
            status = "waiting_for_user"
        if status not in SUPPORT_TICKET_STATUSES:
            raise ValueError("Invalid ticket status")
        if status == "resolved":
            summary = str(payload.get("resolutionSummary") or ticket.get("resolutionSummary") or "").strip()
            if not summary:
                raise ValueError("Resolution summary is required")
            ticket["resolutionSummary"] = summary
            ticket["resolvedAt"] = now_iso()
        if status == "escalated":
            ticket["escalatedToAdmin"] = True
            ticket["escalatedBy"] = user.get("userId")
            ticket["escalatedAt"] = now_iso()
        ticket["status"] = status
    if "resolutionSummary" in payload and str(payload.get("resolutionSummary") or "").strip():
        ticket["resolutionSummary"] = str(payload.get("resolutionSummary")).strip()
    ticket["updatedAt"] = now_iso()
    return support_ticket_record(ticket)
