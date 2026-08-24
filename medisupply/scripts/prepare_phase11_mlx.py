#!/usr/bin/env python3
"""Convert Phase 11 split artifacts into prompt-masked MLX-LM chat data."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from src.models.gold_dataset import load_jsonl
from src.models.phase11_dataset import Phase11Example
from src.models.phase11_training import prompt_hash, training_messages
from src.models.phase12_dataset import atomic_write_text

DEFAULT_INPUT = REPOSITORY_ROOT / "data" / "modeling" / "phase11"
DEFAULT_OUTPUT = DEFAULT_INPUT / "mlx"
CLASS_REPEATS = {
    "manufacturing_quality_problem": 1,
    "labeling_packaging_error": 3,
    "regulatory_noncompliance": 3,
}


def _cap_exact_text(rows: list[Phase11Example]) -> list[Phase11Example]:
    representatives = {}
    for row in sorted(rows, key=lambda value: value.candidate_id):
        representatives.setdefault(row.reason_text_id, row)
    return list(representatives.values())


def _write_chat(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    temp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    manifest = {
        "prompt_sha256": prompt_hash(),
        "class_repeats": CLASS_REPEATS,
        "splits": {},
    }
    for source_name, target_name in (
        ("train", "train"),
        ("validation", "valid"),
        ("test", "test"),
    ):
        rows = load_jsonl(args.input_dir / f"{source_name}.jsonl", Phase11Example)
        capped = _cap_exact_text(rows)
        output = []
        for row in capped:
            repeats = (
                CLASS_REPEATS[row.event.primary_cause] if source_name == "train" else 1
            )
            for _ in range(repeats):
                output.append({"messages": training_messages(row)})
        _write_chat(args.output_dir / f"{target_name}.jsonl", output)
        manifest["splits"][target_name] = {
            "source_records": len(rows),
            "unique_reason_texts": len(capped),
            "training_rows_after_class_repeats": len(output),
            "unique_category_counts": dict(
                Counter(row.event.primary_cause for row in capped)
            ),
            "training_category_counts": dict(
                Counter(
                    row.event.primary_cause
                    for row in capped
                    for _ in range(
                        CLASS_REPEATS[row.event.primary_cause]
                        if source_name == "train"
                        else 1
                    )
                )
            ),
        }
    atomic_write_text(
        args.output_dir / "manifest.json",
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )
    print(f"MLX-LM data: {args.output_dir}")
    print(json.dumps(manifest["splits"], indent=2))


if __name__ == "__main__":
    main()
