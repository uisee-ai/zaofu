"""progress.md hybrid generation.

Layer 1 auto-generates the deterministic sections (Currently Active, Completed,
Recent Events). Layer 2 owns the "Layer 2 Insights" section — appends narrative
notes that survive regeneration via marker-based merge.

Hybrid model:
  - Layer 1 owns the structure + the deterministic facts (rewrites every dispatch)
  - Layer 2 owns the insights (only appends; Layer 1 preserves them on regen)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.feature.store import FeatureStore
from zf.core.task.store import TaskStore


LAYER2_INSIGHT_MARKER = "<!-- LAYER2_INSIGHTS -->"
PROGRESS_FILENAME = "progress.md"


def regenerate_progress(state_dir: Path, *, recent_events_limit: int = 30) -> str:
    """Regenerate progress.md from current state, preserving any prior Layer 2 insights.

    Writes the new content to .zf/progress.md and returns the text.
    """
    sections: list[str] = []
    sections.append("# Progress")
    sections.append("")
    sections.append(f"_Last regenerated: {datetime.now(timezone.utc).isoformat()}_")
    sections.append("")

    sections.append("## Currently Active")
    sections.append(_render_active(state_dir))
    sections.append("")

    sections.append("## Completed")
    sections.append(_render_completed(state_dir))
    sections.append("")

    sections.append("## Recent Events")
    sections.append(_render_events(state_dir, recent_events_limit))
    sections.append("")

    sections.append("## Layer 2 Insights")
    sections.append(LAYER2_INSIGHT_MARKER)
    sections.append(_extract_existing_insights(state_dir))

    text = "\n".join(sections)
    progress_path = state_dir / PROGRESS_FILENAME
    progress_path.write_text(text, encoding="utf-8")
    return text


def _render_active(state_dir: Path) -> str:
    fs_path = state_dir / "feature_list.json"
    ts_path = state_dir / "kanban.json"

    parts: list[str] = []
    if fs_path.exists():
        active_features = [
            f for f in FeatureStore(fs_path).list_all()
            if f.status in ("planning", "active")
        ]
        if active_features:
            parts.append("**Features**:")
            for f in active_features:
                parts.append(f"- `{f.id}` [{f.status}] p{f.priority} — {f.title}")
    if ts_path.exists():
        active_tasks = [
            t for t in TaskStore(ts_path).list_all()
            if t.status in ("backlog", "in_progress", "review", "testing", "blocked")
        ]
        if active_tasks:
            if parts:
                parts.append("")
            parts.append("**Tasks**:")
            for t in active_tasks:
                assignee = f"@{t.assigned_to}" if t.assigned_to else "(unassigned)"
                parts.append(f"- `{t.id}` [{t.status}] {assignee} — {t.title}")
    return "\n".join(parts) if parts else "_(none)_"


def _render_completed(state_dir: Path) -> str:
    fs_path = state_dir / "feature_list.json"
    ts_path = state_dir / "kanban.json"

    parts: list[str] = []
    if fs_path.exists():
        done_features = [
            f for f in FeatureStore(fs_path).list_all_with_archive()
            if f.status == "done"
        ]
        if done_features:
            parts.append("**Features**:")
            for f in done_features:
                parts.append(f"- `{f.id}` — {f.title}")
    if ts_path.exists():
        done_tasks = [
            t for t in TaskStore(ts_path).list_all_with_archive()
            if t.status == "done"
        ]
        if done_tasks:
            if parts:
                parts.append("")
            parts.append("**Tasks**:")
            for t in done_tasks:
                parts.append(f"- `{t.id}` — {t.title}")
    return "\n".join(parts) if parts else "_(none)_"


def _render_events(state_dir: Path, limit: int) -> str:
    path = state_dir / "events.jsonl"
    if not path.exists():
        return "_(no events)_"
    log = EventLog(path)
    events = log.read_all()[-limit:]
    if not events:
        return "_(no events)_"
    return "\n".join(
        f"- `{e.ts}` `{e.type}` actor=`{e.actor or '?'}` task=`{e.task_id or '?'}`"
        for e in events
    )


def _extract_existing_insights(state_dir: Path) -> str:
    """Read the existing progress.md (if present) and pull out everything after
    the Layer 2 marker. This preserves Layer 2's free-text narrative across
    regenerations."""
    path = state_dir / PROGRESS_FILENAME
    if not path.exists():
        return "_(none yet — Layer 2 will append insights here)_"
    text = path.read_text(encoding="utf-8")
    if LAYER2_INSIGHT_MARKER not in text:
        return "_(none yet — Layer 2 will append insights here)_"
    after = text.split(LAYER2_INSIGHT_MARKER, 1)[1]
    after = after.strip()
    return after if after else "_(none yet — Layer 2 will append insights here)_"
