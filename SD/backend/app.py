from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from .aws_shipment_service import ShipmentServiceError, get_live_shipments_for_user
    from .sensor_processor import process_sensor_data
    from .storage import (
        add_aws_event,
        authenticate_user,
        create_admin_organization,
        create_admin_sensor,
        create_admin_user,
        create_or_update_organization,
        create_or_update_shipment,
        create_or_update_van,
        create_organization_shipment,
        create_organization_ticket,
        create_driver_incident,
        create_driver_support_ticket,
        add_organization_ticket_message,
        add_support_internal_note,
        add_support_ticket_reply,
        accept_driver_delivery,
        assign_organization_driver,
        get_all_shipments,
        get_admin_foundation_dashboard_data,
        get_dashboard_data,
        get_driver_dashboard_data,
        get_driver_delivery,
        get_driver_support_tickets,
        get_hospital_dashboard_data,
        get_organization_foundation_dashboard_data,
        get_organization_alerts,
        get_organization_drivers,
        get_organization_reports,
        get_organization_sensors,
        get_organization_shipment,
        get_organization_shipments,
        get_organization_ticket,
        get_organization_tickets,
        get_support_foundation_dashboard_data,
        get_support_ticket,
        get_shipment_by_id,
        get_user_by_token,
        get_user_profile,
        is_admin_user,
        is_driver_user,
        is_organization_user,
        update_admin_alert,
        update_admin_organization,
        update_admin_sensor,
        update_admin_ticket,
        update_admin_user,
        update_platform_settings,
        update_organization_alert,
        update_organization_driver,
        complete_driver_delivery,
        respond_to_driver_alert,
        start_driver_delivery,
        update_support_ticket,
        verify_organization_delivery,
    )
except ImportError:
    from aws_shipment_service import ShipmentServiceError, get_live_shipments_for_user
    from sensor_processor import process_sensor_data
    from storage import (
        add_aws_event,
        authenticate_user,
        create_admin_organization,
        create_admin_sensor,
        create_admin_user,
        create_or_update_organization,
        create_or_update_shipment,
        create_or_update_van,
        create_organization_shipment,
        create_organization_ticket,
        create_driver_incident,
        create_driver_support_ticket,
        add_organization_ticket_message,
        add_support_internal_note,
        add_support_ticket_reply,
        accept_driver_delivery,
        assign_organization_driver,
        get_all_shipments,
        get_admin_foundation_dashboard_data,
        get_dashboard_data,
        get_driver_dashboard_data,
        get_driver_delivery,
        get_driver_support_tickets,
        get_hospital_dashboard_data,
        get_organization_foundation_dashboard_data,
        get_organization_alerts,
        get_organization_drivers,
        get_organization_reports,
        get_organization_sensors,
        get_organization_shipment,
        get_organization_shipments,
        get_organization_ticket,
        get_organization_tickets,
        get_support_foundation_dashboard_data,
        get_support_ticket,
        get_shipment_by_id,
        get_user_by_token,
        get_user_profile,
        is_admin_user,
        is_driver_user,
        is_organization_user,
        update_admin_alert,
        update_admin_organization,
        update_admin_sensor,
        update_admin_ticket,
        update_admin_user,
        update_platform_settings,
        update_organization_alert,
        update_organization_driver,
        complete_driver_delivery,
        respond_to_driver_alert,
        start_driver_delivery,
        update_support_ticket,
        verify_organization_delivery,
    )

try:
    from .alerting import InMemoryAlertRepository
    from .monitoring_service import LotTripNotFoundError, MonitoringService
    from .operational_service import OperationalTelemetryService
    from .product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
    )
    from .state_repository import InMemoryTelemetryStateRepository
    from .shipment_registration import V2ShipmentRegistrationService
    from .shipment_lifecycle import V2ShipmentLifecycleService
    from .telemetry_http import (
        TelemetryHttpAdapter,
        serialize_alert,
        serialize_live_state,
        serialize_trip_identity,
    )
    from .telemetry_processor import TelemetryProcessor
    from .trip_identity import DeviceAssignment, TripIdentity, TripStatus
except ImportError:
    from alerting import InMemoryAlertRepository
    from monitoring_service import LotTripNotFoundError, MonitoringService
    from operational_service import OperationalTelemetryService
    from product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
    )
    from state_repository import InMemoryTelemetryStateRepository
    from shipment_registration import V2ShipmentRegistrationService
    from shipment_lifecycle import V2ShipmentLifecycleService
    from telemetry_http import (
        TelemetryHttpAdapter,
        serialize_alert,
        serialize_live_state,
        serialize_trip_identity,
    )
    from telemetry_processor import TelemetryProcessor
    from trip_identity import DeviceAssignment, TripIdentity, TripStatus


HOST = "127.0.0.1"
PORT = 8000
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"

V2_PROTOTYPE_TRIP = TripIdentity(
    trip_id="trip-sim-001",
    lot_trip_id="lot-trip-sim-001",
    lot_id="lot-sim-001",
    device_id="device-sim-001",
    product_id=GARDASIL_9_PRODUCT_ID,
    presentation=GARDASIL_9_PRESENTATION,
    state=GARDASIL_9_STATE,
    product_rule_version=GARDASIL_9_SOURCE_VERSION,
    origin="Beirut Distribution Center",
    destination="AUB Medical Center",
    start_time=datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc),
    status=TripStatus.ACTIVE,
)
V2_PROTOTYPE_ASSIGNMENT = DeviceAssignment(
    assignment_id="assignment-sim-001",
    device_id=V2_PROTOTYPE_TRIP.device_id,
    trip_id=V2_PROTOTYPE_TRIP.trip_id,
    lot_trip_id=V2_PROTOTYPE_TRIP.lot_trip_id,
    assigned_at=V2_PROTOTYPE_TRIP.start_time,
    active=True,
)
V2_STATE_REPOSITORY = InMemoryTelemetryStateRepository()
V2_STATE_REPOSITORY.register_trip(V2_PROTOTYPE_TRIP)
V2_STATE_REPOSITORY.register_device_assignment(V2_PROTOTYPE_ASSIGNMENT)
V2_ALERT_REPOSITORY = InMemoryAlertRepository()
V2_TELEMETRY_PROCESSOR = TelemetryProcessor(
    V2_STATE_REPOSITORY,
    V2_STATE_REPOSITORY,
)
V2_OPERATIONAL_SERVICE = OperationalTelemetryService(
    V2_TELEMETRY_PROCESSOR,
    V2_ALERT_REPOSITORY,
)
V2_TELEMETRY_HTTP_ADAPTER = TelemetryHttpAdapter(V2_OPERATIONAL_SERVICE)
V2_MONITORING_SERVICE = MonitoringService(
    V2_STATE_REPOSITORY,
    V2_STATE_REPOSITORY,
    V2_ALERT_REPOSITORY,
)
V2_SHIPMENT_REGISTRATION_SERVICE = V2ShipmentRegistrationService(
    V2_STATE_REPOSITORY
)
V2_SHIPMENT_LIFECYCLE_SERVICE = V2ShipmentLifecycleService(
    V2_STATE_REPOSITORY
)


class ApiHandler(BaseHTTPRequestHandler):
    """HTTP API layer.

    This file connects frontend/API requests to the backend services:
    sensor processing, risk rules, hospital lookup, alerts, and ML prediction.
    """

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/login":
            self.handle_login_form()
            return

        if path == "/api/login":
            self.handle_login()
            return

        if path == "/api/admin/organizations":
            self.handle_admin_create(create_admin_organization, "organization")
            return

        if path == "/api/admin/users":
            self.handle_admin_create(create_admin_user, "user")
            return

        if path == "/api/admin/devices":
            self.handle_admin_create(create_admin_sensor, "device")
            return

        if path == "/api/admin/settings":
            self.handle_admin_update(update_platform_settings, "settings")
            return

        if path == "/api/sensor-data":
            self.handle_sensor_data()
            return

        if path == "/api/v2/sensor-data":
            self.handle_v2_sensor_data()
            return

        if path == "/api/shipments":
            self.handle_create_or_update_shipment()
            return

        if path == "/api/organization/shipments":
            self.handle_create_organization_shipment()
            return

        if path == "/api/organization/tickets":
            self.handle_organization_operation(lambda org, payload, user: create_organization_ticket(org, payload, user), "ticket", 201)
            return

        if path.startswith("/api/organization/tickets/") and path.endswith("/messages"):
            ticket_id = path.removeprefix("/api/organization/tickets/").removesuffix("/messages").strip("/")
            self.handle_organization_operation(lambda org, payload, user: add_organization_ticket_message(org, ticket_id, payload, user), "ticket", 201)
            return

        if path == "/api/driver/incidents":
            self.handle_driver_operation(lambda driver, payload, user: create_driver_incident(driver, payload, user), "incident", 201)
            return

        if path == "/api/driver/support":
            self.handle_driver_operation(lambda driver, payload, user: create_driver_support_ticket(driver, payload, user), "ticket", 201)
            return

        if path.startswith("/api/support/tickets/") and path.endswith("/messages"):
            ticket_id = path.removeprefix("/api/support/tickets/").removesuffix("/messages").strip("/")
            self.handle_support_operation(lambda payload, user: add_support_ticket_reply(ticket_id, payload, user), "ticket", 201)
            return

        if path.startswith("/api/support/tickets/") and path.endswith("/notes"):
            ticket_id = path.removeprefix("/api/support/tickets/").removesuffix("/notes").strip("/")
            self.handle_support_operation(lambda payload, user: add_support_internal_note(ticket_id, payload, user), "ticket", 201)
            return

        if path == "/api/organizations":
            self.handle_create_or_update_organization()
            return

        if path == "/api/vans":
            self.handle_create_or_update_van()
            return

        if path == "/api/aws-events":
            self.handle_add_aws_event()
            return

        self.send_json({"error": "Endpoint not found"}, status=404)

    def do_PATCH(self):
        path = urlparse(self.path).path
        if path.startswith("/api/driver/shipments/") and path.endswith("/accept"):
            shipment_id = path.removeprefix("/api/driver/shipments/").removesuffix("/accept").strip("/")
            self.handle_driver_operation(lambda driver, payload, user: accept_driver_delivery(driver, shipment_id, user), "delivery")
            return
        if path.startswith("/api/driver/shipments/") and path.endswith("/start"):
            shipment_id = path.removeprefix("/api/driver/shipments/").removesuffix("/start").strip("/")
            self.handle_driver_operation(
                lambda driver, payload, user: start_driver_delivery(
                    driver,
                    shipment_id,
                    payload,
                    user,
                    V2_SHIPMENT_LIFECYCLE_SERVICE,
                ),
                "delivery",
            )
            return
        if path.startswith("/api/driver/shipments/") and path.endswith("/complete"):
            shipment_id = path.removeprefix("/api/driver/shipments/").removesuffix("/complete").strip("/")
            self.handle_driver_operation(
                lambda driver, payload, user: complete_driver_delivery(
                    driver,
                    shipment_id,
                    payload,
                    user,
                    V2_SHIPMENT_LIFECYCLE_SERVICE,
                ),
                "delivery",
            )
            return
        if path.startswith("/api/driver/alerts/"):
            alert_id = path.removeprefix("/api/driver/alerts/").strip("/")
            self.handle_driver_operation(lambda driver, payload, user: respond_to_driver_alert(driver, alert_id, payload, user), "alert")
            return
        if path.startswith("/api/support/tickets/"):
            ticket_id = path.removeprefix("/api/support/tickets/").strip("/")
            self.handle_support_operation(lambda payload, user: update_support_ticket(ticket_id, payload, user), "ticket")
            return
        if path.startswith("/api/organization/shipments/") and path.endswith("/driver"):
            shipment_id = path.removeprefix("/api/organization/shipments/").removesuffix("/driver").strip("/")
            self.handle_organization_operation(lambda org, payload, user: assign_organization_driver(org, shipment_id, payload), "shipment")
            return
        if path.startswith("/api/organization/shipments/") and path.endswith("/verification"):
            shipment_id = path.removeprefix("/api/organization/shipments/").removesuffix("/verification").strip("/")
            self.handle_organization_operation(lambda org, payload, user: verify_organization_delivery(org, shipment_id, payload, user), "shipment")
            return
        if path.startswith("/api/organization/drivers/"):
            driver_id = path.removeprefix("/api/organization/drivers/").strip("/")
            self.handle_organization_operation(lambda org, payload, user: update_organization_driver(org, driver_id, payload), "driver")
            return
        if path.startswith("/api/organization/alerts/"):
            alert_id = path.removeprefix("/api/organization/alerts/").strip("/")
            self.handle_organization_operation(lambda org, payload, user: update_organization_alert(org, alert_id, payload, user), "alert")
            return
        routes = [
            ("/api/admin/organizations/", update_admin_organization, "organization"),
            ("/api/admin/users/", update_admin_user, "user"),
            ("/api/admin/devices/", update_admin_sensor, "device"),
            ("/api/admin/alerts/", update_admin_alert, "alert"),
            ("/api/admin/tickets/", update_admin_ticket, "ticket"),
        ]
        for prefix, operation, response_key in routes:
            if path.startswith(prefix):
                record_id = path.removeprefix(prefix)
                if not record_id or "/" in record_id:
                    self.send_json({"error": "Invalid resource ID"}, status=400)
                    return
                self.handle_admin_update(operation, response_key, record_id)
                return
        self.send_json({"error": "Endpoint not found"}, status=404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_common_headers()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path.startswith("/api/v2/monitor/live/"):
            lot_trip_id = path.removeprefix("/api/v2/monitor/live/")
            self.handle_v2_monitor_live(lot_trip_id)
            return

        if path.startswith("/api/v2/monitor/alerts/"):
            lot_trip_id = path.removeprefix("/api/v2/monitor/alerts/")
            self.handle_v2_monitor_alerts(lot_trip_id)
            return

        if path == "/api/me":
            user = self.require_authenticated_user()
            if not user:
                return
            self.send_json({"user": get_user_profile(user)})
            return

        if path == "/api/admin/dashboard":
            user = self.require_admin_user()
            if not user:
                return
            self.send_json(get_admin_foundation_dashboard_data())
            return

        if path == "/api/support/dashboard":
            user = self.require_support_user()
            if not user:
                return
            self.send_json(get_support_foundation_dashboard_data(user.get("userId")))
            return

        if path.startswith("/api/support/tickets/"):
            user = self.require_support_user()
            if not user:
                return
            try:
                self.send_json({"ticket": get_support_ticket(path.split("/")[-1])})
            except KeyError as error:
                self.send_json({"error": str(error).strip("'")}, status=404)
            return

        if path == "/api/organization/dashboard":
            user = self.require_organization_user()
            if not user:
                return
            self.send_json(get_organization_foundation_dashboard_data(user["organizationId"]))
            return

        organization_collections = {
            "/api/organization/shipments": ("shipments", get_organization_shipments),
            "/api/organization/drivers": ("drivers", get_organization_drivers),
            "/api/organization/sensors": ("sensors", get_organization_sensors),
            "/api/organization/alerts": ("alerts", get_organization_alerts),
            "/api/organization/tickets": ("tickets", get_organization_tickets),
            "/api/organization/reports": ("reports", get_organization_reports),
        }
        if path in organization_collections:
            user = self.require_organization_user()
            if not user:
                return
            key, operation = organization_collections[path]
            self.send_json({key: operation(user["organizationId"])})
            return

        if path.startswith("/api/organization/shipments/"):
            user = self.require_organization_user()
            if not user:
                return
            try:
                self.send_json({"shipment": get_organization_shipment(user["organizationId"], path.split("/")[-1])})
            except KeyError as error:
                self.send_json({"error": str(error).strip("'")}, status=404)
            return

        if path.startswith("/api/organization/tickets/"):
            user = self.require_organization_user()
            if not user:
                return
            try:
                self.send_json({"ticket": get_organization_ticket(user["organizationId"], path.split("/")[-1])})
            except KeyError as error:
                self.send_json({"error": str(error).strip("'")}, status=404)
            return

        if path == "/api/driver/dashboard":
            user = self.require_driver_user()
            if not user:
                return
            self.send_json(get_driver_dashboard_data(user.get("driverId")))
            return

        if path == "/api/driver/support":
            user = self.require_driver_user()
            if not user:
                return
            self.send_json({"tickets": get_driver_support_tickets(user.get("driverId"))})
            return

        if path.startswith("/api/driver/shipments/"):
            user = self.require_driver_user()
            if not user:
                return
            try:
                self.send_json({"delivery": get_driver_delivery(user.get("driverId"), path.split("/")[-1])})
            except KeyError as error:
                self.send_json({"error": str(error).strip("'")}, status=404)
            return

        if path == "/api/hospital/dashboard":
            user = self.require_hospital_user()
            if not user:
                return
            self.send_json(get_hospital_dashboard_data(user["organizationId"]))
            return

        if path == "/api/shipments/live":
            user = self.require_hospital_or_support_user()
            if not user:
                return
            try:
                self.send_json(get_live_shipments_for_user(user))
            except ShipmentServiceError as error:
                self.send_json({"error": str(error)}, status=503)
            return

        if path == "/api/shipments":
            if not self.require_admin_user():
                return
            self.send_json({"shipments": get_all_shipments()})
            return

        if path == "/api/dashboard":
            if not self.require_admin_user():
                return
            self.send_json(get_dashboard_data())
            return

        if path.startswith("/api/shipments/"):
            user = self.require_authenticated_user()
            if not user:
                return
            shipment_id = path.split("/")[-1]
            shipment = get_shipment_by_id(shipment_id)

            if shipment is None:
                self.send_json({"error": "Shipment not found"}, status=404)
                return

            if not is_admin_user(user):
                self.send_json({"error": "You do not have access to this shipment"}, status=403)
                return

            self.send_json({"shipment": shipment})
            return

        self.serve_frontend_file(path)
        return

    def handle_v2_monitor_live(self, lot_trip_id):
        try:
            snapshot = V2_MONITORING_SERVICE.get_live_snapshot(lot_trip_id)
        except (LotTripNotFoundError, ValueError):
            self.send_v2_lot_trip_not_found(lot_trip_id)
            return
        self.send_json(
            {
                "success": True,
                "tripIdentity": serialize_trip_identity(snapshot.trip_identity),
                "liveState": serialize_live_state(snapshot.live_state),
                "openAlertCount": snapshot.open_alert_count,
                "latestAlert": serialize_alert(snapshot.latest_alert),
            }
        )

    def handle_v2_monitor_alerts(self, lot_trip_id):
        try:
            alerts = V2_MONITORING_SERVICE.list_alerts(lot_trip_id)
        except (LotTripNotFoundError, ValueError):
            self.send_v2_lot_trip_not_found(lot_trip_id)
            return
        self.send_json(
            {
                "success": True,
                "lotTripId": lot_trip_id,
                "count": len(alerts),
                "alerts": [serialize_alert(alert) for alert in alerts],
            }
        )

    def send_v2_lot_trip_not_found(self, lot_trip_id):
        self.send_json(
            {
                "success": False,
                "error": {
                    "code": "LOT_TRIP_NOT_FOUND",
                    "message": "Lot trip is not registered",
                    "lotTripId": lot_trip_id,
                },
            },
            status=404,
        )

    def serve_frontend_file(self, path):
        """Serves the dashboard from SD/frontend using the same backend server."""
        if path in ["/", "/dashboard", "/admin", "/organization", "/driver", "/support", "/hospital", "/login", "/403"]:
            file_path = FRONTEND_DIR / "index.html"
        else:
            file_path = FRONTEND_DIR / path.lstrip("/")

        try:
            resolved_path = file_path.resolve()
            resolved_path.relative_to(FRONTEND_DIR.resolve())
        except ValueError:
            self.send_json({"error": "Invalid frontend path"}, status=400)
            return

        if not resolved_path.is_file():
            self.send_json({"error": "Endpoint not found"}, status=404)
            return

        content = resolved_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", get_content_type(resolved_path))
        self.send_common_headers()
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def handle_sensor_data(self):
        """Receives raw sensor readings and sends them into backend processing."""
        if not self.require_admin_user():
            return
        try:
            payload = self.read_json_body()
            result = process_sensor_data(payload)
            self.send_json(result, status=201)
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)

    def handle_v2_sensor_data(self):
        """Translate HTTP JSON into the isolated operational telemetry pipeline."""
        try:
            payload = self.read_json_body()
        except ValueError:
            self.send_json(
                {
                    "success": False,
                    "telemetryAccepted": False,
                    "alertPersisted": False,
                    "error": {
                        "code": "INVALID_JSON",
                        "message": "Request body must be a valid JSON object",
                    },
                },
                status=400,
            )
            return

        response = V2_TELEMETRY_HTTP_ADAPTER.handle_post(payload)
        self.send_json(response.body, status=response.status_code)

    def handle_create_or_update_shipment(self):
        """Creates shipment records from real API data instead of hardcoded examples."""
        if not self.require_admin_user():
            return
        try:
            payload = self.read_json_body()
            shipment = create_or_update_shipment(payload)
            self.send_json({"shipment": shipment}, status=201)
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)

    def handle_create_organization_shipment(self):
        """Creates a validated shipment owned by the authenticated organization."""
        user = self.require_organization_user()
        if not user:
            return
        try:
            shipment, created = create_organization_shipment(
                self.read_json_body(),
                user,
                V2_SHIPMENT_REGISTRATION_SERVICE,
            )
            self.send_json({"shipment": shipment, "created": created}, status=201 if created else 200)
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)
        except KeyError as error:
            self.send_json({"error": str(error).strip("'")}, status=404)

    def handle_organization_operation(self, operation, response_key, success_status=200):
        user = self.require_organization_user()
        if not user:
            return
        try:
            record = operation(user["organizationId"], self.read_json_body(), user)
            if isinstance(record, tuple):
                self.send_json({response_key: record[0], "ticket": record[1]}, status=success_status)
            else:
                self.send_json({response_key: record}, status=success_status)
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)
        except KeyError as error:
            self.send_json({"error": str(error).strip("'")}, status=404)

    def handle_driver_operation(self, operation, response_key, success_status=200):
        user = self.require_driver_user()
        if not user:
            return
        try:
            record = operation(user["driverId"], self.read_json_body(), user)
            self.send_json({response_key: record}, status=success_status)
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)
        except KeyError as error:
            self.send_json({"error": str(error).strip("'")}, status=404)

    def handle_support_operation(self, operation, response_key, success_status=200):
        user = self.require_support_user()
        if not user:
            return
        try:
            record = operation(self.read_json_body(), user)
            self.send_json({response_key: record}, status=success_status)
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)
        except KeyError as error:
            self.send_json({"error": str(error).strip("'")}, status=404)

    def handle_create_or_update_organization(self):
        """Stores real hospital, NGO, distributor, or partner records sent by APIs."""
        if not self.require_admin_user():
            return
        try:
            payload = self.read_json_body()
            organization = create_or_update_organization(payload)
            self.send_json({"organization": organization}, status=201)
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)

    def handle_create_or_update_van(self):
        """Stores real van or vehicle records sent by transport/GPS systems."""
        if not self.require_admin_user():
            return
        try:
            payload = self.read_json_body()
            van = create_or_update_van(payload)
            self.send_json({"van": van}, status=201)
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)

    def handle_add_aws_event(self):
        """Stores events forwarded from AWS services such as IoT Core or Lambda."""
        if not self.require_admin_user():
            return
        payload = self.read_json_body()
        event = add_aws_event(payload)
        self.send_json({"event": event}, status=201)

    def handle_login(self):
        try:
            payload = self.read_json_body()
            token, user = authenticate_user(payload.get("username"), payload.get("password"))
            if not user:
                self.send_json({"error": "Invalid username or password"}, status=401)
                return
            self.send_json({"token": token, "user": get_user_profile(user)})
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)

    def handle_admin_create(self, operation, response_key):
        if not self.require_admin_user():
            return
        try:
            record = operation(self.read_json_body())
            self.send_json({response_key: record}, status=201)
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)

    def handle_admin_update(self, operation, response_key, record_id=None):
        if not self.require_admin_user():
            return
        try:
            payload = self.read_json_body()
            record = operation(record_id, payload) if record_id is not None else operation(payload)
            self.send_json({response_key: record})
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)
        except KeyError as error:
            self.send_json({"error": str(error).strip("'\"")}, status=404)

    def handle_login_form(self):
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length).decode("utf-8")
        form = parse_qs(raw_body)
        username = first_form_value(form, "username")
        password = first_form_value(form, "password")
        token, user = authenticate_user(username, password)

        if not user:
            self.redirect("/login?error=invalid")
            return

        target_path = dashboard_path_for_role(user.get("role"))
        self.send_response(303)
        self.send_header("Location", target_path)
        self.send_header("Set-Cookie", f"vitae_token={token}; Path=/; SameSite=Lax")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()

    def read_json_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length).decode("utf-8")

        if not raw_body:
            raise ValueError("Request body cannot be empty")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as error:
            raise ValueError("Request body must be valid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def send_json(self, data, status=200):
        encoded = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_common_headers()
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def require_authenticated_user(self):
        token = self.get_bearer_token()
        user = get_user_by_token(token)

        if not user:
            self.send_json({"error": "Authentication required"}, status=401)
            return None

        return user

    def require_admin_user(self):
        user = self.require_authenticated_user()
        if not user:
            return None

        if not is_admin_user(user):
            self.send_json({"error": "Admin access required"}, status=403)
            return None

        return user

    def require_hospital_user(self):
        user = self.require_authenticated_user()
        if not user:
            return None

        if user.get("role") != "hospital" or not user.get("organizationId"):
            self.send_json({"error": "Hospital access required"}, status=403)
            return None

        return user

    def require_organization_user(self):
        user = self.require_authenticated_user()
        if not user:
            return None
        if not is_organization_user(user):
            self.send_json({"error": "Organization access required"}, status=403)
            return None
        return user

    def require_driver_user(self):
        user = self.require_authenticated_user()
        if not user:
            return None
        if not is_driver_user(user):
            self.send_json({"error": "Driver access required"}, status=403)
            return None
        return user

    def require_support_user(self):
        user = self.require_authenticated_user()
        if not user:
            return None

        if user.get("role") != "support":
            self.send_json({"error": "Support access required"}, status=403)
            return None

        return user

    def require_hospital_or_support_user(self):
        user = self.require_authenticated_user()
        if not user:
            return None

        role = user.get("role")
        if role == "support":
            return user
        if role in ["hospital", "organization_user"] and user.get("organizationId"):
            return user
        if role == "driver" and user.get("driverId"):
            return user

        self.send_json({"error": "Shipment monitoring is not available for this role"}, status=403)
        return None

    def get_bearer_token(self):
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header.removeprefix("Bearer ").strip()
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "vitae_token":
                return value
        return None

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_common_headers()
        self.end_headers()

    def send_common_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")


def run_server():
    server = ThreadingHTTPServer((HOST, PORT), ApiHandler)
    server.daemon_threads = True
    print(f"SD dashboard running at http://{HOST}:{PORT}")
    print("Endpoints: POST /api/login, GET /api/me, GET /api/admin/dashboard, GET /api/hospital/dashboard, GET /api/shipments/live")
    server.serve_forever()


def get_content_type(file_path):
    extension = file_path.suffix.lower()
    if extension == ".html":
        return "text/html; charset=utf-8"
    if extension == ".css":
        return "text/css; charset=utf-8"
    if extension == ".js":
        return "application/javascript; charset=utf-8"
    return "application/octet-stream"


def first_form_value(form, name):
    values = form.get(name, [])
    return values[0] if values else None


def dashboard_path_for_role(role):
    if role == "admin":
        return "/admin"
    if role == "support":
        return "/support"
    if role == "driver":
        return "/driver"
    if role in ["hospital", "organization_user"]:
        return "/organization"
    return "/403"


if __name__ == "__main__":
    run_server()
