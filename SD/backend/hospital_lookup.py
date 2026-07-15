import json
from math import asin, cos, radians, sin, sqrt
from urllib.parse import urlencode
from urllib.request import urlopen


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_SEARCH_RADIUS_METERS = 10000


def find_nearest_hospital(lat, lng):
    """Finds the closest real hospital using the OpenStreetMap Overpass API."""
    hospitals = fetch_hospitals_from_overpass(lat, lng)

    if not hospitals:
        return None

    nearest = None
    nearest_distance = None

    for hospital in hospitals:
        distance = distance_km(lat, lng, hospital["lat"], hospital["lng"])

        if nearest_distance is None or distance < nearest_distance:
            nearest = hospital
            nearest_distance = distance

    return {
        "id": nearest["id"],
        "name": nearest["name"],
        "lat": nearest["lat"],
        "lng": nearest["lng"],
        "distanceKm": round(nearest_distance, 2),
    }


def fetch_hospitals_from_overpass(lat, lng, radius_meters=DEFAULT_SEARCH_RADIUS_METERS):
    query = f"""
    [out:json][timeout:10];
    (
      node["amenity"="hospital"](around:{radius_meters},{lat},{lng});
      way["amenity"="hospital"](around:{radius_meters},{lat},{lng});
      relation["amenity"="hospital"](around:{radius_meters},{lat},{lng});
    );
    out center tags;
    """
    url = f"{OVERPASS_URL}?{urlencode({'data': query})}"

    try:
        with urlopen(url, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []

    hospitals = []
    for element in data.get("elements", []):
        element_lat = element.get("lat") or element.get("center", {}).get("lat")
        element_lng = element.get("lon") or element.get("center", {}).get("lon")

        if element_lat is None or element_lng is None:
            continue

        tags = element.get("tags", {})
        hospitals.append({
            "id": str(element.get("id")),
            "name": tags.get("name", "Unnamed hospital"),
            "lat": float(element_lat),
            "lng": float(element_lng),
        })

    return hospitals


def distance_km(lat1, lng1, lat2, lng2):
    earth_radius_km = 6371
    lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])

    delta_lat = lat2 - lat1
    delta_lng = lng2 - lng1

    a = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lng / 2) ** 2
    c = 2 * asin(sqrt(a))

    return earth_radius_km * c
