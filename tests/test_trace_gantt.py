"""#V fix: zf trace gantt — per-dev Mermaid swim-lane Gantt + DAG.

Operator UX sprint (cangjie 2026-05-22 r4 feedback): productize
the prototype `/tmp/dag_gantt.py` into `zf trace gantt` CLI command.

Output: Mermaid markdown (gantt + flowchart LR) by default, or JSON
for web consumption.

Refs: tasks/2026-05-22-0752-zf-trace-gantt-per-dev-swim-lane.md
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from contextlib import redirect_stdout

import pytest

from zf.cli.trace import run_gantt


def _setup_state(tmp_path: Path, events: list[dict], kanban: list[dict] | None = None,
                 terminal_index: dict | None = None) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    # zf.yaml so project_context resolves
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n"
        f"  state_dir: {state_dir}\n"
        "session:\n"
        "  tmux_session: t\n"
        "roles: []\n"
    )
    events_path = state_dir / "events.jsonl"
    events_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    if kanban is not None:
        (state_dir / "kanban.json").write_text(json.dumps(kanban))
    if terminal_index is not None:
        (state_dir / "kanban-terminal-index.json").write_text(json.dumps(terminal_index))
    return state_dir


def _run(state_dir: Path, **kwargs) -> str:
    args = argparse.Namespace(
        state_dir=str(state_dir),
        format=kwargs.get("format", "mermaid"),
        only=kwargs.get("only", "both"),
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run_gantt(args)
    assert rc == 0, "run_gantt should succeed"
    return buf.getvalue()


# ─── core: Gantt swim-lane per dev ────────────────────────────────────────


def test_gantt_groups_by_implementing_dev(tmp_path: Path):
    """task.dispatched assignee=dev-N → swim lane section dev-N."""
    events = [
        {"type": "task.dispatched", "ts": "2026-05-22T02:00:00",
         "task_id": "TASK-X", "payload": {"task_id": "TASK-X", "assignee": "dev-1"}},
        {"type": "dev.build.done", "ts": "2026-05-22T02:10:00",
         "task_id": "TASK-X", "payload": {"task_id": "TASK-X"}},
        {"type": "task.dispatched", "ts": "2026-05-22T02:00:00",
         "task_id": "TASK-Y", "payload": {"task_id": "TASK-Y", "assignee": "dev-2"}},
        {"type": "dev.build.done", "ts": "2026-05-22T02:15:00",
         "task_id": "TASK-Y", "payload": {"task_id": "TASK-Y"}},
    ]
    state_dir = _setup_state(tmp_path, events)
    out = _run(state_dir)
    assert "```mermaid" in out
    assert "gantt" in out
    assert "section dev-1" in out
    assert "section dev-2" in out
    # output strips TASK- prefix per Mermaid convention
    assert "X :" in out  # X under dev-1
    assert "Y :" in out  # Y under dev-2


def test_gantt_first_dispatch_wins(tmp_path: Path):
    """If task re-dispatched to judge/review later, swim lane stays
    on first implementing dev (not polluted by stage progression)."""
    events = [
        {"type": "task.dispatched", "ts": "2026-05-22T02:00:00",
         "task_id": "TASK-A", "payload": {"task_id": "TASK-A", "assignee": "dev-1"}},
        {"type": "dev.build.done", "ts": "2026-05-22T02:10:00",
         "task_id": "TASK-A", "payload": {"task_id": "TASK-A"}},
        # task moves to review, then test, then judge
        {"type": "task.dispatched", "ts": "2026-05-22T02:11:00",
         "task_id": "TASK-A", "payload": {"task_id": "TASK-A", "assignee": "review"}},
        {"type": "task.dispatched", "ts": "2026-05-22T02:12:00",
         "task_id": "TASK-A", "payload": {"task_id": "TASK-A", "assignee": "judge"}},
    ]
    state_dir = _setup_state(tmp_path, events)
    out = _run(state_dir)
    # output strips TASK- prefix
    assert "section dev-1" in out
    # judge/review should NOT have their own swim lane (writer-role-only filter)
    assert "section judge" not in out
    assert "section review" not in out


def test_gantt_done_marker(tmp_path: Path):
    """Task with task.status_changed to=done gets `done,` Mermaid marker."""
    events = [
        {"type": "task.dispatched", "ts": "2026-05-22T02:00:00",
         "task_id": "TASK-X", "payload": {"task_id": "TASK-X", "assignee": "dev-1"}},
        {"type": "dev.build.done", "ts": "2026-05-22T02:10:00",
         "task_id": "TASK-X", "payload": {"task_id": "TASK-X"}},
        {"type": "task.status_changed", "ts": "2026-05-22T02:15:00",
         "task_id": "TASK-X", "payload": {"task_id": "TASK-X", "to": "done"}},
    ]
    state_dir = _setup_state(tmp_path, events)
    out = _run(state_dir)
    assert "done," in out  # done marker present


def test_gantt_blocked_marker(tmp_path: Path):
    """dev.blocked task gets `crit,` Mermaid marker."""
    events = [
        {"type": "task.dispatched", "ts": "2026-05-22T02:00:00",
         "task_id": "TASK-X", "payload": {"task_id": "TASK-X", "assignee": "dev-1"}},
        {"type": "dev.blocked", "ts": "2026-05-22T02:10:00",
         "task_id": "TASK-X", "payload": {"task_id": "TASK-X", "reason": "phase_gate_violation"}},
    ]
    state_dir = _setup_state(tmp_path, events)
    out = _run(state_dir)
    assert "crit," in out


# ─── DAG section ─────────────────────────────────────────────────────────


def test_dag_renders_blocked_by_edges(tmp_path: Path):
    """kanban blocked_by relations rendered as flowchart edges."""
    events = []
    kanban = [
        {"id": "TASK-A", "status": "done", "blocked_by": []},
        {"id": "TASK-B", "status": "backlog", "blocked_by": ["TASK-A"]},
        {"id": "TASK-C", "status": "backlog", "blocked_by": ["TASK-A", "TASK-B"]},
    ]
    state_dir = _setup_state(tmp_path, events, kanban=kanban)
    out = _run(state_dir)
    assert "flowchart LR" in out
    assert "A --> B" in out
    assert "A --> C" in out
    assert "B --> C" in out


def test_dag_color_classes(tmp_path: Path):
    """DAG nodes colored by status (done/blocked/in_progress/backlog/archived)."""
    events = [
        {"type": "task.dispatched", "ts": "2026-05-22T02:00:00",
         "task_id": "TASK-IN", "payload": {"task_id": "TASK-IN", "assignee": "dev-1"}},
    ]
    kanban = [
        {"id": "TASK-DONE", "status": "done", "blocked_by": []},
        {"id": "TASK-BACKLOG", "status": "backlog", "blocked_by": []},
        {"id": "TASK-BLOCKED", "status": "blocked", "blocked_by": []},
        {"id": "TASK-IN", "status": "in_progress", "blocked_by": []},
    ]
    terminal_index = {"TASK-ARCHIVED": "2026-05-21"}
    state_dir = _setup_state(tmp_path, events, kanban=kanban, terminal_index=terminal_index)
    out = _run(state_dir)
    assert "classDef done" in out
    assert "classDef inflight" in out
    assert "classDef blocked" in out
    assert "classDef backlog" in out
    assert "classDef archived" in out
    # archived task appears in node list
    assert "ARCHIVED" in out
    assert ":::archived" in out


# ─── flags: --only / --format ─────────────────────────────────────────────


def test_only_gantt_skips_dag(tmp_path: Path):
    events = [
        {"type": "task.dispatched", "ts": "2026-05-22T02:00:00",
         "task_id": "TASK-X", "payload": {"task_id": "TASK-X", "assignee": "dev-1"}},
    ]
    kanban = [{"id": "TASK-X", "status": "in_progress", "blocked_by": []}]
    state_dir = _setup_state(tmp_path, events, kanban=kanban)
    out = _run(state_dir, only="gantt")
    assert "gantt" in out
    assert "flowchart LR" not in out


def test_only_dag_skips_gantt(tmp_path: Path):
    events = [
        {"type": "task.dispatched", "ts": "2026-05-22T02:00:00",
         "task_id": "TASK-X", "payload": {"task_id": "TASK-X", "assignee": "dev-1"}},
    ]
    kanban = [{"id": "TASK-X", "status": "in_progress", "blocked_by": []}]
    state_dir = _setup_state(tmp_path, events, kanban=kanban)
    out = _run(state_dir, only="dag")
    assert "flowchart LR" in out
    assert "section dev-1" not in out


def test_format_json(tmp_path: Path):
    events = [
        {"type": "task.dispatched", "ts": "2026-05-22T02:00:00",
         "task_id": "TASK-X", "payload": {"task_id": "TASK-X", "assignee": "dev-1"}},
        {"type": "dev.build.done", "ts": "2026-05-22T02:10:00",
         "task_id": "TASK-X", "payload": {"task_id": "TASK-X"}},
    ]
    kanban = [{"id": "TASK-X", "status": "in_progress", "blocked_by": []}]
    state_dir = _setup_state(tmp_path, events, kanban=kanban)
    out = _run(state_dir, format="json")
    data = json.loads(out)
    assert "per_dev" in data
    assert "task_deps" in data
    assert "task_status" in data
    # per_dev keyed by short task_id, value contains dev
    assert "X" in data["per_dev"]
    assert data["per_dev"]["X"]["dev"] == "dev-1"


# ─── edge cases ───────────────────────────────────────────────────────────


def test_empty_events_friendly_output(tmp_path: Path):
    """Empty events.jsonl + no kanban → friendly empty output."""
    state_dir = _setup_state(tmp_path, [], kanban=[])
    out = _run(state_dir)
    # Should produce minimal Mermaid (gantt header + flowchart header, no content)
    # OR a friendly note
    assert "gantt" in out or "no dispatched tasks" in out.lower()
