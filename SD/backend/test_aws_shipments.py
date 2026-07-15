import json

try:
    from .aws_shipment_service import get_live_shipments_for_user
except ImportError:
    from aws_shipment_service import get_live_shipments_for_user


def main():
    user = {
        "role": "support",
        "organizationId": None,
    }
    payload = get_live_shipments_for_user(user)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
