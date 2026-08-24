"""Compare the two newest complete openFDA snapshots."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    from ._openfda import REPOSITORY_ROOT, SOURCE_CONFIGS
except ImportError:  # Allow: python src/ingestion/diff_snapshots.py
    from _openfda import REPOSITORY_ROOT, SOURCE_CONFIGS


SNAPSHOT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}$")
Record = dict[str, Any]
RecordKey = tuple[str, ...]


def _load_records(snapshot: Path, source: str) -> list[Record]:
    path = snapshot / f"{source}.json"
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        records = payload["results"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(f"Cannot parse {source} records from {path}") from error
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise ValueError(f"{path} does not contain a JSON results list")
    return records


def _value(record: Record, field: str) -> str:
    value = record.get(field)
    return "" if value is None else str(value)


def _shortage_key(record: Record) -> RecordKey:
    return tuple(
        _value(record, field)
        for field in ("package_ndc", "initial_posting_date", "presentation")
    )


def _recall_key(record: Record) -> RecordKey:
    recall_number = _value(record, "recall_number")
    if recall_number:
        return ("recall_number", recall_number)
    return (
        "fallback",
        _value(record, "event_id"),
        _value(record, "recall_initiation_date"),
        _value(record, "recalling_firm"),
        _value(record, "product_description"),
    )


def _index_records(
    records: list[Record], key_fn: Callable[[Record], RecordKey]
) -> dict[RecordKey, Record]:
    index: dict[RecordKey, Record] = {}
    for record in records:
        key = key_fn(record)
        existing = index.get(key)
        if existing is not None and existing != record:
            raise ValueError(f"Non-unique record identity: {key}")
        index[key] = record
    return index


def _shortage_identity(record: Record) -> dict[str, str]:
    return {
        field: _value(record, field)
        for field in ("package_ndc", "initial_posting_date", "presentation")
    }


def find_complete_snapshots(root: Path) -> list[Path]:
    if not root.exists():
        return []
    required = {f"{source}.json" for source in SOURCE_CONFIGS} | {"manifest.json"}
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir()
        and SNAPSHOT_PATTERN.fullmatch(path.name)
        and all((path / filename).is_file() for filename in required)
    )


def compare_snapshots(previous: Path, latest: Path) -> dict[str, Any]:
    previous_shortages = _index_records(_load_records(previous, "shortages"), _shortage_key)
    latest_shortages = _index_records(_load_records(latest, "shortages"), _shortage_key)
    previous_recalls = _index_records(_load_records(previous, "recalls"), _recall_key)
    latest_recalls = _index_records(_load_records(latest, "recalls"), _recall_key)

    new_shortage_keys = sorted(latest_shortages.keys() - previous_shortages.keys())
    removed_shortage_keys = sorted(previous_shortages.keys() - latest_shortages.keys())
    common_shortage_keys = sorted(previous_shortages.keys() & latest_shortages.keys())
    new_recall_keys = sorted(latest_recalls.keys() - previous_recalls.keys())

    resolved_shortages = [
        {
            "change_type": "removed",
            "identity": _shortage_identity(previous_shortages[key]),
            "before": previous_shortages[key],
        }
        for key in removed_shortage_keys
    ]
    for key in common_shortage_keys:
        before = previous_shortages[key]
        after = latest_shortages[key]
        if before.get("status") != after.get("status"):
            resolved_shortages.append(
                {
                    "change_type": "status_changed",
                    "identity": _shortage_identity(after),
                    "before_status": before.get("status"),
                    "after_status": after.get("status"),
                    "before": before,
                    "after": after,
                }
            )

    changed_shortage_fields = []
    for key in common_shortage_keys:
        before = previous_shortages[key]
        after = latest_shortages[key]
        changes = {
            field: {"before": before.get(field), "after": after.get(field)}
            for field in ("shortage_reason", "availability")
            if before.get(field) != after.get(field)
        }
        if changes:
            changed_shortage_fields.append(
                {
                    "identity": _shortage_identity(after),
                    "changes": changes,
                    "before": before,
                    "after": after,
                }
            )

    return {
        "meta": {
            "previous_snapshot": previous.name,
            "latest_snapshot": latest.name,
            "generated_at": datetime.now().astimezone().isoformat(),
        },
        "summary": {
            "new_shortages": len(new_shortage_keys),
            "resolved_shortages": len(resolved_shortages),
            "new_recalls": len(new_recall_keys),
            "changed_shortage_fields": len(changed_shortage_fields),
        },
        "new_shortages": [latest_shortages[key] for key in new_shortage_keys],
        "resolved_shortages": resolved_shortages,
        "new_recalls": [latest_recalls[key] for key in new_recall_keys],
        "changed_shortage_fields": changed_shortage_fields,
    }


def _write_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        try:
            json.dump(report, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    os.chmod(temp_path, 0o644)
    os.replace(temp_path, output_path)


def main() -> Path:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, help="Older snapshot directory")
    parser.add_argument("--latest", type=Path, help="Newer snapshot directory")
    parser.add_argument("--output", type=Path, help="Report JSON path")
    args = parser.parse_args()

    if bool(args.previous) != bool(args.latest):
        parser.error("--previous and --latest must be provided together")
    if args.previous:
        previous, latest = args.previous, args.latest
    else:
        snapshots = find_complete_snapshots(REPOSITORY_ROOT / "data" / "snapshots")
        if len(snapshots) < 2:
            parser.error("At least two complete snapshots are required")
        previous, latest = snapshots[-2:]

    report = compare_snapshots(previous, latest)
    output_path = args.output or latest / f"diff_from_{previous.name}.json"
    _write_report(report, output_path)

    summary = report["summary"]
    print(f"Compared {previous.name} -> {latest.name}")
    print(f"New shortages: {summary['new_shortages']:,}")
    print(f"Resolved shortages: {summary['resolved_shortages']:,}")
    print(f"New recalls: {summary['new_recalls']:,}")
    print(f"Changed shortage fields: {summary['changed_shortage_fields']:,}")
    print(f"Report: {output_path}")
    return output_path


if __name__ == "__main__":
    main()
