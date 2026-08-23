import json
import unittest

try:
    from .storage import (
        get_organization_foundation_dashboard_data,
        organization_shipment_record,
    )
except ImportError:
    from storage import (
        get_organization_foundation_dashboard_data,
        organization_shipment_record,
    )


class V2ShipmentMappingTests(unittest.TestCase):
    def test_prototype_organization_shipment_has_v2_lot_trip_mapping(self):
        data = get_organization_foundation_dashboard_data("hospital-a")
        mapped = [item for item in data["shipments"] if item.get("lotTripId")]
        self.assertEqual(len(mapped), 1)
        self.assertEqual(mapped[0]["shipmentId"], "ship-a-v2-001")
        self.assertEqual(mapped[0]["lotTripId"], "lot-trip-sim-001")
        self.assertEqual(mapped[0]["productName"], "GARDASIL 9")
        self.assertEqual(mapped[0]["status"], "in_transit")
        self.assertIn(mapped[0], data["activeShipments"])
        json.dumps(data, allow_nan=False)

    def test_unmapped_shipment_serializes_without_inventing_lot_trip_id(self):
        record = organization_shipment_record(
            {
                "shipmentId": "legacy-only",
                "organizationId": "hospital-a",
                "status": "planned",
                "temperature": 5.0,
            }
        )
        self.assertIsNone(record["lotTripId"])
        self.assertEqual(record["temperature"], 5.0)
        self.assertIsNone(record["conditionStatus"])


if __name__ == "__main__":
    unittest.main()
