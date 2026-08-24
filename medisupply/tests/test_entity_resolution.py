import unittest

from src.cleaning.entity_resolution import NDCNameMapper, shortage_context_fields


class NDCNameMapperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mapper = NDCNameMapper.from_records(
            [
                {
                    "product_id": "0002-8215_example",
                    "product_ndc": "0002-8215",
                    "generic_name": "Semaglutide",
                    "brand_name": "Ozempic",
                    "labeler_name": "Novo Nordisk, Inc.",
                    "active_ingredients": [{"name": "SEMAGLUTIDE"}],
                    "packaging": [{"package_ndc": "0002-8215-01"}],
                }
            ]
        )

    def test_maps_different_package_hyphenation_to_ndc_names(self) -> None:
        result = self.mapper.resolve_shortage({"package_ndc": "002-8215-01"})
        self.assertEqual(result.method, "package_ndc")
        self.assertEqual(result.identity.generic_name, "Semaglutide")
        self.assertEqual(result.identity.brand_name, "Ozempic")
        self.assertEqual(result.identity.normalized_manufacturer, "novo nordisk")

    def test_falls_back_to_product_ndc(self) -> None:
        result = self.mapper.resolve_shortage({"package_ndc": "0002-8215-99"})
        self.assertEqual(result.method, "product_ndc")
        self.assertEqual(result.identity.active_ingredients, ("SEMAGLUTIDE",))

    def test_does_not_guess_an_unknown_ndc(self) -> None:
        result = self.mapper.resolve_shortage({"package_ndc": "99999-9999-99"})
        self.assertEqual(result.method, "unmatched")
        self.assertIsNone(result.identity)


class ShortageContextTest(unittest.TestCase):
    def test_current_related_info_becomes_operational_context(self) -> None:
        fields = shortage_context_fields(
            {
                "status": "Current",
                "related_info": "Check wholesalers for inventory",
            }
        )
        self.assertEqual(
            fields,
            {
                "shortage_reason": None,
                "operational_context": "Check wholesalers for inventory",
                "discontinuation_context": None,
            },
        )

    def test_discontinued_related_info_is_not_backfilled_as_reason(self) -> None:
        fields = shortage_context_fields(
            {
                "status": "To Be Discontinued",
                "related_info": "A business decision was made to discontinue the drug.",
            }
        )
        self.assertIsNone(fields["shortage_reason"])
        self.assertIsNone(fields["operational_context"])
        self.assertEqual(
            fields["discontinuation_context"],
            "A business decision was made to discontinue the drug.",
        )

    def test_fda_shortage_reason_is_preserved(self) -> None:
        fields = shortage_context_fields(
            {
                "status": "Current",
                "shortage_reason": "Manufacturing delay",
                "related_info": "Additional lots will be released.",
            }
        )
        self.assertEqual(fields["shortage_reason"], "Manufacturing delay")
        self.assertEqual(fields["operational_context"], "Additional lots will be released.")


if __name__ == "__main__":
    unittest.main()
