"""Execution claims require actual post-dispatch replan, new binding and closure."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from tests.e2e.prd_ui.execution_replan import check_execution_order, initialize_protocol, verify_protocol_product
from zf.core.events.model import ZfEvent


def _case():
    rows = [
        ("plan.approved", {"plan_id": "old"}),
        ("task.pipeline.stage.dispatched", {"task_map_generation": "g1"}),
        ("prd.plan.requested", {}),
        ("plan.approved", {"plan_id": "new"}),
        ("task.pipeline.stage.dispatched", {"task_map_generation": "g2"}),
        ("run.goal.completed", {"candidate_event_id": "candidate-final"}),
    ]
    events = [ZfEvent(id=f"evt-{i}", type=kind, correlation_id="run-1",
                     payload={**body, "workflow_run_id": "run-1"}) for i, (kind, body) in enumerate(rows)]
    return events, {"trigger_event_id": "evt-1", "workflow_run_id": "run-1"}


@pytest.mark.parametrize("trigger", ["orchestrator.replan_requested", "prd.plan.requested"])
def test_execution_replan_requires_new_plan_generation_and_delivery(trigger):
    events, change = _case()
    events[2].type = trigger
    assert check_execution_order(events, change)["revised_plan"] == "new"


@pytest.mark.parametrize("fault", ["preview-only", "old-approval", "old-generation", "other-run", "no-delivery", "duplicate-delivery"])
def test_reject_partial_or_unbound_recovery(fault):
    events, change = _case()
    if fault == "preview-only":
        events[1], events[2] = events[2], events[1]
    elif fault == "old-approval":
        events[3].payload["plan_id"] = "old"
    elif fault == "old-generation":
        events[4].payload["task_map_generation"] = "g1"
    elif fault == "other-run":
        events[-1].payload["workflow_run_id"] = "run-2"
    elif fault == "no-delivery":
        events.pop()
    else:
        extra = deepcopy(events[-1]); extra.id = "evt-extra"; events.append(extra)
    with pytest.raises(AssertionError):
        check_execution_order(events, change)


def test_initial_external_fixture_is_isolated(tmp_path: Path):
    initialize_protocol(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["protocol.json"]


@pytest.mark.parametrize("decoy_run", ["run-1", "run-2"])
def test_final_oracle_uses_completed_candidate_binding(tmp_path, monkeypatch, decoy_run):
    from tests.e2e.prd_ui import execution_replan

    events, change = _case()
    events.extend([
        ZfEvent(id="candidate-final", type="candidate.ready", correlation_id="run-1", payload={"candidate_commit": "correct"}),
        ZfEvent(type="candidate.ready", correlation_id=decoy_run, payload={"candidate_commit": "unrelated"}),
    ])
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence/protocol-change.json").write_text(json.dumps(change))
    monkeypatch.setattr(execution_replan, "_events", lambda _: events)
    monkeypatch.setattr(execution_replan, "verify_protocol_product",
                        lambda _, commit: {"passed": commit == "correct", "target_commit": commit})
    result = execution_replan.audit_execution_replan(tmp_path, tmp_path / "project")
    assert result["oracle"]["target_commit"] == "correct"


@pytest.mark.parametrize("version", [1, 2])
def test_oracle_uses_complete_frozen_commit_not_dirty_checkout(tmp_path, version):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Protocol test")
    git("config", "user.email", "test@example.invalid")
    (tmp_path / "render_protocol.py").write_text("from implementation import main\nmain()\n")
    key = "text" if version == 2 else "message"
    source = f'''import json, sys
def main():
    try:
        value = json.load(sys.stdin)
        if not isinstance(value, dict) or set(value) != {{"{key}"}} or not isinstance(value["{key}"], str):
            raise ValueError("invalid input")
        print("v{version}:" + value["{key}"])
    except (ValueError, TypeError):
        print("invalid input", file=sys.stderr)
        raise SystemExit(1)
'''
    (tmp_path / "implementation.py").write_text(source)
    git("add", "--", "render_protocol.py", "implementation.py")
    git("commit", "-qm", "test: freeze protocol fixture")
    commit = git("rev-parse", "HEAD")
    (tmp_path / "implementation.py").write_text("raise RuntimeError('uncommitted decoy')\n")
    report = verify_protocol_product(tmp_path, commit)
    assert report["passed"] == (version == 2)
    assert report["target_commit"] == commit
    assert len(report["cases"]) == 8
