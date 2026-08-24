"""SQLite schema and canonical graph keys."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

try:
    from ..cleaning.normalizers import normalize_drug_name
except ImportError:  # Allow direct imports from src/graph.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.cleaning.normalizers import normalize_drug_name


SCHEMA = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE manufacturers (
    manufacturer_id INTEGER PRIMARY KEY,
    normalized_name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL
);

CREATE TABLE sponsors (
    sponsor_id INTEGER PRIMARY KEY,
    normalized_name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL
);

CREATE TABLE applications (
    application_number TEXT PRIMARY KEY,
    raw_application_number TEXT,
    sponsor_id INTEGER REFERENCES sponsors(sponsor_id)
);

CREATE TABLE drug_products (
    product_id TEXT PRIMARY KEY,
    product_ndc TEXT,
    raw_product_ndc TEXT,
    generic_name TEXT,
    brand_name TEXT,
    dosage_form TEXT,
    route TEXT,
    finished INTEGER NOT NULL DEFAULT 1 CHECK (finished IN (0, 1)),
    manufacturer_id INTEGER NOT NULL REFERENCES manufacturers(manufacturer_id),
    application_number TEXT REFERENCES applications(application_number)
);

CREATE TABLE ndcs (
    ndc TEXT PRIMARY KEY,
    ndc_type TEXT NOT NULL CHECK (ndc_type IN ('product', 'package'))
);

CREATE TABLE product_ndcs (
    product_id TEXT NOT NULL REFERENCES drug_products(product_id),
    ndc TEXT NOT NULL REFERENCES ndcs(ndc),
    source_field TEXT NOT NULL,
    PRIMARY KEY (product_id, ndc)
);

CREATE TABLE active_ingredients (
    ingredient_id INTEGER PRIMARY KEY,
    normalized_name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL
);

CREATE TABLE product_ingredients (
    product_id TEXT NOT NULL REFERENCES drug_products(product_id),
    ingredient_id INTEGER NOT NULL REFERENCES active_ingredients(ingredient_id),
    strength TEXT NOT NULL,
    normalized_strength TEXT NOT NULL,
    PRIMARY KEY (product_id, ingredient_id, normalized_strength)
);

CREATE TABLE shortages (
    shortage_id INTEGER PRIMARY KEY,
    package_ndc TEXT,
    generic_name TEXT,
    company_name TEXT,
    status TEXT,
    shortage_reason TEXT,
    operational_context TEXT,
    discontinuation_context TEXT,
    initial_posting_date TEXT,
    update_date TEXT
);

CREATE TABLE product_shortages (
    product_id TEXT NOT NULL REFERENCES drug_products(product_id),
    shortage_id INTEGER NOT NULL REFERENCES shortages(shortage_id),
    match_method TEXT NOT NULL,
    PRIMARY KEY (product_id, shortage_id)
);

CREATE TABLE recalls (
    recall_id TEXT PRIMARY KEY,
    recall_number TEXT,
    event_id TEXT,
    status TEXT,
    classification TEXT,
    recalling_firm TEXT,
    product_description TEXT,
    reason_for_recall TEXT,
    recall_initiation_date TEXT
);

CREATE TABLE product_recalls (
    product_id TEXT NOT NULL REFERENCES drug_products(product_id),
    recall_id TEXT NOT NULL REFERENCES recalls(recall_id),
    match_method TEXT NOT NULL,
    PRIMARY KEY (product_id, recall_id)
);

CREATE TABLE equivalence_groups (
    group_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL UNIQUE,
    method TEXT NOT NULL
);

CREATE TABLE product_equivalence_groups (
    product_id TEXT NOT NULL REFERENCES drug_products(product_id),
    group_id TEXT NOT NULL REFERENCES equivalence_groups(group_id),
    PRIMARY KEY (product_id, group_id)
);

CREATE INDEX idx_products_manufacturer ON drug_products(manufacturer_id);
CREATE INDEX idx_products_application ON drug_products(application_number);
CREATE INDEX idx_product_ndcs_ndc ON product_ndcs(ndc);
CREATE INDEX idx_product_ingredients_ingredient ON product_ingredients(ingredient_id);
CREATE INDEX idx_product_shortages_shortage ON product_shortages(shortage_id);
CREATE INDEX idx_product_recalls_recall ON product_recalls(recall_id);
CREATE INDEX idx_equivalence_members_group ON product_equivalence_groups(group_id);

CREATE VIEW manufacturer_markets_products AS
SELECT m.manufacturer_id, m.display_name AS manufacturer_name,
       p.product_id, p.generic_name, p.brand_name, p.product_ndc
FROM manufacturers m
JOIN drug_products p ON p.manufacturer_id = m.manufacturer_id;

CREATE VIEW drug_product_active_ingredients AS
SELECT p.product_id, p.generic_name, p.brand_name,
       i.ingredient_id, i.display_name AS ingredient_name,
       pi.strength
FROM drug_products p
JOIN product_ingredients pi ON pi.product_id = p.product_id
JOIN active_ingredients i ON i.ingredient_id = pi.ingredient_id;

CREATE VIEW ndc_active_ingredients AS
SELECT pn.ndc, n.ndc_type, pi.ingredient_id,
       i.display_name AS ingredient_name, pi.strength
FROM product_ndcs pn
JOIN ndcs n ON n.ndc = pn.ndc
JOIN product_ingredients pi ON pi.product_id = pn.product_id
JOIN active_ingredients i ON i.ingredient_id = pi.ingredient_id;

CREATE VIEW current_product_shortages AS
SELECT p.product_id, s.shortage_id, s.status, s.shortage_reason,
       s.operational_context, s.discontinuation_context
FROM drug_products p
JOIN product_shortages ps ON ps.product_id = p.product_id
JOIN shortages s ON s.shortage_id = ps.shortage_id
WHERE s.status = 'Current';

CREATE VIEW ongoing_product_recalls AS
SELECT p.product_id, r.recall_id, r.recall_number, r.classification,
       r.reason_for_recall
FROM drug_products p
JOIN product_recalls pr ON pr.product_id = p.product_id
JOIN recalls r ON r.recall_id = pr.recall_id
WHERE r.status = 'Ongoing';

CREATE VIEW therapeutic_equivalent_products AS
SELECT left_member.product_id AS product_id,
       right_member.product_id AS equivalent_product_id,
       left_member.group_id
FROM product_equivalence_groups left_member
JOIN product_equivalence_groups right_member
  ON right_member.group_id = left_member.group_id
 AND right_member.product_id <> left_member.product_id;
"""


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def normalize_application_number(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"[^A-Z0-9]", "", str(value).upper())
    return normalized or None


def normalize_strength(value: str | None) -> str | None:
    normalized = normalize_drug_name(value)
    return normalized.replace(" ", "") if normalized else None


def equivalence_signature(record: dict[str, Any]) -> str | None:
    """Return the proxy TE signature or None when any required field is missing."""
    dosage_form = normalize_drug_name(record.get("dosage_form"))
    routes = record.get("route") or []
    if isinstance(routes, str):
        routes = [routes]
    normalized_routes = sorted(
        route for route in (normalize_drug_name(value) for value in routes) if route
    )

    ingredients = []
    for ingredient in record.get("active_ingredients") or []:
        if not isinstance(ingredient, dict):
            return None
        name = normalize_drug_name(ingredient.get("name"))
        strength = normalize_strength(ingredient.get("strength"))
        if not name or not strength:
            return None
        ingredients.append((name, strength))
    if not dosage_form or not normalized_routes or not ingredients:
        return None

    return json.dumps(
        {
            "active_ingredients": sorted(ingredients),
            "dosage_form": dosage_form,
            "route": normalized_routes,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
