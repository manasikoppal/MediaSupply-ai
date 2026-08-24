#!/usr/bin/env python3
"""Build the final Phase 11 feasibility report from saved evaluation artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from src.models.phase12_dataset import atomic_write_text

DEFAULT_DATA = REPOSITORY_ROOT / "data" / "modeling" / "phase11"
DEFAULT_REPORT = REPOSITORY_ROOT / "reports" / "phase11_distillation.md"
DEFAULT_JSON = REPOSITORY_ROOT / "reports" / "phase11_distillation.json"
DEFAULT_TRAINING = REPOSITORY_ROOT / "artifacts" / "phase11" / "training_run.json"
DEFAULT_ADAPTER = REPOSITORY_ROOT / "artifacts" / "phase11" / "adapters"
DEFAULT_MODEL = REPOSITORY_ROOT / "artifacts" / "phase11" / "qwen2.5-1.5b-instruct-4bit"


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _format_bytes(value: int) -> str:
    return f"{value / 1024 / 1024:.1f} MiB"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()

    data_quality = _load(args.data_dir / "manifest.json")
    mlx_manifest = _load(args.data_dir / "mlx" / "manifest.json")
    base = _load(args.data_dir / "evaluation" / "base_metrics.json")
    tuned = _load(args.data_dir / "evaluation" / "tuned_metrics.json")
    training = _load(DEFAULT_TRAINING)
    if not data_quality or not mlx_manifest:
        raise RuntimeError("Phase 11 data artifacts are missing")

    result = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "experiment_status": "complete"
        if tuned and tuned.get("completed") == 400
        else "incomplete",
        "scope_statement": (
            "Trained on a leakage-safe subset covering 3 of 13 categories due to data "
            "availability, not a complete distilled classifier."
        ),
        "data_quality": data_quality,
        "mlx_data": mlx_manifest,
        "training": training,
        "base_evaluation": base,
        "tuned_evaluation": tuned,
        "model_size_bytes": _size(DEFAULT_MODEL),
        "adapter_size_bytes": _size(DEFAULT_ADAPTER),
        "comparators": {
            "phase8_rule_baseline_accuracy_percentage": 79.25,
            "claude_teacher_accuracy_percentage": 95.50,
        },
    }
    atomic_write_text(args.json, json.dumps(result, indent=2) + "\n")

    lines = [
        "# Phase 11 SLM Distillation Feasibility Report",
        "",
        f"Generated: {result['generated_at']}",
        "",
        "## Scope and conclusion",
        "",
        f"**{result['scope_statement']}**",
        "",
        "This experiment measures whether a small local model can learn the output contract and the three supported causal categories. Results for every other category are explicitly marked `unsupported_category`; they are not presented as ordinary classification mistakes from a fully supervised model.",
        "",
        "## Same-gold-set comparison",
        "",
        "| Model | Gold records | Primary-cause accuracy |",
        "|---|---:|---:|",
        "| Phase 8 rule baseline | 400 | 79.25% |",
        "| Claude teacher | 400 | 95.50% |",
    ]
    if base:
        lines.append(
            f"| Qwen2.5-1.5B zero-shot | {base['records']} | {base['accuracy_percentage']:.2f}% |"
        )
    if tuned:
        lines.append(
            f"| Phase 11 Qwen2.5-1.5B QLoRA | {tuned['records']} | {tuned['accuracy_percentage']:.2f}% |"
        )
    else:
        lines.append("| Phase 11 Qwen2.5-1.5B QLoRA | pending | pending |")

    if tuned:
        supported = tuned["supported_categories"]
        unsupported = tuned["unsupported_categories"]
        baseline_gap = tuned["accuracy_percentage"] - 79.25
        teacher_gap = tuned["accuracy_percentage"] - 95.50
        lines.extend(
            [
                "",
                f"The small model is **{abs(baseline_gap):.2f} percentage points {'above' if baseline_gap >= 0 else 'below'}** the rule baseline and **{abs(teacher_gap):.2f} points {'above' if teacher_gap >= 0 else 'below'}** the teacher on the same gold set. It is not a production replacement.",
                "",
                "## Gold accuracy by category",
                "",
                f"Supported-category aggregate: **{supported['correct']}/{supported['records']} ({supported['accuracy_percentage']:.2f}%)**.",
                "",
                f"Explicit unsupported-category aggregate: **{unsupported['correct']}/{unsupported['records']} ({unsupported['accuracy_percentage']:.2f}%)**. Any correct predictions here are zero-shot carryover, not evidence of supervised category coverage.",
                "",
                "| Human category | Outcome type | Records | Correct | Accuracy | Predictions |",
                "|---|---|---:|---:|---:|---|",
            ]
        )
        for category, row in tuned["by_category"].items():
            predictions = ", ".join(
                f"{name}: {count}"
                for name, count in sorted(row["predicted_categories"].items())
            ).replace("|", "\\|")
            accuracy = (
                f"{row['accuracy_percentage']:.2f}%"
                if row["accuracy_percentage"] is not None
                else "N/A"
            )
            lines.append(
                f"| `{category}` | `{row['support_outcome']}` | {row['records']} | {row['correct']} | {accuracy} | {predictions or 'none'} |"
            )
        lines.extend(
            [
                "",
                f"Strict JSON/schema/evidence validation: **{tuned['valid_json_schema_evidence']}/{tuned['records']}**.",
                "",
                "## Latency and footprint",
                "",
                f"Local tuned inference latency: mean **{tuned['latency']['mean_seconds']:.2f}s**, p50 **{tuned['latency']['p50_seconds']:.2f}s**, p95 **{tuned['latency']['p95_seconds']:.2f}s** per record.",
                "",
                f"Generation throughput: **{tuned['mean_generation_tokens_per_second']:.2f} tokens/s**; measured peak MLX memory: **{tuned['peak_memory_gb']:.2f} GB**.",
                "",
                f"Quantized base model: **{_format_bytes(result['model_size_bytes'])}**; LoRA adapter/checkpoints: **{_format_bytes(result['adapter_size_bytes'])}**.",
                "",
                "Local inference has no marginal API charge after deployment and does not require network access. Phase 10 teacher calls averaged roughly $0.007–$0.008 per validated record; teacher latency was not instrumented, so no unsupported latency comparison is claimed.",
            ]
        )
    if training:
        status = training.get(
            "status", "completed" if training["return_code"] == 0 else "interrupted"
        )
        lines.extend(
            [
                "",
                "## Training run",
                "",
                f"Timed local MLX-LM execution: **{training['duration_seconds'] / 60:.1f} minutes**; status: **`{status}`**.",
                "",
                f"Selected checkpoint: iteration **{training.get('selected_checkpoint_iteration', 'final')}**. Validation loss moved from **{training.get('initial_validation_loss', 0):.3f}** to **{training.get('selected_validation_loss', 0):.3f}**. The longer configured run was intentionally stopped after the validated adapter was saved.",
                "",
                f"Reproduction configuration: `{training.get('reproduction_config', training['config'])}`.",
                "",
                "The training input capped repeated exact reason text to one representative, then repeated only minority-class rows inside training. Validation, auxiliary test, and gold evaluation were not oversampled.",
            ]
        )
    lines.extend(
        [
            "",
            "## What a balanced production version needs",
            "",
            "- More incident-independent teacher or human labels for the ten unsupported categories. Exact duplicate filings do not add independent supervision.",
            "- A practical next target is at least 75–100 distinct incidents and distinct reason texts per category before splitting, with additional examples for boundary-heavy categories such as shipping delay versus temperature-related quality problems.",
            "- Preserve a human-only final test set. New teacher labels should be grouped by `incident_id` and `reason_text_id` before sampling so train/validation/test remain leakage-free.",
            "- Add genuine `unknown` examples whose holdout policy does not connect every missing-reason sentinel into the gold component—either by collecting a separate policy-training corpus or by approving a versioned exception for non-semantic missing-text sentinels.",
            "- Retrain with class-aware sampling and early stopping only after each category has independent train and validation support. Repeat the identical 400-gold evaluation for an apples-to-apples comparison.",
            "",
        ]
    )
    atomic_write_text(args.report, "\n".join(lines))
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
