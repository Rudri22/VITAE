from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Optional, Sequence, Union


class ApplicationStatus(str, Enum):
    SAFE = "SAFE"
    MONITOR = "MONITOR"
    AT_RISK = "AT_RISK"
    CRITICAL = "CRITICAL"
    RULE_VIOLATION = "RULE_VIOLATION"
    DATA_ERROR = "DATA_ERROR"


class ProductRuleType(str, Enum):
    NORMAL_STORAGE = "NORMAL_STORAGE"
    PERMITTED_EXCURSION = "PERMITTED_EXCURSION"


TimestampValue = Union[datetime, str, None]


@dataclass(frozen=True)
class TelemetrySample:
    product_id: str
    temperature: Optional[float]
    timestamp: TimestampValue


@dataclass(frozen=True)
class ProductRule:
    rule_id: str
    product_id: str
    rule_type: Union[ProductRuleType, str]
    min_temperature: float
    max_temperature: float
    maximum_duration_minutes: Optional[float] = None
    cumulative: bool = False
    verified: bool = False
    version: Optional[str] = None
    source: Optional[str] = None
    presentation: Optional[str] = None
    state: Optional[str] = None
    source_url: Optional[str] = None


@dataclass(frozen=True)
class PreviousState:
    last_sample_timestamp: TimestampValue = None
    active_rule_id: Optional[str] = None
    excursion_started_at: TimestampValue = None
    cumulative_excursion_duration_minutes: float = 0.0


@dataclass(frozen=True)
class StatusDecision:
    status: ApplicationStatus
    reason_code: str
    active_rule_id: Optional[str]
    excursion_episode_duration_minutes: float
    cumulative_excursion_duration_minutes: float
    excursion_utilization: Optional[float]
    excursion_started_at: Optional[datetime] = None


def evaluate_status(
    telemetry_sample: TelemetrySample,
    product_rules: Sequence[ProductRule],
    previous_state: Optional[PreviousState] = None,
) -> StatusDecision:
    """Evaluate one temperature sample against explicit, verified product rules.

    Utilization is returned as a fraction, where 0.5 means 50%. Cumulative
    rules use total excursion time; non-cumulative rules use the current
    uninterrupted episode.
    """
    if telemetry_sample.temperature is None:
        return _data_error("MISSING_TEMPERATURE")
    if not _is_number(telemetry_sample.temperature):
        return _data_error("INVALID_TEMPERATURE")

    sample_timestamp = _parse_timestamp(telemetry_sample.timestamp)
    if sample_timestamp is None:
        return _data_error("INVALID_TIMESTAMP")

    product_id = str(telemetry_sample.product_id or "").strip()
    applicable_rules = [
        rule
        for rule in product_rules
        if rule.verified and str(rule.product_id or "").strip() == product_id
    ]
    if not product_id or not applicable_rules:
        return _data_error("NO_VERIFIED_APPLICABLE_RULES")
    if any(not _valid_rule(rule) for rule in applicable_rules):
        return _data_error("INVALID_VERIFIED_PRODUCT_RULE")

    normal_rules = [rule for rule in applicable_rules if _rule_type(rule) == ProductRuleType.NORMAL_STORAGE]
    if not normal_rules:
        return _data_error("NO_VERIFIED_NORMAL_STORAGE_RULE")

    state = previous_state or PreviousState()
    state_values = _state_durations(state, sample_timestamp)
    if state_values is None:
        return _data_error("INVALID_PREVIOUS_STATE")
    prior_cumulative, active_delta, active_episode, active_started_at = state_values

    temperature = float(telemetry_sample.temperature)
    matching_normal = [rule for rule in normal_rules if _contains(rule, temperature)]
    if matching_normal:
        cumulative_duration = prior_cumulative + active_delta
        return StatusDecision(
            status=ApplicationStatus.SAFE,
            reason_code="TEMPERATURE_WITHIN_NORMAL_RANGE",
            active_rule_id=None,
            excursion_episode_duration_minutes=0.0,
            cumulative_excursion_duration_minutes=cumulative_duration,
            excursion_utilization=None,
            excursion_started_at=None,
        )

    excursion_rules = [
        rule
        for rule in applicable_rules
        if _rule_type(rule) == ProductRuleType.PERMITTED_EXCURSION and _contains(rule, temperature)
    ]
    if not excursion_rules:
        return StatusDecision(
            status=ApplicationStatus.RULE_VIOLATION,
            reason_code="TEMPERATURE_OUTSIDE_VERIFIED_RULES",
            active_rule_id=None,
            excursion_episode_duration_minutes=active_episode,
            cumulative_excursion_duration_minutes=prior_cumulative + active_delta,
            excursion_utilization=None,
            excursion_started_at=active_started_at,
        )

    active_matches = [rule for rule in excursion_rules if rule.rule_id == state.active_rule_id]
    if len(active_matches) == 1:
        rule = active_matches[0]
        episode_duration = active_episode
        cumulative_duration = prior_cumulative + active_delta
        excursion_started_at = active_started_at
    elif len(excursion_rules) == 1:
        rule = excursion_rules[0]
        episode_duration = 0.0
        cumulative_duration = prior_cumulative
        excursion_started_at = sample_timestamp
    else:
        return _data_error("AMBIGUOUS_EXCURSION_RULES", prior_cumulative)

    utilized_duration = cumulative_duration if rule.cumulative else episode_duration
    utilization = utilized_duration / float(rule.maximum_duration_minutes)

    if utilization >= 1.0:
        status = ApplicationStatus.RULE_VIOLATION
        reason_code = "CUMULATIVE_EXCURSION_LIMIT_REACHED" if rule.cumulative else "EXCURSION_DURATION_LIMIT_REACHED"
    elif utilization >= 0.9:
        status = ApplicationStatus.CRITICAL
        reason_code = "EXCURSION_UTILIZATION_AT_LEAST_90_PERCENT"
    elif utilization >= 0.5:
        status = ApplicationStatus.AT_RISK
        reason_code = "EXCURSION_UTILIZATION_AT_LEAST_50_PERCENT"
    else:
        status = ApplicationStatus.MONITOR
        reason_code = "PERMITTED_EXCURSION_BELOW_50_PERCENT"

    return StatusDecision(
        status=status,
        reason_code=reason_code,
        active_rule_id=rule.rule_id,
        excursion_episode_duration_minutes=episode_duration,
        cumulative_excursion_duration_minutes=cumulative_duration,
        excursion_utilization=utilization,
        excursion_started_at=excursion_started_at,
    )


def _state_durations(previous_state, sample_timestamp):
    cumulative = previous_state.cumulative_excursion_duration_minutes
    if not _is_number(cumulative) or cumulative < 0:
        return None
    cumulative = float(cumulative)

    if previous_state.active_rule_id is None:
        if previous_state.excursion_started_at is not None:
            return None
        if previous_state.last_sample_timestamp is not None:
            last_timestamp = _parse_timestamp(previous_state.last_sample_timestamp)
            if last_timestamp is None or last_timestamp > sample_timestamp:
                return None
        return cumulative, 0.0, 0.0, None

    last_timestamp = _parse_timestamp(previous_state.last_sample_timestamp)
    started_at = _parse_timestamp(previous_state.excursion_started_at)
    if last_timestamp is None or started_at is None:
        return None
    if started_at > last_timestamp or last_timestamp > sample_timestamp:
        return None

    delta = (sample_timestamp - last_timestamp).total_seconds() / 60.0
    episode = (sample_timestamp - started_at).total_seconds() / 60.0
    return cumulative, delta, episode, started_at


def _valid_rule(rule):
    rule_type = _rule_type(rule)
    if not rule.rule_id or rule_type is None:
        return False
    if not _is_number(rule.min_temperature) or not _is_number(rule.max_temperature):
        return False
    if rule.min_temperature > rule.max_temperature:
        return False
    if rule_type == ProductRuleType.PERMITTED_EXCURSION:
        return _is_number(rule.maximum_duration_minutes) and rule.maximum_duration_minutes > 0
    return True


def _rule_type(rule):
    try:
        return ProductRuleType(rule.rule_type)
    except (TypeError, ValueError):
        return None


def _contains(rule, temperature):
    return float(rule.min_temperature) <= temperature <= float(rule.max_temperature)


def _parse_timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _is_number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value)


def _data_error(reason_code, cumulative_duration=0.0):
    return StatusDecision(
        status=ApplicationStatus.DATA_ERROR,
        reason_code=reason_code,
        active_rule_id=None,
        excursion_episode_duration_minutes=0.0,
        cumulative_excursion_duration_minutes=float(cumulative_duration),
        excursion_utilization=None,
        excursion_started_at=None,
    )


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
        reasons.append("Temperature is outside the product storage range")
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
