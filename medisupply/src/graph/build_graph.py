"""Build a queryable SQLite knowledge graph from the newest cleaned FDA snapshot."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from .query_graph import query_drug
    from .schema import (
        create_schema,
        equivalence_signature,
        normalize_application_number,
        normalize_strength,
    )
    from ..cleaning.normalizers import (
        normalize_drug_name,
        normalize_manufacturer,
        normalize_ndc,
    )
except ImportError:  # Allow: python src/graph/build_graph.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.cleaning.normalizers import (
        normalize_drug_name,
        normalize_manufacturer,
        normalize_ndc,
    )
    from src.graph.query_graph import query_drug
    from src.graph.schema import (
        create_schema,
        equivalence_signature,
        normalize_application_number,
        normalize_strength,
    )


LOGGER = logging.getLogger("medisupply.graph")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}$")
NDC_IN_TEXT = re.compile(
    r"\bNDC(?:\s*(?:No\.?|#|:))?\s*[\(\[]?(\d{4,5}-\d{3,4}(?:-\d{1,2})?)[\)\]]?",
    re.IGNORECASE,
)


def _latest_inputs() -> tuple[Path, Path]:
    snapshots_root = REPOSITORY_ROOT / "data" / "snapshots"
    candidates = sorted(
        path
        for path in snapshots_root.iterdir()
        if path.is_dir()
        and SNAPSHOT_PATTERN.fullmatch(path.name)
        and all(
            (path / f"{source}.json").is_file()
            for source in ("ndc", "recalls", "drugsfda")
        )
    )
    if not candidates:
        raise RuntimeError("No complete FDA snapshot is available")
    snapshot = candidates[-1]
    enriched = (
        REPOSITORY_ROOT
        / "data"
        / "processed"
        / snapshot.name
        / "shortages_enriched.json"
    )
    if not enriched.is_file():
        raise RuntimeError(f"Run Phase 5 cleaning first; missing {enriched}")
    return snapshot, enriched


def _load_results(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError(f"{path} does not contain a results list")
    return results


def _preferred_names(values: Iterable[str]) -> dict[str, str]:
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    for value in values:
        normalized = normalize_manufacturer(value) or normalize_drug_name(value)
        if normalized:
            grouped[normalized][value] += 1
    return {
        normalized: counts.most_common(1)[0][0]
        for normalized, counts in grouped.items()
    }


def _recall_id(record: dict[str, Any]) -> str:
    if record.get("recall_number"):
        return str(record["recall_number"])
    identity = "|".join(
        str(record.get(field) or "")
        for field in ("event_id", "report_date", "product_description", "recalling_firm")
    )
    return "missing:" + hashlib.sha256(identity.encode()).hexdigest()[:20]


def _lookup_ndc_products(connection: sqlite3.Connection, value: str | None) -> list[str]:
    normalized = normalize_ndc(value)
    if not normalized:
        return []
    return [
        row[0]
        for row in connection.execute(
            "SELECT product_id FROM product_ndcs WHERE ndc = ?", (normalized,)
        )
    ]


def _insert_ndc_graph(
    connection: sqlite3.Connection, records: list[dict[str, Any]]
) -> dict[str, Any]:
    manufacturer_names = _preferred_names(
        record["labeler_name"] for record in records if record.get("labeler_name")
    )
    connection.executemany(
        "INSERT INTO manufacturers(normalized_name, display_name) VALUES (?, ?)",
        sorted(manufacturer_names.items()),
    )
    manufacturer_ids = dict(
        connection.execute("SELECT normalized_name, manufacturer_id FROM manufacturers")
    )

    ingredient_names: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        for ingredient in record.get("active_ingredients") or []:
            if not isinstance(ingredient, dict) or not ingredient.get("name"):
                continue
            normalized = normalize_drug_name(ingredient["name"])
            if normalized:
                ingredient_names[normalized][ingredient["name"]] += 1
    connection.executemany(
        "INSERT INTO active_ingredients(normalized_name, display_name) VALUES (?, ?)",
        (
            (normalized, counts.most_common(1)[0][0])
            for normalized, counts in sorted(ingredient_names.items())
        ),
    )
    ingredient_ids = dict(
        connection.execute("SELECT normalized_name, ingredient_id FROM active_ingredients")
    )

    applications = {
        normalize_application_number(record.get("application_number"))
        for record in records
        if normalize_application_number(record.get("application_number"))
    }
    connection.executemany(
        "INSERT INTO applications(application_number, raw_application_number) VALUES (?, ?)",
        ((application, application) for application in sorted(applications)),
    )

    equivalence_members: dict[str, list[str]] = defaultdict(list)
    invalid_package_ndcs = 0
    products_with_application = 0
    products_with_equivalence_signature = 0
    finished_products = 0

    for record in records:
        product_id = record["product_id"]
        manufacturer_key = normalize_manufacturer(record.get("labeler_name")) or normalize_drug_name(
            record.get("labeler_name")
        )
        application = normalize_application_number(record.get("application_number"))
        if application:
            products_with_application += 1
        is_finished = record.get("finished") is True
        if is_finished:
            finished_products += 1
        routes = record.get("route") or []
        if isinstance(routes, str):
            routes = [routes]
        product_ndc = normalize_ndc(record.get("product_ndc"))
        connection.execute(
            """
            INSERT INTO drug_products(
                product_id, product_ndc, raw_product_ndc, generic_name, brand_name,
                dosage_form, route, finished, manufacturer_id, application_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                product_ndc,
                record.get("product_ndc"),
                record.get("generic_name"),
                record.get("brand_name"),
                record.get("dosage_form"),
                "|".join(sorted(str(route) for route in routes)) or None,
                int(is_finished),
                manufacturer_ids[manufacturer_key],
                application,
            ),
        )
        if product_ndc:
            connection.execute(
                "INSERT OR IGNORE INTO ndcs(ndc, ndc_type) VALUES (?, 'product')",
                (product_ndc,),
            )
            connection.execute(
                "INSERT OR IGNORE INTO product_ndcs(product_id, ndc, source_field) VALUES (?, ?, 'product_ndc')",
                (product_id, product_ndc),
            )
        for package in record.get("packaging") or []:
            if not isinstance(package, dict):
                continue
            package_ndc = normalize_ndc(package.get("package_ndc"))
            if not package_ndc:
                invalid_package_ndcs += 1
                continue
            connection.execute(
                "INSERT OR IGNORE INTO ndcs(ndc, ndc_type) VALUES (?, 'package')",
                (package_ndc,),
            )
            connection.execute(
                "INSERT OR IGNORE INTO product_ndcs(product_id, ndc, source_field) VALUES (?, ?, 'package_ndc')",
                (product_id, package_ndc),
            )
        for ingredient in record.get("active_ingredients") or []:
            if not isinstance(ingredient, dict):
                continue
            normalized_name = normalize_drug_name(ingredient.get("name"))
            normalized_strength = normalize_strength(ingredient.get("strength"))
            if not normalized_name or not normalized_strength:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO product_ingredients(
                    product_id, ingredient_id, strength, normalized_strength
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    product_id,
                    ingredient_ids[normalized_name],
                    ingredient.get("strength"),
                    normalized_strength,
                ),
            )
        signature = equivalence_signature(record)
        if signature and is_finished:
            products_with_equivalence_signature += 1
            equivalence_members[signature].append(product_id)

    multi_groups = {
        signature: product_ids
        for signature, product_ids in equivalence_members.items()
        if len(product_ids) > 1
    }
    for signature, product_ids in multi_groups.items():
        group_id = hashlib.sha256(signature.encode()).hexdigest()[:24]
        connection.execute(
            "INSERT INTO equivalence_groups(group_id, signature, method) VALUES (?, ?, ?)",
            (
                group_id,
                signature,
                "proxy_active_ingredient_strength_dosage_form_route",
            ),
        )
        connection.executemany(
            "INSERT INTO product_equivalence_groups(product_id, group_id) VALUES (?, ?)",
            ((product_id, group_id) for product_id in product_ids),
        )

    return {
        "invalid_package_ndcs": invalid_package_ndcs,
        "products_with_application": products_with_application,
        "finished_products": finished_products,
        "unfinished_products": len(records) - finished_products,
        "products_with_equivalence_signature": products_with_equivalence_signature,
        "equivalence_groups": len(multi_groups),
        "products_in_equivalence_groups": sum(len(values) for values in multi_groups.values()),
    }


def _insert_applications(
    connection: sqlite3.Connection, records: list[dict[str, Any]]
) -> dict[str, int]:
    sponsor_names = _preferred_names(
        record["sponsor_name"] for record in records if record.get("sponsor_name")
    )
    connection.executemany(
        "INSERT INTO sponsors(normalized_name, display_name) VALUES (?, ?)",
        sorted(sponsor_names.items()),
    )
    sponsor_ids = dict(connection.execute("SELECT normalized_name, sponsor_id FROM sponsors"))
    for record in records:
        application = normalize_application_number(record.get("application_number"))
        sponsor_key = normalize_manufacturer(record.get("sponsor_name")) or normalize_drug_name(
            record.get("sponsor_name")
        )
        connection.execute(
            """
            INSERT INTO applications(application_number, raw_application_number, sponsor_id)
            VALUES (?, ?, ?)
            ON CONFLICT(application_number) DO UPDATE SET
                raw_application_number = excluded.raw_application_number,
                sponsor_id = excluded.sponsor_id
            """,
            (application, record.get("application_number"), sponsor_ids[sponsor_key]),
        )
    return {"drugsfda_applications": len(records), "sponsors": len(sponsor_names)}


def _insert_shortages(
    connection: sqlite3.Connection, records: list[dict[str, Any]]
) -> dict[str, Any]:
    method_counts: Counter[str] = Counter()
    linked = 0
    for shortage_id, record in enumerate(records, start=1):
        package_ndc = normalize_ndc(record.get("package_ndc"))
        connection.execute(
            """
            INSERT INTO shortages(
                shortage_id, package_ndc, generic_name, company_name, status,
                shortage_reason, operational_context, discontinuation_context,
                initial_posting_date, update_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                shortage_id,
                package_ndc,
                record.get("generic_name"),
                record.get("company_name"),
                record.get("status"),
                record.get("shortage_reason"),
                record.get("operational_context"),
                record.get("discontinuation_context"),
                record.get("initial_posting_date"),
                record.get("update_date"),
            ),
        )
        resolution = (record.get("cleaning") or {}).get("ndc_resolution") or {}
        identity = resolution.get("identity") or {}
        product_id = identity.get("product_id")
        method = resolution.get("method") or "unmatched"
        method_counts[method] += 1
        if product_id and connection.execute(
            "SELECT 1 FROM drug_products WHERE product_id = ?", (product_id,)
        ).fetchone():
            connection.execute(
                "INSERT INTO product_shortages(product_id, shortage_id, match_method) VALUES (?, ?, ?)",
                (product_id, shortage_id, method),
            )
            linked += 1
    return {
        "total": len(records),
        "linked": linked,
        "success_rate": 100 * linked / len(records) if records else 0.0,
        "methods": dict(sorted(method_counts.items())),
    }


def _insert_recalls(
    connection: sqlite3.Connection, records: list[dict[str, Any]]
) -> dict[str, Any]:
    method_counts: Counter[str] = Counter()
    edge_counts: Counter[str] = Counter()
    harmonized_records = 0
    harmonized_linked = 0
    linked_records = 0

    for record in records:
        recall_id = _recall_id(record)
        connection.execute(
            """
            INSERT INTO recalls(
                recall_id, recall_number, event_id, status, classification,
                recalling_firm, product_description, reason_for_recall,
                recall_initiation_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recall_id,
                record.get("recall_number"),
                record.get("event_id"),
                record.get("status"),
                record.get("classification"),
                record.get("recalling_firm"),
                record.get("product_description"),
                record.get("reason_for_recall"),
                record.get("recall_initiation_date"),
            ),
        )

        openfda = record.get("openfda") or {}
        if openfda:
            harmonized_records += 1
        product_methods: dict[str, str] = {}
        for field in ("package_ndc", "product_ndc"):
            values = openfda.get(field) or []
            if isinstance(values, str):
                values = [values]
            for value in values:
                for product_id in _lookup_ndc_products(connection, value):
                    product_methods.setdefault(product_id, f"openfda_{field}")

        record_method = "openfda_ndc" if product_methods else None
        if not product_methods:
            text = " ".join(
                str(record.get(field) or "")
                for field in ("product_description", "code_info", "more_code_info")
            )
            for value in NDC_IN_TEXT.findall(text):
                for product_id in _lookup_ndc_products(connection, value):
                    product_methods.setdefault(product_id, "explicit_ndc_text")
            if product_methods:
                record_method = "explicit_ndc_text"

        if not product_methods:
            applications = openfda.get("application_number") or []
            if isinstance(applications, str):
                applications = [applications]
            for value in applications:
                application = normalize_application_number(value)
                if not application:
                    continue
                for (product_id,) in connection.execute(
                    "SELECT product_id FROM drug_products WHERE application_number = ?",
                    (application,),
                ):
                    product_methods.setdefault(product_id, "application_number")
            if product_methods:
                record_method = "application_number"

        if product_methods:
            linked_records += 1
            if openfda:
                harmonized_linked += 1
            method_counts[record_method] += 1
        else:
            method_counts["unmatched"] += 1
        for product_id, method in product_methods.items():
            connection.execute(
                "INSERT OR IGNORE INTO product_recalls(product_id, recall_id, match_method) VALUES (?, ?, ?)",
                (product_id, recall_id, method),
            )
            edge_counts[method] += 1

    return {
        "total": len(records),
        "harmonized_records": harmonized_records,
        "linked": linked_records,
        "success_rate": 100 * linked_records / len(records) if records else 0.0,
        "harmonized_link_rate": (
            100 * harmonized_linked / harmonized_records
            if harmonized_records
            else 0.0
        ),
        "methods": dict(sorted(method_counts.items())),
        "edges_by_method": dict(sorted(edge_counts.items())),
    }


def _graph_counts(connection: sqlite3.Connection) -> tuple[dict[str, int], dict[str, int]]:
    node_tables = (
        "manufacturers",
        "drug_products",
        "ndcs",
        "active_ingredients",
        "shortages",
        "recalls",
        "applications",
        "sponsors",
        "equivalence_groups",
    )
    edge_tables = (
        "product_ndcs",
        "product_ingredients",
        "product_shortages",
        "product_recalls",
        "product_equivalence_groups",
    )
    nodes = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in node_tables
    }
    edges = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in edge_tables
    }
    edges["application_owned_by_sponsor"] = connection.execute(
        "SELECT COUNT(*) FROM applications WHERE sponsor_id IS NOT NULL"
    ).fetchone()[0]
    edges["manufacturer_markets_product"] = nodes["drug_products"]
    edges["ndc_contains_ingredient"] = connection.execute(
        "SELECT COUNT(*) FROM ndc_active_ingredients"
    ).fetchone()[0]
    edges["therapeutic_equivalent_product"] = connection.execute(
        """
        SELECT COALESCE(SUM(member_count * (member_count - 1)), 0)
        FROM (
            SELECT COUNT(*) AS member_count
            FROM product_equivalence_groups
            GROUP BY group_id
        )
        """
    ).fetchone()[0]
    return nodes, edges


def _worked_examples(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    candidates = [
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT p.product_ndc
            FROM current_product_shortages s
            JOIN drug_products p ON p.product_id = s.product_id
            JOIN product_ingredients pi ON pi.product_id = p.product_id
            WHERE p.product_ndc IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM product_ingredients other_pi
                  JOIN drug_products other ON other.product_id = other_pi.product_id
                  WHERE other_pi.ingredient_id = pi.ingredient_id
                    AND other.manufacturer_id <> p.manufacturer_id
              )
            ORDER BY p.product_ndc
            LIMIT 30
            """
        )
    ]
    scored = []
    for ndc in candidates:
        result = query_drug(connection, ndc, sample_limit=8)
        summary = result["summary"]
        score = (
            100 * summary["alternatives_under_ongoing_recall"]
            + 10 * summary["alternatives_currently_in_shortage"]
            + summary["alternative_manufacturers"]
        )
        ingredient_key = tuple(
            ingredient["name"]
            for ingredient in result["matched_products"][0]["active_ingredients"]
        )
        scored.append((score, ingredient_key, result))

    examples = []
    seen_ingredients = set()
    for _, ingredient_key, result in sorted(scored, key=lambda item: item[0], reverse=True):
        if ingredient_key in seen_ingredients:
            continue
        seen_ingredients.add(ingredient_key)
        examples.append(result)
        if len(examples) == 3:
            break
    return examples


def _quality_markdown(metrics: dict[str, Any]) -> str:
    lines = [
        "# Knowledge Graph Data Quality Report",
        "",
        f"Generated: {metrics['generated_at']}",
        "",
        f"Snapshot: `{metrics['snapshot']}`",
        "",
        "Database: `data/processed/{}/knowledge_graph.sqlite`".format(metrics["snapshot"]),
        "",
        "## Graph size",
        "",
        "### Nodes",
        "",
        "| Node type | Count |",
        "|---|---:|",
    ]
    for name, count in metrics["nodes"].items():
        lines.append(f"| `{name}` | {count:,} |")
    lines.extend(["", "### Relationships", "", "| Edge type | Count |", "|---|---:|"])
    for name, count in metrics["edges"].items():
        lines.append(f"| `{name}` | {count:,} |")

    shortage = metrics["shortage_links"]
    recall = metrics["recall_links"]
    application = metrics["application_links"]
    equivalence = metrics["therapeutic_equivalence"]
    lines.extend(
        [
            "",
            "## Cross-source link quality",
            "",
            "| Relationship | Linked | Total | Success rate |",
            "|---|---:|---:|---:|",
            f"| Shortage → NDC product | {shortage['linked']:,} | {shortage['total']:,} | {shortage['success_rate']:.2f}% |",
            f"| Recall → NDC product | {recall['linked']:,} | {recall['total']:,} | {recall['success_rate']:.2f}% |",
            f"| Drugs@FDA application → Sponsor | {application['drugsfda_applications']:,} | {application['drugsfda_applications']:,} | 100.00% |",
            f"| All graph applications → Sponsor | {metrics['edges']['application_owned_by_sponsor']:,} | {metrics['nodes']['applications']:,} | {application['application_sponsor_rate']:.2f}% |",
            f"| NDC product → sponsored application | {application['products_with_sponsored_application']:,} | {metrics['nodes']['drug_products']:,} | {application['product_sponsor_rate']:.2f}% |",
            "",
            f"Recall linkage is {recall['harmonized_link_rate']:.2f}% for the {recall['harmonized_records']:,} records where openFDA supplies harmonized identifiers. Older recalls without identifiers remain unlinked unless their text contains an explicit NDC.",
            "",
            "### Recall match methods",
            "",
            "| Method | Recall records |",
            "|---|---:|",
        ]
    )
    for method, count in recall["methods"].items():
        lines.append(f"| `{method}` | {count:,} |")

    lines.extend(
        [
            "",
            "## Therapeutic-equivalence proxy",
            "",
            "Orange Book data is not present in the current ingestion snapshot. Candidate equivalence therefore requires a finished drug product with the same normalized active-ingredient set, strength, dosage form, and route.",
            "",
            f"- Products with a complete proxy signature: {equivalence['eligible_products']:,}",
            f"- Products belonging to a multi-product equivalence group: {equivalence['products_in_groups']:,}",
            f"- Multi-product equivalence groups: {equivalence['groups']:,}",
            "- These are candidates, not FDA-rated therapeutic-equivalence determinations.",
            f"- Unfinished NDC products retained as graph nodes but excluded from alternative/equivalence results: {metrics['product_scope']['unfinished_products']:,}",
            "",
            "## Worked graph traversals",
            "",
            "Each example follows product → active ingredient → other products/manufacturers, then checks current shortages, ongoing recalls, and proxy-equivalent products.",
        ]
    )
    for number, example in enumerate(metrics["worked_examples"], start=1):
        product = example["matched_products"][0]
        summary = example["summary"]
        ingredient_text = ", ".join(
            f"{item['name']} ({item['strength']})" for item in product["active_ingredients"]
        )
        lines.extend(
            [
                "",
                f"### {number}. {product['brand_name'] or product['generic_name']} — `{example['query']}`",
                "",
                f"- Manufacturer: {product['manufacturer']}",
                f"- Active ingredient traversal: {ingredient_text}",
                f"- Selected product currently in shortage: {'Yes' if product['currently_in_shortage'] else 'No'}",
                f"- Selected product under ongoing recall: {'Yes' if product['ongoing_recall'] else 'No'}",
                f"- Other manufacturers sharing an ingredient: {summary['alternative_manufacturers']:,}",
                f"- Alternative products currently in shortage: {summary['alternatives_currently_in_shortage']:,}",
                f"- Alternative products under ongoing recall: {summary['alternatives_under_ongoing_recall']:,}",
                f"- Proxy-equivalent products: {summary['therapeutically_equivalent_products']:,}",
                "",
                "| Example alternative manufacturer | Product | Current shortage | Ongoing recall |",
                "|---|---|---:|---:|",
            ]
        )
        alternatives = [
            product
            for group in example["alternative_manufacturers"]
            for product in group["products"]
        ][:5]
        for alternative in alternatives:
            name = alternative["brand_name"] or alternative["generic_name"] or alternative["product_id"]
            lines.append(
                f"| {alternative['manufacturer']} | {name} | {'Yes' if alternative['currently_in_shortage'] else 'No'} | {'Yes' if alternative['ongoing_recall'] else 'No'} |"
            )
    lines.append("")
    return "\n".join(lines)


def _atomic_write(path: Path, content: str | dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temp_path = Path(handle.name)
            if isinstance(content, str):
                handle.write(content)
            else:
                json.dump(content, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    except BaseException:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise


def build_graph() -> tuple[Path, Path]:
    snapshot, enriched_path = _latest_inputs()
    output_dir = REPOSITORY_ROOT / "data" / "processed" / snapshot.name
    output_dir.mkdir(parents=True, exist_ok=True)
    database_path = output_dir / "knowledge_graph.sqlite"
    temp = tempfile.NamedTemporaryFile(dir=output_dir, prefix=".knowledge_graph.", delete=False)
    temp_path = Path(temp.name)
    temp.close()
    generated_at = datetime.now().astimezone().isoformat()

    connection = sqlite3.connect(temp_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        create_schema(connection)

        LOGGER.info("Loading NDC graph nodes and edges")
        ndc_records = _load_results(snapshot / "ndc.json")
        ndc_metrics = _insert_ndc_graph(connection, ndc_records)
        del ndc_records

        LOGGER.info("Linking Drugs@FDA applications and sponsors")
        drugsfda_records = _load_results(snapshot / "drugsfda.json")
        application_metrics = _insert_applications(connection, drugsfda_records)
        del drugsfda_records

        LOGGER.info("Linking cleaned shortage records")
        shortage_metrics = _insert_shortages(connection, _load_results(enriched_path))

        LOGGER.info("Linking recall records")
        recall_metrics = _insert_recalls(
            connection, _load_results(snapshot / "recalls.json")
        )

        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('snapshot', ?)", (snapshot.name,)
        )
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('generated_at', ?)", (generated_at,)
        )
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('equivalence_method', 'proxy_active_ingredient_strength_dosage_form_route')"
        )
        connection.commit()

        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if foreign_key_errors or integrity != "ok":
            raise RuntimeError(
                f"SQLite validation failed: integrity={integrity}, foreign_keys={foreign_key_errors[:5]}"
            )

        nodes, edges = _graph_counts(connection)
        products_with_sponsor = connection.execute(
            """
            SELECT COUNT(*) FROM drug_products p
            JOIN applications a ON a.application_number = p.application_number
            WHERE a.sponsor_id IS NOT NULL
            """
        ).fetchone()[0]
        examples = _worked_examples(connection)
        metrics = {
            "snapshot": snapshot.name,
            "generated_at": generated_at,
            "nodes": nodes,
            "edges": edges,
            "shortage_links": shortage_metrics,
            "recall_links": recall_metrics,
            "application_links": {
                **application_metrics,
                "ndc_products_with_application": ndc_metrics["products_with_application"],
                "products_with_sponsored_application": products_with_sponsor,
                "product_sponsor_rate": 100 * products_with_sponsor / nodes["drug_products"],
                "application_sponsor_rate": 100
                * edges["application_owned_by_sponsor"]
                / nodes["applications"],
            },
            "product_scope": {
                "finished_products": ndc_metrics["finished_products"],
                "unfinished_products": ndc_metrics["unfinished_products"],
            },
            "therapeutic_equivalence": {
                "method": "proxy_active_ingredient_strength_dosage_form_route",
                "orange_book_available": False,
                "eligible_products": ndc_metrics["products_with_equivalence_signature"],
                "products_in_groups": ndc_metrics["products_in_equivalence_groups"],
                "groups": ndc_metrics["equivalence_groups"],
            },
            "ndc_quality": {
                "invalid_package_ndcs": ndc_metrics["invalid_package_ndcs"]
            },
            "worked_examples": examples,
            "validation": {"sqlite_integrity": integrity, "foreign_key_errors": 0},
        }
    except BaseException:
        connection.close()
        temp_path.unlink(missing_ok=True)
        raise
    else:
        connection.close()

    os.chmod(temp_path, 0o644)
    os.replace(temp_path, database_path)
    reports_dir = REPOSITORY_ROOT / "reports"
    _atomic_write(reports_dir / "knowledge_graph_quality.json", metrics)
    report_path = reports_dir / "knowledge_graph_quality.md"
    _atomic_write(report_path, _quality_markdown(metrics))
    LOGGER.info("Knowledge graph: %s", database_path)
    LOGGER.info("Quality report: %s", report_path)
    return database_path, report_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build_graph()
