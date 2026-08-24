#!/usr/bin/env python3
"""Build the Phase 11 combined pool and leakage-safe split artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from src.models.gold_dataset import GoldCandidate, GoldLabelRecord, load_jsonl
from src.models.phase11_dataset import (
    build_phase11_pool,
    phase11_metrics,
    render_phase11_report,
    write_phase11_outputs,
)
from src.models.phase12_dataset import atomic_write_text
from src.models.teacher_labeling import TeacherLabelRecord

DEFAULT_TEACHER = REPOSITORY_ROOT / "data" / "teacher" / "labeled.jsonl"
DEFAULT_CANDIDATES = REPOSITORY_ROOT / "data" / "gold" / "candidates.jsonl"
DEFAULT_GOLD = REPOSITORY_ROOT / "data" / "gold" / "labeled.jsonl"
DEFAULT_OUTPUT = REPOSITORY_ROOT / "data" / "modeling" / "phase11"
DEFAULT_REPORT_JSON = REPOSITORY_ROOT / "reports" / "phase11_data_quality.json"
DEFAULT_REPORT_MD = REPOSITORY_ROOT / "reports" / "phase11_data_quality.md"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--teacher-labels", type=Path, default=DEFAULT_TEACHER)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--gold-labels", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    args = parser.parse_args()

    teacher = load_jsonl(args.teacher_labels, TeacherLabelRecord)
    candidates = load_jsonl(args.candidates, GoldCandidate)
    gold = load_jsonl(args.gold_labels, GoldLabelRecord)
    if len(gold) != 400 or len(candidates) != 400:
        raise RuntimeError("Phase 11 requires the complete 400-record human gold set")

    pool, splits = build_phase11_pool(teacher, candidates, gold)
    metrics = phase11_metrics(pool, splits)
    write_phase11_outputs(args.output_dir, pool, splits, metrics)
    atomic_write_text(
        args.report_json,
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n",
    )
    atomic_write_text(args.report_md, render_phase11_report(metrics))

    print(f"Combined Phase 11 pool: {len(pool):,} records")
    print(f"Leakage-safe non-gold pool: {metrics['eligible_records']:,} records")
    print(f"Held-out human gold: {len(splits['gold_evaluation']):,} records")
    print(f"Report: {args.report_md}")


if __name__ == "__main__":
    main()
