import unittest

from src.cleaning.normalizers import (
    normalize_drug_name,
    normalize_manufacturer,
    normalize_ndc,
    product_ndc_from_package,
)


class NDCNormalizerTest(unittest.TestCase):
    def test_normalizes_all_three_ten_digit_package_patterns(self) -> None:
        self.assertEqual(normalize_ndc("1234-5678-90"), "01234567890")
        self.assertEqual(normalize_ndc("12345-678-90"), "12345067890")
        self.assertEqual(normalize_ndc("12345-6789-0"), "12345678900")

    def test_normalizes_product_codes(self) -> None:
        self.assertEqual(normalize_ndc("1234-5678"), "012345678")
        self.assertEqual(normalize_ndc("12345-678"), "123450678")
        self.assertEqual(normalize_ndc("12345-6789"), "123456789")
        self.assertEqual(product_ndc_from_package("1234-5678-90"), "012345678")

    def test_rejects_ambiguous_or_invalid_values(self) -> None:
        self.assertIsNone(normalize_ndc("1234567890"))
        self.assertIsNone(normalize_ndc("not-an-ndc"))
        self.assertIsNone(normalize_ndc(None))


class NameNormalizerTest(unittest.TestCase):
    def test_manufacturer_variants_share_a_canonical_name(self) -> None:
        expected = "novo nordisk"
        self.assertEqual(normalize_manufacturer("Novo Nordisk"), expected)
        self.assertEqual(normalize_manufacturer("Novo Nordisk, Inc."), expected)
        self.assertEqual(normalize_manufacturer("NOVO NORDISK INC"), expected)

    def test_removes_parent_company_tag(self) -> None:
        self.assertEqual(normalize_manufacturer("Hospira, Inc., a Pfizer Company"), "hospira")

    def test_normalizes_drug_name_punctuation_and_case(self) -> None:
        self.assertEqual(normalize_drug_name("ACETAMINOPHEN 500-mg"), "acetaminophen 500 mg")


if __name__ == "__main__":
    unittest.main()
