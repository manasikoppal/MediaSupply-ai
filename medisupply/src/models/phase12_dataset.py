"""Incident-aware modeling data for Phase 12 training and evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .gold_dataset import GoldCandidate, GoldLabelRecord, atomic_write_jsonl
from .schema import DisruptionEvent, PrimaryCause

SplitName = Literal["train", "validation", "test"]
WeightingMode = Literal["weights", "cap"]
IncidentIdSource = Literal["fda_event_id", "shortage_surrogate", "record_fallback"]

SPLIT_RATIOS: dict[SplitName, float] = {
    "train": 0.70,
    "validation": 0.15,
    "test": 0.15,
}
MIN_INDEPENDENT_SUPPORT = 3
DEFAULT_MAX_EVENTS_PER_TEXT = 5
_WHITESPACE = re.compile(r"\s+")


class Phase12Example(BaseModel):
    """A gold label enriched with leakage and weighting metadata."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    taxonomy_version: str
    snapshot: str
    source: Literal["shortage", "recall"]
    source_record_id: str
    raw_text: str | None
    source_context: dict[str, Any]
    event: DisruptionEvent
    incident_id: str
    incident_id_source: IncidentIdSource
    reason_text_id: str
    split_group_id: str
    incident_text_filing_count: int = Field(ge=1)
    text_incident_count: int = Field(ge=1)
    sample_weight: float = Field(gt=0.0, le=1.0)
    weighting_method: WeightingMode = "weights"


def normalize_reason_text(value: str | None) -> str:
    """Normalize only presentation differences used for leakage detection."""
    if value is None or not value.strip():
        return "[fda reason missing]"
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return _WHITESPACE.sub(" ", normalized)


def _stable_id(prefix: str, *values: Any) -> str:
    material = "\x1f".join(str(value or "") for value in values)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def reason_text_id(value: str | None) -> str:
    return _stable_id("reason", normalize_reason_text(value))


def shortage_incident_id(
    source_context: dict[str, Any], *, fallback_source_record_id: str
) -> tuple[str, IncidentIdSource]:
    shortage_parts = (
        source_context.get("generic_name"),
        source_context.get("company_name"),
        source_context.get("initial_posting_date"),
    )
    if any(str(value or "").strip() for value in shortage_parts):
        normalized = tuple(
            normalize_reason_text(str(value or "")) for value in shortage_parts
        )
        return _stable_id("shortage_event", *normalized), "shortage_surrogate"
    return f"shortage_record:{fallback_source_record_id}", "record_fallback"


def incident_identity(candidate: GoldCandidate) -> tuple[str, IncidentIdSource]:
    """Use FDA event IDs for recalls and a documented shortage surrogate."""
    if candidate.source == "recall":
        event_id = str(candidate.source_context.get("event_id") or "").strip()
        if event_id:
            return f"recall_event:{event_id}", "fda_event_id"
        return f"recall_record:{candidate.source_record_id}", "record_fallback"

    return shortage_incident_id(
        candidate.source_context,
        fallback_source_record_id=candidate.source_record_id,
    )


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _leakage_group_ids(
    identities: list[tuple[str, str]],
) -> list[str]:
    """Connect examples sharing an incident or normalized exact reason."""
    union_find = _UnionFind()
    nodes = []
    for incident_id, text_id in identities:
        incident_node = f"incident|{incident_id}"
        text_node = f"text|{text_id}"
        union_find.union(incident_node, text_node)
        nodes.append((incident_node, text_node))

    component_nodes: dict[str, set[str]] = defaultdict(set)
    for node in union_find.parent:
        component_nodes[union_find.find(node)].add(node)
    component_ids = {
        root: _stable_id("split_group", *sorted(members))
        for root, members in component_nodes.items()
    }
    return [component_ids[union_find.find(incident)] for incident, _ in nodes]


def leakage_group_ids(identities: list[tuple[str, str]]) -> list[str]:
    """Return stable connected-component IDs for incident/text identities.

    Phase 11 reuses this public wrapper so distillation data follows exactly
    the same leakage boundary as the Phase 12 evaluation transformation.
    """
    return _leakage_group_ids(identities)


def build_phase12_examples(
    candidates: list[GoldCandidate], labels: list[GoldLabelRecord]
) -> list[Phase12Example]:
    """Join immutable gold labels to candidate context and add modeling metadata."""
    candidate_index = {candidate.candidate_id: candidate for candidate in candidates}
    if len(candidate_index) != len(candidates):
        raise ValueError("Candidate queue contains duplicate candidate IDs")
    label_ids = [label.candidate_id for label in labels]
    if len(set(label_ids)) != len(label_ids):
        raise ValueError("Gold labels contain duplicate candidate IDs")

    base_rows = []
    for label in labels:
        candidate = candidate_index.get(label.candidate_id)
        if candidate is None:
            raise ValueError(f"Label has no candidate: {label.candidate_id}")
        if (
            label.source != candidate.source
            or label.source_record_id != candidate.source_record_id
            or label.raw_text != candidate.raw_text
        ):
            raise ValueError(
                f"Label provenance differs from candidate: {label.candidate_id}"
            )
        incident_id, incident_source = incident_identity(candidate)
        text_id = reason_text_id(label.raw_text)
        base_rows.append((candidate, label, incident_id, incident_source, text_id))

    incident_text_counts = Counter((row[2], row[4]) for row in base_rows)
    text_incidents: dict[str, set[str]] = defaultdict(set)
    for _, _, incident_id, _, text_id in base_rows:
        text_incidents[text_id].add(incident_id)
    group_ids = _leakage_group_ids([(row[2], row[4]) for row in base_rows])

    examples = []
    for row, group_id in zip(base_rows, group_ids):
        candidate, label, incident_id, incident_source, text_id = row
        filing_count = incident_text_counts[(incident_id, text_id)]
        independent_incidents = len(text_incidents[text_id])
        examples.append(
            Phase12Example(
                candidate_id=label.candidate_id,
                taxonomy_version=label.taxonomy_version,
                snapshot=label.snapshot,
                source=label.source,
                source_record_id=label.source_record_id,
                raw_text=label.raw_text,
                source_context=candidate.source_context,
                event=label.event,
                incident_id=incident_id,
                incident_id_source=incident_source,
                reason_text_id=text_id,
                split_group_id=group_id,
                incident_text_filing_count=filing_count,
                text_incident_count=independent_incidents,
                sample_weight=1.0 / (filing_count * independent_incidents),
                weighting_method="weights",
            )
        )
    return examples


def cap_examples(
    examples: list[Phase12Example],
    *,
    max_events_per_text: int = DEFAULT_MAX_EVENTS_PER_TEXT,
    seed: int = 202612,
) -> list[Phase12Example]:
    """Fallback for trainers without weights: one filing/event, capped events/text."""
    if max_events_per_text < 1:
        raise ValueError("max_events_per_text must be positive")
    by_text: dict[str, dict[str, list[Phase12Example]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for example in examples:
        by_text[example.reason_text_id][example.incident_id].append(example)

    selected = []
    for text_id, incidents in sorted(by_text.items()):
        ranked_incidents = sorted(
            incidents,
            key=lambda incident: _stable_id("cap_incident", seed, text_id, incident),
        )[:max_events_per_text]
        for incident_id in ranked_incidents:
            representative = min(
                incidents[incident_id],
                key=lambda row: _stable_id("cap_record", seed, row.candidate_id),
            )
            selected.append(
                representative.model_copy(
                    update={"sample_weight": 1.0, "weighting_method": "cap"}
                )
            )
    return sorted(selected, key=lambda row: row.candidate_id)


def grouped_stratified_split(
    examples: list[Phase12Example],
    *,
    ratios: dict[SplitName, float] | None = None,
    seed: int = 202612,
) -> dict[SplitName, list[Phase12Example]]:
    """Approximate stratification without splitting leakage-connected components."""
    ratios = ratios or SPLIT_RATIOS
    if set(ratios) != set(SPLIT_RATIOS):
        raise ValueError("ratios must define train, validation, and test")
    if not math.isclose(sum(ratios.values()), 1.0):
        raise ValueError("split ratios must sum to one")

    groups: dict[str, list[Phase12Example]] = defaultdict(list)
    for example in examples:
        groups[example.split_group_id].append(example)

    categories = sorted({row.event.primary_cause for row in examples})
    category_totals = Counter()
    for example in examples:
        category_totals[example.event.primary_cause] += example.sample_weight
    total_weight = sum(example.sample_weight for example in examples)
    targets = {
        split: {
            category: ratios[split] * category_totals[category]
            for category in categories
        }
        for split in ratios
    }
    total_targets = {split: ratios[split] * total_weight for split in ratios}

    group_stats = []
    for group_id, rows in groups.items():
        category_weights = Counter()
        for row in rows:
            category_weights[row.event.primary_cause] += row.sample_weight
        rarity = sum(
            weight / max(category_totals[category], 1e-12)
            for category, weight in category_weights.items()
        )
        group_stats.append(
            (group_id, rows, category_weights, sum(category_weights.values()), rarity)
        )
    group_stats.sort(
        key=lambda value: (
            -value[4],
            -value[3],
            _stable_id("split_order", seed, value[0]),
        )
    )

    allocations: dict[SplitName, list[Phase12Example]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    allocated_categories = {split: Counter() for split in allocations}
    allocated_totals = Counter()

    def assignment_cost(
        target_split: SplitName,
        category_weights: Counter[PrimaryCause],
        group_weight: float,
    ) -> float:
        cost = 0.0
        for split in allocations:
            for category in categories:
                value = allocated_categories[split][category]
                if split == target_split:
                    value += category_weights[category]
                target = targets[split][category]
                cost += ((value - target) / max(target, 0.25)) ** 2
            total = allocated_totals[split]
            if split == target_split:
                total += group_weight
            target_total = total_targets[split]
            cost += 0.25 * ((total - target_total) / max(target_total, 0.25)) ** 2
        return cost

    for _, rows, category_weights, group_weight, _ in group_stats:
        selected_split = min(
            allocations,
            key=lambda split: (
                assignment_cost(split, category_weights, group_weight),
                _stable_id("split_tie", seed, rows[0].split_group_id, split),
            ),
        )
        allocations[selected_split].extend(rows)
        allocated_categories[selected_split].update(category_weights)
        allocated_totals[selected_split] += group_weight

    for rows in allocations.values():
        rows.sort(key=lambda row: row.candidate_id)
    return allocations


def category_support(
    rows: list[tuple[PrimaryCause, str, str]],
) -> dict[PrimaryCause, dict[str, Any]]:
    """Summarize records, incidents, and distinct text for each final category."""
    grouped: dict[PrimaryCause, dict[str, Any]] = defaultdict(
        lambda: {"records": 0, "incident_ids": set(), "reason_text_ids": set()}
    )
    for category, incident_id, text_id in rows:
        values = grouped[category]
        values["records"] += 1
        values["incident_ids"].add(incident_id)
        values["reason_text_ids"].add(text_id)

    result = {}
    for category, values in grouped.items():
        incidents = len(values["incident_ids"])
        texts = len(values["reason_text_ids"])
        sufficient = (
            incidents >= MIN_INDEPENDENT_SUPPORT and texts >= MIN_INDEPENDENT_SUPPORT
        )
        result[category] = {
            "records": values["records"],
            "independent_incidents": incidents,
            "unique_reason_texts": texts,
            "support_status": (
                "sufficient" if sufficient else "insufficient_independent_examples"
            ),
        }
    return result


def candidate_support(
    candidates: list[GoldCandidate],
) -> dict[PrimaryCause, dict[str, Any]]:
    rows = []
    for candidate in candidates:
        incident_id, _ = incident_identity(candidate)
        rows.append(
            (
                candidate.baseline.primary_cause,
                incident_id,
                reason_text_id(candidate.raw_text),
            )
        )
    return category_support(rows)


def recall_duplication_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Audit exact reason duplication and FDA event structure in the raw snapshot."""
    by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_reason[str(row.get("reason_for_recall") or "")].append(row)
        by_event[str(row.get("event_id") or "")].append(row)
    duplicate_groups = [group for group in by_reason.values() if len(group) > 1]
    event_reason_pairs = {
        (
            str(row.get("event_id") or row.get("recall_number")),
            reason_text_id(row.get("reason_for_recall")),
        )
        for row in rows
    }
    events_per_text: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        events_per_text[reason_text_id(row.get("reason_for_recall"))].add(
            str(row.get("event_id") or row.get("recall_number"))
        )
    thresholds = {}
    for threshold in (2, 5, 10, 20, 35, 50, 100):
        selected = [group for group in by_reason.values() if len(group) >= threshold]
        records = sum(map(len, selected))
        thresholds[str(threshold)] = {
            "groups": len(selected),
            "records": records,
            "percentage": 100 * records / len(rows) if rows else 0.0,
        }
    cap_sizes = {
        str(cap): sum(min(cap, len(events)) for events in events_per_text.values())
        for cap in (1, 2, 3, 5, 10, 20)
    }
    top_groups = []
    for reason, group in sorted(
        by_reason.items(), key=lambda item: (-len(item[1]), item[0])
    )[:10]:
        top_groups.append(
            {
                "reason": " ".join(reason.split()),
                "records": len(group),
                "independent_incidents": len(
                    {str(row.get("event_id") or "") for row in group}
                ),
            }
        )
    return {
        "records": len(rows),
        "unique_exact_reasons": len(by_reason),
        "unique_normalized_reasons": len(events_per_text),
        "duplicate_reason_groups": len(duplicate_groups),
        "records_in_duplicate_groups": sum(map(len, duplicate_groups)),
        "excess_records_after_one_per_reason": sum(
            len(group) - 1 for group in duplicate_groups
        ),
        "unique_event_ids": len({key for key in by_event if key}),
        "missing_event_ids": len(by_event.get("", [])),
        "unique_event_reason_pairs": len(event_reason_pairs),
        "thresholds": thresholds,
        "cap_sizes": cap_sizes,
        "top_exact_reason_groups": top_groups,
    }


def split_metrics(
    splits: dict[SplitName, list[Phase12Example]],
) -> dict[SplitName, dict[str, Any]]:
    result = {}
    for split, rows in splits.items():
        result[split] = {
            "records": len(rows),
            "effective_weight": sum(row.sample_weight for row in rows),
            "split_groups": len({row.split_group_id for row in rows}),
            "category_records": dict(Counter(row.event.primary_cause for row in rows)),
        }
    return result


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
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


def write_phase12_outputs(
    output_dir: Path,
    splits: dict[SplitName, list[Phase12Example]],
    *,
    weighting_mode: WeightingMode,
    max_events_per_text: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for split, rows in splits.items():
        atomic_write_jsonl(output_dir / f"{split}.jsonl", rows)
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "weighting_mode": weighting_mode,
        "max_events_per_text": max_events_per_text,
        "split_ratios": SPLIT_RATIOS,
        "splits": split_metrics(splits),
        "category_support": category_support(
            [
                (row.event.primary_cause, row.incident_id, row.reason_text_id)
                for rows in splits.values()
                for row in rows
            ]
        ),
        "evaluation_policy": {
            "unsupported_status": "insufficient_independent_examples",
            "unsupported_category_accuracy": None,
            "minimum_independent_incidents": MIN_INDEPENDENT_SUPPORT,
            "minimum_unique_reason_texts": MIN_INDEPENDENT_SUPPORT,
        },
    }
    atomic_write_text(
        output_dir / "manifest.json",
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )


def render_phase12_report(metrics: dict[str, Any]) -> str:
    raw = metrics["raw_recall_duplication"]
    readiness = metrics["gold_readiness"]
    candidate_rows = metrics["candidate_support"]
    labeled_rows = metrics["labeled_support"]
    adverse = candidate_rows.get("adverse_event_signal", {})
    lines = [
        "# Phase 12 Incident-Aware Modeling Data Report",
        "",
        f"Generated: {metrics['generated_at']}",
        "",
        f"Snapshot: `{metrics['snapshot']}`",
        "",
        "## Headline",
        "",
        f"Exact duplicate recall reasons affect **{raw['records_in_duplicate_groups']:,}/{raw['records']:,} ({100 * raw['records_in_duplicate_groups'] / raw['records']:.2f}%)** raw recall filings.",
        "",
        "Phase 12 therefore uses incident-aware identifiers, one-total-vote text weights, and leakage-connected split groups. Raw snapshots, the knowledge graph, and Phase 9 labels remain immutable inputs.",
        "",
        "## Gold readiness",
        "",
        f"Human labels available: **{readiness['labels']:,}/{readiness['candidates']:,}**; remaining: **{readiness['remaining']:,}**.",
        "",
        f"Final Phase 12 split artifacts: **{'materialized' if readiness['splits_materialized'] else 'not materialized'}**.",
        "",
    ]
    if not readiness["splits_materialized"]:
        lines.extend(
            [
                "The transformation intentionally refuses to publish partial train/validation/test files. Re-run after all Phase 9 candidates have human labels.",
                "",
            ]
        )
    lines.extend(
        [
            "## Identity and weighting policy",
            "",
            "- Recall `incident_id` is FDA `event_id`; `recall_number` remains product-level provenance.",
            "- Shortages use a documented surrogate derived from generic name, manufacturer, and initial posting date because FDA supplies no equivalent event ID.",
            "- `reason_text_id` hashes Unicode-normalized, case-folded, whitespace-collapsed FDA reason text.",
            "- `split_group_id` is the connected component formed by shared `incident_id` or `reason_text_id`; a component can appear in only one split.",
            "- Default row weight is `1 / (filings in incident+text × independent incidents with text)`, so each exact text contributes total weight 1.",
            f"- Trainers without sample-weight support may use the deterministic fallback: one filing per incident and at most {metrics['max_events_per_text']} incidents per exact text.",
            "",
            "FDA documents that an event may contain multiple recalled products and that a recall number identifies a specific classified recalled product: https://www.fda.gov/safety/enforcement-reports/enforcement-report-information-and-definitions",
            "",
            "## Full recall duplication audit",
            "",
            f"Unique exact reason texts: **{raw['unique_exact_reasons']:,}**.",
            "",
            f"Unique normalized reason texts: **{raw['unique_normalized_reasons']:,}**.",
            "",
            f"Unique FDA event IDs: **{raw['unique_event_ids']:,}**; missing event IDs: **{raw['missing_event_ids']:,}**.",
            "",
            f"One row per FDA event and normalized exact text would yield **{raw['unique_event_reason_pairs']:,}** modeling units.",
            "",
            f"The five-event fallback cap would retain **{raw['cap_sizes']['5']:,}** normalized event-text rows before gold-label filtering.",
            "",
            "| Minimum repetitions | Exact-text groups | Records | Raw share |",
            "|---:|---:|---:|---:|",
        ]
    )
    for threshold, values in raw["thresholds"].items():
        lines.append(
            f"| {threshold} | {values['groups']:,} | {values['records']:,} | {values['percentage']:.2f}% |"
        )
    lines.extend(
        [
            "",
            "### Largest exact-text groups",
            "",
            "| Records | Independent events | FDA reason |",
            "|---:|---:|---|",
        ]
    )
    for row in raw["top_exact_reason_groups"]:
        reason = row["reason"].replace("|", "\\|")
        if len(reason) > 180:
            reason = reason[:179] + "…"
        lines.append(
            f"| {row['records']:,} | {row['independent_incidents']:,} | {reason} |"
        )
    lines.extend(
        [
            "",
            "## Candidate queue independent support",
            "",
            "Support is insufficient when either independent incidents or unique normalized texts is below three—the minimum needed to place independent evidence in train, validation, and test.",
            "",
            "| Baseline stratum | Records | Incidents | Unique texts | Support |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for category, values in sorted(candidate_rows.items()):
        lines.append(
            f"| `{category}` | {values['records']:,} | {values['independent_incidents']:,} | {values['unique_reason_texts']:,} | `{values['support_status']}` |"
        )
    lines.extend(
        [
            "",
            "## Adverse-event signal limitation",
            "",
            f"**`adverse_event_signal`: {adverse.get('support_status', 'insufficient_independent_examples')}**.",
            "",
            f"The queue contains {adverse.get('records', 0):,} records but only {adverse.get('independent_incidents', 0):,} independent incident and {adverse.get('unique_reason_texts', 0):,} unique reason text. Phase 12 must not report these as independent examples. Keep the taxonomy value, but report supervised validation/test accuracy as unavailable until additional independent incidents exist.",
            "",
            "## Current human-labeled support",
            "",
            "These counts are provisional until labeling reaches 400/400.",
            "",
            "| Final human category | Records | Incidents | Unique texts | Support |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for category, values in sorted(labeled_rows.items()):
        lines.append(
            f"| `{category}` | {values['records']:,} | {values['independent_incidents']:,} | {values['unique_reason_texts']:,} | `{values['support_status']}` |"
        )
    if metrics.get("splits"):
        lines.extend(
            [
                "",
                "## Materialized grouped splits",
                "",
                "| Split | Records | Effective weight | Leakage groups |",
                "|---|---:|---:|---:|",
            ]
        )
        for split in ("train", "validation", "test"):
            values = metrics["splits"][split]
            lines.append(
                f"| {split} | {values['records']:,} | {values['effective_weight']:.3f} | {values['split_groups']:,} |"
            )
    lines.extend(
        [
            "",
            "## Reproduction",
            "",
            "```bash",
            "/opt/anaconda3/envs/medisupply/bin/python scripts/build_phase12_dataset.py",
            "```",
            "",
            "Use `--weighting-mode cap --max-events-per-text 5` only when the Phase 12 trainer cannot consume `sample_weight`.",
            "",
        ]
    )
    return "\n".join(lines)
