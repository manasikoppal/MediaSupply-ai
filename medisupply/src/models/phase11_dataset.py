"""Leakage-safe data preparation for Phase 11 SLM distillation."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .gold_dataset import GoldCandidate, GoldLabelRecord, atomic_write_jsonl
from .phase12_dataset import (
    IncidentIdSource,
    atomic_write_text,
    grouped_stratified_split,
    incident_identity,
    leakage_group_ids,
    reason_text_id,
)
from .schema import DisruptionEvent
from .teacher_labeling import MISSING_REASON_SENTINEL, TeacherLabelRecord

AnnotationOrigin = Literal["gold", "teacher", "deterministic"]
Phase11Split = Literal["train", "validation", "test", "gold_evaluation", "excluded"]


class Phase11Example(BaseModel):
    """One immutable annotation plus Phase 11 eligibility and split metadata."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    taxonomy_version: str
    snapshot: str
    source: Literal["shortage", "recall"]
    source_record_ids: list[str] = Field(min_length=1)
    raw_text: str
    source_context: dict[str, Any]
    event: DisruptionEvent
    annotation_origin: AnnotationOrigin
    incident_id: str
    incident_id_source: IncidentIdSource
    reason_text_id: str
    split_group_id: str
    sample_weight: float = Field(gt=0.0, le=1.0)
    training_eligible: bool
    exclusion_reasons: list[str]
    split: Phase11Split
    teacher_model: str | None = None
    prompt_version: str | None = None


def _teacher_incident_source(row: TeacherLabelRecord) -> IncidentIdSource:
    if row.source == "recall" and row.incident_id.startswith("recall_event:"):
        return "fda_event_id"
    if row.source == "shortage" and row.incident_id.startswith("shortage_event:"):
        return "shortage_surrogate"
    return "record_fallback"


def _base_rows(
    teacher_labels: list[TeacherLabelRecord],
    candidates: list[GoldCandidate],
    gold_labels: list[GoldLabelRecord],
) -> list[dict[str, Any]]:
    candidate_index = {row.candidate_id: row for row in candidates}
    if len(candidate_index) != len(candidates):
        raise ValueError("Gold candidates contain duplicate candidate IDs")
    if len({row.candidate_id for row in gold_labels}) != len(gold_labels):
        raise ValueError("Gold labels contain duplicate candidate IDs")
    if len({row.teacher_id for row in teacher_labels}) != len(teacher_labels):
        raise ValueError("Teacher labels contain duplicate teacher IDs")

    rows: list[dict[str, Any]] = []
    for row in teacher_labels:
        origin: AnnotationOrigin = (
            "deterministic" if row.label_method == "policy_unknown" else "teacher"
        )
        rows.append(
            {
                "candidate_id": row.teacher_id,
                "taxonomy_version": row.taxonomy_version,
                "snapshot": row.snapshot,
                "source": row.source,
                "source_record_ids": row.source_record_ids,
                "raw_text": row.raw_text,
                "source_context": row.source_context,
                "event": row.event,
                "annotation_origin": origin,
                "incident_id": row.incident_id,
                "incident_id_source": _teacher_incident_source(row),
                "reason_text_id": row.reason_text_id,
                "teacher_model": row.model,
                "prompt_version": row.prompt_version,
            }
        )

    for label in gold_labels:
        candidate = candidate_index.get(label.candidate_id)
        if candidate is None:
            raise ValueError(f"Gold label has no candidate: {label.candidate_id}")
        incident_id, incident_source = incident_identity(candidate)
        rows.append(
            {
                "candidate_id": label.candidate_id,
                "taxonomy_version": label.taxonomy_version,
                "snapshot": label.snapshot,
                "source": label.source,
                "source_record_ids": [label.source_record_id],
                "raw_text": label.raw_text or MISSING_REASON_SENTINEL,
                "source_context": candidate.source_context,
                "event": label.event,
                "annotation_origin": "gold",
                "incident_id": incident_id,
                "incident_id_source": incident_source,
                "reason_text_id": reason_text_id(label.raw_text),
                "teacher_model": None,
                "prompt_version": None,
            }
        )
    return rows


def build_phase11_pool(
    teacher_labels: list[TeacherLabelRecord],
    candidates: list[GoldCandidate],
    gold_labels: list[GoldLabelRecord],
) -> tuple[list[Phase11Example], dict[str, list[Phase11Example]]]:
    """Build all-source pool, quarantine gold-connected rows, and split safely."""
    rows = _base_rows(teacher_labels, candidates, gold_labels)
    group_ids = leakage_group_ids(
        [(row["incident_id"], row["reason_text_id"]) for row in rows]
    )
    gold_groups = {
        group_id
        for row, group_id in zip(rows, group_ids)
        if row["annotation_origin"] == "gold"
    }

    eligible_bases: list[dict[str, Any]] = []
    finished: list[Phase11Example] = []
    for row, group_id in zip(rows, group_ids):
        if row["annotation_origin"] == "gold":
            finished.append(
                Phase11Example(
                    **row,
                    split_group_id=group_id,
                    sample_weight=1.0,
                    training_eligible=False,
                    exclusion_reasons=["gold_held_out"],
                    split="gold_evaluation",
                )
            )
        elif group_id in gold_groups:
            finished.append(
                Phase11Example(
                    **row,
                    split_group_id=group_id,
                    sample_weight=1.0,
                    training_eligible=False,
                    exclusion_reasons=["gold_connected_incident_or_reason_text"],
                    split="excluded",
                )
            )
        else:
            row["split_group_id"] = group_id
            eligible_bases.append(row)

    text_incidents: dict[str, set[str]] = defaultdict(set)
    for row in eligible_bases:
        text_incidents[row["reason_text_id"]].add(row["incident_id"])
    eligible = [
        Phase11Example(
            **row,
            sample_weight=1.0 / len(text_incidents[row["reason_text_id"]]),
            training_eligible=True,
            exclusion_reasons=[],
            split="train",  # replaced after grouped allocation
        )
        for row in eligible_bases
    ]
    allocations = grouped_stratified_split(eligible)
    split_rows: dict[str, list[Phase11Example]] = {}
    for split, allocated in allocations.items():
        split_rows[split] = [
            row.model_copy(update={"split": split}) for row in allocated
        ]
        finished.extend(split_rows[split])

    split_rows["gold_evaluation"] = sorted(
        (row for row in finished if row.split == "gold_evaluation"),
        key=lambda row: row.candidate_id,
    )
    split_rows["excluded"] = sorted(
        (row for row in finished if row.split == "excluded"),
        key=lambda row: row.candidate_id,
    )
    return sorted(
        finished, key=lambda row: (row.annotation_origin, row.candidate_id)
    ), split_rows


def _distribution(rows: list[Phase11Example]) -> dict[str, int]:
    return dict(sorted(Counter(row.event.primary_cause for row in rows).items()))


def phase11_metrics(
    pool: list[Phase11Example], split_rows: dict[str, list[Phase11Example]]
) -> dict[str, Any]:
    non_gold = [row for row in pool if row.annotation_origin != "gold"]
    eligible = [row for row in pool if row.training_eligible]
    naive = Counter(row.event.primary_cause for row in non_gold)
    eligible_counts = Counter(row.event.primary_cause for row in eligible)
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "pool_records": len(pool),
        "origin_counts": dict(Counter(row.annotation_origin for row in pool)),
        "origin_category_counts": {
            origin: _distribution(
                [row for row in pool if row.annotation_origin == origin]
            )
            for origin in ("gold", "teacher", "deterministic")
        },
        "naive_non_gold_distribution": dict(sorted(naive.items())),
        "naive_unknown_percentage": 100 * naive["unknown"] / len(non_gold),
        "eligible_records": len(eligible),
        "excluded_non_gold_records": len(split_rows["excluded"]),
        "eligible_distribution": dict(sorted(eligible_counts.items())),
        "eligible_category_count": len(eligible_counts),
        "eligible_majority_percentage": (
            100 * max(eligible_counts.values()) / len(eligible) if eligible else 0.0
        ),
        "splits": {
            split: {
                "records": len(rows),
                "groups": len({row.split_group_id for row in rows}),
                "effective_weight": sum(row.sample_weight for row in rows),
                "categories": _distribution(rows),
            }
            for split, rows in split_rows.items()
        },
        "leakage_checks": {
            "gold_train_group_overlap": len(
                {row.split_group_id for row in split_rows["gold_evaluation"]}
                & {
                    row.split_group_id
                    for split in ("train", "validation", "test")
                    for row in split_rows[split]
                }
            ),
            "cross_training_split_group_overlap": sum(
                len(
                    {row.split_group_id for row in split_rows[left]}
                    & {row.split_group_id for row in split_rows[right]}
                )
                for left, right in (
                    ("train", "validation"),
                    ("train", "test"),
                    ("validation", "test"),
                )
            ),
        },
    }


def write_phase11_outputs(
    output_dir: Path,
    pool: list[Phase11Example],
    split_rows: dict[str, list[Phase11Example]],
    metrics: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_jsonl(output_dir / "combined_pool.jsonl", pool)
    for split, rows in split_rows.items():
        atomic_write_jsonl(output_dir / f"{split}.jsonl", rows)
    atomic_write_text(
        output_dir / "manifest.json",
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n",
    )


def render_phase11_report(metrics: dict[str, Any]) -> str:
    origins = metrics["origin_counts"]
    splits = metrics["splits"]
    lines = [
        "# Phase 11 SLM Distillation Data Report",
        "",
        f"Generated: {metrics['generated_at']}",
        "",
        "## Dataset frozen for Phase 11",
        "",
        f"Combined annotations: **{metrics['pool_records']:,}** — **{origins.get('teacher', 0):,} teacher**, **{origins.get('deterministic', 0):,} deterministic unknown**, and **{origins.get('gold', 0):,} human gold**.",
        "",
        "All records retain `annotation_origin`; raw snapshots, teacher outputs, and human gold labels are unchanged.",
        "",
        "The full 400-record human gold set is reserved as `gold_evaluation` and is never eligible for training.",
        "",
        "## Leakage audit",
        "",
        "Phase 11 uses the Phase 12 connected-component rule: examples sharing either FDA/surrogate `incident_id` or normalized `reason_text_id`, including transitive links, cannot cross data boundaries.",
        "",
        f"This leaves **{metrics['eligible_records']:,}** leakage-safe non-gold examples and quarantines **{metrics['excluded_non_gold_records']:,}** non-gold examples connected to held-out gold.",
        "",
        f"Gold/training group overlap: **{metrics['leakage_checks']['gold_train_group_overlap']}**. Cross-training-split group overlap: **{metrics['leakage_checks']['cross_training_split_group_overlap']}**.",
        "",
        "| Artifact | Records | Connected groups | Effective weight |",
        "|---|---:|---:|---:|",
    ]
    for split in ("train", "validation", "test", "gold_evaluation", "excluded"):
        row = splits[split]
        lines.append(
            f"| `{split}` | {row['records']:,} | {row['groups']:,} | {row['effective_weight']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## Class balance and training-readiness warning",
            "",
            f"Without leakage quarantine, deterministic `unknown` would be **{metrics['naive_unknown_percentage']:.2f}%** of the non-gold pool and risks teaching a broad default at the expense of real causal classes.",
            "",
            f"Under the strict holdout policy, the eligible pool covers only **{metrics['eligible_category_count']}/13 categories**; its majority class is **{metrics['eligible_majority_percentage']:.2f}%** of records.",
            "",
            "| Eligible category | Records |",
            "|---|---:|",
        ]
    )
    for category, count in metrics["eligible_distribution"].items():
        lines.append(f"| `{category}` | {count:,} |")
    lines.extend(
        [
            "",
            "This pool is valid for a leakage-safe experiment, but it is not sufficient to claim broad 13-category supervised coverage. Fine-tuning should not begin without explicitly accepting that limitation or changing the data policy.",
            "",
            "## Model and compute recommendation",
            "",
            "Recommended checkpoint: `Qwen/Qwen2.5-1.5B-Instruct` with 4-bit QLoRA through MLX-LM. Its 1.54B size is a practical middle ground for this 16 GB Apple-silicon machine, its instruction tuning is preferable to a raw pretrained checkpoint for schema-constrained JSON, and its Apache-2.0 license is straightforward. The 0.5B variant should be faster but has less capacity for subtle causal boundaries; a 3B model raises memory, latency, and overfitting risk without fixing missing-category supervision.",
            "",
            "Audited host: MacBook Air (Apple M4, 10 CPU cores, 16 GB unified memory). Local 4-bit LoRA is sufficient; cloud GPU access is optional, not required. Full-precision fine-tuning is not recommended on this machine.",
            "",
            "Estimated local wall time, before benchmarking: about **45–120 minutes per LoRA run**, plus **20–45 minutes** for base and tuned evaluation over 400 gold examples. Allow **2–4 hours end-to-end** for model download, one training run, validation, generation, and reporting; two or three controlled hyperparameter runs may take **4–8 hours** on this fanless laptop.",
            "",
            "The current `medisupply` environment does not yet contain MLX-LM or the Hugging Face training stack. No package or model download has been performed.",
            "",
            "An optional 24 GB L4 cloud instance is currently listed at roughly $0.49/hour, so a conservative one-run budget is about **$1–$3**, including setup/idle time but excluding persistent storage. Verify live pricing before launch.",
            "",
            "References: https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct, https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md, and https://www.runpod.io/pricing",
            "",
            "## Recommended experiment after approval",
            "",
            "1. Evaluate the untouched base model on all 400 gold records.",
            "2. LoRA-tune Qwen2.5-1.5B-Instruct on `train.jsonl`, select only by `validation.jsonl`, and run one final test against all 400 gold records.",
            "3. Validate JSON schema and verbatim evidence, and report primary-cause accuracy overall/per category alongside baseline 79.25% and teacher 95.50%.",
            "4. Measure local p50/p95 latency, throughput, peak memory, model/adapter size, and schema/evidence validation rates.",
            "",
            "The `test.jsonl` artifact is an auxiliary teacher-label holdout. The 400-record `gold_evaluation.jsonl` remains the only human-ground-truth final evaluation set.",
            "",
        ]
    )
    return "\n".join(lines)
