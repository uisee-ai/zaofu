"""Synthetic CPU checksum workload; checkpoints are task output, not ZaoFu state."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time
from uuid import uuid4


def read_chunk(path: Path, seed: str, index: int, seconds: float) -> dict:
    row = json.loads(path.read_text())
    expected = hashlib.sha256(hashlib.sha256(f"{seed}:{index}".encode()).digest() * 32768).hexdigest()
    assert row["seed"] == seed and row["index"] == index
    assert row["digest"] == expected
    assert type(row["passes"]) is int and row["passes"] > 0
    assert math.isfinite(row["cpu_seconds"]) and row["cpu_seconds"] >= seconds
    assert math.isfinite(row["wall_seconds"]) and row["wall_seconds"] >= seconds
    assert type(row["pid"]) is int and row["pid"] > 0 and row["invocation"]
    return row


def atomic(path: Path, row: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(row, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def run(root: Path, seed: str, seconds: float) -> None:
    root.mkdir(exist_ok=True)
    invocation = uuid4().hex
    # Validate the complete committed prefix before touching any output.
    missing = False
    for index in range(3):
        path = root / f"chunk-{index}.json"
        if path.exists():
            assert not missing, "checkpoint gap"
            read_chunk(path, seed, index, seconds)
        else:
            missing = True
    for index in range(3):
        path = root / f"chunk-{index}.json"
        if path.exists():
            continue
        atomic(root / "progress.json", {"index": index, "pid": os.getpid(), "invocation": invocation})
        block = hashlib.sha256(f"{seed}:{index}".encode()).digest() * 32768
        start_cpu, start_wall = time.process_time(), time.monotonic()
        passes = 0
        while time.process_time() - start_cpu < seconds:
            digest = hashlib.sha256(block).hexdigest()
            passes += 1
        atomic(path, {"seed": seed, "index": index, "digest": digest, "passes": passes,
                      "cpu_seconds": time.process_time() - start_cpu,
                      "wall_seconds": time.monotonic() - start_wall,
                      "pid": os.getpid(), "invocation": invocation})
    atomic(root / "progress.json", {"index": 3, "pid": os.getpid(), "invocation": invocation})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "verify"))
    parser.add_argument("--seed", required=True)
    parser.add_argument("--seconds", type=float, required=True)
    args = parser.parse_args()
    assert math.isfinite(args.seconds) and 0 < args.seconds <= 60
    root = Path("long-task")
    if args.action == "run":
        run(root, args.seed, args.seconds)
    for index in range(3):
        read_chunk(root / f"chunk-{index}.json", args.seed, index, args.seconds)
    print("three validated chunks")


if __name__ == "__main__":
    main()
