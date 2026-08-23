"""Explainable rerouting evaluation over trustworthy route and capability data."""

import os
from dataclasses import dataclass
from enum import Enum
from math import asin, cos, isfinite, radians, sin, sqrt
from typing import Mapping, Optional, Protocol, Tuple, runtime_checkable

try:
    from .facility_capabilities import FacilityCapabilityRegistry
    from .route_duration import RouteDurationProvider, RouteEvidence, RouteStatus, route_evidence_document
    from .trip_identity import TripIdentity
except ImportError:
    from facility_capabilities import FacilityCapabilityRegistry
    from route_duration import RouteDurationProvider, RouteEvidence, RouteStatus, route_evidence_document
    from trip_identity import TripIdentity

MIN_ETA_IMPROVEMENT_MINUTES_ENV = "VITAE_REROUTE_MIN_ETA_IMPROVEMENT_MINUTES"
MIN_DISTANCE_IMPROVEMENT_KM_ENV = "VITAE_REROUTE_MIN_DISTANCE_IMPROVEMENT_KM"
MIN_ETA_IMPROVEMENT_MINUTES = 5.0
MIN_DISTANCE_IMPROVEMENT_KM = 1.0


@dataclass(frozen=True)
class ReroutingConfig:
    minimum_eta_improvement_minutes: float = MIN_ETA_IMPROVEMENT_MINUTES
    minimum_distance_improvement_km: float = MIN_DISTANCE_IMPROVEMENT_KM

    def __post_init__(self):
        _optional_non_negative(self.minimum_eta_improvement_minutes, "minimum_eta_improvement_minutes")
        _optional_non_negative(self.minimum_distance_improvement_km, "minimum_distance_improvement_km")

    @classmethod
    def from_environment(cls, environment=None):
        values = os.environ if environment is None else environment
        return cls(
            _environment_non_negative(values, MIN_ETA_IMPROVEMENT_MINUTES_ENV, MIN_ETA_IMPROVEMENT_MINUTES),
            _environment_non_negative(values, MIN_DISTANCE_IMPROVEMENT_KM_ENV, MIN_DISTANCE_IMPROVEMENT_KM),
        )


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float

    def __post_init__(self):
        if not _finite_number(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if not _finite_number(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")


@dataclass(frozen=True)
class RouteCandidate:
    facility_id: str
    display_name: str
    coordinates: Optional[Coordinates]
    eligible: bool
    eligibility_reason: str
    eta_minutes: Optional[float] = None
    distance_km: Optional[float] = None
    capability_basis: Optional[str] = None
    capability_profile_id: Optional[str] = None
    route_evidence: Optional[RouteEvidence] = None

    def __post_init__(self):
        _required_text(self.facility_id, "facility_id")
        _required_text(self.display_name, "display_name")
        _required_text(self.eligibility_reason, "eligibility_reason")
        _optional_non_negative(self.eta_minutes, "eta_minutes")
        _optional_non_negative(self.distance_km, "distance_km")


@dataclass(frozen=True)
class RouteOptions:
    current_destination: Optional[RouteCandidate]
    alternatives: Tuple[RouteCandidate, ...]
    source_coordinates: Optional[Coordinates] = None
    total_route: Optional[RouteEvidence] = None


@runtime_checkable
class RouteCandidateProvider(Protocol):
    def route_options(self, trip: TripIdentity, current_coordinates: Optional[Coordinates]) -> RouteOptions:
        ...


class ReroutingStatus(str, Enum):
    REROUTE_AVAILABLE = "REROUTE_AVAILABLE"
    REROUTE_RECOMMENDED = "REROUTE_RECOMMENDED"
    NO_BETTER_ALTERNATIVE = "NO_BETTER_ALTERNATIVE"
    INSUFFICIENT_ROUTE_DATA = "INSUFFICIENT_ROUTE_DATA"


class RoutingEvidenceQuality(str, Enum):
    ROUTE_DURATION = "ROUTE_DURATION"
    STRAIGHT_LINE_DISTANCE = "STRAIGHT_LINE_DISTANCE"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class ReroutingEvaluation:
    status: ReroutingStatus
    current_destination: Optional[RouteCandidate]
    recommended_candidate: Optional[RouteCandidate]
    alternatives_considered: int
    reason: str
    comparison_metric: Optional[str] = None
    current_value: Optional[float] = None
    candidate_value: Optional[float] = None
    improvement: Optional[float] = None
    routing_evidence_quality: RoutingEvidenceQuality = RoutingEvidenceQuality.INSUFFICIENT


class ReroutingEvaluator:
    """Choose a meaningfully better eligible destination using comparable facts."""

    def __init__(self, config=None, *, minimum_eta_improvement_minutes=None, minimum_distance_improvement_km=None):
        selected = config or ReroutingConfig()
        self._minimum_eta_improvement_minutes = selected.minimum_eta_improvement_minutes if minimum_eta_improvement_minutes is None else minimum_eta_improvement_minutes
        self._minimum_distance_improvement_km = selected.minimum_distance_improvement_km if minimum_distance_improvement_km is None else minimum_distance_improvement_km
        _optional_non_negative(self._minimum_eta_improvement_minutes, "minimum_eta_improvement_minutes")
        _optional_non_negative(self._minimum_distance_improvement_km, "minimum_distance_improvement_km")

    def evaluate(self, options):
        alternatives, current = tuple(options.alternatives), options.current_destination
        if current is None:
            return self._insufficient(current, alternatives, "The configured destination could not be resolved.")
        eligible = tuple(candidate for candidate in alternatives if candidate.eligible)
        if not eligible:
            return ReroutingEvaluation(ReroutingStatus.NO_BETTER_ALTERNATIVE, current, None, len(alternatives), "No alternative has documented administrative and product capability eligibility.")
        comparison = self._comparable_candidates(current, eligible)
        if comparison is None:
            return self._insufficient(current, alternatives, "Comparable route duration or straight-line distance is unavailable for eligible alternatives.")
        metric, current_value, comparable, minimum_improvement, quality = comparison
        best = min(comparable, key=lambda candidate: (_metric(candidate, metric), candidate.facility_id))
        best_value = _metric(best, metric)
        improvement = current_value - best_value
        unit = "minutes" if metric == "ROUTE_DURATION_MINUTES" else "km"
        if improvement < minimum_improvement:
            return ReroutingEvaluation(ReroutingStatus.NO_BETTER_ALTERNATIVE, current, None, len(alternatives), f"No eligible alternative improves the comparable route by at least {minimum_improvement:g} {unit}.", metric, current_value, best_value, improvement, quality)
        description = "route duration" if metric == "ROUTE_DURATION_MINUTES" else "straight-line distance"
        return ReroutingEvaluation(ReroutingStatus.REROUTE_AVAILABLE, current, best, len(alternatives), f"Eligible product-compatible facility reduces {description} by {improvement:.1f} {unit}.", metric, current_value, best_value, improvement, quality)

    def _comparable_candidates(self, current, eligible):
        if _has_route_duration(current):
            candidates = tuple(candidate for candidate in eligible if _has_route_duration(candidate))
            if candidates:
                return "ROUTE_DURATION_MINUTES", current.eta_minutes, candidates, self._minimum_eta_improvement_minutes, RoutingEvidenceQuality.ROUTE_DURATION
            return None
        if current.distance_km is not None:
            candidates = tuple(candidate for candidate in eligible if not _has_route_duration(candidate) and candidate.distance_km is not None)
            if candidates:
                return "DISTANCE_KM", current.distance_km, candidates, self._minimum_distance_improvement_km, RoutingEvidenceQuality.STRAIGHT_LINE_DISTANCE
        return None

    @staticmethod
    def _insufficient(current, alternatives, reason):
        return ReroutingEvaluation(ReroutingStatus.INSUFFICIENT_ROUTE_DATA, current, None, len(alternatives), reason)


class ApplicationFacilityRouteCandidateProvider:
    """Build candidates from exact links, capability metadata, and optional routes."""

    def __init__(self, shipments, facilities, organizations=None, *, capability_registry=None, route_duration_provider=None):
        self._shipments = shipments
        self._facilities = facilities
        self._organizations = organizations or {}
        self._capability_registry = capability_registry
        self._route_duration_provider = route_duration_provider

    def route_options(self, trip, current_coordinates):
        matches = tuple(shipment for shipment in self._shipments.values() if shipment.get("lotTripId") == trip.lot_trip_id)
        if len(matches) != 1:
            return RouteOptions(None, ())
        shipment = matches[0]
        current_facility = self._resolve_destination(shipment, trip.destination)
        current = self._candidate(current_facility, current_coordinates, eligible=True, eligibility_reason="Configured shipment destination.", capability_basis="CONFIGURED_DESTINATION")
        organization_id = shipment.get("organizationId")
        alternatives = []
        for facility in self._facilities.values():
            if current_facility and facility.get("facilityId") == current_facility.get("facilityId"):
                continue
            administrative = facility.get("type") == "receiving" and bool(organization_id) and facility.get("organizationId") == organization_id
            capability = self._capability_registry.evaluate(trip, facility) if administrative and self._capability_registry is not None else None
            eligible = bool(administrative and capability and capability.eligible)
            if not administrative:
                reason = "Facility is not a documented receiving location for this shipment organization."
            elif capability is None:
                reason = "Facility product capability could not be confirmed."
            else:
                reason = capability.reason
            alternatives.append(self._candidate(facility, current_coordinates, eligible=eligible, eligibility_reason=reason, capability_basis=(capability.evidence_kind if capability else None), capability_profile_id=(capability.capability_profile_id if capability else None)))
        source = self._source_coordinates(shipment)
        total_route = self._route(source, current.coordinates if current else None)
        return RouteOptions(current, tuple(candidate for candidate in alternatives if candidate), source, total_route)

    def _candidate(self, facility, current_coordinates, *, eligible, eligibility_reason, capability_basis, capability_profile_id=None):
        if not facility:
            return None
        coordinates = self._coordinates_from_value(facility.get("gps"))
        distance = straight_line_distance_km(current_coordinates, coordinates) if current_coordinates is not None and coordinates is not None else None
        route = self._route(current_coordinates, coordinates) if eligible else None
        eta = route.duration_seconds / 60 if route is not None and route.status == RouteStatus.AVAILABLE else None
        return RouteCandidate(str(facility.get("facilityId") or "").strip(), str(facility.get("name") or "").strip(), coordinates, eligible, eligibility_reason, eta, distance, capability_basis, capability_profile_id, route)

    def _route(self, origin, destination):
        if self._route_duration_provider is None or origin is None or destination is None:
            return None
        try:
            result = self._route_duration_provider.get_route(origin, destination)
        except Exception:
            return None
        return result if isinstance(result, RouteEvidence) else None

    def _source_coordinates(self, shipment):
        source = self._coordinates_from_value(shipment.get("originGps"))
        if source is None:
            origin = self._facilities.get(shipment.get("originFacilityId"))
            source = self._coordinates_from_value(origin.get("gps") if origin else None)
        return source

    def _resolve_destination(self, shipment, trip_destination):
        facility_id = shipment.get("destinationFacilityId")
        if facility_id in self._facilities:
            return self._facilities[facility_id]
        expected_names = {_normalized_name(trip_destination), _normalized_name(shipment.get("destination")), _normalized_name(shipment.get("destinationHospitalName"))}
        matches = tuple(facility for facility in self._facilities.values() if _normalized_name(facility.get("name")) in expected_names)
        if len(matches) == 1:
            return matches[0]
        organization_id = shipment.get("destinationHospitalId")
        organization = self._organizations.get(organization_id)
        coordinates = self._coordinates_from_value(organization.get("gps") if organization else None)
        if organization_id and coordinates is not None:
            return {"facilityId": organization_id, "name": shipment.get("destinationHospitalName") or shipment.get("destination") or organization.get("name"), "gps": {"lat": coordinates.latitude, "lng": coordinates.longitude}}
        return None

    @staticmethod
    def _coordinates_from_value(value):
        if not isinstance(value, Mapping):
            return None
        latitude, longitude = value.get("lat"), value.get("lng")
        if not _finite_number(latitude) or not _finite_number(longitude):
            return None
        try:
            return Coordinates(float(latitude), float(longitude))
        except ValueError:
            return None


def straight_line_distance_km(origin, destination):
    earth_radius_km = 6371.0
    lat1, lon1, lat2, lon2 = map(radians, [origin.latitude, origin.longitude, destination.latitude, destination.longitude])
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    return earth_radius_km * 2 * asin(sqrt(value))


def route_candidate_document(candidate):
    if candidate is None:
        return None
    return {"facilityId": candidate.facility_id, "displayName": candidate.display_name, "coordinates": coordinates_document(candidate.coordinates), "eligible": candidate.eligible, "eligibilityReason": candidate.eligibility_reason, "etaMinutes": candidate.eta_minutes, "distanceKm": candidate.distance_km, "capabilityBasis": candidate.capability_basis, "capabilityProfileId": candidate.capability_profile_id, "routeEvidence": route_evidence_document(candidate.route_evidence)}


def rerouting_evaluation_document(value):
    return {"status": value.status.value, "currentDestination": route_candidate_document(value.current_destination), "recommendedCandidate": route_candidate_document(value.recommended_candidate), "alternativesConsidered": value.alternatives_considered, "reason": value.reason, "routingEvidenceQuality": value.routing_evidence_quality.value, "decisionFactors": {"comparisonMetric": value.comparison_metric, "currentDestinationValue": value.current_value, "candidateValue": value.candidate_value, "improvement": value.improvement}}


def coordinates_document(value):
    return None if value is None else {"latitude": value.latitude, "longitude": value.longitude}


def _has_route_duration(candidate):
    return candidate.eta_minutes is not None and candidate.route_evidence is not None and candidate.route_evidence.status == RouteStatus.AVAILABLE


def _metric(candidate, metric):
    return candidate.eta_minutes if metric == "ROUTE_DURATION_MINUTES" else candidate.distance_km


def _normalized_name(value):
    return " ".join(str(value or "").strip().casefold().split())


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _optional_non_negative(value, field_name):
    if value is not None and (not _finite_number(value) or value < 0):
        raise ValueError(f"{field_name} must be a non-negative finite number")


def _environment_non_negative(values, name, default):
    raw = values.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    _optional_non_negative(value, name)
    return value


def _required_text(value, field_name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()
