import json

import pytest

from tests.test_plan_acceptance_rework import _prepare
from tests.test_module_gap_plan_runtime import _config, _RecordingTransport
from zf.core.events import EventLog, ZfEvent
from zf.runtime.gap_plan_baseline import gap_plan_base_ref
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.plan_artifact_package import PlanArtifactPackageError


def test_gap_bridge_uses_package_map_instead_of_unrelated_latest_ref(tmp_path, monkeypatch):
    args, admitted, _, package = _prepare(tmp_path)
    state = args["state_dir"]
    (state / "latest.json").write_text(json.dumps({"tasks": []}))
    log = EventLog(state / "events.jsonl")
    log.append(admitted)
    orch = Orchestrator(state, _config(state), _RecordingTransport())
    ready = []
    monkeypatch.setattr(orch, "_maybe_start_writer_fanout", ready.append)
    event = ZfEvent(type="flow.gap_plan.ready", correlation_id="run-1", payload={
        "workflow_run_id": "run-1", "pdd_id": "GOAL-1", "flow_kind": "prd",
        "task_map_ref": "latest.json", "task_map_generation": "g1",
        "plan_artifact_package_digest": admitted.payload["package_digest"],
        "gap_tasks": [{"task_id": "T2", "parent_task_id": "T1", "owner_role": "dev",
            "claim_paths": ["src/**"], "acceptance": ["Exact JSON"],
            "verify_commands": ["pytest"], "source_refs": ["requirement.md"]}],
    })
    result = orch._bridge_gap_plan_ready_to_task_map(event)
    assert result.action == "bridge", result
    baseline = next(p for p in package["produced"] if p["logical_name"] == "task_map")
    assert ready[0].payload["amend_of"] == baseline["ref"]
    assert ready[0].payload["supersedes_task_map_generation"] == "g1"
    amended = json.loads((state / ready[0].payload["task_map_ref"]).read_text())
    assert {t["task_id"] for t in amended["tasks"]} == {"T1", "T2"}


@pytest.mark.parametrize("mutation", [
    {"task_map_generation": "unadmitted"},
    {"plan_artifact_package_digest": "wrong"},
    {"workflow_run_id": "another-run"},
])
def test_gap_baseline_rejects_unproven_authority(tmp_path, mutation):
    args, event, _, _ = _prepare(tmp_path)
    with pytest.raises(PlanArtifactPackageError):
        gap_plan_base_ref(state_dir=args["state_dir"], events=[event],
            payload={"workflow_run_id": "run-1", "task_map_generation": "g1", **mutation},
            fallback_ref="r1.json", required=True)
