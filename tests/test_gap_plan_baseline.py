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


def _two_packages(tmp_path, mutation="", selected_port="task_execution_input"):
    from zf.runtime.plan_artifact_package import prepare_plan_artifact_package, package_event_payload
    args, first, gap, old = _prepare(tmp_path)
    state = args["state_dir"]
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    original = json.loads((state / "r1.json").read_text())
    original["tasks"][0]["evidence_contract"] = {"plan_input_selection": {
        "schema_version": "task-plan-input-selection.v1",
        "implementation": list(dict.fromkeys(["task_execution_input", selected_port])),
        "task-verify": list(dict.fromkeys(["task_execution_input", selected_port]))}}
    matrix_ports = ["execution_spec", "task_execution_input", "acceptance_matrix", "test_matrix", "real_e2e_matrix"]
    original["required_plan_ports"] = matrix_ports
    for name in ("r1", "r2"):
        (state / f"{name}.json").write_text(json.dumps(original))
    spec_port = next(p for p in old["produced"] if p["logical_name"] == "product_acceptance_spec")
    spec = dict(hydrate_sidecar_ref(state, spec_port).payload)
    for key in ("workflow_run_id", "flow_kind", "plan_revision", "task_map_generation"):
        spec.pop(key, None)
    scoped_input = {"schema_version": "task-scoped-plan-input.v1", "shared": {"policy": "original"},
                    "tasks": [{"task_id": task_id, "body": {"acceptance": "Exact JSON"}}
                              for task_id in ("T1", "OTHER")]}
    old, descriptor, _ = prepare_plan_artifact_package(events=[], payload={
        "task_map_ref": "r1.json", "task_map_generation": "g1", "requirement_spec_ref": "requirement.md",
        "plan_ports": [{"logical_name": "product_acceptance_spec", "body": spec},
                       *[{"logical_name": name, "body": scoped_input if name == "task_execution_input"
                          else {"status": "ready", "rows": []}} for name in matrix_ports]]}, **args)
    first = ZfEvent(type="plan.artifact_package.admitted", correlation_id="run-1",
        payload=package_event_payload(old, descriptor, status="admitted"))
    body = json.loads((state / "r2.json").read_text())
    body["tasks"].append({"task_id": "OTHER", "acceptance": ["Other task retained"],
                          "acceptance_criteria": ["Other task retained"]})
    if mutation == "task":
        body["tasks"][0]["acceptance"].append("changed")
    (state / "r2.json").write_text(json.dumps(body))
    if mutation in {"input", "execution"}:
        changed_port = "execution_spec" if mutation == "execution" else selected_port
        gap["plan_ports"] = [{"logical_name": changed_port, "body":
            {**scoped_input, "shared": {"policy": "changed"}} if changed_port == "task_execution_input"
            else {"policy": "changed"}}]
    if mutation == "unrelated":
        scoped_input["tasks"][1]["body"] = {"acceptance": "Changed unrelated task"}
        scoped_input["tasks"].reverse()
        gap["plan_ports"] = [{"logical_name": "task_execution_input", "body": scoped_input}]
    package, descriptor, _ = prepare_plan_artifact_package(events=[first], payload=gap, **args)
    second = ZfEvent(type="plan.artifact_package.admitted", correlation_id="run-1",
        payload=package_event_payload(package, descriptor, status="admitted"))
    return args, [first, second], old, package


def _task_delta():
    return {"task_id": "T1-FIX", "supersedes_task_ids": ["T1"], "owner_role": "dev",
            "base_commit": "a" * 40, "claim_paths": ["src/**"], "acceptance": ["Exact JSON"],
            "evidence_contract": {"plan_input_selection": {"schema_version": "task-plan-input-selection.v1",
                "implementation": ["task_execution_input"], "task-verify": ["task_execution_input"]}},
            "verify_commands": ["pytest"], "source_refs": ["requirement.md"]}


@pytest.mark.parametrize("mutation", ["", "execution", "unrelated"])
def test_older_task_gap_rebases_on_current_full_map_without_losing_other_work(tmp_path, monkeypatch, mutation):
    args, events, _, latest = _two_packages(tmp_path, mutation)
    state = args["state_dir"]
    log = EventLog(state / "events.jsonl")
    for event in events:
        log.append(event)
    orch = Orchestrator(state, _config(state), _RecordingTransport())
    ready = []
    monkeypatch.setattr(orch, "_maybe_start_writer_fanout", ready.append)
    event = ZfEvent(type="flow.gap_plan.ready", correlation_id="run-1", payload={
        "workflow_run_id": "run-1", "pdd_id": "GOAL-1", "flow_kind": "prd",
        "task_map_ref": "r1.json", "task_map_generation": "g1",
        "plan_artifact_package_digest": events[0].payload["package_digest"],
        "gap_tasks": [_task_delta()],
    })
    result = orch._bridge_gap_plan_ready_to_task_map(event)
    assert result.action == "bridge", result
    current = next(p for p in latest["produced"] if p["logical_name"] == "task_map")
    assert ready[0].payload["amend_of"] == current["ref"]
    assert ready[0].payload["supersedes_task_map_generation"] == "g2"
    amended = json.loads((state / ready[0].payload["task_map_ref"]).read_text())
    assert {t["task_id"] for t in amended["tasks"]} == {"T1-FIX", "OTHER"}
    assert event.payload["task_map_generation"] == "g1"
    orch._bridge_gap_plan_ready_to_task_map(event)
    assert len(ready) == 1


@pytest.mark.parametrize("mutation", ["task", "unscoped", "tampered", "binding", "input"])
def test_cross_generation_gap_rejects_changed_or_unproven_targets(tmp_path, mutation):
    from zf.runtime.gap_plan_baseline import gap_plan_amend_baseline
    args, events, _, latest = _two_packages(tmp_path, mutation)
    state = args["state_dir"]
    gap = _task_delta()
    payload = {"workflow_run_id": "run-1", "task_map_generation": "g1"}
    if mutation == "unscoped":
        gap.pop("supersedes_task_ids")
    if mutation == "tampered":
        port = next(p for p in latest["produced"] if p["logical_name"] == "task_map")
        (state / port["ref"]).write_text('{"tasks": []}')
    if mutation == "binding":
        payload["plan_artifact_package_digest"] = "wrong"
    with pytest.raises(ValueError):
        gap_plan_amend_baseline(state_dir=state, events=events, payload=payload,
            fallback_ref="r1.json", required=True, gap_tasks=[gap])


def test_unselected_execution_spec_update_keeps_task_delta_rebasable(tmp_path):
    from zf.runtime.gap_plan_baseline import gap_plan_amend_baseline
    args, events, _, latest = _two_packages(tmp_path, "execution")
    ref, generation = gap_plan_amend_baseline(state_dir=args["state_dir"], events=events,
        payload={"workflow_run_id": "run-1", "task_map_generation": "g1"},
        fallback_ref="r1.json", required=True, gap_tasks=[_task_delta()])
    assert generation == "g2"
    assert ref == next(p["ref"] for p in latest["produced"] if p["logical_name"] == "task_map")


def test_selected_execution_spec_drift_remains_rejected(tmp_path):
    from zf.runtime.gap_plan_baseline import gap_plan_amend_baseline
    args, events, _, _ = _two_packages(tmp_path, "execution", selected_port="execution_spec")
    with pytest.raises(PlanArtifactPackageError, match="selected input changed: execution_spec"):
        gap_plan_amend_baseline(state_dir=args["state_dir"], events=events,
            payload={"workflow_run_id": "run-1", "task_map_generation": "g1"},
            fallback_ref="r1.json", required=True, gap_tasks=[_task_delta()])


def test_scoped_delta_package_admission_keeps_current_port_and_original_partition(tmp_path):
    from zf.runtime.module_gap_plan import build_gap_task_map_amend
    from zf.runtime.plan_artifact_package import prepare_plan_artifact_package
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.task_scoped_plan_inputs import _predecessor_input_paths
    args, events, _, latest = _two_packages(tmp_path, "unrelated")
    state = args["state_dir"]
    port = next(p for p in latest["produced"] if p["logical_name"] == "task_map")
    amended = build_gap_task_map_amend(hydrate_sidecar_ref(state, port).payload,
        gap_tasks=[_task_delta()], supersedes_task_map_ref=port["ref"], supersedes_task_ids=["T1"])
    claims = next(p for p in latest["produced"] if p["logical_name"] == "goal_claim_set")
    amended["goal_claims"] = hydrate_sidecar_ref(state, claims).payload["claims"]
    (state / "r3.json").write_text(json.dumps(amended))
    package, _, _ = prepare_plan_artifact_package(events=events, payload={
        "task_map_ref": "r3.json", "task_map_generation": "g3", "requirement_spec_ref": "requirement.md",
        "resume_scope": "gap_tasks_only", "amend_of": port["ref"],
        "supersedes_task_map_generation": "g2", "task_ids": ["T1-FIX"]}, **args)
    ports = {p["logical_name"]: p for p in package["produced"] + package["inherited"]}
    original = next(p for p in latest["produced"] if p["logical_name"] == "task_execution_input")
    assert ports["task_execution_input"]["sha256"] == original["sha256"]
    paths, parent = _predecessor_input_paths(state, ports=ports, name="task_execution_input",
        body=hydrate_sidecar_ref(state, ports["task_execution_input"]).payload,
        task_id="T1-FIX", workflow_run_id="run-1")
    assert parent == "T1" and paths == ["$.shared", "$.tasks[1]"]


def test_gap_rejects_intermediate_shared_change_even_when_restored(tmp_path):
    from zf.runtime.gap_plan_baseline import gap_plan_amend_baseline
    from zf.runtime.plan_artifact_package import prepare_plan_artifact_package, package_event_payload
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    args, events, original, latest = _two_packages(tmp_path, "input")
    restored = next(p for p in original["produced"] if p["logical_name"] == "task_execution_input")
    package, descriptor, _ = prepare_plan_artifact_package(events=events, payload={
        "task_map_ref": "r2.json", "task_map_generation": "g3", "requirement_spec_ref": "requirement.md",
        "resume_scope": "gap_tasks_only", "amend_of": "r2.json", "supersedes_task_map_generation": "g2",
        "plan_ports": [{"logical_name": "task_execution_input", "body": hydrate_sidecar_ref(args["state_dir"], restored).payload}]}, **args)
    events.append(ZfEvent(type="plan.artifact_package.admitted", correlation_id="run-1",
        payload=package_event_payload(package, descriptor, status="admitted")))
    with pytest.raises(PlanArtifactPackageError, match="selected input changed: task_execution_input"):
        gap_plan_amend_baseline(state_dir=args["state_dir"], events=events,
            payload={"workflow_run_id": "run-1", "task_map_generation": "g1"},
            fallback_ref="r1.json", required=True, gap_tasks=[_task_delta()])
