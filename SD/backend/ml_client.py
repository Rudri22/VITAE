import json
import os
from urllib.request import Request, urlopen


def get_spoilage_prediction(shipment, reading):
    """Calls the real ML prediction API when ML_API_URL is configured."""
    ml_api_url = os.getenv("ML_API_URL")

    if not ml_api_url:
        return {
            "status": "not_configured",
            "message": "Set ML_API_URL to connect the backend to the ML prediction API",
        }

    payload = {
        "shipment": shipment,
        "latestReading": reading,
    }

    request = Request(
        ml_api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as error:
        return {
            "status": "error",
            "message": "ML prediction API request failed",
            "details": str(error),
        }
