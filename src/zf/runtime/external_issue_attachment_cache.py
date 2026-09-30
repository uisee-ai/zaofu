"""Reusable attachment capture projection, validated against evidence sidecars."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from zf.core.state.atomic_io import atomic_write_text


def cache_path(state_dir: Path, key: tuple[str, str]) -> Path:
    digest = hashlib.sha256(json.dumps(key).encode()).hexdigest()
    return state_dir / "issue-triage" / "attachment-cache" / f"{digest}.json"


def load_capture(state_dir: Path, key: tuple[str, str], now: datetime) -> list[dict] | None:
    try:
        value = json.loads(cache_path(state_dir, key).read_text())
        age = now.timestamp() - float(value["captured_at"])
        if not 0 <= age < (86400 if value["complete"] else 300):
            return None
        descriptors = value["descriptors"]
        if not isinstance(descriptors, list):
            return None
        root = (state_dir / "artifacts" / "external-issues" / "attachments").resolve()
        for descriptor in descriptors:
            path = (state_dir / descriptor["ref"]).resolve()
            if root not in path.parents or not path.is_file():
                return None
            if hashlib.sha256(path.read_bytes()).hexdigest() != descriptor["sha256"]:
                return None
        return descriptors
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_capture(state_dir: Path, key: tuple[str, str], now: datetime,
                 descriptors: list[dict], *, complete: bool) -> None:
    atomic_write_text(cache_path(state_dir, key), json.dumps({
        "captured_at": now.timestamp(), "complete": complete,
        "descriptors": descriptors,
    }, ensure_ascii=False))
