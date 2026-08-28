import unittest
from datetime import datetime, timezone

try:
    from .facility_capabilities import ApplicationFacilityCapabilityRegistry
    from .rerouting import (
        ApplicationFacilityRouteCandidateProvider,
        Coordinates,
        ReroutingEvaluator,
        ReroutingStatus,
        RouteCandidate,
        RouteOptions,
        rerouting_evaluation_document,
    )
    from .route_duration import RouteEvidence, RouteStatus
except ImportError:
    from facility_capabilities import ApplicationFacilityCapabilityRegistry
    from rerouting import (
        ApplicationFacilityRouteCandidateProvider,
        Coordinates,
        ReroutingEvaluator,
        ReroutingStatus,
        RouteCandidate,
        RouteOptions,
        rerouting_evaluation_document,
    )
    from route_duration import RouteEvidence, RouteStatus


class ReroutingEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.evaluator = ReroutingEvaluator()
        self.current = candidate("current", eta=80, distance=30)

    def test_no_candidate_provider_data_is_insufficient(self):
        result = self.evaluator.evaluate(RouteOptions(None, ()))
        self.assertEqual(result.status, ReroutingStatus.INSUFFICIENT_ROUTE_DATA)
        self.assertIsNone(result.recommended_candidate)

    def test_eligible_substantially_shorter_eta_is_available(self):
        result = self.evaluator.evaluate(
            RouteOptions(self.current, (candidate("better", eta=40),))
        )
        self.assertEqual(result.status, ReroutingStatus.REROUTE_AVAILABLE)
        self.assertEqual(result.recommended_candidate.facility_id, "better")
        self.assertEqual(result.comparison_metric, "ROUTE_DURATION_MINUTES")
        self.assertEqual(result.improvement, 40)

    def test_closer_but_ineligible_candidate_is_never_recommended(self):
        result = self.evaluator.evaluate(
            RouteOptions(
                self.current,
                (candidate("ineligible", eta=5, eligible=False),),
            )
        )
        self.assertEqual(result.status, ReroutingStatus.NO_BETTER_ALTERNATIVE)
        self.assertIsNone(result.recommended_candidate)

    def test_best_eligible_candidate_is_deterministic_across_input_order(self):
        first = candidate("facility-z", eta=45)
        best = candidate("facility-a", eta=35)
        slow = candidate("facility-b", eta=60)
        forward = self.evaluator.evaluate(
            RouteOptions(self.current, (first, best, slow))
        )
        reverse = self.evaluator.evaluate(
            RouteOptions(self.current, (slow, best, first))
        )
        self.assertEqual(forward.recommended_candidate, best)
        self.assertEqual(reverse.recommended_candidate, best)

    def test_slower_alternative_is_not_available(self):
        result = self.evaluator.evaluate(
            RouteOptions(self.current, (candidate("slower", eta=90),))
        )
        self.assertEqual(result.status, ReroutingStatus.NO_BETTER_ALTERNATIVE)
        self.assertIsNone(result.recommended_candidate)

    def test_real_route_duration_overrides_closer_straight_line_distance(self):
        current = candidate("current", eta=40, distance=20)
        closer_but_slower = candidate("closer", eta=48, distance=2)
        result = self.evaluator.evaluate(RouteOptions(current, (closer_but_slower,)))
        self.assertEqual(result.status, ReroutingStatus.NO_BETTER_ALTERNATIVE)
        self.assertEqual(result.comparison_metric, "ROUTE_DURATION_MINUTES")

    def test_eta_improvement_below_threshold_does_not_reroute(self):
        alternative = candidate("candidate", eta=36)
        result = self.evaluator.evaluate(
            RouteOptions(candidate("current", eta=40), (alternative,))
        )
        self.assertEqual(result.status, ReroutingStatus.NO_BETTER_ALTERNATIVE)
        self.assertEqual(result.evaluated_candidates, (alternative,))
        self.assertEqual(result.minimum_required_improvement, 5.0)
        document = rerouting_evaluation_document(result)
        self.assertEqual(document["evaluatedCandidates"][0]["facilityId"], "candidate")
        self.assertEqual(
            document["decisionFactors"]["minimumRequiredImprovement"], 5.0
        )

    def test_missing_route_duration_and_distance_is_insufficient(self):
        current = candidate("current", eta=None, distance=None)
        alternative = candidate("alternative", eta=None, distance=None)
        result = self.evaluator.evaluate(RouteOptions(current, (alternative,)))
        self.assertEqual(result.status, ReroutingStatus.INSUFFICIENT_ROUTE_DATA)

    def test_distance_is_used_only_when_eta_is_unavailable_for_current_route(self):
        current = candidate("current", eta=None, distance=12)
        alternative = candidate("alternative", eta=None, distance=8)
        result = self.evaluator.evaluate(RouteOptions(current, (alternative,)))
        self.assertEqual(result.status, ReroutingStatus.REROUTE_AVAILABLE)
        self.assertEqual(result.comparison_metric, "DISTANCE_KM")
        self.assertAlmostEqual(result.improvement, 4)


class ApplicationFacilityRouteCandidateProviderTests(unittest.TestCase):
    def test_exact_application_records_supply_coordinates_without_inventing_eta(self):
        shipments = {
            "shipment-1": {
                "lotTripId": "lot-trip-1",
                "organizationId": "org-1",
                "originFacilityId": "origin",
                "destinationFacilityId": "destination",
                "originGps": {"lat": 33.8, "lng": 35.4},
            }
        }
        facilities = {
            "origin": facility("origin", "warehouse", 33.8, 35.4),
            "destination": facility("destination", "receiving", 33.9, 35.5),
            "alternative": facility("alternative", "receiving", 33.85, 35.45),
            "other-org": facility(
                "other-org", "receiving", 33.81, 35.41, organization="org-2"
            ),
        }
        facilities["alternative"]["capabilityProfileId"] = "profile-1"
        provider = ApplicationFacilityRouteCandidateProvider(
            shipments,
            facilities,
            capability_registry=ApplicationFacilityCapabilityRegistry(
                {"profile-1": {"supportedProductIds": ["product-1"]}}
            ),
        )
        trip = StubTrip("lot-trip-1", "Configured destination", "product-1")
        options = provider.route_options(trip, Coordinates(33.82, 35.42))

        self.assertEqual(options.current_destination.facility_id, "destination")
        self.assertIsNone(options.current_destination.eta_minutes)
        candidates = {value.facility_id: value for value in options.alternatives}
        self.assertTrue(candidates["alternative"].eligible)
        self.assertFalse(candidates["other-org"].eligible)
        self.assertGreater(candidates["alternative"].distance_km, 0)

    def test_real_routes_and_product_capability_make_alternative_eligible(self):
        shipments = {
            "shipment-1": {
                "lotTripId": "lot-trip-1",
                "organizationId": "org-1",
                "originGps": {"lat": 33.8, "lng": 35.4},
                "destinationFacilityId": "destination",
            }
        }
        facilities = {
            "destination": facility("destination", "receiving", 33.9, 35.5),
            "alternative": {
                **facility("alternative", "receiving", 33.85, 35.45),
                "capabilityProfileId": "profile-1",
            },
        }
        routes = _RouteProvider({
            (33.82, 35.42, 33.9, 35.5): (3000, 20000),
            (33.82, 35.42, 33.85, 35.45): (900, 7000),
            (33.8, 35.4, 33.9, 35.5): (3600, 25000),
        })
        provider = ApplicationFacilityRouteCandidateProvider(
            shipments,
            facilities,
            capability_registry=ApplicationFacilityCapabilityRegistry(
                {"profile-1": {"supportedProductIds": ["product-1"], "evidenceKind": "DEMO"}}
            ),
            route_duration_provider=routes,
        )
        options = provider.route_options(
            StubTrip("lot-trip-1", "Destination", "product-1"),
            Coordinates(33.82, 35.42),
        )
        alternative = options.alternatives[0]
        self.assertTrue(alternative.eligible)
        self.assertEqual(alternative.eta_minutes, 15)
        self.assertEqual(alternative.capability_profile_id, "profile-1")
        self.assertEqual(options.current_destination.eta_minutes, 50)
        self.assertEqual(options.total_route.duration_seconds, 3600)

    def test_missing_or_incompatible_capability_is_ineligible(self):
        shipments = {"s": {"lotTripId": "lot", "organizationId": "org-1", "destinationFacilityId": "destination"}}
        facilities = {
            "destination": facility("destination", "receiving", 33.9, 35.5),
            "missing": facility("missing", "receiving", 33.8, 35.4),
            "wrong": {**facility("wrong", "receiving", 33.7, 35.3), "capabilityProfileId": "profile"},
        }
        provider = ApplicationFacilityRouteCandidateProvider(
            shipments,
            facilities,
            capability_registry=ApplicationFacilityCapabilityRegistry(
                {"profile": {"supportedProductIds": ["different-product"]}}
            ),
        )
        options = provider.route_options(StubTrip("lot", "Destination", "product-1"), None)
        candidates = {item.facility_id: item for item in options.alternatives}
        self.assertFalse(candidates["missing"].eligible)
        self.assertIn("missing", candidates["missing"].eligibility_reason.lower())
        self.assertFalse(candidates["wrong"].eligible)
        self.assertIn("does not support", candidates["wrong"].eligibility_reason)

    def test_one_failed_candidate_route_does_not_block_other_candidate(self):
        shipments = {"s": {"lotTripId": "lot", "organizationId": "org-1", "destinationFacilityId": "destination"}}
        facilities = {
            "destination": facility("destination", "receiving", 33.9, 35.5),
            "failed": {**facility("failed", "receiving", 33.7, 35.3), "capabilityProfileId": "profile"},
            "working": {**facility("working", "receiving", 33.85, 35.45), "capabilityProfileId": "profile"},
        }
        routes = _RouteProvider({
            (33.82, 35.42, 33.9, 35.5): (3000, 20000),
            (33.82, 35.42, 33.85, 35.45): (900, 7000),
        })
        provider = ApplicationFacilityRouteCandidateProvider(
            shipments,
            facilities,
            capability_registry=ApplicationFacilityCapabilityRegistry(
                {"profile": {"supportedProductIds": ["product-1"]}}
            ),
            route_duration_provider=routes,
        )
        options = provider.route_options(
            StubTrip("lot", "Destination", "product-1"), Coordinates(33.82, 35.42)
        )
        candidates = {item.facility_id: item for item in options.alternatives}
        self.assertIsNone(candidates["failed"].eta_minutes)
        self.assertEqual(candidates["working"].eta_minutes, 15)
        result = ReroutingEvaluator().evaluate(options)
        self.assertEqual(result.recommended_candidate.facility_id, "working")

    def test_exact_destination_organization_link_can_supply_destination_coordinates(self):
        shipments = {
            "shipment-1": {
                "lotTripId": "lot-trip-1",
                "organizationId": "org-1",
                "destinationHospitalId": "org-1",
                "destinationHospitalName": "Receiving Organization",
            }
        }
        organizations = {
            "org-1": {
                "organizationId": "org-1",
                "name": "Receiving Organization",
                "gps": {"lat": 33.9, "lng": 35.5},
            }
        }
        provider = ApplicationFacilityRouteCandidateProvider(
            shipments, {}, organizations
        )
        options = provider.route_options(
            StubTrip("lot-trip-1", "Receiving Organization", "product-1"),
            Coordinates(33.8, 35.4),
        )
        self.assertEqual(options.current_destination.facility_id, "org-1")
        self.assertEqual(
            options.current_destination.coordinates, Coordinates(33.9, 35.5)
        )
        self.assertIsNone(options.current_destination.eta_minutes)


class StubTrip:
    def __init__(self, lot_trip_id, destination, product_id):
        self.lot_trip_id = lot_trip_id
        self.destination = destination
        self.product_id = product_id


def candidate(facility_id, *, eta=None, distance=None, eligible=True):
    route = (
        RouteEvidence(
            RouteStatus.AVAILABLE,
            "FAKE_ROUTES",
            datetime(2026, 8, 23, tzinfo=timezone.utc),
            "DRIVE",
            duration_seconds=eta * 60,
            distance_meters=10000,
        )
        if eta is not None
        else None
    )
    return RouteCandidate(
        facility_id=facility_id,
        display_name=facility_id.replace("-", " ").title(),
        coordinates=None,
        eligible=eligible,
        eligibility_reason=("Eligible" if eligible else "Not eligible"),
        eta_minutes=eta,
        distance_km=distance,
        capability_basis=("TEST_FIXTURE" if eligible else None),
        route_evidence=route,
    )


def facility(facility_id, facility_type, latitude, longitude, organization="org-1"):
    return {
        "facilityId": facility_id,
        "organizationId": organization,
        "name": facility_id.replace("-", " ").title(),
        "type": facility_type,
        "gps": {"lat": latitude, "lng": longitude},
    }


class _RouteProvider:
    def __init__(self, routes):
        self.routes = routes

    def get_route(self, origin, destination, context=None):
        key = (origin.latitude, origin.longitude, destination.latitude, destination.longitude)
        duration, distance = self.routes[key]
        return RouteEvidence(
            RouteStatus.AVAILABLE,
            "FAKE_ROUTES",
            datetime(2026, 8, 23, tzinfo=timezone.utc),
            "DRIVE",
            duration,
            distance,
        )


if __name__ == "__main__":
    unittest.main()
