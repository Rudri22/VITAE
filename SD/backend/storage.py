from copy import deepcopy
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
        "currentLocation": "Beirut coastal route - checkpoint 3",
        "routeProgress": 68,
        "status": "at_risk",
        "temperature": 9.8,
        "batteryLevel": 42,
        "coolingUnitStatus": "warning",
        "lastUpdated": "2026-07-09T08:45:00Z",
        "riskLevel": "high",
        "supplies": ["Insulin - 10 boxes"],
        "timeline": [
            {"timestamp": "2026-07-09T08:05:00Z", "label": "Departed Central Cold Storage"},
            {"timestamp": "2026-07-09T08:45:00Z", "label": "Temperature warning detected"},
        ],
        "destinationGps": None,
        "organizationId": "hospital-a",
        "latestReading": {"temperature": 9.8, "batteryLevel": 42, "gps": {"lat": 33.89, "lng": 35.50}, "timestamp": "2026-07-09T08:45:00Z"},
        "readings": [],
        "risk": {"score": 60, "level": "high", "reasons": ["Temperature is outside the safe medicine range"]},
        "mlPrediction": None,
        "nearestHospital": None,
        "alerts": [{"severity": "high", "type": "cold-chain", "message": "Insulin shipment for Hospital A is above safe range"}],
    },
    "ship-b-220": {
        "shipmentId": "ship-b-220",
        "id": "ship-b-220",
        "origin": "Regional Supply Hub",
        "destinationHospitalId": "hospital-b",
        "destinationHospitalName": "Hospital B",
        "medicineType": "Syringes",
        "safeTemperatureMin": None,
        "safeTemperatureMax": None,
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
        "status": "arrived",
        "temperature": 4.1,
        "batteryLevel": 63,
        "coolingUnitStatus": "normal",
        "lastUpdated": "2026-07-09T09:20:00Z",
        "destinationGps": None,
        "organizationId": "hospital-a",
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
    return deepcopy(user) if user else None


def authenticate_user(username, password):
    normalized_username = str(username or "").strip().lower()
    for token, user in USERS.items():
        allowed_names = [user.get("username"), user.get("email")]
        if normalized_username in [str(name or "").lower() for name in allowed_names] and user.get("password") == password:
            return token, deepcopy(user)
    return None, None


def is_admin_user(user):
    return bool(user and user.get("role") == "admin")


def is_central_user(user):
    return bool(user and user.get("role") in ["admin", "support"])


def get_user_profile(user):
    profile = sanitize_user(user)
    organization_id = user.get("organizationId")
    profile["organization"] = public_organization(ORGANIZATIONS.get(organization_id)) if organization_id else None
    return profile


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
        "medicineType",
        "safeTemperatureMin",
        "safeTemperatureMax",
        "destination",
        "destinationGps",
        "organizationId",
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
        "safeTemperatureMin": None,
        "safeTemperatureMax": None,
        "destination": None,
        "destinationGps": None,
        "organizationId": None,
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
    shipment["readings"].append(reading)


def update_shipment_result(shipment_id, risk, ml_prediction, nearest_hospital, alerts):
    shipment = SHIPMENTS[shipment_id]
    shipment["risk"] = risk
    shipment["mlPrediction"] = ml_prediction
    shipment["nearestHospital"] = nearest_hospital
    shipment["alerts"] = alerts


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


def shipment_monitor_record(shipment):
    latest = shipment.get("latestReading") or {}
    temperature = shipment.get("temperature", latest.get("temperature"))
    battery_level = shipment.get("batteryLevel", latest.get("batteryLevel"))
    safe_min = shipment.get("safeTemperatureMin")
    safe_max = shipment.get("safeTemperatureMax")
    readings = shipment.get("readings") or []
    history = readings[-6:] if readings else [latest] if latest else []
    destination_id = shipment.get("destinationHospitalId") or shipment.get("organizationId")
    destination_gps = shipment.get("destinationGps") or (ORGANIZATIONS.get(destination_id) or {}).get("gps")
    origin_gps = shipment.get("originGps") or ORIGIN_LOCATIONS.get(shipment.get("origin"))
    current_gps = latest.get("gps")
    route_points = [point for point in [
        {"label": shipment.get("origin") or "Origin", "gps": origin_gps},
        {"label": shipment.get("currentLocation") or "Current truck location", "gps": current_gps},
        {"label": shipment.get("destinationHospitalName") or shipment.get("destination") or "Destination", "gps": destination_gps},
    ] if point.get("gps")]

    return {
        "id": shipment.get("id") or shipment.get("shipmentId"),
        "shipmentId": shipment.get("shipmentId"),
        "origin": shipment.get("origin") or "Origin not assigned",
        "originGps": origin_gps,
        "destinationHospitalId": destination_id,
        "destinationHospitalName": shipment.get("destinationHospitalName") or shipment.get("destination"),
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
        "lastUpdated": shipment.get("lastUpdated") or latest.get("timestamp"),
        "riskLevel": shipment.get("riskLevel") or get_risk_level(shipment),
        "alerts": deepcopy(shipment.get("alerts", [])),
        "supplies": deepcopy(shipment.get("supplies") or [shipment.get("medicineType")] if shipment.get("medicineType") else []),
        "timeline": deepcopy(shipment.get("timeline", [])),
        "temperatureHistory": [
            {"timestamp": reading.get("timestamp"), "value": reading.get("temperature")}
            for reading in history
            if reading
        ],
        "batteryHistory": [
            {"timestamp": reading.get("timestamp"), "value": reading.get("batteryLevel")}
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


def sanitize_user(user):
    return {
        "userId": user.get("userId"),
        "username": user.get("username"),
        "name": user.get("name"),
        "role": user.get("role"),
        "organizationId": user.get("organizationId"),
        "permissions": list(user.get("permissions", [])),
    }


def get_permission_matrix():
    return [
        {"role": "admin", "access": "All monitoring, hospital management, user management, coordination"},
        {"role": "support", "access": "Monitoring and coordination without user administration"},
        {"role": "hospital", "access": "Own inventory, own requests, shared network records, matching opportunities"},
    ]
