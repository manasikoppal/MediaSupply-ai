"""Validated records, splitting, and reporting for the Phase 9 gold dataset."""

from __future__ import annotations

import json
import os
import random
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schema import (
    DisruptionEvent,
    PRIMARY_CAUSE_TAXONOMY_VERSION,
    PrimaryCause,
)


Source = Literal["shortage", "recall"]
Confidence = Literal["low", "medium", "high"]
SplitName = Literal["train", "validation", "test"]


class BaselineSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_cause: PrimaryCause
    confidence: Confidence
    confidence_score: float = Field(ge=0.0, le=1.0)
    confident_keyword_match: bool
    fallback: bool
    collision: bool
    matched_categories: list[PrimaryCause]
    matched_rules: dict[PrimaryCause, list[str]]
    event: DisruptionEvent


class GoldCandidate(BaseModel):
    """One sampled FDA record awaiting independent human review."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    taxonomy_version: str
    snapshot: str = Field(min_length=1)
    sampling_stratum: PrimaryCause
    selection_reason: str = Field(min_length=1)
    source: Source
    source_record_id: str = Field(min_length=1)
    text_field: Literal["shortage_reason", "reason_for_recall"]
    raw_text: str | None
    source_context: dict[str, Any]
    baseline: BaselineSuggestion

    @field_validator("taxonomy_version")
    @classmethod
    def taxonomy_must_be_locked_version(cls, value: str) -> str:
        if value != PRIMARY_CAUSE_TAXONOMY_VERSION:
            raise ValueError(
                f"expected taxonomy {PRIMARY_CAUSE_TAXONOMY_VERSION}, got {value}"
            )
        return value


class GoldLabelRecord(BaseModel):
    """Human-reviewed label with provenance and a validated event payload."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    taxonomy_version: str
    snapshot: str = Field(min_length=1)
    sampling_stratum: PrimaryCause
    source: Source
    source_record_id: str = Field(min_length=1)
    text_field: Literal["shortage_reason", "reason_for_recall"]
    raw_text: str | None
    baseline_primary_cause: PrimaryCause
    baseline_confidence: Confidence
    baseline_collision: bool
    event: DisruptionEvent
    annotator: str = Field(min_length=1)
    labeled_at: str = Field(min_length=1)
    disagreement_note: str | None = None

    @field_validator("taxonomy_version")
    @classmethod
    def taxonomy_must_be_locked_version(cls, value: str) -> str:
        if value != PRIMARY_CAUSE_TAXONOMY_VERSION:
            raise ValueError(
                f"expected taxonomy {PRIMARY_CAUSE_TAXONOMY_VERSION}, got {value}"
            )
        return value


def load_jsonl(path: Path, model: type[BaseModel]) -> list[Any]:
    """Load and validate a JSONL file, reporting the failing line."""
    if not path.exists():
        return []
    values = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                values.append(model.model_validate_json(line))
            except Exception as error:
                raise ValueError(f"Invalid {path} line {line_number}") from error
    return values


def atomic_write_jsonl(path: Path, records: list[BaseModel]) -> None:
    """Atomically replace a JSONL artifact after validating every record."""
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
            for record in records:
                handle.write(record.model_dump_json())
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    except BaseException:
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise


def _allocate_holdout(
    category_sizes: dict[PrimaryCause, int], total: int, reserved: dict[PrimaryCause, int]
) -> dict[PrimaryCause, int]:
    """Allocate an exact holdout total while retaining one train item per class."""
    allocation = dict(reserved)
    remaining = total - sum(allocation.values())
    if remaining < 0:
        raise ValueError("Holdout target is smaller than required class coverage")

    while remaining:
        eligible = [
            category
            for category, size in category_sizes.items()
            if allocation.get(category, 0) < size - 1
        ]
        if not eligible:
            raise ValueError("Not enough records to allocate requested holdout")
        category = max(
            eligible,
            key=lambda item: (
                0.15 * category_sizes[item] - allocation.get(item, 0),
                category_sizes[item],
                item,
            ),
        )
        allocation[category] = allocation.get(category, 0) + 1
        remaining -= 1
    return allocation


def stratified_split(
    records: list[GoldLabelRecord], *, seed: int = 202609
) -> dict[SplitName, list[GoldLabelRecord]]:
    """Create exact 70/15/15 splits with per-class coverage when n >= 3."""
    total = len(records)
    validation_total = round(total * 0.15)
    test_total = round(total * 0.15)
    groups: dict[PrimaryCause, list[GoldLabelRecord]] = defaultdict(list)
    for record in records:
        groups[record.event.primary_cause].append(record)
    sizes = {category: len(group) for category, group in groups.items()}

    required_validation = {category: 1 for category, size in sizes.items() if size >= 3}
    validation_counts = _allocate_holdout(sizes, validation_total, required_validation)
    remaining_sizes = {
        category: size - validation_counts.get(category, 0)
        for category, size in sizes.items()
    }
    required_test = {category: 1 for category, size in sizes.items() if size >= 3}
    test_counts = _allocate_holdout(remaining_sizes, test_total, required_test)

    splits: dict[SplitName, list[GoldLabelRecord]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    for category, group in sorted(groups.items()):
        shuffled = list(group)
        random.Random(f"{seed}:{category}").shuffle(shuffled)
        validation_count = validation_counts.get(category, 0)
        test_count = test_counts.get(category, 0)
        splits["validation"].extend(shuffled[:validation_count])
        splits["test"].extend(
            shuffled[validation_count : validation_count + test_count]
        )
        splits["train"].extend(shuffled[validation_count + test_count :])

    for split, values in splits.items():
        random.Random(f"{seed}:{split}").shuffle(values)
    expected = {
        "train": total - validation_total - test_total,
        "validation": validation_total,
        "test": test_total,
    }
    actual = {split: len(values) for split, values in splits.items()}
    if actual != expected:
        raise AssertionError(f"Unexpected split sizes: {actual}, expected {expected}")
    return splits


def _percentage(count: int, total: int) -> str:
    return f"{100 * count / total:.2f}%" if total else "0.00%"


def _escape(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "[FDA reason missing]").split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def gold_metrics(
    records: list[GoldLabelRecord], splits: dict[SplitName, list[GoldLabelRecord]]
) -> dict[str, Any]:
    distribution = Counter(record.event.primary_cause for record in records)
    agreements: dict[PrimaryCause, Counter[str]] = defaultdict(Counter)
    disagreements = []
    for record in records:
        agrees = record.baseline_primary_cause == record.event.primary_cause
        agreements[record.event.primary_cause]["total"] += 1
        agreements[record.event.primary_cause]["agreed"] += agrees
        if not agrees:
            disagreements.append(
                {
                    "candidate_id": record.candidate_id,
                    "source": record.source,
                    "source_record_id": record.source_record_id,
                    "raw_text": record.raw_text,
                    "baseline": record.baseline_primary_cause,
                    "human": record.event.primary_cause,
                    "baseline_confidence": record.baseline_confidence,
                    "baseline_collision": record.baseline_collision,
                    "note": record.disagreement_note,
                }
            )
    split_distribution = {
        split: dict(Counter(row.event.primary_cause for row in values))
        for split, values in splits.items()
    }
    overall_agreed = sum(
        record.baseline_primary_cause == record.event.primary_cause for record in records
    )
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "taxonomy_version": PRIMARY_CAUSE_TAXONOMY_VERSION,
        "total": len(records),
        "category_distribution": dict(distribution),
        "baseline_agreement": {
            "agreed": overall_agreed,
            "total": len(records),
            "percentage": 100 * overall_agreed / len(records) if records else 0.0,
            "by_final_category": {
                category: {
                    "agreed": counts["agreed"],
                    "total": counts["total"],
                    "percentage": 100 * counts["agreed"] / counts["total"],
                }
                for category, counts in agreements.items()
            },
        },
        "splits": {
            split: {
                "records": len(values),
                "percentage": 100 * len(values) / len(records) if records else 0.0,
                "category_distribution": split_distribution[split],
            }
            for split, values in splits.items()
        },
        "disagreements": disagreements,
    }


def render_gold_report(metrics: dict[str, Any]) -> str:
    agreement = metrics["baseline_agreement"]
    lines = [
        "# Phase 9 Gold Dataset Report",
        "",
        f"Generated: {metrics['generated_at']}",
        "",
        f"Taxonomy: `{metrics['taxonomy_version']}`",
        "",
        f"Human-labeled records: **{metrics['total']:,}**",
        "",
        f"Baseline agreement: **{agreement['percentage']:.2f}% ({agreement['agreed']:,}/{agreement['total']:,})**",
        "",
        "## Final category distribution and baseline agreement",
        "",
        "| Final category | Records | Share | Baseline agreement |",
        "|---|---:|---:|---:|",
    ]
    for category, count in sorted(
        metrics["category_distribution"].items(), key=lambda item: (-item[1], item[0])
    ):
        category_agreement = agreement["by_final_category"][category]
        lines.append(
            f"| `{category}` | {count:,} | {_percentage(count, metrics['total'])} | {category_agreement['agreed']:,}/{category_agreement['total']:,} ({category_agreement['percentage']:.2f}%) |"
        )

    lines.extend(
        [
            "",
            "## Stratified splits",
            "",
            "| Split | Records | Share |",
            "|---|---:|---:|",
        ]
    )
    for split in ("train", "validation", "test"):
        values = metrics["splits"][split]
        lines.append(
            f"| {split} | {values['records']:,} | {values['percentage']:.2f}% |"
        )

    lines.extend(
        [
            "",
            "### Category coverage by split",
            "",
            "| Category | Train | Validation | Test |",
            "|---|---:|---:|---:|",
        ]
    )
    categories = sorted(metrics["category_distribution"])
    for category in categories:
        lines.append(
            f"| `{category}` | {metrics['splits']['train']['category_distribution'].get(category, 0):,} | {metrics['splits']['validation']['category_distribution'].get(category, 0):,} | {metrics['splits']['test']['category_distribution'].get(category, 0):,} |"
        )

    lines.extend(
        [
            "",
            "## Human disagreements with the baseline",
            "",
            "| Record | Baseline | Human | Confidence | Collision | Review note | FDA text |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in metrics["disagreements"]:
        lines.append(
            f"| `{row['source']}:{row['source_record_id']}` | `{row['baseline']}` | `{row['human']}` | {row['baseline_confidence']} | {'yes' if row['baseline_collision'] else 'no'} | {_escape(row['note'], 120)} | {_escape(row['raw_text'])} |"
        )
    if not metrics["disagreements"]:
        lines.append("| — | — | — | — | — | No disagreements recorded | — |")

    lines.extend(
        [
            "",
            "The disagreement notes are human-entered during review. They should be inspected before changing the locked taxonomy or keyword baseline.",
            "",
        ]
    )
    return "\n".join(lines)

