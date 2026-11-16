"""Real-provider regression: state-relative indexes must survive both gates."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.orchestrator_fanout import FanoutCoordinationMixin
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from tests.test_plan_candidate_preflight import (
    _PlanSynthHarness,
    _source_index,
    _task_map,
)
from tests.test_writer_fanout_runtime import _start, _state


@pytest.mark.parametrize("ref_kind", ["state", "project", "absolute", "alias"])
def test_source_gate_resolves_custom_state_directory(tmp_path, ref_kind):
    state = tmp_path / ".ui-state"
    index = state / "artifacts/plan/source_index.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps(_source_index()))
    refs = {
        "state": "artifacts/plan/source_index.json",
        "project": ".ui-state/artifacts/plan/source_index.json",
        "absolute": str(index),
        "alias": ".zf/artifacts/plan/source_index.json",
    }
    owner = SimpleNamespace(
        project_root=tmp_path,
        state_dir=state,
        config=SimpleNamespace(workflow=SimpleNamespace(harness_profile="strict")),
        event_writer=EventWriter(EventLog(state / "events.jsonl")),
    )
    loaded = SimpleNamespace(
        source_index_ref=refs[ref_kind],
        task_map_ref="task-map.json",
        task_items=[{"task_id": "TASK-1"}],
    )
    result = FanoutCoordinationMixin._writer_source_index_gate(
        owner,
        loaded=loaded,
        stage_id="impl",
        trigger_event=ZfEvent(type="task_map.ready", actor="test"),
        trace_id="run",
    )
    assert result.passed
    assert EventLog(state / "events.jsonl").read_all() == []


def test_state_relative_index_survives_actual_writer_dispatch(tmp_path):
    state, log, transport, orch = _state(
        tmp_path, synthesize_canonical=True, harness_profile="strict"
    )
    index = state / "artifacts/F-11111111/source_index.json"
    index.write_text(
        json.dumps(
            {
                "schema_version": "source-index.v1",
                "tasks": [
                    {"task_id": t, "source_ref": f"prd.md#{t}"}
                    for t in ["TASK-1", "TASK-2"]
                ],
            }
        )
    )
    path = index.with_name("task_map.json")
    data = json.loads(path.read_text())
    data["source_refs"] = {"source_index_ref": "artifacts/F-11111111/source_index.json"}
    path.write_text(json.dumps(data))
    _start(orch)
    assert not [e for e in log.read_all() if e.type == "fanout.cancelled"]
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2"]


def test_missing_per_task_source_rejected_before_critic(tmp_path):
    harness = _PlanSynthHarness(
        tmp_path,
        [
            {
                "child_id": "planner",
                "report": {
                    "task_map": _task_map(),
                    "source_index": {"schema_version": "source-index.v1", "tasks": []},
                    "plan_md": "# Plan",
                },
            }
        ],
    )
    harness.config.workflow.harness_profile = "strict"
    harness._dispatch_fanout_synth(
        "fanout-plan",
        {
            "fanout_id": "fanout-plan",
            "trace_id": "run",
            "stage_id": "issue-triage",
            "trigger_event_id": "evt-input",
            "trigger_payload": {"flow_kind": "issue"},
            "aggregate_config": {
                "success_event": "task_map.ready",
                "synth_role": "plan-critic",
            },
            "children": [],
        },
        "wait_for_all",
        "plan-critic",
    )
    assert not harness.critic_dispatch_attempted
    assert harness.finalized.payload["failure_class"] == "plan_candidate_preflight"
    assert any(
        x.get("code") == "source_index_gap"
        for x in harness.finalized.payload["report"]["findings"]
    )


@pytest.mark.parametrize(
    "profile, expected", [("strict", True), ("release", True), ("baseline", False)]
)
def test_preflight_respects_source_gate_profile(tmp_path, profile, expected):
    result = evaluate_plan_candidate_preflight(
        state_dir=tmp_path / ".ui-state",
        project_root=tmp_path,
        reports=[
            {
                "report": {
                    "task_map": _task_map(),
                    "source_index": {"schema_version": "source-index.v1", "tasks": []},
                    "plan_md": "# Plan",
                }
            }
        ],
        manifest={"trigger_payload": {"flow_kind": "issue"}},
        metadata={"flow_kind": "issue"},
        writer_policy={"harness_profile": profile},
    )
    assert any(e["code"] == "source_index_gap" for e in result["errors"]) is expected
