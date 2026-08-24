#!/usr/bin/env python3
"""Evaluate base or LoRA-tuned Phase 11 SLM against all 400 human gold rows."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from src.models.gold_dataset import load_jsonl
from src.models.phase11_dataset import Phase11Example
from src.models.phase11_training import (
    TRAINED_CATEGORIES,
    evaluation_metrics,
    input_messages,
    validate_prediction,
)
from src.models.phase12_dataset import atomic_write_text

DEFAULT_MODEL = REPOSITORY_ROOT / "artifacts" / "phase11" / "qwen2.5-1.5b-instruct-4bit"
DEFAULT_ADAPTER = REPOSITORY_ROOT / "artifacts" / "phase11" / "adapters"
DEFAULT_GOLD = (
    REPOSITORY_ROOT / "data" / "modeling" / "phase11" / "gold_evaluation.jsonl"
)
DEFAULT_OUTPUT = REPOSITORY_ROOT / "data" / "modeling" / "phase11" / "evaluation"


def _load_existing(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                rows[row["candidate_id"]] = row
    return rows


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _latency(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "mean_seconds": statistics.fmean(values),
        "p50_seconds": statistics.median(values),
        "p95_seconds": ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("base", "tuned"), required=True)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument(
        "--metrics-only",
        action="store_true",
        help="Rebuild metrics from checkpointed predictions without loading MLX",
    )
    args = parser.parse_args()

    adapter = str(args.adapter) if args.variant == "tuned" else None
    rows = load_jsonl(args.gold, Phase11Example)
    if args.limit:
        rows = rows[: args.limit]
    predictions_path = args.output_dir / f"{args.variant}_predictions.jsonl"
    metrics_path = args.output_dir / f"{args.variant}_metrics.json"
    existing = _load_existing(predictions_path)

    if not args.metrics_only:
        from mlx_lm import load, stream_generate

        model, tokenizer = load(str(args.model), adapter_path=adapter)
        for index, row in enumerate(rows, 1):
            if row.candidate_id in existing:
                continue
            prompt = tokenizer.apply_chat_template(
                input_messages(row), tokenize=False, add_generation_prompt=True
            )
            started = time.perf_counter()
            pieces = []
            last_response = None
            for response in stream_generate(
                model, tokenizer, prompt, max_tokens=args.max_tokens
            ):
                pieces.append(response.text)
                last_response = response
            latency = time.perf_counter() - started
            raw_output = "".join(pieces)
            event, validation_error = validate_prediction(raw_output, row.raw_text)
            predicted = event.primary_cause if event else None
            supported = row.event.primary_cause in TRAINED_CATEGORIES
            correct = predicted == row.event.primary_cause
            if not supported:
                outcome = "unsupported_category"
            elif validation_error:
                outcome = "supported_invalid_output"
            elif correct:
                outcome = "supported_correct"
            else:
                outcome = "supported_misclassified"
            result = {
                "candidate_id": row.candidate_id,
                "variant": args.variant,
                "human_primary_cause": row.event.primary_cause,
                "predicted_primary_cause": predicted,
                "primary_cause_correct": correct,
                "outcome_type": outcome,
                "validation_error": validation_error,
                "raw_output": raw_output,
                "latency_seconds": latency,
                "prompt_tokens": last_response.prompt_tokens if last_response else None,
                "generation_tokens": last_response.generation_tokens
                if last_response
                else None,
                "prompt_tokens_per_second": last_response.prompt_tps
                if last_response
                else None,
                "generation_tokens_per_second": last_response.generation_tps
                if last_response
                else None,
                "peak_memory_gb": last_response.peak_memory if last_response else None,
            }
            _append(predictions_path, result)
            existing[row.candidate_id] = result
            print(
                f"[{index:03d}/{len(rows):03d}] {row.candidate_id}: "
                f"{predicted or 'INVALID'} ({outcome}, {latency:.2f}s)",
                flush=True,
            )

    completed = [
        existing[row.candidate_id] for row in rows if row.candidate_id in existing
    ]
    metrics = evaluation_metrics(completed)
    latencies = [row["latency_seconds"] for row in completed]
    metrics.update(
        {
            "variant": args.variant,
            "model_path": str(args.model),
            "adapter_path": adapter,
            "completed": len(completed),
            "latency": _latency(latencies) if latencies else None,
            "mean_generation_tokens_per_second": statistics.fmean(
                row["generation_tokens_per_second"] for row in completed
            )
            if completed
            else None,
            "peak_memory_gb": max(row["peak_memory_gb"] for row in completed)
            if completed
            else None,
        }
    )
    atomic_write_text(metrics_path, json.dumps(metrics, indent=2) + "\n")
    print(f"Metrics: {metrics_path}")


if __name__ == "__main__":
    main()
