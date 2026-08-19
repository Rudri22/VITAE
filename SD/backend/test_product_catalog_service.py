import unittest
from dataclasses import replace
from inspect import getsource

try:
    from .product_catalog_service import (
        ProductCatalogService,
        ProductCatalogServiceError,
    )
    from .product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_PRODUCT_NAME,
        GARDASIL_9_RULES,
        GARDASIL_9_SOURCE,
        GARDASIL_9_SOURCE_URL,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
    )
except ImportError:
    from product_catalog_service import (
        ProductCatalogService,
        ProductCatalogServiceError,
    )
    from product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_PRODUCT_NAME,
        GARDASIL_9_RULES,
        GARDASIL_9_SOURCE,
        GARDASIL_9_SOURCE_URL,
        GARDASIL_9_SOURCE_VERSION,
        GARDASIL_9_STATE,
    )


class ProductCatalogServiceTests(unittest.TestCase):
    def test_default_catalog_exposes_one_gardasil_context(self):
        contexts = ProductCatalogService().list_supported_contexts()
        self.assertEqual(len(contexts), 1)
        context = contexts[0]
        self.assertEqual(context.product_id, GARDASIL_9_PRODUCT_ID)
        self.assertEqual(context.product_name, GARDASIL_9_PRODUCT_NAME)
        self.assertEqual(context.presentation, GARDASIL_9_PRESENTATION)
        self.assertEqual(context.state, GARDASIL_9_STATE)
        self.assertEqual(
            context.product_rule_version,
            GARDASIL_9_SOURCE_VERSION,
        )

    def test_context_retains_provenance_and_rule_ids(self):
        context = ProductCatalogService().list_supported_contexts()[0]
        self.assertEqual(context.source, GARDASIL_9_SOURCE)
        self.assertEqual(context.source_url, GARDASIL_9_SOURCE_URL)
        self.assertEqual(
            context.rule_ids,
            tuple(sorted(rule.rule_id for rule in GARDASIL_9_RULES)),
        )

    def test_unverified_context_is_not_exposed(self):
        unverified = tuple(replace(rule, verified=False) for rule in GARDASIL_9_RULES)
        self.assertEqual(
            ProductCatalogService(unverified).list_supported_contexts(),
            (),
        )

    def test_catalog_data_changes_flow_through_without_service_hardcoding(self):
        changed = tuple(
            replace(
                rule,
                product_id="catalog-test-product",
                product_name="Catalog Test Product",
                presentation="catalog-test-presentation",
                state="catalog-test-state",
                version="catalog-test-version",
                source="Catalog test source",
                source_url="https://example.test/catalog",
            )
            for rule in GARDASIL_9_RULES
        )
        context = ProductCatalogService(changed).list_supported_contexts()[0]
        self.assertEqual(context.product_id, "catalog-test-product")
        self.assertEqual(context.product_name, "Catalog Test Product")
        self.assertEqual(context.presentation, "catalog-test-presentation")
        self.assertEqual(context.state, "catalog-test-state")
        self.assertEqual(context.product_rule_version, "catalog-test-version")

    def test_inconsistent_rule_versions_are_rejected(self):
        conflicting = (
            GARDASIL_9_RULES[0],
            replace(GARDASIL_9_RULES[1], version="different-version"),
            GARDASIL_9_RULES[2],
        )
        with self.assertRaises(ProductCatalogServiceError):
            ProductCatalogService(conflicting).list_supported_contexts()

    def test_conflicting_product_names_are_rejected(self):
        conflicting = (
            GARDASIL_9_RULES[0],
            replace(GARDASIL_9_RULES[1], product_name="Conflicting Name"),
            GARDASIL_9_RULES[2],
        )
        with self.assertRaisesRegex(ProductCatalogServiceError, "product name"):
            ProductCatalogService(conflicting).list_supported_contexts()

    def test_service_contains_no_product_id_to_name_mapping(self):
        source = getsource(ProductCatalogService)
        self.assertNotIn(GARDASIL_9_PRODUCT_ID, source)
        self.assertNotIn(GARDASIL_9_PRODUCT_NAME, source)


if __name__ == "__main__":
    unittest.main()
