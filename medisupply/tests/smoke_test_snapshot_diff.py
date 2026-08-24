"""Manual smoke test for shortage status change detection."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INGESTION_DIR = REPOSITORY_ROOT / "src" / "ingestion"
SNAPSHOTS_ROOT = REPOSITORY_ROOT / "data" / "snapshots"


def shortage_key(record: dict[str, object]) -> tuple[str, str, str]:
    return tuple(
        "" if record.get(field) is None else str(record[field])
        for field in ("package_ndc", "initial_posting_date", "presentation")
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_snapshot() -> Path:
    sys.path.insert(0, str(INGESTION_DIR))
    from diff_snapshots import find_complete_snapshots

    snapshots = find_complete_snapshots(SNAPSHOTS_ROOT)
    if not snapshots:
        raise RuntimeError("No complete snapshot is available to test")
    return snapshots[-1]


def main() -> int:
    original = latest_snapshot()
    original_shortages = original / "shortages.json"
    original_checksum = sha256(original_shortages)

    with tempfile.TemporaryDirectory(prefix="medisupply-diff-smoke-") as directory:
        modified = Path(directory) / f"{original.name}_modified"
        shutil.copytree(original, modified)

        modified_shortages = modified / "shortages.json"
        with modified_shortages.open(encoding="utf-8") as handle:
            payload = json.load(handle)

        records = payload["results"]
        key_counts = Counter(shortage_key(record) for record in records)
        target = next(
            record
            for record in records
            if record.get("status") == "Current" and key_counts[shortage_key(record)] == 1
        )
        target_key = shortage_key(target)
        target["status"] = "Resolved"

        with modified_shortages.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")

        report_path = Path(directory) / "diff_report.json"
        command = [
            sys.executable,
            str(INGESTION_DIR / "diff_snapshots.py"),
            "--previous",
            str(original),
            "--latest",
            str(modified),
            "--output",
            str(report_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        if completed.returncode != 0:
            print("Detected status change: NO (diff command failed)")
            return 1

        with report_path.open(encoding="utf-8") as handle:
            report = json.load(handle)
        detected = any(
            item.get("change_type") == "status_changed"
            and item.get("before_status") == "Current"
            and item.get("after_status") == "Resolved"
            and shortage_key(item["after"]) == target_key
            for item in report["resolved_shortages"]
        )

    original_unchanged = sha256(original_shortages) == original_checksum
    print(f"Detected Current -> Resolved change: {'YES' if detected else 'NO'}")
    print(f"Original snapshot unchanged: {'YES' if original_unchanged else 'NO'}")
    return 0 if detected and original_unchanged else 1


if __name__ == "__main__":
    raise SystemExit(main())
