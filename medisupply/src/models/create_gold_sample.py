"""Create the deterministic 400-record Phase 9 human-review queue."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from .baseline_classifier import (
        CATEGORY_PRIORITY,
        REPOSITORY_ROOT,
        ClassificationResult,
        _latest_snapshot,
        _load_records,
        classify_record,
    )
    from .gold_dataset import BaselineSuggestion, GoldCandidate, atomic_write_jsonl
    from .schema import PRIMARY_CAUSE_TAXONOMY_VERSION, PrimaryCause
except ImportError:  # Allow: python src/models/create_gold_sample.py
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.models.baseline_classifier import (
        CATEGORY_PRIORITY,
        REPOSITORY_ROOT,
        ClassificationResult,
        _latest_snapshot,
        _load_records,
        classify_record,
    )
    from src.models.gold_dataset import (
        BaselineSuggestion,
        GoldCandidate,
        atomic_write_jsonl,
    )
    from src.models.schema import PRIMARY_CAUSE_TAXONOMY_VERSION, PrimaryCause


SAMPLE_SEED = 202609
TARGETS: dict[PrimaryCause, int] = {
    "active_ingredient_shortage": 20,
    "inactive_ingredient_shortage": 4,
    "manufacturing_quality_problem": 70,
    "manufacturing_capacity": 15,
    "regulatory_delay": 3,
    "shipping_delay": 25,
    "demand_increase": 25,
    "product_discontinuation": 25,
    "labeling_packaging_error": 50,
    "regulatory_noncompliance": 45,
    "adverse_event_signal": 40,
    "recall": 70,
    "unknown": 8,
}
assert sum(TARGETS.values()) == 400

CAPACITY_CONTEXT = re.compile(
    r"production|manufactur|facility|plant|equipment|supply", re.IGNORECASE
)
CAPACITY_PRESSURE = re.compile(
    r"capacity|unable|cannot|could not|delay|shortage|interruption|shutdown|closed|closure|out of business|production issue|equipment failure",
    re.IGNORECASE,
)


def _stable_hash(parts: list[Any], length: int = 16) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:length]


def _source_id(source: str, record: dict[str, Any]) -> str:
    if source == "recall":
        return str(
            record.get("recall_number")
            or f"event-{record.get('event_id', 'missing')}-{_stable_hash([record.get('product_description'), record.get('recall_initiation_date')], 10)}"
        )
    suffix = _stable_hash(
        [
            record.get("package_ndc"),
            record.get("initial_posting_date"),
            record.get("presentation"),
            record.get("company_name"),
        ],
        10,
    )
    return f"{record.get('package_ndc', 'missing')}:{suffix}"


def _context(source: str, record: dict[str, Any]) -> dict[str, Any]:
    if source == "recall":
        fields = (
            "recall_number",
            "event_id",
            "classification",
            "status",
            "recalling_firm",
            "product_description",
            "recall_initiation_date",
        )
    else:
        fields = (
            "package_ndc",
            "generic_name",
            "company_name",
            "status",
            "availability",
            "related_info",
            "initial_posting_date",
        )
    return {field: record.get(field) for field in fields}


def _raw_text(source: str, record: dict[str, Any]) -> str | None:
    field = "reason_for_recall" if source == "recall" else "shortage_reason"
    value = record.get(field)
    return str(value).strip() if value is not None and str(value).strip() else None


def _pool_item(
    source: str, record: dict[str, Any], result: ClassificationResult
) -> dict[str, Any]:
    source_id = _source_id(source, record)
    return {
        "source": source,
        "source_record_id": source_id,
        "candidate_id": f"{source}:{source_id}",
        "raw_text": _raw_text(source, record),
        "record": record,
        "result": result,
    }


def _diverse_order(items: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    shuffled = list(items)
    random.Random(seed).shuffle(shuffled)
    first_by_text = []
    repeated = []
    seen_text = set()
    for item in shuffled:
        normalized = " ".join((item["raw_text"] or "[missing]").lower().split())
        if normalized in seen_text:
            repeated.append(item)
        else:
            seen_text.add(normalized)
            first_by_text.append(item)
    return first_by_text + repeated


def _pick_stratum(
    pool: list[dict[str, Any]],
    count: int,
    *,
    seed: int,
    selected_ids: set[str],
) -> list[dict[str, Any]]:
    available = [item for item in pool if item["candidate_id"] not in selected_ids]
    edge = [
        item
        for item in available
        if item["result"].collision or item["result"].confidence != "high"
    ]
    regular = [item for item in available if item not in edge]
    edge_target = min(len(edge), round(count * 0.40))
    chosen = _diverse_order(edge, seed)[:edge_target]
    chosen_ids = {item["candidate_id"] for item in chosen}
    remainder = [item for item in regular if item["candidate_id"] not in chosen_ids]
    remainder.extend(
        item
        for item in edge
        if item["candidate_id"] not in chosen_ids
    )
    chosen.extend(_diverse_order(remainder, seed + 1)[: count - len(chosen)])
    if len(chosen) != count:
        raise ValueError(f"Requested {count} records but only found {len(chosen)}")
    selected_ids.update(item["candidate_id"] for item in chosen)
    return chosen


def _selection_reason(stratum: PrimaryCause, item: dict[str, Any]) -> str:
    if stratum == "manufacturing_capacity":
        return "targeted_capacity_boundary_review"
    if stratum == "unknown":
        return (
            "unknown_fda_other"
            if item["raw_text"] and item["raw_text"].lower() == "other"
            else "unknown_missing_shortage_reason"
        )
    result: ClassificationResult = item["result"]
    if result.collision:
        return "baseline_collision_boundary"
    if result.confidence != "high":
        return "baseline_low_confidence"
    return "baseline_confident"


def create_candidates(snapshot: Path, *, seed: int = SAMPLE_SEED) -> list[GoldCandidate]:
    shortages = _load_records(snapshot, "shortages")
    recalls = _load_records(snapshot, "recalls")
    reason_shortages = [
        row
        for row in shortages
        if str(row.get("shortage_reason") or "").strip()
        and str(row.get("shortage_reason")).strip().lower() != "other"
    ]
    unknown_shortages = [
        row
        for row in shortages
        if not str(row.get("shortage_reason") or "").strip()
        or str(row.get("shortage_reason")).strip().lower() == "other"
    ]

    regular_items = [
        _pool_item("shortage", row, classify_record(row, source="shortage"))
        for row in reason_shortages
    ] + [
        _pool_item("recall", row, classify_record(row, source="recall"))
        for row in recalls
    ]
    unknown_items = [
        _pool_item("shortage", row, classify_record(row, source="shortage"))
        for row in unknown_shortages
    ]

    unique_items: dict[str, dict[str, Any]] = {}
    for item in regular_items + unknown_items:
        existing = unique_items.get(item["candidate_id"])
        if existing is not None and existing["record"] != item["record"]:
            raise ValueError(f"Conflicting source record ID: {item['candidate_id']}")
        unique_items.setdefault(item["candidate_id"], item)
    regular_ids = {item["candidate_id"] for item in regular_items}
    unknown_ids = {item["candidate_id"] for item in unknown_items}
    regular_items = [unique_items[item_id] for item_id in sorted(regular_ids)]
    unknown_items = [unique_items[item_id] for item_id in sorted(unknown_ids)]

    predicted: dict[PrimaryCause, list[dict[str, Any]]] = {
        category: [] for category in CATEGORY_PRIORITY
    }
    for item in regular_items:
        predicted[item["result"].event.primary_cause].append(item)

    capacity_pool = [
        item
        for item in regular_items
        if item["result"].event.primary_cause == "manufacturing_capacity"
        or (
            item["source"] == "recall"
            and CAPACITY_CONTEXT.search(item["raw_text"] or "")
            and CAPACITY_PRESSURE.search(item["raw_text"] or "")
        )
    ]

    selected_ids: set[str] = set()
    selected: list[tuple[PrimaryCause, dict[str, Any]]] = []

    capacity_items = _pick_stratum(
        capacity_pool,
        TARGETS["manufacturing_capacity"],
        seed=seed + 101,
        selected_ids=selected_ids,
    )
    selected.extend(("manufacturing_capacity", item) for item in capacity_items)

    other_pool = [
        item
        for item in unknown_items
        if (item["raw_text"] or "").lower() == "other"
    ]
    missing_pool = [item for item in unknown_items if item["raw_text"] is None]
    unknown_chosen = _pick_stratum(
        other_pool, 4, seed=seed + 201, selected_ids=selected_ids
    ) + _pick_stratum(
        missing_pool, 4, seed=seed + 202, selected_ids=selected_ids
    )
    selected.extend(("unknown", item) for item in unknown_chosen)

    for index, category in enumerate(CATEGORY_PRIORITY):
        if category in {"manufacturing_capacity", "unknown"}:
            continue
        chosen = _pick_stratum(
            predicted[category],
            TARGETS[category],
            seed=seed + 1000 + index,
            selected_ids=selected_ids,
        )
        selected.extend((category, item) for item in chosen)

    if len(selected) != 400 or len(selected_ids) != 400:
        raise AssertionError("Sampling did not produce 400 unique source records")
    random.Random(seed + 9999).shuffle(selected)

    candidates = []
    for sequence, (stratum, item) in enumerate(selected, 1):
        result: ClassificationResult = item["result"]
        candidates.append(
            GoldCandidate(
                candidate_id=item["candidate_id"],
                sequence=sequence,
                taxonomy_version=PRIMARY_CAUSE_TAXONOMY_VERSION,
                snapshot=snapshot.name,
                sampling_stratum=stratum,
                selection_reason=_selection_reason(stratum, item),
                source=item["source"],
                source_record_id=item["source_record_id"],
                text_field=(
                    "reason_for_recall"
                    if item["source"] == "recall"
                    else "shortage_reason"
                ),
                raw_text=item["raw_text"],
                source_context=_context(item["source"], item["record"]),
                baseline=BaselineSuggestion(
                    primary_cause=result.event.primary_cause,
                    confidence=result.confidence,
                    confidence_score=result.confidence_score,
                    confident_keyword_match=result.confident_keyword_match,
                    fallback=result.fallback,
                    collision=result.collision,
                    matched_categories=list(result.matched_categories),
                    matched_rules={
                        category: list(rules)
                        for category, rules in result.matched_rules.items()
                    },
                    event=result.event,
                ),
            )
        )
    return candidates


def render_sampling_report(candidates: list[GoldCandidate]) -> str:
    strata = Counter(candidate.sampling_stratum for candidate in candidates)
    predictions = Counter(candidate.baseline.primary_cause for candidate in candidates)
    sources = Counter(candidate.source for candidate in candidates)
    edge_cases = sum(
        candidate.baseline.collision or candidate.baseline.confidence != "high"
        for candidate in candidates
    )
    lines = [
        "# Phase 9 Gold Sampling Plan",
        "",
        f"Taxonomy: `{PRIMARY_CAUSE_TAXONOMY_VERSION}`",
        "",
        f"Candidates: **{len(candidates):,}**",
        "",
        f"Sources: {sources['shortage']:,} shortages and {sources['recall']:,} recalls.",
        "",
        f"Low-confidence or collision-boundary candidates: {edge_cases:,}.",
        "",
        "These are review candidates, not gold labels. `sampling_stratum` records why a record was selected; only the interactive human decision becomes `event.primary_cause` in `labeled.jsonl`.",
        "",
        "## Sampling strata",
        "",
        "| Target stratum | Candidates | Baseline predicts same category |",
        "|---|---:|---:|",
    ]
    for category in TARGETS:
        same = sum(
            candidate.sampling_stratum == category
            and candidate.baseline.primary_cause == category
            for candidate in candidates
        )
        lines.append(f"| `{category}` | {strata[category]:,} | {same:,} |")

    lines.extend(
        [
            "",
            "## Important source constraints",
            "",
            "- FDA exposes only eight distinct populated shortage-reason phrases in this snapshot. Several shortage strata therefore contain different real records with identical reason text.",
            "- Only three direct `regulatory_delay` records and four direct `inactive_ingredient_shortage` records exist; all are included.",
            "- Only one allowed reason field directly matches `manufacturing_capacity`. The 15-record capacity stratum includes that record plus targeted production/facility boundary cases for human review. It does not pre-assign those cases to capacity.",
            "- The eight `unknown` controls are the explicit exception to the non-null/non-`Other` shortage rule: four FDA `Other` records and four records with a missing shortage reason.",
            "- Splits are created only after all 400 records have independent human labels.",
            "",
            "## Baseline suggestions in the queue",
            "",
            "| Baseline prediction | Candidates |",
            "|---|---:|",
        ]
    )
    for category, count in predictions.most_common():
        lines.append(f"| `{category}` | {count:,} |")
    lines.append("")
    return "\n".join(lines)


def main() -> Path:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, help="Defaults to newest snapshot")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "data" / "gold" / "candidates.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPOSITORY_ROOT / "reports" / "gold_sampling_plan.md",
    )
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    args = parser.parse_args()

    snapshot = args.snapshot or _latest_snapshot()
    candidates = create_candidates(snapshot, seed=args.seed)
    atomic_write_jsonl(args.output, candidates)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_sampling_report(candidates), encoding="utf-8")
    print(f"Created {len(candidates):,} pending human-review candidates")
    print(f"Queue: {args.output}")
    print(f"Sampling report: {args.report}")
    return args.output


if __name__ == "__main__":
    main()
