"""Fetch all configured openFDA sources into one hourly snapshot."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

try:
    from ._openfda import REPOSITORY_ROOT, SOURCE_CONFIGS, fetch_source
except ImportError:  # Allow: python src/ingestion/run_ingestion.py
    from _openfda import REPOSITORY_ROOT, SOURCE_CONFIGS, fetch_source


def run_ingestion(now: datetime | None = None) -> Path:
    started_at = (now or datetime.now().astimezone()).astimezone()
    snapshot_name = started_at.strftime("%Y-%m-%d_%H")
    snapshots_root = REPOSITORY_ROOT / "data" / "snapshots"
    snapshots_root.mkdir(parents=True, exist_ok=True)
    final_dir = snapshots_root / snapshot_name
    if final_dir.exists():
        raise FileExistsError(
            f"Snapshot {final_dir} already exists; wait for the next hourly slot to preserve history"
        )

    temp_dir = Path(tempfile.mkdtemp(prefix=f".{snapshot_name}.", dir=snapshots_root))
    counts: dict[str, int] = {}
    try:
        for source in SOURCE_CONFIGS:
            _, count = fetch_source(source, temp_dir / f"{source}.json")
            counts[source] = count
            print(f"{source}: {count:,} records", flush=True)

        completed_at = datetime.now().astimezone()
        manifest = {
            "snapshot": snapshot_name,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "sources": {
                source: {
                    "endpoint": SOURCE_CONFIGS[source][0],
                    "file": f"{source}.json",
                    "record_count": counts[source],
                }
                for source in SOURCE_CONFIGS
            },
        }
        manifest_path = temp_dir / "manifest.json"
        with manifest_path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.chmod(temp_dir, 0o755)
        os.replace(temp_dir, final_dir)
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    print(f"Snapshot complete: {final_dir}", flush=True)
    return final_dir


if __name__ == "__main__":
    run_ingestion()
