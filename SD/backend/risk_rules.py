def calculate_risk(shipment, reading):
    """Calculates explainable rule-based risk before ML is applied."""
    reasons = []
    score = 0

    temperature = reading["temperature"]
    battery = reading["batteryLevel"]
    min_temp = shipment.get("safeTemperatureMin")
    max_temp = shipment.get("safeTemperatureMax")

    if min_temp is not None and max_temp is not None and (temperature < min_temp or temperature > max_temp):
        score += 60
        reasons.append("Temperature is outside the safe medicine range")
    elif min_temp is None or max_temp is None:
        reasons.append("Safe temperature range was not provided for this shipment")

    if battery <= 15:
        score += 30
        reasons.append("Container battery is critically low")
    elif battery <= 30:
        score += 15
        reasons.append("Container battery is low")

    if score >= 80:
        level = "critical"
    elif score >= 50:
        level = "high"
    elif score >= 20:
        level = "medium"
    else:
        level = "low"
        if not reasons:
            reasons.append("Shipment is currently within safe rule limits")

    return {
        "score": min(score, 100),
        "level": level,
        "reasons": reasons,
    }
