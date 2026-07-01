"""Shared constants and helpers for Project Spine Review."""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.workspace import stable_project_id


SCHEMA_VERSION = "project-spine-review.v1"
REFLECTION_SCHEMA_VERSION = "spine-review.reflection.v1"
INSIGHT_SCHEMA_VERSION = "spine-review-insight.v1"
PROPOSAL_SCHEMA_VERSION = "spine-review.proposal.v1"
ARTIFACT_EVENT = "spine_review.artifact.created"
PROPOSAL_EVENT = "spine_review.proposal.created"
FAULT_EVENT_TYPES = {
    "orchestrator.dispatch_failed",
    "worker.stuck",
    "task.done.blocked",
    "gate.failed",
    "discriminator.failed",
}
WORKFLOW_REQUIRED_EVENTS = (
    "task.dispatched",
    "arch.proposal.done",
    "design.critique.done",
    "dev.build.done",
    "static_gate.passed",
    "review.approved",
    "test.passed",
    "judge.passed",
)


class SpineReviewError(RuntimeError):
    """Raised for fail-closed review context / artifact errors."""


def read_events(state_dir: Path, *, config: ZfConfig | None) -> list[ZfEvent]:
    path = state_dir / "events.jsonl"
    if not path.exists():
        return []
    try:
        return event_log_from_project(state_dir, config=config, warn=False).read_all()
    except Exception:
        return []


def artifact_ref(kind: str, path: Path, *, state_dir: Path) -> dict[str, str]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        rel = str(path.relative_to(state_dir))
    except ValueError:
        rel = str(path)
    return {
        "kind": kind,
        "path": rel,
        "sha256": digest,
    }


def project_id(*, config: ZfConfig | None, project_root: Path) -> str:
    name = config.project.name if config is not None and config.project.name else project_root.name
    return stable_project_id(name=name, root=project_root)


def review_id(project_id_value: str, reviewed_at: str, events: list[ZfEvent]) -> str:
    seed = f"{project_id_value}|{reviewed_at}|{len(events)}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
    return f"sprev-{reviewed_at.replace(':', '').replace('+', 'Z')}-{digest}"


def cli_identity() -> str:
    executable = Path(sys.argv[0]).name if sys.argv else "zf"
    project = os.environ.get("UV_PROJECT", "")
    return f"{executable} python={sys.executable}" + (f" uv_project={project}" if project else "")


def parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip().lower()
    try:
        if raw.endswith("h"):
            return datetime.now(timezone.utc) - timedelta(hours=float(raw[:-1]))
        if raw.endswith("d"):
            return datetime.now(timezone.utc) - timedelta(days=float(raw[:-1]))
        if raw.endswith("m"):
            return datetime.now(timezone.utc) - timedelta(minutes=float(raw[:-1]))
    except ValueError:
        return None
    return parse_ts(raw)


def parse_ts(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
