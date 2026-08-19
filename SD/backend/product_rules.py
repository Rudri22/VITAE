from typing import Sequence, Tuple

try:
    from .risk_rules import ProductRule, ProductRuleType
except ImportError:
    from risk_rules import ProductRule, ProductRuleType


GARDASIL_9_PRODUCT_ID = "gardasil-9"
GARDASIL_9_PRODUCT_NAME = "GARDASIL 9"
GARDASIL_9_PRESENTATION = "single-dose-prefilled-syringe-0.5-ml"
GARDASIL_9_STATE = "unopened"
GARDASIL_9_SOURCE = "FDA GARDASIL 9 Package Insert, section 16"
GARDASIL_9_SOURCE_URL = "https://www.fda.gov/media/90064/download"
GARDASIL_9_SOURCE_VERSION = "uspi-v503-i-2503r017"

GARDASIL_9_RULES = (
    ProductRule(
        rule_id="gardasil9-pfs-unopened-normal-storage-v1",
        product_id=GARDASIL_9_PRODUCT_ID,
        product_name=GARDASIL_9_PRODUCT_NAME,
        presentation=GARDASIL_9_PRESENTATION,
        state=GARDASIL_9_STATE,
        rule_type=ProductRuleType.NORMAL_STORAGE,
        min_temperature=2.0,
        max_temperature=8.0,
        verified=True,
        version=GARDASIL_9_SOURCE_VERSION,
        source=GARDASIL_9_SOURCE,
        source_url=GARDASIL_9_SOURCE_URL,
    ),
    ProductRule(
        rule_id="gardasil9-pfs-unopened-high-temp-excursion-v1",
        product_id=GARDASIL_9_PRODUCT_ID,
        product_name=GARDASIL_9_PRODUCT_NAME,
        presentation=GARDASIL_9_PRESENTATION,
        state=GARDASIL_9_STATE,
        rule_type=ProductRuleType.PERMITTED_EXCURSION,
        min_temperature=8.0,
        max_temperature=25.0,
        maximum_duration_minutes=4320.0,
        cumulative=True,
        verified=True,
        version=GARDASIL_9_SOURCE_VERSION,
        source=GARDASIL_9_SOURCE,
        source_url=GARDASIL_9_SOURCE_URL,
    ),
    ProductRule(
        rule_id="gardasil9-pfs-unopened-low-temp-excursion-v1",
        product_id=GARDASIL_9_PRODUCT_ID,
        product_name=GARDASIL_9_PRODUCT_NAME,
        presentation=GARDASIL_9_PRESENTATION,
        state=GARDASIL_9_STATE,
        rule_type=ProductRuleType.PERMITTED_EXCURSION,
        min_temperature=0.0,
        max_temperature=2.0,
        maximum_duration_minutes=4320.0,
        cumulative=True,
        verified=True,
        version=GARDASIL_9_SOURCE_VERSION,
        source=GARDASIL_9_SOURCE,
        source_url=GARDASIL_9_SOURCE_URL,
    ),
)

PRODUCT_RULE_CATALOG = GARDASIL_9_RULES


class ProductRulesError(ValueError):
    pass


class ProductRulesNotFoundError(ProductRulesError):
    pass


class ProductRuleConflictError(ProductRulesError):
    pass


class ProductRuleCatalog:
    def __init__(self, rules: Sequence[ProductRule]):
        self._rules = tuple(rules)

    def resolve(self, product_id: str, presentation: str, state: str) -> Tuple[ProductRule, ...]:
        context = (
            _normalize_context(product_id),
            _normalize_context(presentation),
            _normalize_context(state),
        )
        applicable = tuple(
            rule
            for rule in self._rules
            if rule.verified
            and (
                _normalize_context(rule.product_id),
                _normalize_context(rule.presentation),
                _normalize_context(rule.state),
            ) == context
        )
        if not all(context) or not applicable:
            raise ProductRulesNotFoundError(
                "No verified ProductRules exist for the exact product, presentation, and state"
            )

        _validate_resolved_rules(applicable)
        return applicable


DEFAULT_PRODUCT_RULE_CATALOG = ProductRuleCatalog(PRODUCT_RULE_CATALOG)


def resolve_applicable_rules(
    product_id: str,
    presentation: str,
    state: str,
) -> Tuple[ProductRule, ...]:
    """Return verified rules for one exact product context without fallback."""
    return DEFAULT_PRODUCT_RULE_CATALOG.resolve(product_id, presentation, state)


def _validate_resolved_rules(rules: Sequence[ProductRule]) -> None:
    rule_ids = [rule.rule_id for rule in rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise ProductRuleConflictError("Conflicting active ProductRules use the same rule_id")

    normal_rules = [rule for rule in rules if rule.rule_type == ProductRuleType.NORMAL_STORAGE]
    if len(normal_rules) != 1:
        raise ProductRuleConflictError(
            "An exact product context must have exactly one active normal-storage rule"
        )

    signatures = [
        (rule.rule_type, float(rule.min_temperature), float(rule.max_temperature))
        for rule in rules
    ]
    if len(signatures) != len(set(signatures)):
        raise ProductRuleConflictError("Conflicting active ProductRules duplicate a numeric rule")


def _normalize_context(value: str) -> str:
    return str(value or "").strip().casefold()
