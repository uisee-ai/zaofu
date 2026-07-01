"""1204: CostTracker backend dimension + `zf cost --by-backend` CLI +
MetricsSnapshot backend breakdown.

Covers T1..T6 in a single file so the regression surface is easy to
audit. Wire-up proof (T6) lives at the bottom.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from zf.core.cost.tracker import CostTracker, CostSummary


# -- T1: record_usage accepts backend + writes to jsonl --

def test_record_usage_accepts_backend_param(tmp_path: Path):
    tracker = CostTracker(tmp_path / "cost.jsonl")
    tracker.record_usage(
        role="dev", input_tokens=1000, output_tokens=500,
        model="default", backend="claude-code",
    )
    raw = (tmp_path / "cost.jsonl").read_text().strip()
    entry = json.loads(raw)
    assert entry["backend"] == "claude-code"


def test_record_usage_backend_defaults_to_empty_for_backward_compat(tmp_path: Path):
    """Old call sites without backend kwarg must keep working."""
    tracker = CostTracker(tmp_path / "cost.jsonl")
    tracker.record_usage(
        role="dev", input_tokens=100, output_tokens=50, model="default",
    )
    entry = json.loads((tmp_path / "cost.jsonl").read_text().strip())
    # Either absent or empty string — both are acceptable
    assert entry.get("backend", "") == ""


def test_read_old_cost_file_without_backend_field_is_safe(tmp_path: Path):
    """Existing .zf/cost.jsonl written before 1204 must still parse."""
    cost_path = tmp_path / "cost.jsonl"
    # Seed with legacy entry lacking backend
    cost_path.write_text(json.dumps({
        "role": "dev", "instance_id": "dev-1", "input_tokens": 100,
        "output_tokens": 50, "model": "default", "cost_usd": 0.001,
        "ts": 1700000000.0,
    }) + "\n")

    tracker = CostTracker(cost_path)
    totals = tracker.per_role_totals()
    assert totals["dev"].total_usd > 0
    # summary_by_backend treats missing backend as "unknown"
    by_backend = tracker.summary_by_backend()
    assert "unknown" in by_backend


# -- T2: summary_by_backend --

def test_summary_by_backend_aggregates_across_roles(tmp_path: Path):
    tracker = CostTracker(tmp_path / "cost.jsonl")
    # 2 claude roles + 1 codex role
    tracker.record_usage(role="orchestrator", input_tokens=1000,
                          output_tokens=100, model="default",
                          backend="claude-code")
    tracker.record_usage(role="review", input_tokens=500,
                          output_tokens=200, model="default",
                          backend="claude-code")
    tracker.record_usage(role="dev", input_tokens=2000,
                          output_tokens=800, model="default",
                          backend="codex")

    by_backend = tracker.summary_by_backend()
    assert set(by_backend.keys()) == {"claude-code", "codex"}
    assert by_backend["claude-code"].entries == 2
    assert by_backend["codex"].entries == 1
    # tokens aggregate correctly
    assert by_backend["claude-code"].input_tokens == 1500
    assert by_backend["codex"].input_tokens == 2000


def test_summary_by_backend_returns_cost_summary_shape(tmp_path: Path):
    tracker = CostTracker(tmp_path / "cost.jsonl")
    tracker.record_usage(role="dev", input_tokens=100, output_tokens=50,
                          model="default", backend="codex")
    by_backend = tracker.summary_by_backend()
    assert isinstance(by_backend["codex"], CostSummary)
    assert by_backend["codex"].total_usd > 0


# -- T3: call-site — apply_agent_usage_event must pass backend --

def test_apply_agent_usage_event_passes_backend_when_config_provided(
    tmp_path: Path,
):
    from zf.core.events.model import ZfEvent
    from zf.runtime.housekeeping import apply_agent_usage_event

    tracker = CostTracker(tmp_path / "cost.jsonl")
    # Mini role → backend resolver; in production the config is passed in
    role_to_backend = {"dev": "codex", "review": "claude-code"}

    event = ZfEvent(
        type="agent.usage",
        actor="dev-1",
        payload={"usage": {"input_tokens": 500, "output_tokens": 300}},
    )
    apply_agent_usage_event(tracker, event, role_backends=role_to_backend)

    entry = json.loads((tmp_path / "cost.jsonl").read_text().strip())
    assert entry["backend"] == "codex"


def test_apply_agent_usage_event_without_config_still_works(
    tmp_path: Path,
):
    """Backward compat: calls without role_backends default to ""."""
    from zf.core.events.model import ZfEvent
    from zf.runtime.housekeeping import apply_agent_usage_event

    tracker = CostTracker(tmp_path / "cost.jsonl")
    event = ZfEvent(
        type="agent.usage",
        actor="dev-1",
        payload={"usage": {"input_tokens": 500, "output_tokens": 300}},
    )
    apply_agent_usage_event(tracker, event)  # no role_backends
    entry = json.loads((tmp_path / "cost.jsonl").read_text().strip())
    # backend absent or empty
    assert entry.get("backend", "") == ""


# -- T4: `zf cost --by-backend` CLI --

def test_cli_by_backend_groups_output(tmp_path: Path, monkeypatch, capsys):
    from zf.cli.cost import run as cost_run

    # Seed cost.jsonl inside cwd/.zf/
    zf_dir = tmp_path / ".zf"
    zf_dir.mkdir()
    tracker = CostTracker(zf_dir / "cost.jsonl")
    tracker.record_usage(role="dev", input_tokens=1000, output_tokens=500,
                          model="default", backend="codex")
    tracker.record_usage(role="review", input_tokens=500, output_tokens=200,
                          model="default", backend="claude-code")

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        budget=None, days=None, by_instance=False, by_backend=True,
    )
    cost_run(args)
    captured = capsys.readouterr().out
    # Output should mention both backend names
    assert "codex" in captured
    assert "claude-code" in captured


# -- T5: MetricsSnapshot cost_by_backend / tokens_by_backend --

def test_metrics_snapshot_has_cost_by_backend_fields():
    from zf.core.metrics.collector import MetricsSnapshot
    snap = MetricsSnapshot()
    # Fields default to empty dict
    assert snap.cost_by_backend == {}
    assert snap.tokens_by_backend == {}


def test_metrics_collector_populates_backend_breakdown(tmp_path: Path):
    from zf.core.events.log import EventLog
    from zf.core.metrics.collector import MetricsCollector
    from zf.core.task.store import TaskStore

    # Seed cost tracker with two backends
    tracker = CostTracker(tmp_path / "cost.jsonl")
    tracker.record_usage(role="dev", input_tokens=1000, output_tokens=500,
                          model="default", backend="codex")
    tracker.record_usage(role="review", input_tokens=500, output_tokens=200,
                          model="default", backend="claude-code")

    # Empty tasks + events
    events = EventLog(tmp_path / "events.jsonl")
    tasks = TaskStore(tmp_path / "kanban.json")

    snap = MetricsCollector.compute(
        events=events, tasks=tasks, cost=tracker,
    )
    assert set(snap.cost_by_backend.keys()) == {"codex", "claude-code"}
    assert snap.tokens_by_backend["codex"] == 1500  # 1000 + 500
    assert snap.tokens_by_backend["claude-code"] == 700


# -- T6: wire-up proof --

def test_wire_record_usage_call_sites_pass_backend():
    """T6: orchestrator's _apply_housekeeping or the apply_agent_usage_event
    path must resolve a role→backend lookup from config so cost tracker
    can record the backend dimension."""
    src = Path(__file__).resolve().parents[1] / "src" / "zf" / "runtime"
    housekeeping = (src / "housekeeping.py").read_text()
    # apply_agent_usage_event exposes role_backends kwarg
    assert "role_backends" in housekeeping, \
        "apply_agent_usage_event must accept role_backends mapping"

    orchestrator = (src / "orchestrator.py").read_text()
    # orchestrator must build & pass the mapping
    assert "role_backends" in orchestrator, \
        "orchestrator must compute role_backends and forward to housekeeping"


def test_wire_cost_cli_has_by_backend_flag():
    """T6: verify the --by-backend flag is declared in cost.py."""
    src = Path(__file__).resolve().parents[1] / "src" / "zf" / "cli"
    cost_py = (src / "cost.py").read_text()
    assert "--by-backend" in cost_py, \
        "zf cost must expose --by-backend flag"
