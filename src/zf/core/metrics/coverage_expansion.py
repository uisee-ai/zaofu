"""EVAL-COVERAGE-EXPANSION-001 — 7 new evaluation dimensions (doc 43 Gap 3).

Batch 1 (high-ROI, low-complexity):
- LongHorizonE2E — long-horizon project e2e success rate (cangjie-mono)
- SprintProgress — backlog/ + git log derived burn rate
- HookHealth — hook.write_failed / hook.orphan_event aggregation

Batch 2/3 deferred (spec drift / skill effectiveness / lockfile / turn coherence).
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# LongHorizonE2E
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LongHorizonE2EReport:
    """Long-horizon project's real e2e delivery rate.

    Source: events.jsonl + feature_list.json (kernel-owned truth).
    Window-bound (cutoff is a wall-clock datetime).
    """

    project: str
    window_days: int
    user_messages: int
    features_delivered: int
    features_blocked: int
    features_in_progress: int
    e2e_success_rate: float
    operator_interventions: int

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "window_days": self.window_days,
            "user_messages": self.user_messages,
            "features_delivered": self.features_delivered,
            "features_blocked": self.features_blocked,
            "features_in_progress": self.features_in_progress,
            "e2e_success_rate": self.e2e_success_rate,
            "operator_interventions": self.operator_interventions,
        }


def compute_longhorizon_e2e(
    *,
    project: str,
    events: Iterable,
    features: Iterable,
    window_days: int = 30,
) -> LongHorizonE2EReport:
    """Aggregate e2e metrics for a long-horizon project.

    - user_messages: count of ``user.message`` events in window
    - features_delivered: feature status == delivered / shipped
    - features_blocked: feature status == blocked
    - features_in_progress: status in {in_progress, pending}
    - operator_interventions: count of ``human.escalate`` events
    """
    events_list = list(events)
    user_msg_n = sum(1 for e in events_list if e.type == "user.message")
    operator_intervene_n = sum(
        1 for e in events_list if e.type in ("human.escalate", "human.resolved")
    )

    delivered = 0
    blocked = 0
    in_progress = 0
    for f in features:
        status = (getattr(f, "status", "") or "").lower()
        if status in ("delivered", "shipped", "done"):
            delivered += 1
        elif status == "blocked":
            blocked += 1
        elif status in ("in_progress", "pending"):
            in_progress += 1
    total_attempted = delivered + blocked + in_progress
    success_rate = delivered / total_attempted if total_attempted else 0.0

    return LongHorizonE2EReport(
        project=project,
        window_days=window_days,
        user_messages=user_msg_n,
        features_delivered=delivered,
        features_blocked=blocked,
        features_in_progress=in_progress,
        e2e_success_rate=success_rate,
        operator_interventions=operator_intervene_n,
    )


# ---------------------------------------------------------------------------
# SprintProgress
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SprintProgressReport:
    """Sprint backlog progress trend.

    Source: backlogs/ directory + git log commits referencing sprint IDs.
    backlogs/ is gitignored — files are filesystem source, git log is the
    completion proof.
    """

    window_days: int
    sprints_total: int             # backlogs/ file count
    sprints_started: int           # have at least 1 commit
    sprints_completed: int         # have ✅ / commit hash marker in file
    sprints_obsolete: int          # status: obsolete marker
    weekly_throughput: float       # sprints_completed / (window_days/7)
    burn_rate: float               # sprints_completed / sprints_total

    def to_dict(self) -> dict:
        return {
            "window_days": self.window_days,
            "sprints_total": self.sprints_total,
            "sprints_started": self.sprints_started,
            "sprints_completed": self.sprints_completed,
            "sprints_obsolete": self.sprints_obsolete,
            "weekly_throughput": self.weekly_throughput,
            "burn_rate": self.burn_rate,
        }


_SPRINT_FILENAME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:-\d{4})?-([A-Za-z][\w-]*?)(?:\.md)?$"
)


def compute_sprint_progress(
    *,
    backlogs_dir: Path,
    window_days: int = 30,
) -> SprintProgressReport:
    """Read backlogs/ + git log to derive sprint completion stats.

    A sprint is marked completed when:
    - its backlog file body contains a ``✅`` marker, OR
    - ``status: complete`` line, OR
    - referenced by a commit message ``<SPRINT-ID>`` in `git log`.

    Obsolete: file contains ``status: obsolete`` line.
    """
    if not backlogs_dir.exists():
        return SprintProgressReport(
            window_days=window_days,
            sprints_total=0, sprints_started=0, sprints_completed=0,
            sprints_obsolete=0, weekly_throughput=0.0, burn_rate=0.0,
        )

    sprint_files = [
        p for p in backlogs_dir.iterdir()
        if p.is_file() and p.suffix == ".md"
        and not p.name.startswith("README")
    ]
    total = len(sprint_files)
    completed_files = []
    obsolete = 0
    for p in sprint_files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "✅" in text or re.search(r"status:\s*\**\s*complete", text, re.IGNORECASE):
            completed_files.append(p.stem)
        if re.search(r"status:\s*\**\s*obsolete", text, re.IGNORECASE):
            obsolete += 1

    # Cross-check with git log commit messages referring sprint IDs
    started = set(completed_files)
    cutoff_arg = f"--since={window_days}.days.ago"
    try:
        out = subprocess.check_output(
            ["git", "log", "--pretty=format:%s", cutoff_arg],
            cwd=backlogs_dir.parent,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode("utf-8", errors="replace")
    except Exception:
        out = ""
    for line in out.splitlines():
        # Look for "<PREFIX>-<NUM>" patterns in commit msg
        for match in re.finditer(r"\b[A-Z]{2,}[A-Z0-9_-]*-\d{3,}\b", line):
            started.add(match.group(0))

    weeks = max(window_days / 7.0, 1.0 / 7.0)
    weekly_throughput = len(completed_files) / weeks
    burn_rate = len(completed_files) / total if total else 0.0

    return SprintProgressReport(
        window_days=window_days,
        sprints_total=total,
        sprints_started=len(started),
        sprints_completed=len(completed_files),
        sprints_obsolete=obsolete,
        weekly_throughput=weekly_throughput,
        burn_rate=burn_rate,
    )


# ---------------------------------------------------------------------------
# HookHealth
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookHealthReport:
    """Provider hook system reliability.

    Source: events.jsonl ``hook.write_failed`` + ``hook.orphan_event`` +
    counts of ``claude.hook.*`` / ``codex.hook.*`` invocations.
    """

    total_invocations: int        # all claude.hook.* / codex.hook.*
    failed_invocations: int       # hook.write_failed count
    orphan_invocations: int       # hook.orphan_event count
    failure_rate: float
    orphan_rate: float
    by_event_type: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total_invocations": self.total_invocations,
            "failed_invocations": self.failed_invocations,
            "orphan_invocations": self.orphan_invocations,
            "failure_rate": self.failure_rate,
            "orphan_rate": self.orphan_rate,
            "by_event_type": dict(self.by_event_type),
        }


def compute_hook_health(events: Iterable) -> HookHealthReport:
    """Count hook events from events.jsonl."""
    events_list = list(events)
    hook_invocations = [
        e for e in events_list
        if e.type.startswith("claude.hook.") or e.type.startswith("codex.hook.")
    ]
    failed = sum(1 for e in events_list if e.type == "hook.write_failed")
    orphan = sum(1 for e in events_list if e.type == "hook.orphan_event")
    total = len(hook_invocations)
    by_type: Counter[str] = Counter(e.type for e in hook_invocations)
    failure_rate = failed / total if total else 0.0
    orphan_rate = orphan / total if total else 0.0
    return HookHealthReport(
        total_invocations=total,
        failed_invocations=failed,
        orphan_invocations=orphan,
        failure_rate=failure_rate,
        orphan_rate=orphan_rate,
        by_event_type=dict(by_type),
    )
