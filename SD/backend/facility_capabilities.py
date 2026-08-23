"""Facility capability evidence used for conservative destination eligibility."""

from dataclasses import dataclass
from typing import Mapping, Optional, Protocol, Tuple, runtime_checkable

try:
    from .trip_identity import TripIdentity
except ImportError:
    from trip_identity import TripIdentity


@dataclass(frozen=True)
class FacilityEligibility:
    eligible: bool
    reason: str
    capability_profile_id: Optional[str]
    evidence_kind: str


@runtime_checkable
class FacilityCapabilityRegistry(Protocol):
    def evaluate(self, trip: TripIdentity, facility: Mapping) -> FacilityEligibility:
        ...


class ApplicationFacilityCapabilityRegistry:
    """Resolve generic product support from explicit application capability data."""

    def __init__(self, profiles: Mapping[str, Mapping]):
        self._profiles = profiles

    def evaluate(self, trip, facility):
        profile_id = str(facility.get("capabilityProfileId") or "").strip()
        if not profile_id:
            return FacilityEligibility(
                eligible=False,
                reason="Facility capability metadata is missing.",
                capability_profile_id=None,
                evidence_kind="MISSING_CAPABILITY_METADATA",
            )
        profile = self._profiles.get(profile_id)
        if not isinstance(profile, Mapping):
            return FacilityEligibility(
                eligible=False,
                reason="Facility capability profile is unavailable.",
                capability_profile_id=profile_id,
                evidence_kind="MISSING_CAPABILITY_PROFILE",
            )
        product_ids = _string_tuple(profile.get("supportedProductIds"))
        if trip.product_id not in product_ids:
            return FacilityEligibility(
                eligible=False,
                reason="Facility capability profile does not support this product identifier.",
                capability_profile_id=profile_id,
                evidence_kind=str(profile.get("evidenceKind") or "CONFIGURED_METADATA"),
            )
        return FacilityEligibility(
            eligible=True,
            reason="Facility capability profile supports this product identifier.",
            capability_profile_id=profile_id,
            evidence_kind=str(profile.get("evidenceKind") or "CONFIGURED_METADATA"),
        )


def _string_tuple(value) -> Tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        normalized
        for normalized in (str(item or "").strip() for item in value)
        if normalized
    )
