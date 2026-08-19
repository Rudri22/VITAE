import unittest
from dataclasses import replace
from datetime import datetime, timezone

try:
    from .product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_RULES,
        GARDASIL_9_STATE,
        ProductRuleCatalog,
        ProductRuleConflictError,
        ProductRulesNotFoundError,
        resolve_applicable_rules,
    )
    from .risk_rules import ApplicationStatus, ProductRuleType, TelemetrySample, evaluate_status
except ImportError:
    from product_rules import (
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_RULES,
        GARDASIL_9_STATE,
        ProductRuleCatalog,
        ProductRuleConflictError,
        ProductRulesNotFoundError,
        resolve_applicable_rules,
    )
    from risk_rules import ApplicationStatus, ProductRuleType, TelemetrySample, evaluate_status


NORMAL_RULE_ID = "gardasil9-pfs-unopened-normal-storage-v1"
HIGH_RULE_ID = "gardasil9-pfs-unopened-high-temp-excursion-v1"
LOW_RULE_ID = "gardasil9-pfs-unopened-low-temp-excursion-v1"


def resolved_rules():
    return resolve_applicable_rules(
        GARDASIL_9_PRODUCT_ID,
        GARDASIL_9_PRESENTATION,
        GARDASIL_9_STATE,
    )


def rule_by_id(rule_id):
    return next(rule for rule in resolved_rules() if rule.rule_id == rule_id)


class ProductRuleCatalogTests(unittest.TestCase):
    def test_known_gardasil_9_context_resolves(self):
        self.assertEqual(len(resolved_rules()), 3)

    def test_exactly_one_normal_storage_rule_is_present(self):
        normal_rules = [rule for rule in resolved_rules() if rule.rule_type == ProductRuleType.NORMAL_STORAGE]
        self.assertEqual(len(normal_rules), 1)
        self.assertEqual(normal_rules[0].rule_id, NORMAL_RULE_ID)

    def test_high_temperature_excursion_is_8_to_25_celsius(self):
        rule = rule_by_id(HIGH_RULE_ID)
        self.assertEqual((rule.min_temperature, rule.max_temperature), (8.0, 25.0))

    def test_low_temperature_excursion_is_0_to_2_celsius(self):
        rule = rule_by_id(LOW_RULE_ID)
        self.assertEqual((rule.min_temperature, rule.max_temperature), (0.0, 2.0))

    def test_both_excursion_rules_allow_4320_minutes(self):
        excursion_rules = [rule for rule in resolved_rules() if rule.rule_type == ProductRuleType.PERMITTED_EXCURSION]
        self.assertEqual([rule.maximum_duration_minutes for rule in excursion_rules], [4320.0, 4320.0])

    def test_excursion_rules_are_cumulative(self):
        excursion_rules = [rule for rule in resolved_rules() if rule.rule_type == ProductRuleType.PERMITTED_EXCURSION]
        self.assertTrue(all(rule.cumulative for rule in excursion_rules))

    def test_returned_rules_are_verified(self):
        self.assertTrue(all(rule.verified for rule in resolved_rules()))

    def test_source_and_version_provenance_are_present(self):
        for rule in resolved_rules():
            with self.subTest(rule.rule_id):
                self.assertTrue(rule.source)
                self.assertTrue(rule.source_url)
                self.assertTrue(rule.version)

    def test_unknown_product_does_not_fall_back(self):
        with self.assertRaises(ProductRulesNotFoundError):
            resolve_applicable_rules("unknown-product", GARDASIL_9_PRESENTATION, GARDASIL_9_STATE)

    def test_wrong_presentation_does_not_fall_back(self):
        with self.assertRaises(ProductRulesNotFoundError):
            resolve_applicable_rules(GARDASIL_9_PRODUCT_ID, "single-dose-vial-0.5-ml", GARDASIL_9_STATE)

    def test_wrong_state_does_not_fall_back(self):
        with self.assertRaises(ProductRulesNotFoundError):
            resolve_applicable_rules(GARDASIL_9_PRODUCT_ID, GARDASIL_9_PRESENTATION, "opened")

    def test_unverified_rule_is_excluded(self):
        draft_rule = replace(
            GARDASIL_9_RULES[0],
            product_id="draft-product",
            verified=False,
        )
        catalog = ProductRuleCatalog((draft_rule,))
        with self.assertRaises(ProductRulesNotFoundError):
            catalog.resolve("draft-product", GARDASIL_9_PRESENTATION, GARDASIL_9_STATE)

    def test_conflicting_duplicate_active_rules_are_detected(self):
        conflicting_normal = replace(
            GARDASIL_9_RULES[0],
            rule_id="gardasil9-conflicting-normal-storage-v1",
            max_temperature=7.0,
        )
        catalog = ProductRuleCatalog((*GARDASIL_9_RULES, conflicting_normal))
        with self.assertRaises(ProductRuleConflictError):
            catalog.resolve(GARDASIL_9_PRODUCT_ID, GARDASIL_9_PRESENTATION, GARDASIL_9_STATE)

    def test_resolved_rules_pass_directly_to_status_engine(self):
        decision = evaluate_status(
            TelemetrySample(
                product_id=GARDASIL_9_PRODUCT_ID,
                temperature=5.0,
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            resolved_rules(),
        )
        self.assertEqual(decision.status, ApplicationStatus.SAFE)


if __name__ == "__main__":
    unittest.main()
