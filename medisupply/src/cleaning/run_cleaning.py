"""Clean the newest FDA snapshot and generate data-quality metrics."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from .entity_resolution import NDCNameMapper, shortage_context_fields
    from .normalizers import normalize_drug_name, normalize_manufacturer, normalize_ndc
except ImportError:  # Allow: python src/cleaning/run_cleaning.py
    from entity_resolution import NDCNameMapper, shortage_context_fields
    from normalizers import normalize_drug_name, normalize_manufacturer, normalize_ndc


LOGGER = logging.getLogger("medisupply.cleaning")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}$")
SOURCE_FIELDS = {
    "shortages": (
        "package_ndc",
        "generic_name",
        "company_name",
        "status",
        "shortage_reason",
        "availability",
    ),
    "ndc": (
        "product_ndc",
        "generic_name",
        "brand_name",
        "labeler_name",
        "active_ingredients",
        "packaging",
    ),
    "recalls": (
        "recall_number",
        "recalling_firm",
        "product_description",
        "reason_for_recall",
        "classification",
    ),
    "drugsfda": ("application_number", "sponsor_name", "products", "submissions"),
}


def _latest_snapshot() -> Path:
    root = REPOSITORY_ROOT / "data" / "snapshots"
    required = {f"{source}.json" for source in SOURCE_FIELDS} | {"manifest.json"}
    snapshots = sorted(
        path
        for path in root.iterdir()
        if path.is_dir()
        and SNAPSHOT_PATTERN.fullmatch(path.name)
        and all((path / filename).is_file() for filename in required)
    )
    if not snapshots:
        raise RuntimeError("No complete FDA snapshot is available")
    return snapshots[-1]


def _load_records(snapshot: Path, source: str) -> list[dict[str, Any]]:
    with (snapshot / f"{source}.json").open(encoding="utf-8") as handle:
        payload = json.load(handle)
    records = payload.get("results")
    if not isinstance(records, list):
        raise ValueError(f"{source}.json does not contain a results list")
    return records


def _is_missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _missing_metrics(records: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[str, Any]:
    total = len(records)
    return {
        field: {
            "missing": sum(_is_missing(record.get(field)) for record in records),
            "percentage": (
                100 * sum(_is_missing(record.get(field)) for record in records) / total
                if total
                else 0.0
            ),
        }
        for field in fields
    }


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    except BaseException:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    except BaseException:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise


def _format_percentage(value: float, count: int) -> str:
    if count and 0 < value < 0.01:
        return "<0.01%"
    return f"{value:.2f}%"


def _markdown_report(metrics: dict[str, Any]) -> str:
    join = metrics["shortage_ndc_join"]
    entities = metrics["unique_entities"]
    lines = [
        "# FDA Data Quality Report",
        "",
        f"Generated: {metrics['generated_at']}",
        "",
        f"Snapshot: `{metrics['snapshot']}`",
        "",
        "## Shortage → NDC join quality",
        "",
        f"**Join success: {join['success_rate']:.2f}% ({join['matched']:,}/{join['total']:,} shortage records).**",
        "",
        "| Match result | Records | Percentage |",
        "|---|---:|---:|",
    ]
    for method, count in join["methods"].items():
        lines.append(f"| `{method}` | {count:,} | {100 * count / join['total']:.2f}% |")

    lines.extend(
        [
            "",
            "Package-level matches are preferred. Product-level fallback is used only when the package code is absent from the current NDC Directory. Ambiguous candidates are not counted as successful joins.",
            "",
            "### Join rate by shortage status",
            "",
            "| Status | Matched | Total | Success rate |",
            "|---|---:|---:|---:|",
        ]
    )
    for status, values in join["by_status"].items():
        lines.append(
            f"| {status} | {values['matched']:,} | {values['total']:,} | {values['success_rate']:.2f}% |"
        )

    context = metrics["shortage_context"]
    lines.extend(
        [
            "",
            "## Shortage context fields",
            "",
            f"- `shortage_reason` remains null for all {context['missing_reason_records']:,} records where FDA did not supply it.",
            f"- `operational_context` captures `related_info` for {context['operational_context_present']:,}/{context['current_missing_reason_records']:,} Current records missing a reason.",
            f"- `discontinuation_context` captures `related_info` for {context['discontinuation_context_present']:,}/{context['discontinued_missing_reason_records']:,} To Be Discontinued records missing a reason.",
            "- `availability` is not used to infer or backfill a shortage reason.",
        ]
    )

    lines.extend(
        [
            "",
            "## Missing fields",
            "",
            "| Source | Field | Missing | Percentage |",
            "|---|---|---:|---:|",
        ]
    )
    for source, fields in metrics["missing_fields"].items():
        for field, values in fields.items():
            lines.append(
                f"| {source} | `{field}` | {values['missing']:,} | {_format_percentage(values['percentage'], values['missing'])} |"
            )

    lines.extend(
        [
            "",
            "## Unique entities",
            "",
            "| Entity | Raw count | Normalized count |",
            "|---|---:|---:|",
            f"| NDC manufacturers | {entities['ndc_manufacturers_raw']:,} | {entities['ndc_manufacturers_normalized']:,} |",
            f"| Shortage manufacturers | {entities['shortage_manufacturers_raw']:,} | {entities['shortage_manufacturers_normalized']:,} |",
            f"| NDC generic names | {entities['generic_names_raw']:,} | {entities['generic_names_normalized']:,} |",
            f"| NDC brand names | {entities['brand_names_raw']:,} | {entities['brand_names_normalized']:,} |",
            f"| Active ingredients | {entities['ingredients_raw']:,} | {entities['ingredients_normalized']:,} |",
            "",
            "## NDC normalization",
            "",
            f"- Unique valid package NDCs: {metrics['ndc_normalization']['unique_package_ndcs']:,}",
            f"- Invalid NDC Directory package values: {metrics['ndc_normalization']['invalid_ndc_packages']:,}",
            f"- Invalid shortage package values: {metrics['ndc_normalization']['invalid_shortage_ndcs']:,}",
            "- Canonical format is 5-4 for products (9 digits) and 5-4-2 for packages (11 digits).",
            "- Unhyphenated 10-digit inputs are rejected as ambiguous rather than padded heuristically.",
            "",
            "## Unmatched shortage examples",
            "",
            "| Package NDC | Generic name | Manufacturer | Status |",
            "|---|---|---|---|",
        ]
    )
    for record in join["unmatched_examples"]:
        cells = [
            str(record.get(field) or "").replace("|", "\\|")
            for field in ("package_ndc", "generic_name", "company_name", "status")
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def run_cleaning() -> tuple[Path, Path]:
    snapshot = _latest_snapshot()
    generated_at = datetime.now().astimezone().isoformat()
    LOGGER.info("Cleaning FDA snapshot %s", snapshot.name)

    shortage_records = _load_records(snapshot, "shortages")
    ndc_records = _load_records(snapshot, "ndc")
    missing_fields = {
        "shortages": _missing_metrics(shortage_records, SOURCE_FIELDS["shortages"]),
        "ndc": _missing_metrics(ndc_records, SOURCE_FIELDS["ndc"]),
    }

    ndc_manufacturers = {record.get("labeler_name") for record in ndc_records if record.get("labeler_name")}
    shortage_manufacturers = {
        record.get("company_name") for record in shortage_records if record.get("company_name")
    }
    generic_names = {record.get("generic_name") for record in ndc_records if record.get("generic_name")}
    brand_names = {record.get("brand_name") for record in ndc_records if record.get("brand_name")}
    ingredients = {
        ingredient.get("name")
        for record in ndc_records
        for ingredient in record.get("active_ingredients") or []
        if isinstance(ingredient, dict) and ingredient.get("name")
    }
    package_values = [
        package.get("package_ndc")
        for record in ndc_records
        for package in record.get("packaging") or []
        if isinstance(package, dict)
    ]

    mapper = NDCNameMapper.from_records(ndc_records)
    del ndc_records

    method_counts: Counter[str] = Counter()
    status_totals: Counter[str] = Counter()
    status_matches: Counter[str] = Counter()
    enriched_records = []
    unmatched_examples = []
    for shortage in shortage_records:
        resolution = mapper.resolve_shortage(shortage)
        method_counts[resolution.method] += 1
        status = str(shortage.get("status") or "Missing")
        status_totals[status] += 1
        if resolution.identity is not None:
            status_matches[status] += 1
        if resolution.identity is None and len(unmatched_examples) < 10:
            unmatched_examples.append(shortage)
        enriched_records.append(
            {
                **shortage,
                **shortage_context_fields(shortage),
                "cleaning": {
                    "normalized_package_ndc": normalize_ndc(shortage.get("package_ndc")),
                    "normalized_manufacturer": normalize_manufacturer(shortage.get("company_name")),
                    "ndc_resolution": asdict(resolution),
                },
            }
        )

    matched = sum(
        count for method, count in method_counts.items() if method != "unmatched" and not method.startswith("ambiguous_")
    )
    total_shortages = len(shortage_records)
    success_rate = 100 * matched / total_shortages if total_shortages else 0.0
    LOGGER.info(
        "Shortage-to-NDC join success: %.2f%% (%s/%s)", success_rate, matched, total_shortages
    )

    for source in ("recalls", "drugsfda"):
        records = _load_records(snapshot, source)
        missing_fields[source] = _missing_metrics(records, SOURCE_FIELDS[source])
        del records

    missing_reason_records = [
        record for record in enriched_records if record["shortage_reason"] is None
    ]
    current_missing_reason_records = [
        record for record in missing_reason_records if record.get("status") == "Current"
    ]
    discontinued_missing_reason_records = [
        record
        for record in missing_reason_records
        if record.get("status") == "To Be Discontinued"
    ]

    metrics = {
        "snapshot": snapshot.name,
        "generated_at": generated_at,
        "shortage_ndc_join": {
            "total": total_shortages,
            "matched": matched,
            "unmatched": total_shortages - matched,
            "success_rate": success_rate,
            "methods": dict(sorted(method_counts.items())),
            "by_status": {
                status: {
                    "total": total,
                    "matched": status_matches[status],
                    "success_rate": 100 * status_matches[status] / total,
                }
                for status, total in sorted(status_totals.items())
            },
            "unmatched_examples": [
                {field: record.get(field) for field in ("package_ndc", "generic_name", "company_name", "status")}
                for record in unmatched_examples
            ],
        },
        "shortage_context": {
            "missing_reason_records": len(missing_reason_records),
            "current_missing_reason_records": len(current_missing_reason_records),
            "operational_context_present": sum(
                record["operational_context"] is not None
                for record in current_missing_reason_records
            ),
            "discontinued_missing_reason_records": len(discontinued_missing_reason_records),
            "discontinuation_context_present": sum(
                record["discontinuation_context"] is not None
                for record in discontinued_missing_reason_records
            ),
        },
        "missing_fields": missing_fields,
        "unique_entities": {
            "ndc_manufacturers_raw": len(ndc_manufacturers),
            "ndc_manufacturers_normalized": len(
                {normalize_manufacturer(value) for value in ndc_manufacturers if normalize_manufacturer(value)}
            ),
            "shortage_manufacturers_raw": len(shortage_manufacturers),
            "shortage_manufacturers_normalized": len(
                {
                    normalize_manufacturer(value)
                    for value in shortage_manufacturers
                    if normalize_manufacturer(value)
                }
            ),
            "generic_names_raw": len(generic_names),
            "generic_names_normalized": len(
                {normalize_drug_name(value) for value in generic_names if normalize_drug_name(value)}
            ),
            "brand_names_raw": len(brand_names),
            "brand_names_normalized": len(
                {normalize_drug_name(value) for value in brand_names if normalize_drug_name(value)}
            ),
            "ingredients_raw": len(ingredients),
            "ingredients_normalized": len(
                {normalize_drug_name(value) for value in ingredients if normalize_drug_name(value)}
            ),
        },
        "ndc_normalization": {
            "unique_package_ndcs": len({normalize_ndc(value) for value in package_values if normalize_ndc(value)}),
            "invalid_ndc_packages": sum(normalize_ndc(value) is None for value in package_values),
            "invalid_shortage_ndcs": sum(
                normalize_ndc(record.get("package_ndc")) is None for record in shortage_records
            ),
        },
    }

    processed_path = REPOSITORY_ROOT / "data" / "processed" / snapshot.name / "shortages_enriched.json"
    _atomic_json(
        processed_path,
        {
            "meta": {
                "snapshot": snapshot.name,
                "generated_at": generated_at,
                "join_success_rate": success_rate,
            },
            "results": enriched_records,
        },
    )
    reports_dir = REPOSITORY_ROOT / "reports"
    _atomic_json(reports_dir / "data_quality.json", metrics)
    report_path = reports_dir / "data_quality.md"
    _atomic_text(report_path, _markdown_report(metrics))
    LOGGER.info("Wrote enriched shortages to %s", processed_path)
    LOGGER.info("Wrote data-quality report to %s", report_path)
    return processed_path, report_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_cleaning()
