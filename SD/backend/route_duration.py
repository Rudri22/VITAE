"""Backend route-duration providers and a small deterministic TTL cache."""

import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from math import isfinite
from threading import RLock
from typing import Callable, Dict, Mapping, Optional, Protocol, Tuple, runtime_checkable
from urllib.request import Request, urlopen

GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
ROUTE_PROVIDER_ENV = "VITAE_ROUTE_PROVIDER"
GOOGLE_ROUTES_API_KEY_ENV = "VITAE_GOOGLE_ROUTES_API_KEY"
ROUTE_CACHE_TTL_SECONDS_ENV = "VITAE_ROUTE_CACHE_TTL_SECONDS"
ROUTE_TIMEOUT_SECONDS_ENV = "VITAE_ROUTE_TIMEOUT_SECONDS"
DEFAULT_ROUTE_CACHE_TTL_SECONDS = 300.0
DEFAULT_ROUTE_TIMEOUT_SECONDS = 5.0


class RouteProviderKind(str, Enum):
    NONE = "none"
    GOOGLE_ROUTES = "google_routes"


class RouteStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class RouteRequestContext:
    travel_mode: str = "DRIVE"

    def __post_init__(self):
        if self.travel_mode not in {"DRIVE"}:
            raise ValueError("Only DRIVE travel mode is currently supported")


@dataclass(frozen=True)
class RouteEvidence:
    status: RouteStatus
    provider: str
    calculated_at: datetime
    travel_mode: str
    duration_seconds: Optional[float] = None
    distance_meters: Optional[float] = None
    unavailable_reason: Optional[str] = None
    cache_expires_at: Optional[datetime] = None

    def __post_init__(self):
        if not isinstance(self.status, RouteStatus):
            raise ValueError("Route evidence status is invalid")
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("Route evidence provider is required")
        if (
            not isinstance(self.calculated_at, datetime)
            or self.calculated_at.tzinfo is None
            or self.calculated_at.utcoffset() is None
        ):
            raise ValueError("Route evidence calculated_at must be timezone-aware")
        _optional_non_negative(self.duration_seconds, "duration_seconds")
        _optional_non_negative(self.distance_meters, "distance_meters")
        if self.status == RouteStatus.AVAILABLE:
            if self.duration_seconds is None or self.distance_meters is None:
                raise ValueError("Available route evidence requires duration and distance")
            if self.unavailable_reason is not None:
                raise ValueError("Available route evidence cannot have an unavailable reason")
        elif not isinstance(self.unavailable_reason, str) or not self.unavailable_reason:
            raise ValueError("Unavailable route evidence requires a stable reason")
        if self.cache_expires_at is not None:
            if (
                not isinstance(self.cache_expires_at, datetime)
                or self.cache_expires_at.tzinfo is None
                or self.cache_expires_at.utcoffset() is None
                or self.cache_expires_at < self.calculated_at
            ):
                raise ValueError("cache_expires_at must be an aware time after calculation")


@runtime_checkable
class CoordinatePoint(Protocol):
    latitude: float
    longitude: float


@runtime_checkable
class RouteDurationProvider(Protocol):
    def get_route(
        self,
        origin: CoordinatePoint,
        destination: CoordinatePoint,
        context: RouteRequestContext = RouteRequestContext(),
    ) -> RouteEvidence:
        ...


class RouteDurationConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class RouteDurationConfig:
    provider: RouteProviderKind = RouteProviderKind.NONE
    google_routes_api_key: Optional[str] = None
    cache_ttl_seconds: float = DEFAULT_ROUTE_CACHE_TTL_SECONDS
    timeout_seconds: float = DEFAULT_ROUTE_TIMEOUT_SECONDS

    def __post_init__(self):
        if not isinstance(self.provider, RouteProviderKind):
            raise RouteDurationConfigurationError("Route provider is invalid")
        _positive_config(self.cache_ttl_seconds, "cache_ttl_seconds")
        _positive_config(self.timeout_seconds, "timeout_seconds")

    @classmethod
    def from_environment(cls, environment: Optional[Mapping[str, str]] = None):
        values = os.environ if environment is None else environment
        raw_provider = str(values.get(ROUTE_PROVIDER_ENV, "none")).strip().lower()
        try:
            provider = RouteProviderKind(raw_provider)
        except ValueError as error:
            raise RouteDurationConfigurationError(
                f"{ROUTE_PROVIDER_ENV} must be none or google_routes"
            ) from error
        return cls(
            provider=provider,
            google_routes_api_key=(
                str(values.get(GOOGLE_ROUTES_API_KEY_ENV) or "").strip() or None
            ),
            cache_ttl_seconds=_environment_positive_number(
                values,
                ROUTE_CACHE_TTL_SECONDS_ENV,
                DEFAULT_ROUTE_CACHE_TTL_SECONDS,
            ),
            timeout_seconds=_environment_positive_number(
                values, ROUTE_TIMEOUT_SECONDS_ENV, DEFAULT_ROUTE_TIMEOUT_SECONDS
            ),
        )


class GoogleRoutesDurationProvider:
    """Minimal Google Routes v2 adapter; credentials never enter route evidence."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = DEFAULT_ROUTE_TIMEOUT_SECONDS,
        transport: Optional[Callable] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        if not isinstance(api_key, str) or not api_key.strip():
            raise RouteDurationConfigurationError("Google Routes API key is required")
        _positive_config(timeout_seconds, "timeout_seconds")
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds
        self._transport = transport or _urlopen_transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def get_route(self, origin, destination, context=RouteRequestContext()):
        calculated_at = self._clock()
        payload = {
            "origin": {"location": {"latLng": _lat_lng(origin)}},
            "destination": {"location": {"latLng": _lat_lng(destination)}},
            "travelMode": context.travel_mode,
            "routingPreference": "TRAFFIC_AWARE",
        }
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
        }
        try:
            response = self._transport(
                GOOGLE_ROUTES_URL,
                json.dumps(payload).encode("utf-8"),
                headers,
                self._timeout_seconds,
            )
            return _google_route_evidence(response, calculated_at, context.travel_mode)
        except Exception:
            return RouteEvidence(
                status=RouteStatus.UNAVAILABLE,
                provider="GOOGLE_ROUTES",
                calculated_at=calculated_at,
                travel_mode=context.travel_mode,
                unavailable_reason="ROUTE_PROVIDER_UNAVAILABLE",
            )


class CachedRouteDurationProvider:
    """Cache successful route evidence for a bounded period using an injected clock."""

    def __init__(
        self,
        provider: RouteDurationProvider,
        ttl_seconds: float,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        if not isinstance(provider, RouteDurationProvider):
            raise TypeError("provider must support RouteDurationProvider")
        _positive_config(ttl_seconds, "ttl_seconds")
        self._provider = provider
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._entries: Dict[Tuple[object, ...], Tuple[datetime, RouteEvidence]] = {}
        self._lock = RLock()

    def get_route(self, origin, destination, context=RouteRequestContext()):
        key = (
            origin.latitude,
            origin.longitude,
            destination.latitude,
            destination.longitude,
            context.travel_mode,
        )
        now = self._clock()
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None and now < cached[0]:
                return cached[1]
        evidence = self._provider.get_route(origin, destination, context)
        if evidence.status == RouteStatus.AVAILABLE:
            expires_at = now + self._ttl
            evidence = replace(evidence, cache_expires_at=expires_at)
            with self._lock:
                self._entries[key] = (expires_at, evidence)
        return evidence


def compose_route_duration_provider(
    config: RouteDurationConfig,
) -> Optional[RouteDurationProvider]:
    if config.provider == RouteProviderKind.NONE:
        return None
    if not str(config.google_routes_api_key or "").strip():
        return None
    provider = GoogleRoutesDurationProvider(
        config.google_routes_api_key,
        timeout_seconds=config.timeout_seconds,
    )
    return CachedRouteDurationProvider(provider, config.cache_ttl_seconds)


def route_evidence_document(value):
    if value is None:
        return None
    return {
        "status": value.status.value,
        "provider": value.provider,
        "calculatedAt": value.calculated_at.isoformat(),
        "travelMode": value.travel_mode,
        "durationSeconds": value.duration_seconds,
        "distanceMeters": value.distance_meters,
        "unavailableReason": value.unavailable_reason,
        "cacheExpiresAt": (
            value.cache_expires_at.isoformat() if value.cache_expires_at else None
        ),
    }


def _google_route_evidence(response, calculated_at, travel_mode):
    if isinstance(response, bytes):
        response = json.loads(response.decode("utf-8"))
    if not isinstance(response, dict):
        raise ValueError("Google Routes response must be an object")
    routes = response.get("routes")
    if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
        raise ValueError("Google Routes response has no route")
    route = routes[0]
    duration = _duration_seconds(route.get("duration"))
    distance = route.get("distanceMeters")
    if not _non_negative_number(distance):
        raise ValueError("Google Routes distance is invalid")
    return RouteEvidence(
        status=RouteStatus.AVAILABLE,
        provider="GOOGLE_ROUTES",
        calculated_at=calculated_at,
        travel_mode=travel_mode,
        duration_seconds=duration,
        distance_meters=float(distance),
    )


def _duration_seconds(value):
    if not isinstance(value, str) or not value.endswith("s"):
        raise ValueError("Google Routes duration is invalid")
    try:
        result = float(value[:-1])
    except ValueError as error:
        raise ValueError("Google Routes duration is invalid") from error
    if not _non_negative_number(result):
        raise ValueError("Google Routes duration is invalid")
    return result


def _urlopen_transport(url, body, headers, timeout_seconds):
    request = Request(url, data=body, headers=headers, method="POST")
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def _lat_lng(value):
    return {"latitude": value.latitude, "longitude": value.longitude}


def _environment_positive_number(values, name, default):
    raw = values.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise RouteDurationConfigurationError(f"{name} must be a number") from error
    _positive_config(value, name)
    return value


def _positive_config(value, field_name):
    if not _positive_number(value):
        raise RouteDurationConfigurationError(
            f"{field_name} must be a positive finite number"
        )


def _optional_non_negative(value, field_name):
    if value is not None and not _non_negative_number(value):
        raise ValueError(f"{field_name} must be a non-negative finite number")


def _positive_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
        and value > 0
    )


def _non_negative_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
        and value >= 0
    )
