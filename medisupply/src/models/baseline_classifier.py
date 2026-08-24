"""Deliberately simple keyword baseline for FDA disruption reason text.

The classifier applies independent regular-expression rules, records every
category that matched, and resolves collisions with a fixed priority order.
It is intended as an interpretable baseline, not as gold-label generation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Literal, Pattern

try:
    from .event_examples import HAND_MAPPED_EXAMPLES
    from .schema import DisruptionEvent, PRIMARY_CAUSE_TAXONOMY_VERSION, PrimaryCause
except ImportError:  # Allow: python src/models/baseline_classifier.py
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.models.event_examples import HAND_MAPPED_EXAMPLES
    from src.models.schema import (
        DisruptionEvent,
        PRIMARY_CAUSE_TAXONOMY_VERSION,
        PrimaryCause,
    )


Source = Literal["shortage", "recall"]
Confidence = Literal["low", "medium", "high"]
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}$")
STRONG_RULE_WEIGHT = 1.0


@dataclass(frozen=True)
class KeywordRule:
    category: PrimaryCause
    name: str
    pattern: Pattern[str]
    weight: float = STRONG_RULE_WEIGHT


def _rule(
    category: PrimaryCause, name: str, pattern: str, weight: float = STRONG_RULE_WEIGHT
) -> KeywordRule:
    return KeywordRule(category, name, re.compile(pattern, re.IGNORECASE), weight)


# Narrow, explicit phrases are intentionally preferred over broad language.
# Weak rules can select a category but are reported as low confidence unless a
# strong rule also matched that category.
RULES: tuple[KeywordRule, ...] = (
    _rule(
        "inactive_ingredient_shortage",
        "inactive_ingredient_shortage",
        r"shortage of (?:an )?inactive ingredient(?: component)?|inactive ingredient shortage|excipient shortage",
    ),
    _rule(
        "active_ingredient_shortage",
        "active_ingredient_shortage",
        r"shortage of (?:an )?active (?:pharmaceutical )?ingredient|active (?:pharmaceutical )?ingredient shortage|\bapi shortage\b",
    ),
    _rule(
        "manufacturing_quality_problem",
        "sterility",
        r"steril(?:ity|e|ization)|aseptic",
    ),
    _rule(
        "manufacturing_quality_problem",
        "contamination",
        r"contaminat|cross[- ]contamin|microbial|bacteri|fungal|mold|endotoxin",
    ),
    _rule(
        "manufacturing_quality_problem",
        "gmp_deviation",
        r"\b(?:c?gmp|good manufacturing practice)s?\b|manufacturing quality problem",
    ),
    _rule(
        "manufacturing_quality_problem",
        "failed_quality_specification",
        r"(?:fail(?:ed|ure)?|out[- ]of[- ]specification|\boos\b).{0,45}(?:specification|assay|dissolution|potency|stability)|(?:specification|assay|dissolution|potency|stability).{0,45}(?:fail(?:ed|ure)?|out[- ]of[- ]specification|\boos\b)",
    ),
    _rule(
        "manufacturing_quality_problem",
        "out_of_specification_or_potency",
        r"out[- ]of[- ]specification|\boos\b|\b(?:subpoten|superpoten)",
    ),
    _rule(
        "manufacturing_quality_problem",
        "unsupported_stability_or_expiry",
        r"stability data (?:does|do|did) not support|(?:lack|insufficient|absence) of stability data|expir(?:y|ation).{0,35}not supported",
    ),
    _rule(
        "manufacturing_quality_problem",
        "product_quality_attribute",
        r"impurit|degrad|particul|foreign (?:matter|material|substance|tablet|capsule)|discolor|crystalli|precipitat",
    ),
    _rule(
        "manufacturing_quality_problem",
        "process_or_container_control",
        r"processing controls?|quality controls?|container closure|defective container|fill volume|leak(?:ing|age)?",
    ),
    _rule(
        "manufacturing_quality_problem",
        "quality_language",
        r"\b(?:potency|dissolution|stability|specification)s?\b",
        0.55,
    ),
    _rule(
        "manufacturing_capacity",
        "manufacturing_delay",
        r"manufacturing delays?|production delays?|delay in (?:manufacturing|production)",
    ),
    _rule(
        "manufacturing_capacity",
        "capacity_constraint",
        r"manufacturing capacity|production capacity|capacity constraint|unable to meet (?:production|supply)|supply interruption",
    ),
    _rule(
        "manufacturing_capacity",
        "equipment_or_facility_shutdown",
        r"equipment (?:failure|breakdown)|(?:plant|facility|production line) (?:shutdown|closure)",
    ),
    _rule(
        "regulatory_delay",
        "regulatory_delay",
        r"regulatory delay|approval delay|awaiting (?:fda|regulatory) approval|pending regulatory (?:review|approval)",
    ),
    _rule(
        "shipping_delay",
        "shipping_or_transit_delay",
        r"delay(?:ed)? (?:in )?shipping|shipping delay|distribution delay|transportation delay|transit delay|freight delay|logistics delay",
    ),
    _rule(
        "demand_increase",
        "demand_increase",
        r"demand increase|increase(?:d)? in demand|increased demand|demand (?:exceeds|exceeded)|unexpected demand|surge in demand",
    ),
    _rule(
        "product_discontinuation",
        "product_discontinuation",
        r"discontinuation of (?:the )?manufacture|product discontinuation|product (?:is |was |has been )?discontinued|discontinued (?:the )?(?:drug|product|manufactur|production)|ceas(?:e|ed|ing) (?:manufactur|production)|no longer manufactur",
    ),
    _rule(
        "labeling_packaging_error",
        "labeling_error",
        r"\blabeling\b|\blabel(?:ed|ing)? (?:error|mix[- ]?up|incorrect|missing|omits?|states?|lists?)|incorrect or missing (?:lot|exp(?:iration)?|package insert)",
    ),
    _rule(
        "labeling_packaging_error",
        "mispackaging_or_wrong_product",
        r"mispack|\bmislabel(?:ed|ing)?\b|wrong product|incorrect product|unit dose mix[- ]?up|product mix[- ]?up",
    ),
    _rule(
        "regulatory_noncompliance",
        "unapproved_or_misbranded",
        r"unapproved (?:new )?(?:drug|product)|misbrand|(?:marketed|marked) without (?:an? )?approved (?:nda|anda|nda/anda)|without (?:fda|regulatory) approval",
    ),
    _rule(
        "regulatory_noncompliance",
        "unregistered_manufacturer",
        r"not registered with (?:the )?fda|unregistered (?:drug )?manufacturer",
    ),
    _rule(
        "adverse_event_signal",
        "adverse_event_or_reaction",
        r"adverse (?:event|reaction|drug event|drug reaction)s?",
    ),
)


# Used only when scores tie. More specific causal mechanisms precede broader
# manufacturing categories.
CATEGORY_PRIORITY: tuple[PrimaryCause, ...] = (
    "inactive_ingredient_shortage",
    "active_ingredient_shortage",
    "regulatory_delay",
    "regulatory_noncompliance",
    "labeling_packaging_error",
    "shipping_delay",
    "demand_increase",
    "product_discontinuation",
    "manufacturing_capacity",
    "manufacturing_quality_problem",
    "adverse_event_signal",
    "recall",
    "unknown",
)
PRIORITY_INDEX = {category: index for index, category in enumerate(CATEGORY_PRIORITY)}

STAGES: dict[PrimaryCause, str] = {
    "active_ingredient_shortage": "raw_material_sourcing",
    "inactive_ingredient_shortage": "raw_material_sourcing",
    "manufacturing_quality_problem": "manufacturing",
    "manufacturing_capacity": "manufacturing",
    "regulatory_delay": "regulatory_review",
    "shipping_delay": "distribution",
    "demand_increase": "demand_planning",
    "product_discontinuation": "product_lifecycle",
    "labeling_packaging_error": "packaging_labeling",
    "regulatory_noncompliance": "regulatory_compliance",
    "adverse_event_signal": "post_market_surveillance",
    "recall": "post_market_action",
    "unknown": "unknown",
}

SHORTAGE_REFERENCE_LABELS: dict[str, PrimaryCause] = {
    "Shortage of an active ingredient": "active_ingredient_shortage",
    "Shortage of an inactive ingredient component": "inactive_ingredient_shortage",
    "Requirements related to complying with good manufacturing practices": "manufacturing_quality_problem",
    "Demand increase for the drug": "demand_increase",
    "Delay in shipping of the drug": "shipping_delay",
    "Regulatory delay": "regulatory_delay",
    "Discontinuation of the manufacture of the drug": "product_discontinuation",
    "Other": "unknown",
}

TAXONOMY_GAP_PATTERNS: dict[str, Pattern[str]] = {
    "storage_temperature_excursion": re.compile(
        r"temperature excursion|storage temperature|temperature abuse|improper storage",
        re.IGNORECASE,
    ),
    "counterfeit_or_tampering": re.compile(r"counterfeit|tamper", re.IGNORECASE),
}

RESOLVED_DISCONTINUATION_COLLISION_TEXTS: tuple[dict[str, str], ...] = (
    {
        "text": "CGMP Deviations: Firm went out of business and could no longer continue stability studies.",
        "decision": "manufacturing_quality_problem",
        "rationale": "Business closure is context; the stated recall problem is loss of required stability oversight.",
    },
    {
        "text": "CGMP Deviations; the firm discontinued required stability testing for products on the market still within expiry",
        "decision": "manufacturing_quality_problem",
        "rationale": "The firm discontinued testing, not the drug product or its manufacture.",
    },
)


@dataclass
class ClassificationResult:
    event: DisruptionEvent
    confidence: Confidence
    confidence_score: float
    confident_keyword_match: bool
    fallback: bool
    matched_categories: tuple[PrimaryCause, ...]
    matched_rules: dict[PrimaryCause, tuple[str, ...]]
    category_scores: dict[PrimaryCause, float]

    @property
    def collision(self) -> bool:
        return len(self.matched_categories) > 1


def _severity(
    source: Source,
    *,
    classification: str | None,
    availability: str | None,
    status: str | None,
) -> Literal["low", "medium", "high"]:
    if source == "recall":
        return {"class i": "high", "class ii": "medium", "class iii": "low"}.get(
            (classification or "").strip().lower(), "medium"
        )
    availability_text = (availability or "").strip().lower()
    if availability_text == "unavailable":
        return "high"
    if availability_text == "limited availability":
        return "medium"
    if (status or "").strip().lower() == "resolved":
        return "low"
    return "medium"


def classify_text(
    text: str | None,
    *,
    source: Source,
    classification: str | None = None,
    availability: str | None = None,
    status: str | None = None,
) -> ClassificationResult:
    """Classify one FDA reason string and return the event plus rule diagnostics."""
    normalized_text = (text or "").strip()
    matched: dict[PrimaryCause, list[KeywordRule]] = defaultdict(list)
    for rule in RULES:
        if normalized_text and rule.pattern.search(normalized_text):
            matched[rule.category].append(rule)

    category_scores: dict[PrimaryCause, float] = {
        category: round(sum(rule.weight for rule in category_rules), 3)
        for category, category_rules in matched.items()
    }
    matched_categories = tuple(
        sorted(category_scores, key=lambda item: PRIORITY_INDEX[item])
    )

    if category_scores:
        selected = min(
            category_scores,
            key=lambda category: (-category_scores[category], PRIORITY_INDEX[category]),
        )
        fallback = False
        selected_rules = matched[selected]
        confident_keyword_match = any(
            rule.weight >= STRONG_RULE_WEIGHT for rule in selected_rules
        )
        top_score = category_scores[selected]
        other_scores = [
            score for category, score in category_scores.items() if category != selected
        ]
        separation = top_score / (top_score + max(other_scores, default=0.0))
        confidence_score = round(min(1.0, top_score) * separation, 3)
        if not confident_keyword_match or confidence_score < 0.60:
            confidence: Confidence = "low"
        elif len(category_scores) > 1:
            confidence = "medium"
        else:
            confidence = "high"
    else:
        selected = "unknown"
        fallback = True
        confident_keyword_match = False
        confidence_score = 0.0
        confidence = "low"

    secondary_causes = [
        f"also_matched:{category}"
        for category in matched_categories
        if category != selected
    ]
    if source == "recall" and selected != "recall":
        secondary_causes.append("recall_event")

    field_name = "reason_for_recall" if source == "recall" else "shortage_reason"
    evidence = [
        f"FDA {field_name}: {normalized_text}"
        if normalized_text
        else f"FDA {field_name}: not supplied"
    ]
    for label, value in (
        ("classification", classification),
        ("status", status),
        ("availability", availability),
    ):
        if value:
            evidence.append(f"FDA {label}: {value}")

    event = DisruptionEvent(
        primary_cause=selected,
        secondary_causes=secondary_causes,
        supply_chain_stage=STAGES[selected],
        severity=_severity(
            source,
            classification=classification,
            availability=availability,
            status=status,
        ),
        evidence=evidence,
    )
    return ClassificationResult(
        event=event,
        confidence=confidence,
        confidence_score=confidence_score,
        confident_keyword_match=confident_keyword_match,
        fallback=fallback,
        matched_categories=matched_categories,
        matched_rules={
            category: tuple(rule.name for rule in category_rules)
            for category, category_rules in matched.items()
        },
        category_scores=category_scores,
    )


def classify_record(record: dict[str, Any], *, source: Source) -> ClassificationResult:
    """Classify an openFDA shortage or recall record."""
    if source == "shortage":
        return classify_text(
            record.get("shortage_reason"),
            source=source,
            availability=record.get("availability"),
            status=record.get("status"),
        )
    return classify_text(
        record.get("reason_for_recall"),
        source=source,
        classification=record.get("classification"),
        status=record.get("status"),
    )


def _latest_snapshot() -> Path:
    root = REPOSITORY_ROOT / "data" / "snapshots"
    snapshots = sorted(
        path
        for path in root.iterdir()
        if path.is_dir()
        and SNAPSHOT_PATTERN.fullmatch(path.name)
        and (path / "shortages.json").is_file()
        and (path / "recalls.json").is_file()
    )
    if not snapshots:
        raise RuntimeError("No snapshot with shortages and recalls is available")
    return snapshots[-1]


def _load_records(snapshot: Path, source: str) -> list[dict[str, Any]]:
    path = snapshot / f"{source}.json"
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    records = payload.get("results")
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        raise ValueError(f"{path} does not contain an openFDA results list")
    return records


def _source_metrics(
    rows: list[dict[str, Any]], results: list[ClassificationResult], reason_field: str
) -> dict[str, Any]:
    total = len(results)
    distribution = Counter(result.event.primary_cause for result in results)
    confidence = Counter(result.confidence for result in results)
    collision_pairs: Counter[tuple[str, str]] = Counter()
    for result in results:
        collision_pairs.update(combinations(result.matched_categories, 2))
    no_confident_texts = Counter(
        str(row.get(reason_field) or "")
        for row, result in zip(rows, results)
        if not result.confident_keyword_match
    )
    return {
        "total": total,
        "confident_keyword_matches": sum(
            result.confident_keyword_match for result in results
        ),
        "no_confident_keyword_match": sum(
            not result.confident_keyword_match for result in results
        ),
        "fallbacks": sum(result.fallback for result in results),
        "low_confidence": confidence["low"],
        "collisions": sum(result.collision for result in results),
        "category_distribution": dict(distribution.most_common()),
        "confidence_distribution": {
            level: confidence[level] for level in ("high", "medium", "low")
        },
        "collision_pairs": [
            {"categories": list(pair), "records": count}
            for pair, count in collision_pairs.most_common()
        ],
        "top_no_confident_texts": [
            {"text": text, "records": count}
            for text, count in no_confident_texts.most_common(15)
        ],
    }


def evaluate_snapshot(snapshot: Path) -> dict[str, Any]:
    """Run the baseline across all reason-bearing shortage and recall records."""
    shortage_rows = [
        row
        for row in _load_records(snapshot, "shortages")
        if str(row.get("shortage_reason") or "").strip()
    ]
    recall_rows = [
        row
        for row in _load_records(snapshot, "recalls")
        if str(row.get("reason_for_recall") or "").strip()
    ]
    shortage_results = [classify_record(row, source="shortage") for row in shortage_rows]
    recall_results = [classify_record(row, source="recall") for row in recall_rows]

    shortage_metrics = _source_metrics(
        shortage_rows, shortage_results, "shortage_reason"
    )
    recall_metrics = _source_metrics(recall_rows, recall_results, "reason_for_recall")

    shortage_confusion: Counter[tuple[str, str]] = Counter()
    for row, result in zip(shortage_rows, shortage_results):
        reference = SHORTAGE_REFERENCE_LABELS.get(str(row["shortage_reason"]), "unknown")
        shortage_confusion[(reference, result.event.primary_cause)] += 1
    shortage_correct = sum(
        count for (reference, predicted), count in shortage_confusion.items() if reference == predicted
    )

    recall_by_id = {
        row.get("recall_number"): row for row in recall_rows if row.get("recall_number")
    }
    hand_review = []
    for example in HAND_MAPPED_EXAMPLES:
        if example["source"] != "recalls":
            continue
        record = recall_by_id[example["source_id"]]
        result = classify_record(record, source="recall")
        expected = example["event"]["primary_cause"]
        hand_review.append(
            {
                "source_id": example["source_id"],
                "expected": expected,
                "predicted": result.event.primary_cause,
                "agrees": expected == result.event.primary_cause,
                "confidence": result.confidence,
                "matched_categories": list(result.matched_categories),
            }
        )

    recall_reason_counts = Counter(str(row["reason_for_recall"]) for row in recall_rows)
    resolved_collision_review = []
    for example in RESOLVED_DISCONTINUATION_COLLISION_TEXTS:
        result = classify_text(example["text"], source="recall")
        resolved_collision_review.append(
            {
                **example,
                "records": recall_reason_counts[example["text"]],
                "revised_prediction": result.event.primary_cause,
                "revised_matched_categories": list(result.matched_categories),
            }
        )

    gap_counts = {}
    for name, pattern in TAXONOMY_GAP_PATTERNS.items():
        all_count = 0
        no_confident_count = 0
        for row, result in zip(recall_rows, recall_results):
            if pattern.search(str(row["reason_for_recall"])):
                all_count += 1
                no_confident_count += not result.confident_keyword_match
        gap_counts[name] = {
            "all_recall_records": all_count,
            "no_confident_records": no_confident_count,
        }

    all_results = shortage_results + recall_results
    total = len(all_results)
    no_confident = sum(not result.confident_keyword_match for result in all_results)
    collisions: Counter[tuple[str, str]] = Counter()
    collision_texts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    reason_results = [
        (str(row["shortage_reason"]), result)
        for row, result in zip(shortage_rows, shortage_results)
    ] + [
        (str(row["reason_for_recall"]), result)
        for row, result in zip(recall_rows, recall_results)
    ]
    for text, result in reason_results:
        for pair in combinations(result.matched_categories, 2):
            collisions[pair] += 1
            collision_texts[pair][text] += 1

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "snapshot": snapshot.name,
        "method": {
            "taxonomy_version": PRIMARY_CAUSE_TAXONOMY_VERSION,
            "rules": len(RULES),
            "strong_rule_weight": STRONG_RULE_WEIGHT,
            "tie_break_priority": list(CATEGORY_PRIORITY),
        },
        "overall": {
            "total": total,
            "no_confident_keyword_match": no_confident,
            "no_confident_percentage": 100 * no_confident / total if total else 0.0,
            "fallbacks": sum(result.fallback for result in all_results),
            "low_confidence": sum(result.confidence == "low" for result in all_results),
            "collisions": sum(result.collision for result in all_results),
            "collision_pairs": [
                {
                    "categories": list(pair),
                    "records": count,
                    "examples": [
                        {"text": text, "records": text_count}
                        for text, text_count in collision_texts[pair].most_common(3)
                    ],
                }
                for pair, count in collisions.most_common()
            ],
        },
        "sources": {
            "shortages": shortage_metrics,
            "recalls": recall_metrics,
        },
        "shortage_reference_comparison": {
            "correct": shortage_correct,
            "total": len(shortage_results),
            "accuracy": (
                100 * shortage_correct / len(shortage_results)
                if shortage_results
                else 0.0
            ),
            "confusion": [
                {"reference": reference, "predicted": predicted, "records": count}
                for (reference, predicted), count in sorted(shortage_confusion.items())
            ],
        },
        "recall_hand_review": {
            "agreed": sum(row["agrees"] for row in hand_review),
            "total": len(hand_review),
            "examples": hand_review,
        },
        "resolved_discontinuation_quality_collisions": resolved_collision_review,
        "taxonomy_gap_signals": gap_counts,
    }


def _percentage(count: int, total: int) -> str:
    return f"{100 * count / total:.2f}%" if total else "0.00%"


def _escape(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value).split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_markdown(metrics: dict[str, Any]) -> str:
    """Render the baseline audit into a human-readable report."""
    overall = metrics["overall"]
    lines = [
        "# Phase 8 Rule-Based Baseline Report",
        "",
        f"Generated: {metrics['generated_at']}",
        "",
        f"Snapshot: `{metrics['snapshot']}`",
        "",
        "## Headline",
        "",
        f"**{overall['no_confident_percentage']:.2f}% ({overall['no_confident_keyword_match']:,}/{overall['total']:,}) of reason-bearing records received no confident keyword match.**",
        "",
        "A confident match requires at least one narrow, category-specific rule in the selected category. A weak generic term can still select a lowest-confidence category; no match falls back to `unknown` for both shortages and recalls. Collisions are retained for audit even though deterministic priority selects one primary cause.",
        "",
        "## Locked taxonomy revision",
        "",
        f"The Phase 9 taxonomy `{metrics['method']['taxonomy_version']}` has 13 primary causes. This revision adds `labeling_packaging_error`, `regulatory_noncompliance`, and `adverse_event_signal`. Their motivating no-confident volumes in the previous baseline were 1,466, 588, and 301 recall records respectively. FDA shortage reason `Other` remains `unknown`; it is not promoted into a causal category.",
        "",
        "## Coverage by source",
        "",
        "| Source | Records | No confident match | Pure fallback | Low confidence | Multi-category collision |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for source, values in metrics["sources"].items():
        total = values["total"]
        lines.append(
            f"| {source} | {total:,} | {values['no_confident_keyword_match']:,} ({_percentage(values['no_confident_keyword_match'], total)}) | {values['fallbacks']:,} ({_percentage(values['fallbacks'], total)}) | {values['low_confidence']:,} ({_percentage(values['low_confidence'], total)}) | {values['collisions']:,} ({_percentage(values['collisions'], total)}) |"
        )

    categories = CATEGORY_PRIORITY
    lines.extend(
        [
            "",
            "## Predicted category distribution",
            "",
            "| Category | Shortages | Recalls |",
            "|---|---:|---:|",
        ]
    )
    for category in categories:
        shortage_count = metrics["sources"]["shortages"]["category_distribution"].get(
            category, 0
        )
        recall_count = metrics["sources"]["recalls"]["category_distribution"].get(
            category, 0
        )
        if shortage_count or recall_count:
            lines.append(f"| `{category}` | {shortage_count:,} | {recall_count:,} |")

    lines.extend(
        [
            "",
            "## Resolved discontinuation/quality collision",
            "",
            "The previous rules produced 91 collisions from only two unique FDA narratives. Both were keyword overreach rather than true product-discontinuation labels: the text described ending stability work, not ending the drug product.",
            "",
            "| Records | FDA text | Revised primary cause | Why |",
            "|---:|---|---|---|",
        ]
    )
    for row in metrics["resolved_discontinuation_quality_collisions"]:
        lines.append(
            f"| {row['records']:,} | {_escape(row['text'], 180)} | `{row['revised_prediction']}` | {_escape(row['rationale'], 180)} |"
        )

    lines.extend(
        [
            "",
            "## Most common rule collisions",
            "",
            "| Matched categories | Records | Common real text |",
            "|---|---:|---|",
        ]
    )
    for row in overall["collision_pairs"][:12]:
        example = row["examples"][0] if row["examples"] else {"text": "", "records": 0}
        lines.append(
            f"| {' + '.join(f'`{item}`' for item in row['categories'])} | {row['records']:,} | {_escape(example['text'], 120)} ({example['records']:,}) |"
        )
    if not overall["collision_pairs"]:
        lines.append("| None | 0 | — |")

    reference = metrics["shortage_reference_comparison"]
    lines.extend(
        [
            "",
            "## Shortage proxy-reference comparison",
            "",
            f"The eight standardized FDA shortage phrases provide a useful proxy reference: **{reference['accuracy']:.2f}% ({reference['correct']:,}/{reference['total']:,}) agreement**. This is coverage, not independent validation—the rules were designed around these known phrases.",
            "",
            "| Reference category | Baseline category | Records |",
            "|---|---|---:|",
        ]
    )
    for row in reference["confusion"]:
        lines.append(
            f"| `{row['reference']}` | `{row['predicted']}` | {row['records']:,} |"
        )

    hand = metrics["recall_hand_review"]
    lines.extend(
        [
            "",
            "## Phase 7 recall hand-sample comparison",
            "",
            f"The baseline agrees with **{hand['agreed']}/{hand['total']}** previously hand-reviewed recall examples. Eight examples are too few for an accuracy estimate; disagreements expose rule/taxonomy behavior.",
            "",
            "| Recall | Hand mapping | Baseline | Confidence | All matched categories |",
            "|---|---|---|---|---|",
        ]
    )
    for row in hand["examples"]:
        matched = ", ".join(f"`{item}`" for item in row["matched_categories"]) or "none"
        lines.append(
            f"| `{row['source_id']}` | `{row['expected']}` | `{row['predicted']}` | {row['confidence']} | {matched} |"
        )

    lines.extend(
        [
            "",
            "## Taxonomy-gap signals in recall text",
            "",
            "These keyword families overlap and are diagnostic counts, not labels.",
            "",
            "| Missing concept candidate | All recalls | No confident current-category match |",
            "|---|---:|---:|",
        ]
    )
    for name, values in metrics["taxonomy_gap_signals"].items():
        lines.append(
            f"| `{name}` | {values['all_recall_records']:,} | {values['no_confident_records']:,} |"
        )

    lines.extend(
        [
            "",
            "## Common no-confident-match text",
            "",
            "| Source | Records | FDA reason text |",
            "|---|---:|---|",
        ]
    )
    for source in ("shortages", "recalls"):
        for row in metrics["sources"][source]["top_no_confident_texts"][:10]:
            lines.append(
                f"| {source} | {row['records']:,} | {_escape(row['text'])} |"
            )

    lines.extend(
        [
            "",
            "## Failure-pattern summary",
            "",
            "- The shortage baseline is nearly a lookup table because FDA currently uses only eight reason phrases; its apparent agreement is not evidence of generalization.",
            "- Recall text mixes root cause, observed defect, regulatory action, lifecycle state, and harm in the same field. Multi-category matches are therefore meaningful ambiguity, not merely regex noise.",
            "- Common unmatched quality phrases show vocabulary failure rather than taxonomy failure; a learned model should recognize paraphrases without continually expanding a brittle synonym list.",
            "- Tightening discontinuation to explicit drug/product/manufacturing cessation eliminated the former 91-record discontinuation/quality collision without changing the quality interpretation of those records.",
            "- `shipping_delay` + `manufacturing_quality_problem` commonly describes cold-chain transit delays and possible temperature damage, again mixing initiating cause with resulting product risk.",
            "- A pure no-keyword match abstains to `unknown` for both sources. `recall` remains available only when it is the most specific supported label, not as the default for missing evidence.",
            "- Storage-temperature excursions and counterfeit/tampering remain the clearest unresolved taxonomy candidates. The three high-volume Phase 8 gaps are now first-class categories.",
            "- A Phase 9 gold set should be stratified across predicted categories, collisions, pure fallbacks, weak-only matches, and the candidate gap families. Overall accuracy alone would hide failure on rare categories.",
            "",
            "## Reproduction",
            "",
            "```bash",
            "/opt/anaconda3/envs/medisupply/bin/python src/models/baseline_classifier.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    except BaseException:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise


def main() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, help="Snapshot directory; defaults to newest")
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--markdown-report", type=Path)
    args = parser.parse_args()

    snapshot = args.snapshot or _latest_snapshot()
    metrics = evaluate_snapshot(snapshot)
    json_path = args.json_report or REPOSITORY_ROOT / "reports" / "baseline_classifier_quality.json"
    markdown_path = (
        args.markdown_report
        or REPOSITORY_ROOT / "reports" / "baseline_classifier_quality.md"
    )
    _atomic_write(json_path, json.dumps(metrics, indent=2, ensure_ascii=False) + "\n")
    _atomic_write(markdown_path, render_markdown(metrics))

    overall = metrics["overall"]
    print(f"Snapshot: {metrics['snapshot']}")
    print(
        "No confident keyword match: "
        f"{overall['no_confident_keyword_match']:,}/{overall['total']:,} "
        f"({overall['no_confident_percentage']:.2f}%)"
    )
    print(f"Multi-category collisions: {overall['collisions']:,}")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    return json_path, markdown_path


if __name__ == "__main__":
    main()
