#!/usr/bin/env python3
"""Build incident-aware Phase 12 modeling splits after gold labeling completes."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from src.models.gold_dataset import GoldCandidate, GoldLabelRecord, load_jsonl
from src.models.phase12_dataset import (
    DEFAULT_MAX_EVENTS_PER_TEXT,
    Phase12Example,
    atomic_write_text,
    build_phase12_examples,
    candidate_support,
    cap_examples,
    category_support,
    grouped_stratified_split,
    recall_duplication_metrics,
    render_phase12_report,
    split_metrics,
    write_phase12_outputs,
)

DEFAULT_CANDIDATES = REPOSITORY_ROOT / "data" / "gold" / "candidates.jsonl"
DEFAULT_LABELS = REPOSITORY_ROOT / "data" / "gold" / "labeled.jsonl"
DEFAULT_OUTPUT = REPOSITORY_ROOT / "data" / "modeling" / "phase12"
DEFAULT_REPORT_JSON = REPOSITORY_ROOT / "reports" / "phase12_data_quality.json"
DEFAULT_REPORT_MD = REPOSITORY_ROOT / "reports" / "phase12_data_quality.md"


def _latest_snapshot() -> Path:
    snapshots = REPOSITORY_ROOT / "data" / "snapshots"
    candidates = sorted(
        path for path in snapshots.iterdir() if (path / "recalls.json").exists()
    )
    if not candidates:
        raise RuntimeError("No snapshot containing recalls.json was found")
    return candidates[-1]


def _load_recall_rows(snapshot: Path) -> list[dict]:
    with (snapshot / "recalls.json").open(encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("results") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise TypeError(f"Unexpected recall payload in {snapshot / 'recalls.json'}")
    return rows


def _labeled_support(examples: list[Phase12Example]) -> dict:
    return category_support(
        [
            (row.event.primary_cause, row.incident_id, row.reason_text_id)
            for row in examples
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--weighting-mode",
        choices=("weights", "cap"),
        default="weights",
        help="Use 1/N sample weights, or deterministic capped rows for trainers without weight support",
    )
    parser.add_argument(
        "--max-events-per-text",
        type=int,
        default=DEFAULT_MAX_EVENTS_PER_TEXT,
    )
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    args = parser.parse_args()

    if args.max_events_per_text < 1:
        parser.error("--max-events-per-text must be positive")
    snapshot = args.snapshot or _latest_snapshot()
    candidates = load_jsonl(args.candidates, GoldCandidate)
    labels = load_jsonl(args.labels, GoldLabelRecord)
    if not candidates:
        raise RuntimeError("Candidate queue is empty")
    recall_rows = _load_recall_rows(snapshot)
    examples = build_phase12_examples(candidates, labels)

    candidate_ids = {candidate.candidate_id for candidate in candidates}
    label_ids = {label.candidate_id for label in labels}
    complete = label_ids == candidate_ids
    splits = None
    if complete:
        modeling_examples = (
            examples
            if args.weighting_mode == "weights"
            else cap_examples(
                examples,
                max_events_per_text=args.max_events_per_text,
            )
        )
        splits = grouped_stratified_split(modeling_examples)
        write_phase12_outputs(
            args.output_dir,
            splits,
            weighting_mode=args.weighting_mode,
            max_events_per_text=args.max_events_per_text,
        )

    metrics = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "snapshot": snapshot.name,
        "weighting_mode": args.weighting_mode,
        "max_events_per_text": args.max_events_per_text,
        "gold_readiness": {
            "candidates": len(candidates),
            "labels": len(labels),
            "remaining": len(candidates) - len(labels),
            "splits_materialized": complete,
        },
        "raw_recall_duplication": recall_duplication_metrics(recall_rows),
        "candidate_support": candidate_support(candidates),
        "labeled_support": _labeled_support(examples),
        "splits": split_metrics(splits) if splits else None,
    }
    atomic_write_text(
        args.report_json,
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n",
    )
    atomic_write_text(args.report_md, render_phase12_report(metrics))

    readiness = metrics["gold_readiness"]
    print(f"Phase 12 report: {args.report_md}")
    print(
        f"Gold readiness: {readiness['labels']}/{readiness['candidates']} labels; "
        f"{readiness['remaining']} remaining"
    )
    if complete:
        print(f"Grouped modeling splits: {args.output_dir}")
    else:
        print("Grouped splits were not materialized from incomplete gold labels.")


if __name__ == "__main__":
    main()
