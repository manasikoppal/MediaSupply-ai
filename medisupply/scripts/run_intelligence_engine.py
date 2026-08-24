#!/usr/bin/env python3
"""Generate the Phase 13 validation and highest-risk shortage report."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.intelligence.engine import IntelligenceEngine


def _compact(score: dict[str, Any]) -> dict[str, Any]:
    components = score["components"]
    return {
        "shortage_id": score["shortage_id"],
        "ndc": score["ndc"],
        "package_ndc": score["shortage"]["package_ndc"],
        "generic_name": score["shortage"]["generic_name"],
        "manufacturer": score["shortage"]["manufacturer"],
        "score": score["score"],
        "risk_tier": score["risk_tier"],
        "duration_days": score["shortage"]["duration_days"],
        "initial_posting_date": score["shortage"]["initial_posting_date"],
        "available_manufacturers": components["manufacturer_concentration"]["observed"],
        "recall_overlap": components["recall_overlap"]["observed"],
        "recall_linkage_confidence": components["recall_overlap"]["linkage_confidence"]["level"],
        "available_equivalents": components["alternative_availability"]["observed"],
        "primary_cause": components["manufacturing_root_cause"]["observed"],
        "teacher_label_available": components["manufacturing_root_cause"]["teacher_label_available"],
        "label_method": components["manufacturing_root_cause"]["label_method"],
        "reserved_for_evaluation": components["manufacturing_root_cause"][
            "reserved_for_evaluation"
        ],
        "unknown_reason": components["manufacturing_root_cause"]["unknown_reason"],
        "component_points": {
            name: component["points"] for name, component in components.items()
        },
    }


def _markdown(payload: dict[str, Any]) -> str:
    validation = payload["lisdexamfetamine_validation"]
    vcomponents = validation["components"]
    top = payload["top_current_shortages"]
    validation_cause_note = (
        "reserved for human evaluation; teacher label intentionally excluded"
        if vcomponents["manufacturing_root_cause"]["reserved_for_evaluation"]
        else (
            "teacher label"
            if vcomponents["manufacturing_root_cause"]["teacher_label_available"]
            else "teacher label unavailable; uncertainty prior"
        )
    )
    lines = [
        "# Phase 13 Intelligence Engine Report",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        f"Graph snapshot: `{payload['snapshot']}` (risk observation date `{payload['as_of']}`)",
        "",
        "## Scope and data confidence",
        "",
        "This is a deterministic calculation layer. It makes no AI/API calls. The Phase 10 teacher output is read only as an already-structured root-cause input; raw FDA data, the knowledge graph, and labels are unchanged.",
        "",
        f"- Current FDA shortage records: **{payload['coverage']['current_shortages']:,}**.",
        f"- Current shortages linked to an NDC product and scored: **{payload['coverage']['scored_current_shortages']:,}** ({payload['coverage']['current_shortage_score_coverage_pct']:.2f}%).",
        f"- Current shortages excluded because Phase 6 had no product link: **{payload['coverage']['unlinked_current_shortages']:,}**.",
        f"- Linked current shortages not scored because a product lacked an active-ingredient edge: **{len(payload['coverage']['scoring_failures']):,}**.",
        f"- Scored shortages with an available structured cause state: **{payload['coverage']['teacher_label_available']:,}** ({payload['coverage']['teacher_label_coverage_pct']:.2f}%). Missing labels use the documented `unknown` uncertainty treatment.",
        f"- Cause methods among scored records: **{payload['coverage']['teacher_label_methods'].get('claude', 0):,}** Claude labels, **{payload['coverage']['teacher_label_methods'].get('policy_unknown', 0):,}** saved deterministic unknown-policy labels, and **{payload['coverage']['teacher_label_methods'].get('deterministic_fda_no_reason', 0):,}** newly derived FDA-no-reason labels.",
        f"- Current scored shortages reserved for held-out human evaluation: **{payload['coverage']['evaluation_reserved_current_shortages']:,}**. Their labels remain hidden from scoring and display as `unknown (reserved for evaluation)`.",
        f"- Deterministic FDA-no-reason shortages: **{payload['coverage']['unknown_reason_distribution'].get('fda_reason_not_provided', 0):,}**; new shortages awaiting optional teacher review: **{payload['coverage']['unknown_reason_distribution'].get('needs_teacher_labeling', 0):,}**. Neither path makes an AI/API call.",
        f"- Recall-to-product linkage is **{payload['recall_linkage']['overall_recall_linkage_pct']:.2f}%** overall and **{payload['recall_linkage']['identifier_present_linkage_pct']:.2f}%** when FDA supplies harmonized identifiers.",
        "",
        "A positive recall overlap backed by an explicit/harmonized NDC is labeled high-confidence. A negative result is labeled limited-confidence because older unlinked recalls can hide a real overlap.",
        "",
        "## Explainable 0–100 weighting",
        "",
        "| Component | Max | Rule |",
        "|---|---:|---|",
        "| Few available manufacturers | 20 | 0 → 20; 1 → 18; 2 → 15; 3–4 → 10; 5–9 → 4; 10+ → 0 |",
        "| Current shortage duration | 30 | <30d → 3; 30–89d → 8; 90–179d → 15; 180–364d → 22; 365+d → 30 |",
        "| Same-ingredient recall overlap | 30 | 30 when a different manufacturer's product with the same active-ingredient set has an Ongoing linked recall; otherwise 0 |",
        "| Available strict proxy equivalents | 10 | 0 → 10; 1 → 7; 2–3 → 3; 4+ → 0 |",
        "| Manufacturing-related root cause | 10 | Teacher cause in active/inactive ingredient shortage, manufacturing quality, or capacity → 10; known non-manufacturing → 0; unknown/unlabeled → 3 |",
        "",
        "Risk tiers are high (65–100), elevated (45–64), moderate (25–44), and low (0–24). For combination products, concentration uses the least-diversified active ingredient. “Available” means a finished NDC product with neither a linked Current shortage nor a linked Ongoing recall. Proxy equivalence is the Phase 6 same-ingredient-set + strength + dosage-form + route grouping; it is not an FDA Orange Book therapeutic-equivalence rating.",
        "",
        "## Required validation: lisdexamfetamine `00054-0370`",
        "",
        f"Result: **{validation['score']}/100 ({validation['risk_tier']})**.",
        "",
        "| Component | Observation | Points |",
        "|---|---|---:|",
        f"| Available manufacturers | {vcomponents['manufacturer_concentration']['observed']} | {vcomponents['manufacturer_concentration']['points']}/20 |",
        f"| Ongoing duration | {vcomponents['shortage_duration']['observed_days']:,} days | {vcomponents['shortage_duration']['points']}/30 |",
        f"| Recall overlap | {vcomponents['recall_overlap']['observed']!s} ({vcomponents['recall_overlap']['linkage_confidence']['level']} linkage confidence) | {vcomponents['recall_overlap']['points']}/30 |",
        f"| Available strict proxy equivalents | {vcomponents['alternative_availability']['observed']} of {validation['alternative_availability']['candidate_equivalent_count']} candidates | {vcomponents['alternative_availability']['points']}/10 |",
        f"| Root cause | `{vcomponents['manufacturing_root_cause']['observed']}` ({validation_cause_note}) | {vcomponents['manufacturing_root_cause']['points']}/10 |",
        "",
        f"The overlap traversal found **{validation['recall_overlap']['overlapping_product_count']}** same-ingredient products from another manufacturer under **{validation['recall_overlap']['overlapping_recall_count']}** active linked recall(s). **{len({item['product_id'] for item in validation['recall_overlap']['overlapping_products'] if item['also_in_shortage']})}** of those recalled products are also in Current shortage. Among the **{validation['alternative_availability']['candidate_equivalent_count']}** strict 20 mg capsule proxy alternatives, **{validation['alternative_availability']['unavailable_alternative_count']}** are unavailable and **{validation['alternative_availability']['available_alternative_count']}** remain available; this reproduces the Phase 6 “most alternatives also in shortage” high-risk pattern without overstating it.",
        "",
        "The human gold set contains this record, so it was intentionally excluded from Phase 10 corpus labels. Per the requested input policy, Phase 13 does not leak that human label into the teacher layer: the root cause is treated as unknown, yet the case still scores high from the observable supply constraints.",
        "",
        "## Highest-risk current shortages",
        "",
        "Ranked among graph-linked current shortage records. For a more useful sanity-check list, rows with the same generic name, manufacturer, and initial posting date are shown once; the Packages column reports how many package-level FDA records the row represents. The machine-readable output retains every package record.",
        "",
        "| Rank | Score | Drug / example package NDC | Manufacturer | Packages | Days | Mfrs | Recall | Available TE | Cause |",
        "|---:|---:|---|---|---:|---:|---:|---|---:|---|",
    ]
    for rank, item in enumerate(top, start=1):
        name = str(item["generic_name"] or "[missing]").replace("|", "/")
        manufacturer = str(item["manufacturer"] or "[missing]").replace("|", "/")
        recall = (
            f"Yes ({item['recall_linkage_confidence']})"
            if item["recall_overlap"]
            else f"No ({item['recall_linkage_confidence']})"
        )
        cause = item["primary_cause"]
        if item["primary_cause"] == "unknown":
            if item.get("unknown_reason") == "reserved_for_evaluation":
                cause = "unknown (reserved for evaluation)"
            elif item.get("unknown_reason") == "fda_reason_not_provided":
                cause = "unknown (no reason provided by FDA)"
        lines.append(
            f"| {rank} | **{item['score']}** | {name} / `{item['package_ndc']}` | {manufacturer} | "
            f"{item['represented_package_records']} | {item['duration_days']:,} | {item['available_manufacturers']} | {recall} | "
            f"{item['available_equivalents']} | `{cause}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation limits",
            "",
            "- Scores describe the stated snapshot and do not forecast probability or patient harm.",
            "- A linked Current shortage is conservatively treated as making that NDC product unavailable; FDA's finer availability text is not standardized enough for inventory arithmetic.",
        "- Recall overlap requires the same active-ingredient set (strength may differ) so combination products do not match on only one component; available alternatives use the stricter strength/form/route proxy group.",
            "- Unlinked recalls can create false-negative overlap results, so negative overlap confidence is explicitly limited.",
            "- `unknown` adds 3 uncertainty points. It is not interpreted as a manufacturing cause and does not receive the full 10 points.",
            "",
            "## Reproduction",
            "",
            "```bash",
            "/opt/anaconda3/envs/medisupply/bin/python scripts/run_intelligence_engine.py --top 15",
            "```",
            "",
            "Machine-readable output: `reports/intelligence_engine.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(database: Path | None, top_n: int) -> dict[str, Any]:
    with IntelligenceEngine(database) as engine:
        current_total = int(
            engine.connection.execute(
                "SELECT COUNT(*) FROM shortages WHERE status = 'Current'"
            ).fetchone()[0]
        )
        linked_ids = [
            int(row[0])
            for row in engine.connection.execute(
                """
                SELECT DISTINCT s.shortage_id
                FROM shortages s
                JOIN product_shortages ps ON ps.shortage_id = s.shortage_id
                WHERE s.status = 'Current' ORDER BY s.shortage_id
                """
            )
        ]
        scores = []
        failures = []
        for shortage_id in linked_ids:
            try:
                scores.append(engine.supply_fragility_score(shortage_id))
            except (LookupError, ValueError) as exc:
                failures.append({"shortage_id": shortage_id, "error": str(exc)})
        scores.sort(
            key=lambda item: (
                -int(item["score"]),
                -int(bool(item["recall_overlap"]["recall_overlap"])),
                -(item["shortage"]["duration_days"] or -1),
                int(item["shortage_id"]),
            )
        )
        compact = [_compact(item) for item in scores]
        top_by_incident: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in compact:
            key = (
                str(item["generic_name"] or "").casefold(),
                str(item["manufacturer"] or "").casefold(),
                str(item["initial_posting_date"] or ""),
            )
            if key not in top_by_incident:
                top_by_incident[key] = {
                    **item,
                    "represented_package_records": 1,
                    "evaluation_reserved_records": int(
                        bool(item["reserved_for_evaluation"])
                    ),
                }
            else:
                top_by_incident[key]["represented_package_records"] += 1
                top_by_incident[key]["evaluation_reserved_records"] += int(
                    bool(item["reserved_for_evaluation"])
                )
        teacher_available = sum(
            bool(item["root_cause"]["available"]) for item in scores
        )
        teacher_methods = Counter(
            str(item["root_cause"]["label_method"])
            for item in scores
            if item["root_cause"]["available"]
        )
        cause_distribution = Counter(
            str(item["root_cause"]["primary_cause"]) for item in scores
        )
        evaluation_reserved = sum(
            bool(item["root_cause"]["reserved_for_evaluation"])
            for item in scores
        )
        unknown_reasons = Counter(
            str(item["root_cause"]["unknown_reason"])
            for item in scores
            if item["root_cause"]["primary_cause"] == "unknown"
        )
        tiers = Counter(str(item["risk_tier"]) for item in scores)
        validation = engine.supply_fragility_score("00054-0370")
        if validation["risk_tier"] != "high":
            raise AssertionError(
                "Required lisdexamfetamine case did not validate as high risk: "
                f"{validation['score']}"
            )
        recall_linkage = validation["recall_overlap"]["linkage_confidence"]
        return {
            "generated_at": datetime.now().astimezone().isoformat(),
            "snapshot": engine.snapshot,
            "as_of": engine.as_of.isoformat(),
            "database": str(engine.database.relative_to(REPOSITORY_ROOT)),
            "coverage": {
                "current_shortages": current_total,
                "linked_current_shortages": len(linked_ids),
                "scored_current_shortages": len(scores),
                "unlinked_current_shortages": current_total - len(linked_ids),
                "scoring_failures": failures,
                "current_shortage_score_coverage_pct": 100 * len(scores) / current_total,
                "teacher_label_available": teacher_available,
                "teacher_label_coverage_pct": 100 * teacher_available / len(scores) if scores else 0.0,
                "teacher_label_methods": dict(sorted(teacher_methods.items())),
                "root_cause_distribution": dict(sorted(cause_distribution.items())),
                "evaluation_reserved_current_shortages": evaluation_reserved,
                "unknown_reason_distribution": dict(sorted(unknown_reasons.items())),
                "risk_tier_distribution": dict(sorted(tiers.items())),
            },
            "recall_linkage": {
                key: recall_linkage[key]
                for key in (
                    "overall_recall_linkage_pct",
                    "identifier_present_linkage_pct",
                    "linked_recall_records",
                    "total_recall_records",
                )
            },
            "weighting": {
                name: component["max_points"]
                for name, component in validation["components"].items()
            },
            "lisdexamfetamine_validation": validation,
            "top_current_shortages": list(top_by_incident.values())[:top_n],
            "all_scored_current_shortages": compact,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--top", type=int, default=15, choices=range(10, 21))
    parser.add_argument(
        "--json-output",
        type=Path,
        default=REPOSITORY_ROOT / "reports" / "intelligence_engine.json",
    )
    parser.add_argument(
        "--report-output",
        type=Path,
        default=REPOSITORY_ROOT / "reports" / "intelligence_engine.md",
    )
    args = parser.parse_args()
    payload = build_report(args.database, args.top)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(payload, indent=2) + "\n")
    args.report_output.write_text(_markdown(payload))
    validation = payload["lisdexamfetamine_validation"]
    print(
        f"Scored {payload['coverage']['scored_current_shortages']:,} current shortages; "
        f"lisdexamfetamine={validation['score']}/100 ({validation['risk_tier']}); "
        f"wrote {args.report_output}"
    )


if __name__ == "__main__":
    main()
