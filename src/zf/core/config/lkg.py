"""Last-known-good config snapshot support."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


CONFIG_DIR = "config"
LKG_YAML = "last-known-good.yaml"
LKG_HASH = "last-known-good.hash"
VALIDATION_REPORT = "validation-report.json"


def infer_state_dir(config_path: Path) -> Path:
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return config_path.parent / ".zf"
    if not isinstance(raw, dict):
        return config_path.parent / ".zf"
    project = raw.get("project")
    if isinstance(project, dict) and project.get("state_dir"):
        state_dir = Path(str(project["state_dir"]))
        if state_dir.is_absolute():
            return state_dir
        return config_path.parent / state_dir
    return config_path.parent / ".zf"


def promote_last_known_good(
    *,
    config_path: Path,
    state_dir: Path,
    warnings: list[str] | None = None,
) -> Path:
    cfg_dir = state_dir / CONFIG_DIR
    cfg_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = cfg_dir / LKG_YAML
    hash_path = cfg_dir / LKG_HASH
    text = config_path.read_text(encoding="utf-8")
    digest = _sha256_text(text)
    with locked_path(snapshot_path):
        atomic_write_text(snapshot_path, text)
        atomic_write_text(hash_path, digest + "\n")
    write_validation_report(
        state_dir=state_dir,
        config_path=config_path,
        status="valid",
        errors=[],
        warnings=warnings or [],
        sha256=digest,
    )
    return snapshot_path


def write_validation_report(
    *,
    state_dir: Path,
    config_path: Path,
    status: str,
    errors: list[str],
    warnings: list[str] | None = None,
    sha256: str | None = None,
) -> Path:
    cfg_dir = state_dir / CONFIG_DIR
    cfg_dir.mkdir(parents=True, exist_ok=True)
    report_path = cfg_dir / VALIDATION_REPORT
    digest = sha256
    if digest is None and config_path.exists():
        digest = _sha256_text(config_path.read_text(encoding="utf-8"))
    payload: dict[str, Any] = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "config_path": str(config_path),
        "sha256": digest,
        "errors": errors,
        "warnings": warnings or [],
        "last_known_good": str(cfg_dir / LKG_YAML)
        if (cfg_dir / LKG_YAML).exists()
        else None,
    }
    atomic_write_text(
        report_path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    return report_path


def last_known_good_path(state_dir: Path) -> Path:
    return state_dir / CONFIG_DIR / LKG_YAML


def lkg_hint(config_path: Path) -> str | None:
    state_dir = infer_state_dir(config_path)
    path = last_known_good_path(state_dir)
    if path.exists():
        return f"Last-known-good snapshot exists at {path}; it was not used automatically."
    return None


def _sha256_text(text: str) -> str:
    """Deprecation alias — delegates to the canonical helper in
    ``zf.core.security.hash``. Kept for backward compat with internal
    callers in this module; remove after 1 release."""
    from zf.core.security.hash import sha256_text

    return sha256_text(text)
