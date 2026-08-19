from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

try:
    from .product_rules import PRODUCT_RULE_CATALOG, ProductRuleCatalog
    from .risk_rules import ProductRule
except ImportError:
    from product_rules import PRODUCT_RULE_CATALOG, ProductRuleCatalog
    from risk_rules import ProductRule


@dataclass(frozen=True)
class SupportedProductContext:
    product_id: str
    product_name: str
    presentation: str
    state: str
    product_rule_version: str
    source: str
    source_url: Optional[str]
    rule_ids: Tuple[str, ...]


class ProductCatalogServiceError(ValueError):
    pass


class ProductCatalogService:
    def __init__(self, rules: Sequence[ProductRule] = PRODUCT_RULE_CATALOG):
        self._rules = tuple(rules)
        self._catalog = ProductRuleCatalog(self._rules)

    def list_supported_contexts(self) -> Tuple[SupportedProductContext, ...]:
        """Project verified exact contexts from the ProductRules source of truth."""
        keys = sorted(
            {
                (rule.product_id, rule.presentation, rule.state)
                for rule in self._rules
                if rule.verified
            }
        )
        contexts = []
        for product_id, presentation, state in keys:
            rules = self._catalog.resolve(product_id, presentation, state)
            versions = {rule.version for rule in rules if rule.version}
            product_names = {
                rule.product_name for rule in rules if rule.product_name
            }
            sources = {rule.source for rule in rules if rule.source}
            source_urls = {rule.source_url for rule in rules if rule.source_url}
            if (
                len(versions) != 1
                or len(product_names) != 1
                or len(sources) != 1
                or len(source_urls) > 1
            ):
                raise ProductCatalogServiceError(
                    "A supported product context must have one product name and consistent provenance"
                )
            contexts.append(
                SupportedProductContext(
                    product_id=product_id,
                    product_name=next(iter(product_names)),
                    presentation=presentation,
                    state=state,
                    product_rule_version=next(iter(versions)),
                    source=next(iter(sources)),
                    source_url=(
                        next(iter(source_urls)) if source_urls else None
                    ),
                    rule_ids=tuple(sorted(rule.rule_id for rule in rules)),
                )
            )
        return tuple(contexts)
