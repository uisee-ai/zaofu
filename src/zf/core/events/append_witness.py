"""Optional acceleration for an already-opened ledger index; missing proof forces reconstruction."""

import os
from pathlib import Path
import json

from zf.core.state.atomic_io import atomic_write_text


def signature(path: Path):
    try:
        s = path.stat()
        return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]
    except FileNotFoundError:
        return []


def witness_path(path):
    return path.parent / "private" / "event-append-witness" / "append.json"


def read_witness(path):
    try:
        data = json.loads(witness_path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def arm_witness(path, stamp):
    witness = witness_path(path)
    witness.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_text(witness, json.dumps({"base": stamp, "head": stamp}))
    os.chmod(witness, 0o600)


def observe_append(path, before):
    # Under the EventLog writer lock. This is strictly an optional proof of
    # append-only growth; a crash or nonparticipating writer forces rechecking.
    record = read_witness(path)
    if not record.get("base") or record.get("head") != before:
        return
    after = signature(path)
    if not (before and after and before[:2] == after[:2] and before[2] < after[2]):
        return
    try:
        atomic_write_text(witness_path(path), json.dumps({"base": record["base"], "head": after}))
        os.chmod(witness_path(path), 0o600)
    except OSError:
        pass
