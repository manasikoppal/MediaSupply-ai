"""Traverse the SQLite knowledge graph for a drug name, ingredient, or NDC."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from ..cleaning.normalizers import normalize_ndc
except ImportError:  # Allow: python src/graph/query_graph.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.cleaning.normalizers import normalize_ndc


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _placeholders(values: list[Any]) -> str:
    return ",".join("?" for _ in values)


def _product_summary(connection: sqlite3.Connection, product_id: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT p.product_id, p.product_ndc, p.generic_name, p.brand_name,
               p.dosage_form, p.route, p.finished, m.display_name
        FROM drug_products p
        JOIN manufacturers m ON m.manufacturer_id = p.manufacturer_id
        WHERE p.product_id = ?
        """,
        (product_id,),
    ).fetchone()
    ingredients = [
        {"name": ingredient, "strength": strength}
        for ingredient, strength in connection.execute(
            """
            SELECT i.display_name, pi.strength
            FROM product_ingredients pi
            JOIN active_ingredients i ON i.ingredient_id = pi.ingredient_id
            WHERE pi.product_id = ?
            ORDER BY i.display_name, pi.strength
            """,
            (product_id,),
        )
    ]
    package_ndcs = [
        value
        for (value,) in connection.execute(
            """
            SELECT pn.ndc FROM product_ndcs pn
            JOIN ndcs n ON n.ndc = pn.ndc
            WHERE pn.product_id = ? AND n.ndc_type = 'package'
            ORDER BY pn.ndc
            """,
            (product_id,),
        )
    ]
    current_shortages = connection.execute(
        "SELECT COUNT(*) FROM current_product_shortages WHERE product_id = ?", (product_id,)
    ).fetchone()[0]
    ongoing_recalls = connection.execute(
        "SELECT COUNT(*) FROM ongoing_product_recalls WHERE product_id = ?", (product_id,)
    ).fetchone()[0]
    return {
        "product_id": row[0],
        "product_ndc": row[1],
        "generic_name": row[2],
        "brand_name": row[3],
        "dosage_form": row[4],
        "route": row[5],
        "finished": bool(row[6]),
        "manufacturer": row[7],
        "active_ingredients": ingredients,
        "package_ndcs": package_ndcs,
        "currently_in_shortage": bool(current_shortages),
        "ongoing_recall": bool(ongoing_recalls),
    }


def _matching_product_ids(
    connection: sqlite3.Connection, query: str, limit: int
) -> list[str]:
    normalized_ndc = normalize_ndc(query)
    if normalized_ndc:
        return [
            row[0]
            for row in connection.execute(
                "SELECT product_id FROM product_ndcs WHERE ndc = ? ORDER BY product_id LIMIT ?",
                (normalized_ndc, limit),
            )
        ]

    pattern = f"%{query.casefold()}%"
    return [
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT p.product_id
            FROM drug_products p
            LEFT JOIN product_ingredients pi ON pi.product_id = p.product_id
            LEFT JOIN active_ingredients i ON i.ingredient_id = pi.ingredient_id
            WHERE p.finished = 1
              AND (
                   lower(COALESCE(p.generic_name, '')) LIKE ?
                OR lower(COALESCE(p.brand_name, '')) LIKE ?
                OR lower(COALESCE(i.display_name, '')) LIKE ?
              )
            ORDER BY p.product_id
            LIMIT ?
            """,
            (pattern, pattern, pattern, limit),
        )
    ]


def query_drug(
    connection: sqlite3.Connection, query: str, sample_limit: int = 25
) -> dict[str, Any]:
    matched_ids = _matching_product_ids(connection, query, sample_limit)
    if not matched_ids:
        return {
            "query": query,
            "matched_products": [],
            "summary": {
                "alternative_manufacturers": 0,
                "alternative_products": 0,
                "alternatives_currently_in_shortage": 0,
                "alternatives_under_ongoing_recall": 0,
                "therapeutically_equivalent_products": 0,
            },
            "alternative_manufacturers": [],
            "therapeutically_equivalent_products": [],
        }

    placeholders = _placeholders(matched_ids)
    alternative_cte = f"""
        WITH selected_ingredients AS (
            SELECT DISTINCT ingredient_id
            FROM product_ingredients
            WHERE product_id IN ({placeholders})
        ),
        selected_manufacturers AS (
            SELECT DISTINCT manufacturer_id
            FROM drug_products
            WHERE product_id IN ({placeholders})
        ),
        alternatives AS (
            SELECT DISTINCT other.product_id
            FROM product_ingredients other_ingredient
            JOIN selected_ingredients selected
              ON selected.ingredient_id = other_ingredient.ingredient_id
            JOIN drug_products other ON other.product_id = other_ingredient.product_id
            WHERE other.manufacturer_id NOT IN (SELECT manufacturer_id FROM selected_manufacturers)
              AND other.finished = 1
        )
    """
    parameters = matched_ids + matched_ids
    alternative_stats = connection.execute(
        alternative_cte
        + """
        SELECT COUNT(*), COUNT(DISTINCT p.manufacturer_id),
               SUM(EXISTS(SELECT 1 FROM current_product_shortages s WHERE s.product_id = a.product_id)),
               SUM(EXISTS(SELECT 1 FROM ongoing_product_recalls r WHERE r.product_id = a.product_id))
        FROM alternatives a
        JOIN drug_products p ON p.product_id = a.product_id
        """,
        parameters,
    ).fetchone()
    alternative_ids = [
        row[0]
        for row in connection.execute(
            alternative_cte
            + """
            , ranked AS (
                SELECT a.product_id, p.manufacturer_id,
                       EXISTS(
                           SELECT 1 FROM ongoing_product_recalls r
                           WHERE r.product_id = a.product_id
                       ) AS ongoing_recall,
                       EXISTS(
                           SELECT 1 FROM current_product_shortages s
                           WHERE s.product_id = a.product_id
                       ) AS current_shortage,
                       ROW_NUMBER() OVER (
                           PARTITION BY p.manufacturer_id
                           ORDER BY
                               EXISTS(
                                   SELECT 1 FROM ongoing_product_recalls r
                                   WHERE r.product_id = a.product_id
                               ) DESC,
                               EXISTS(
                                   SELECT 1 FROM current_product_shortages s
                                   WHERE s.product_id = a.product_id
                               ) DESC,
                               a.product_id
                       ) AS manufacturer_rank
                FROM alternatives a
                JOIN drug_products p ON p.product_id = a.product_id
            )
            SELECT product_id
            FROM ranked
            WHERE manufacturer_rank = 1
            ORDER BY ongoing_recall DESC, current_shortage DESC, product_id
            LIMIT ?
            """,
            parameters + [sample_limit],
        )
    ]

    equivalent_cte = f"""
        WITH equivalents AS (
            SELECT DISTINCT other.product_id
            FROM product_equivalence_groups selected
            JOIN product_equivalence_groups other ON other.group_id = selected.group_id
            WHERE selected.product_id IN ({placeholders})
              AND other.product_id NOT IN ({placeholders})
        )
    """
    equivalent_count = connection.execute(
        equivalent_cte + "SELECT COUNT(*) FROM equivalents", parameters
    ).fetchone()[0]
    equivalent_ids = [
        row[0]
        for row in connection.execute(
            equivalent_cte + "SELECT product_id FROM equivalents ORDER BY product_id LIMIT ?",
            parameters + [sample_limit],
        )
    ]

    alternative_summaries = [
        _product_summary(connection, product_id) for product_id in alternative_ids
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for product in alternative_summaries[:sample_limit]:
        grouped[product["manufacturer"]].append(product)

    return {
        "query": query,
        "matched_products": [
            _product_summary(connection, product_id) for product_id in matched_ids
        ],
        "summary": {
            "alternative_manufacturers": alternative_stats[1] or 0,
            "alternative_products": alternative_stats[0] or 0,
            "alternatives_currently_in_shortage": alternative_stats[2] or 0,
            "alternatives_under_ongoing_recall": alternative_stats[3] or 0,
            "therapeutically_equivalent_products": equivalent_count,
        },
        "alternative_manufacturers": [
            {"manufacturer": manufacturer, "products": products}
            for manufacturer, products in grouped.items()
        ],
        "therapeutically_equivalent_products": [
            _product_summary(connection, product_id)
            for product_id in equivalent_ids[:sample_limit]
        ],
    }


def _latest_database() -> Path:
    candidates = sorted(
        (REPOSITORY_ROOT / "data" / "processed").glob("????-??-??_??/knowledge_graph.sqlite")
    )
    if not candidates:
        raise FileNotFoundError("No knowledge_graph.sqlite has been built")
    return candidates[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Drug name, active ingredient, product NDC, or package NDC")
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    database = args.database or _latest_database()
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        result = query_drug(connection, args.query, args.limit)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
