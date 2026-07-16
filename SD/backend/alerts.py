def build_alerts(shipment, reading, risk, ml_prediction, nearest_hospital):
    """Creates user-facing alerts and recommended actions."""
    alerts = []

    if risk["level"] in ["high", "critical"]:
        alerts.append({
            "type": "RULE_BASED_RISK",
            "severity": risk["level"],
            "message": "; ".join(risk["reasons"]),
            "recommendedAction": "Check shipment immediately and prepare rerouting if needed",
            "timestamp": reading["timestamp"],
        })

    ml_risk_level = ml_prediction.get("riskLevel") if isinstance(ml_prediction, dict) else None
    if ml_risk_level in ["high", "critical"]:
        destination = nearest_hospital["name"] if nearest_hospital else "the nearest compatible facility"
        alerts.append({
            "type": "ML_SPOILAGE_RISK",
            "severity": ml_risk_level,
            "message": "ML model predicts spoilage risk before arrival",
            "recommendedAction": f"Consider redirecting to {destination}",
            "timestamp": reading["timestamp"],
        })

    if reading["batteryLevel"] <= 15:
        alerts.append({
            "type": "LOW_BATTERY",
            "severity": "high",
            "message": "Container battery is critically low",
            "recommendedAction": "Replace or recharge the container battery",
            "timestamp": reading["timestamp"],
        })

    return alerts
