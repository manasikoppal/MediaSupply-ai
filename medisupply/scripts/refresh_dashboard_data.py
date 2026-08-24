#!/usr/bin/env python3
"""Validate and atomically promote Phase 13 output to the live dashboard."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPOSITORY_ROOT / "reports" / "intelligence_engine.json"
DEFAULT_DESTINATION = REPOSITORY_ROOT / "data" / "dashboard" / "current.json"
REQUIRED_SNAPSHOT_FILES = {
    "shortages.json",
    "recalls.json",
    "ndc.json",
    "drugsfda.json",
    "manifest.json",
}
VALID_UNKNOWN_REASONS = {
    "reserved_for_evaluation",
    "fda_reason_not_provided",
    "needs_teacher_labeling",
}


def _validate(payload: Any, repository_root: Path) -> tuple[str, int]:
    if not isinstance(payload, dict):
        raise TypeError("Intelligence artifact must be a JSON object")
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, str) or not snapshot:
        raise ValueError("Intelligence artifact has no snapshot identifier")
    snapshot_dir = repository_root / "data" / "snapshots" / snapshot
    missing = sorted(
        name for name in REQUIRED_SNAPSHOT_FILES if not (snapshot_dir / name).is_file()
    )
    if missing:
        raise ValueError(
            f"Snapshot {snapshot} is incomplete; missing: {', '.join(missing)}"
        )

    database_value = payload.get("database")
    if not isinstance(database_value, str):
        raise TypeError("Intelligence artifact has no knowledge-graph database path")
    database = (repository_root / database_value).resolve()
    try:
        database.relative_to(repository_root.resolve())
    except ValueError as exc:
        raise ValueError("Knowledge-graph path escapes the repository") from exc
    if not database.is_file() or database.parent.name != snapshot:
        raise ValueError(
            f"Knowledge graph does not match promoted snapshot {snapshot}: {database}"
        )

    records = payload.get("all_scored_current_shortages")
    if not isinstance(records, list) or not records:
        raise ValueError("Intelligence artifact has no scored current shortages")
    for record in records:
        if not isinstance(record, dict):
            raise TypeError("Intelligence artifact contains a non-object record")
        if record.get("primary_cause") == "unknown":
            reason = record.get("unknown_reason")
            if reason not in VALID_UNKNOWN_REASONS:
                raise ValueError(
                    f"Unknown cause lacks a supported provenance flag: {reason!r}"
                )
    return snapshot, len(records)


def promote_dashboard_data(
    source: Path = DEFAULT_SOURCE,
    destination: Path = DEFAULT_DESTINATION,
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> tuple[str, int]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    snapshot, record_count = _validate(payload, repository_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return snapshot, record_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    snapshot, record_count = promote_dashboard_data(args.source, args.destination)
    print(
        f"Promoted dashboard snapshot {snapshot} with "
        f"{record_count:,} scored current-shortage records"
    )


if __name__ == "__main__":
    main()
