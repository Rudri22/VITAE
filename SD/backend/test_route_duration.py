import json
import unittest
from datetime import datetime, timedelta, timezone

try:
    from .rerouting import Coordinates
    from .route_duration import (
        CachedRouteDurationProvider,
        GoogleRoutesDurationProvider,
        RouteDurationConfig,
        RouteEvidence,
        RouteProviderKind,
        RouteStatus,
        compose_route_duration_provider,
        route_evidence_document,
    )
except ImportError:
    from rerouting import Coordinates
    from route_duration import (
        CachedRouteDurationProvider,
        GoogleRoutesDurationProvider,
        RouteDurationConfig,
        RouteEvidence,
        RouteProviderKind,
        RouteStatus,
        compose_route_duration_provider,
        route_evidence_document,
    )


class _Clock:
    def __init__(self):
        self.now = datetime(2026, 8, 23, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


class _Provider:
    def __init__(self, clock):
        self.clock = clock
        self.calls = 0

    def get_route(self, origin, destination, context):
        self.calls += 1
        return RouteEvidence(
            RouteStatus.AVAILABLE,
            "FAKE_ROUTES",
            self.clock(),
            context.travel_mode,
            1200,
            9000,
        )


class RouteDurationTests(unittest.TestCase):
    def test_default_configuration_disables_routes(self):
        config = RouteDurationConfig.from_environment({})
        self.assertEqual(config.provider, RouteProviderKind.NONE)
        self.assertIsNone(compose_route_duration_provider(config))

    def test_google_configuration_without_backend_key_degrades_to_disabled(self):
        config = RouteDurationConfig.from_environment(
            {"VITAE_ROUTE_PROVIDER": "google_routes"}
        )
        self.assertIsNone(compose_route_duration_provider(config))

    def test_google_request_and_response_are_structured_without_key_disclosure(self):
        calls = []

        def transport(url, body, headers, timeout):
            calls.append((url, json.loads(body), headers, timeout))
            return {"routes": [{"duration": "1234.5s", "distanceMeters": 9876}]}

        provider = GoogleRoutesDurationProvider("secret-test-key", transport=transport)
        result = provider.get_route(Coordinates(33.8, 35.4), Coordinates(33.9, 35.5))
        self.assertEqual(result.status, RouteStatus.AVAILABLE)
        self.assertEqual(result.duration_seconds, 1234.5)
        self.assertEqual(result.distance_meters, 9876)
        self.assertEqual(calls[0][2]["X-Goog-FieldMask"], "routes.duration,routes.distanceMeters")
        self.assertNotIn("secret-test-key", json.dumps(route_evidence_document(result)))

    def test_provider_timeout_or_malformed_response_degrades_to_unavailable(self):
        for transport in (
            lambda *_: (_ for _ in ()).throw(TimeoutError("late")),
            lambda *_: {"routes": [{"duration": "bad", "distanceMeters": 10}]},
        ):
            with self.subTest(transport=transport):
                result = GoogleRoutesDurationProvider("key", transport=transport).get_route(
                    Coordinates(33.8, 35.4), Coordinates(33.9, 35.5)
                )
                self.assertEqual(result.status, RouteStatus.UNAVAILABLE)
                self.assertEqual(result.unavailable_reason, "ROUTE_PROVIDER_UNAVAILABLE")

    def test_zero_remaining_route_is_valid_at_destination(self):
        provider = GoogleRoutesDurationProvider(
            "key",
            transport=lambda *_: {"routes": [{"duration": "0s", "distanceMeters": 0}]},
        )
        result = provider.get_route(Coordinates(33.9, 35.5), Coordinates(33.9, 35.5))
        self.assertEqual(result.status, RouteStatus.AVAILABLE)
        self.assertEqual(result.duration_seconds, 0)

    def test_cache_hit_and_expiry_use_injected_clock(self):
        clock = _Clock()
        underlying = _Provider(clock)
        cached = CachedRouteDurationProvider(underlying, 60, clock=clock)
        origin, destination = Coordinates(33.8, 35.4), Coordinates(33.9, 35.5)
        first = cached.get_route(origin, destination)
        self.assertEqual(first.cache_expires_at, clock.now + timedelta(seconds=60))
        self.assertIs(cached.get_route(origin, destination), first)
        self.assertEqual(underlying.calls, 1)
        clock.now += timedelta(seconds=60)
        self.assertIsNot(cached.get_route(origin, destination), first)
        self.assertEqual(underlying.calls, 2)


if __name__ == "__main__":
    unittest.main()
