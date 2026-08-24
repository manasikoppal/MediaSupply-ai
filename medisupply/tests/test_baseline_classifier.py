import unittest

from src.models.baseline_classifier import (
    SHORTAGE_REFERENCE_LABELS,
    classify_text,
)


class BaselineClassifierTest(unittest.TestCase):
    def test_maps_all_standardized_shortage_reasons(self) -> None:
        for text, expected in SHORTAGE_REFERENCE_LABELS.items():
            with self.subTest(text=text):
                result = classify_text(text, source="shortage")
                self.assertEqual(result.event.primary_cause, expected)

    def test_inactive_ingredient_does_not_collide_with_active_ingredient(self) -> None:
        result = classify_text(
            "Shortage of an inactive ingredient component", source="shortage"
        )
        self.assertEqual(
            result.matched_categories, ("inactive_ingredient_shortage",)
        )

    def test_reports_multi_category_collision(self) -> None:
        result = classify_text(
            "A manufacturing delay caused a shipping delay", source="shortage"
        )
        self.assertTrue(result.collision)
        self.assertEqual(result.event.primary_cause, "shipping_delay")
        self.assertEqual(
            set(result.matched_categories),
            {"manufacturing_capacity", "shipping_delay"},
        )
        self.assertIn(
            "also_matched:manufacturing_capacity", result.event.secondary_causes
        )

    def test_maps_labeling_error_to_new_category(self) -> None:
        result = classify_text(
            "Labeling: Incorrect or Missing Lot and/or Exp Date",
            source="recall",
            classification="Class III",
        )
        self.assertEqual(result.event.primary_cause, "labeling_packaging_error")
        self.assertEqual(result.event.severity, "low")
        self.assertTrue(result.confident_keyword_match)
        self.assertFalse(result.fallback)

    def test_maps_new_regulatory_and_adverse_event_categories(self) -> None:
        cases = {
            "Marketed Without An Approved NDA/ANDA": "regulatory_noncompliance",
            "The firm received reports of adverse reactions": "adverse_event_signal",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                result = classify_text(text, source="recall")
                self.assertEqual(result.event.primary_cause, expected)
                self.assertTrue(result.confident_keyword_match)

    def test_stopping_stability_work_is_not_product_discontinuation(self) -> None:
        texts = (
            "CGMP Deviations: Firm went out of business and could no longer continue stability studies.",
            "CGMP Deviations; the firm discontinued required stability testing for products on the market still within expiry",
        )
        for text in texts:
            with self.subTest(text=text):
                result = classify_text(text, source="recall")
                self.assertEqual(
                    result.event.primary_cause, "manufacturing_quality_problem"
                )
                self.assertNotIn(
                    "product_discontinuation", result.matched_categories
                )

    def test_weak_quality_term_is_explicitly_low_confidence(self) -> None:
        result = classify_text("Stability concern", source="recall")
        self.assertEqual(result.event.primary_cause, "manufacturing_quality_problem")
        self.assertFalse(result.confident_keyword_match)
        self.assertFalse(result.fallback)
        self.assertEqual(result.confidence, "low")

    def test_recall_causal_match_keeps_recall_as_secondary_context(self) -> None:
        result = classify_text(
            "Lack of Assurance of Sterility",
            source="recall",
            classification="Class II",
        )
        self.assertEqual(result.event.primary_cause, "manufacturing_quality_problem")
        self.assertIn("recall_event", result.event.secondary_causes)
        self.assertEqual(result.event.severity, "medium")

    def test_zero_match_recall_falls_back_to_unknown(self) -> None:
        result = classify_text(
            "No configured phrase applies here",
            source="recall",
            classification="Class II",
        )
        self.assertEqual(result.event.primary_cause, "unknown")
        self.assertTrue(result.fallback)
        self.assertFalse(result.confident_keyword_match)
        self.assertEqual(result.confidence_score, 0.0)
        self.assertEqual(result.matched_categories, ())
        self.assertEqual(result.matched_rules, {})
        self.assertIn("recall_event", result.event.secondary_causes)


if __name__ == "__main__":
    unittest.main()
