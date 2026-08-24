import sqlite3
import unittest

from src.graph.query_graph import query_drug
from src.graph.schema import create_schema, equivalence_signature


class EquivalenceSignatureTest(unittest.TestCase):
    def test_signature_is_insensitive_to_case_and_ingredient_order(self) -> None:
        first = {
            "active_ingredients": [
                {"name": "INGREDIENT B", "strength": "10 mg"},
                {"name": "Ingredient A", "strength": "5MG"},
            ],
            "dosage_form": "TABLET",
            "route": ["ORAL"],
        }
        second = {
            "active_ingredients": [
                {"name": "ingredient a", "strength": "5 mg"},
                {"name": "ingredient b", "strength": "10MG"},
            ],
            "dosage_form": "Tablet",
            "route": ["Oral"],
        }
        self.assertEqual(equivalence_signature(first), equivalence_signature(second))

    def test_signature_requires_route_and_strength(self) -> None:
        self.assertIsNone(
            equivalence_signature(
                {
                    "active_ingredients": [{"name": "Ingredient", "strength": "10 mg"}],
                    "dosage_form": "TABLET",
                    "route": [],
                }
            )
        )


class GraphTraversalTest(unittest.TestCase):
    def test_finds_other_manufacturer_and_risk_flags(self) -> None:
        connection = sqlite3.connect(":memory:")
        create_schema(connection)
        connection.executemany(
            "INSERT INTO manufacturers(manufacturer_id, normalized_name, display_name) VALUES (?, ?, ?)",
            [(1, "maker one", "Maker One"), (2, "maker two", "Maker Two")],
        )
        connection.executemany(
            """
            INSERT INTO drug_products(
                product_id, product_ndc, generic_name, dosage_form, route, manufacturer_id
            ) VALUES (?, ?, ?, 'TABLET', 'ORAL', ?)
            """,
            [
                ("P1", "000010001", "Example Drug", 1),
                ("P2", "000020002", "Example Drug", 2),
            ],
        )
        connection.executemany(
            "INSERT INTO ndcs(ndc, ndc_type) VALUES (?, 'product')",
            [("000010001",), ("000020002",)],
        )
        connection.executemany(
            "INSERT INTO product_ndcs(product_id, ndc, source_field) VALUES (?, ?, 'product_ndc')",
            [("P1", "000010001"), ("P2", "000020002")],
        )
        connection.execute(
            "INSERT INTO active_ingredients(ingredient_id, normalized_name, display_name) VALUES (1, 'example ingredient', 'EXAMPLE INGREDIENT')"
        )
        connection.executemany(
            "INSERT INTO product_ingredients VALUES (?, 1, '10 mg', '10 mg')",
            [("P1",), ("P2",)],
        )
        connection.execute(
            "INSERT INTO shortages(shortage_id, status) VALUES (1, 'Current')"
        )
        connection.execute(
            "INSERT INTO product_shortages VALUES ('P2', 1, 'package_ndc')"
        )
        connection.execute(
            "INSERT INTO recalls(recall_id, recall_number, status) VALUES ('R1', 'R1', 'Ongoing')"
        )
        connection.execute("INSERT INTO product_recalls VALUES ('P2', 'R1', 'openfda_product_ndc')")
        connection.execute(
            "INSERT INTO equivalence_groups VALUES ('G1', 'signature', 'proxy')"
        )
        connection.executemany(
            "INSERT INTO product_equivalence_groups VALUES (?, 'G1')", [("P1",), ("P2",)]
        )

        result = query_drug(connection, "0001-0001")

        self.assertEqual(result["matched_products"][0]["product_id"], "P1")
        self.assertEqual(result["summary"]["alternative_manufacturers"], 1)
        self.assertEqual(result["summary"]["alternatives_currently_in_shortage"], 1)
        self.assertEqual(result["summary"]["alternatives_under_ongoing_recall"], 1)
        self.assertEqual(result["summary"]["therapeutically_equivalent_products"], 1)
        connection.close()


if __name__ == "__main__":
    unittest.main()
