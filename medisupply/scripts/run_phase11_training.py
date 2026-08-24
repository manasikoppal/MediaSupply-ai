#!/usr/bin/env python3
"""Run the approved MLX-LM LoRA configuration and persist timing metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPOSITORY_ROOT / "configs" / "phase11_qwen_lora.yaml"
DEFAULT_LOG = REPOSITORY_ROOT / "logs" / "phase11_training.log"
DEFAULT_METADATA = REPOSITORY_ROOT / "artifacts" / "phase11" / "training_run.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    args = parser.parse_args()

    command = [sys.executable, "-m", "mlx_lm.lora", "--config", str(args.config)]
    args.log.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().astimezone().isoformat()
    started = time.perf_counter()
    with args.log.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=REPOSITORY_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
        return_code = process.wait()
    duration = time.perf_counter() - started
    metadata = {
        "experiment": "phase11_feasibility_3_of_13",
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(),
        "duration_seconds": duration,
        "return_code": return_code,
        "command": command,
        "config": str(args.config),
        "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
        "log": str(args.log),
    }
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    if return_code:
        raise SystemExit(return_code)
    print(f"Training metadata: {args.metadata}")


if __name__ == "__main__":
    main()
