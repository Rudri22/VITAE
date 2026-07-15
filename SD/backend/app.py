from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from .aws_shipment_service import ShipmentServiceError, get_live_shipments_for_user
    from .sensor_processor import process_sensor_data
    from .storage import (
        add_aws_event,
        authenticate_user,
        create_or_update_organization,
        create_or_update_shipment,
        create_or_update_van,
        get_all_shipments,
        get_admin_dashboard_data,
        get_dashboard_data,
        get_hospital_dashboard_data,
        get_support_dashboard_data,
        get_shipment_by_id,
        get_user_by_token,
        get_user_profile,
        is_admin_user,
    )
except ImportError:
    from aws_shipment_service import ShipmentServiceError, get_live_shipments_for_user
    from sensor_processor import process_sensor_data
    from storage import (
        add_aws_event,
        authenticate_user,
        create_or_update_organization,
        create_or_update_shipment,
        create_or_update_van,
        get_all_shipments,
        get_admin_dashboard_data,
        get_dashboard_data,
        get_hospital_dashboard_data,
        get_support_dashboard_data,
        get_shipment_by_id,
        get_user_by_token,
        get_user_profile,
        is_admin_user,
    )


HOST = "127.0.0.1"
PORT = 8000
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


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

        if path == "/api/sensor-data":
            self.handle_sensor_data()
            return

        if path == "/api/shipments":
            self.handle_create_or_update_shipment()
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

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_common_headers()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

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
            self.send_json(get_admin_dashboard_data())
            return

        if path == "/api/support/dashboard":
            user = self.require_support_user()
            if not user:
                return
            self.send_json(get_support_dashboard_data())
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

    def serve_frontend_file(self, path):
        """Serves the dashboard from SD/frontend using the same backend server."""
        if path in ["/", "/dashboard", "/admin", "/support", "/hospital", "/login", "/403"]:
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
            return json.loads(raw_body)
        except json.JSONDecodeError as error:
            raise ValueError("Request body must be valid JSON") from error

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
        if role == "hospital" and user.get("organizationId"):
            return user

        self.send_json({"error": "Shipment monitoring is only available to hospital and support users"}, status=403)
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")


def run_server():
    server = HTTPServer((HOST, PORT), ApiHandler)
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
    return "/hospital"


if __name__ == "__main__":
    run_server()
