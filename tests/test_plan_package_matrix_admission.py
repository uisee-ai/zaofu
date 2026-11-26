"""Cross-port closure applies to new packages, including incremental callers."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from zf.core.events import ZfEvent
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.plan_artifact_package import reduce_plan_artifact_packages
from zf.runtime.plan_artifact_package_runtime import admit_task_map_trigger_package
from zf.runtime.run_contract import stable_json_sha256, write_run_contract
from tests.test_module_gap_plan_runtime import _state
from tests.test_plan_candidate_preflight import _plan_ports, _task_map


@pytest.fixture
def case(tmp_path):
    state, log, transport, runtime = _state(tmp_path)
    runtime.config.workflow.flow_metadata = {
        "flow_kind": "prd", "artifact_package": {"mode": "blocking"},
    }
    contract = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    write_run_contract(state, {**contract, "contract_digest": stable_json_sha256(contract)})
    (state / "requirement.md").write_text("The command succeeds.\n")
    body = _task_map()
    body["tasks"][0]["owner_role"] = "dev"
    body["required_plan_ports"] = [port["logical_name"] for port in _plan_ports()]
    return SimpleNamespace(state=state, log=log, runtime=runtime, transport=transport,
                           body=body, ports=_plan_ports())


def _ready(case, *, body=None, ports=None, revision="first"):
    path = case.state / f"{revision}.json"
    path.write_text(json.dumps(body if body is not None else case.body))
    return ZfEvent(type="task_map.ready", correlation_id="matrix-run", payload={
        "pdd_id": "MATRIX", "workflow_run_id": "matrix-run", "flow_kind": "prd",
        "task_map_ref": path.name, "requirement_spec_ref": "requirement.md",
        "plan_ports": deepcopy(ports if ports is not None else case.ports),
    })


def _admit(case, event):
    return admit_task_map_trigger_package(case.runtime, event, case.runtime.config.workflow.stages)


def _rejections(case):
    return [e for e in case.log.read_all() if e.type == "plan.artifact_package.rejected"]


@pytest.mark.parametrize("fault", ["acceptance", "command", "capability"])
def test_new_package_rejects_incomplete_declared_matrix_before_tasks(case, fault):
    if fault == "acceptance":
        case.ports[1]["body"]["acceptance"] = []
    elif fault == "command":
        case.ports[2]["body"]["commands"] = []
    else:
        case.ports[0]["body"]["capabilities"] = []
    event = _ready(case)
    case.runtime._maybe_start_writer_fanout(event)
    assert _rejections(case), "incomplete matrix was admitted"
    assert "matrix" in _rejections(case)[-1].payload["reason"]
    assert not case.runtime.task_store.list_all()
    assert not case.transport.sent
    assert not any(e.type == "plan.artifact_package.admitted" for e in case.log.read_all())


@pytest.mark.parametrize("mode", ["off", "shadow", "blocking"])
def test_matrix_failure_preserves_package_mode(case, mode):
    case.runtime.config.workflow.flow_metadata["artifact_package"]["mode"] = mode
    case.ports[1]["body"]["acceptance"] = []
    event = _ready(case)
    result = _admit(case, event)
    if mode == "off":
        assert result is event
        assert not _rejections(case)
        assert not list((case.state / "artifacts/plan-packages").glob("*.json"))
    elif mode == "shadow":
        assert result is event
        assert _rejections(case)[-1].payload["mode"] == mode
    else:
        assert result is None
        assert _rejections(case)[-1].payload["mode"] == mode


@pytest.mark.parametrize("legacy", ["unrequired", "no_enrichment", "string_acceptance", "partial"])
def test_legacy_and_partial_matrix_contracts_keep_existing_admission(case, legacy):
    case.ports[1]["body"]["acceptance"] = []
    if legacy == "unrequired":
        case.body.pop("required_plan_ports")
    elif legacy == "no_enrichment":
        for port in case.ports:
            port["body"].pop("metadata")
    elif legacy == "string_acceptance":
        case.body["tasks"][0]["acceptance_criteria"] = ["The command succeeds."]
        case.body["tasks"][0].pop("validation")
    else:
        case.body["required_plan_ports"] = ["acceptance_matrix"]
    assert _admit(case, _ready(case)) is not None
    assert not _rejections(case)


def test_same_generation_reuses_exact_admitted_package(case, monkeypatch):
    event = _ready(case)
    first = _admit(case, event)
    assert first is not None
    before = case.log.read_all()
    def unexpected_recheck(**_kwargs):
        pytest.fail("same-generation replay must retain admitted evidence")
    monkeypatch.setattr("zf.runtime.plan_package_matrix_admission.validate_package_matrix_closure",
                        unexpected_recheck)
    repeated = _admit(case, event)
    assert repeated.payload["plan_artifact_package_digest"] == first.payload["plan_artifact_package_digest"]
    assert case.log.read_all() == before


@pytest.mark.parametrize("required", [False, True])
def test_optional_real_e2e_port_is_not_promoted_to_required(case, required):
    case.ports.append({"logical_name": "real_e2e_matrix", "body": {
        "schema_version": "real-e2e-matrix.v1", "status": "ready",
        "metadata": {"enrichment_contract": {"status": "fulfilled"}},
        "rows": [{"id": "REAL-1", "command_id": "unknown-command", "acceptance_ids": ["AC-1"]}],
    }})
    if required:
        case.body["required_plan_ports"].append("real_e2e_matrix")
    result = _admit(case, _ready(case))
    if required:
        assert result is None
        assert "real_e2e_command_ref_unknown" in _rejections(case)[-1].payload["reason"]
    else:
        assert result is not None
        assert not _rejections(case)


@pytest.mark.parametrize("bad_acceptance", ["mixed", "canonical_commands", "row", "container"])
def test_modern_matrix_cannot_downgrade_through_malformed_acceptance(case, bad_acceptance):
    criteria = case.body["tasks"][0]["acceptance_criteria"]
    if bad_acceptance == "mixed":
        criteria.append("legacy-looking stray row")
    elif bad_acceptance == "canonical_commands":
        case.body["tasks"][0]["acceptance_criteria"] = ["Lost canonical AC identity"]
    elif bad_acceptance == "row":
        criteria.append(42)
    else:
        case.body["tasks"][0]["acceptance_criteria"] = "not a row list"
    assert _admit(case, _ready(case)) is None
    assert "acceptance" in _rejections(case)[-1].payload["reason"]


def _second_task():
    task = json.loads(json.dumps(_task_map()["tasks"][0]).replace("TASK-1", "TASK-2")
                      .replace("AC-1", "AC-2").replace("test-command", "test-second")
                      .replace("src/command.py", "src/second.py")
                      .replace("tests/test_command.py", "tests/test_second.py"))
    task.update(owner_role="dev", goal_claim_ids=[], source_refs=["requirement.md", "git:" + "a" * 40],
                base_commit="a" * 40)
    return task


def _complete_ports():
    ports = _plan_ports()
    ports[0]["body"]["capabilities"][0]["task_ids"].append("TASK-2")
    ports[0]["body"]["capabilities"][0]["acceptance_ids"].append("AC-2")
    ports[1]["body"]["acceptance"].append({"id": "AC-2", "capability_id": "CAP-1",
        "task_id": "TASK-2", "verification_command_ids": ["test-second"]})
    ports[2]["body"]["commands"].append({"id": "test-second",
        "command": "pytest -q tests/test_second.py", "acceptance_ids": ["AC-2"]})
    return ports


def test_inherited_matrices_are_checked_and_explicit_repairs_replace_them(case):
    first = _admit(case, _ready(case))
    assert first is not None
    old_digest = first.payload["plan_artifact_package_digest"]
    amended = deepcopy(case.body)
    amended["tasks"].append(_second_task())
    assert _admit(case, _ready(case, body=amended, ports=[], revision="missing")) is None
    reason = _rejections(case)[-1].payload["reason"]
    assert "acceptance_set_mismatch" in reason and "command_registry_mismatch" in reason
    current = reduce_plan_artifact_packages(case.log.read_all(), workflow_run_id="matrix-run")["current"]
    assert current["package_digest"] == old_digest
    fixed = _admit(case, _ready(case, body=amended, ports=_complete_ports(), revision="fixed"))
    assert fixed is not None
    assert fixed.payload["plan_artifact_package_digest"] != old_digest


@pytest.mark.parametrize("route", ["controlled", "bridge"])
def test_actual_gap_routes_reject_stale_inherited_matrix_before_materialization(case, route):
    first = _admit(case, _ready(case))
    assert first is not None
    payload = {**first.payload, "task_map_generation": first.payload["task_map_generation"],
               "source_commit": "a" * 40, "candidate_base_commit": "a" * 40}
    payload.pop("plan_ports")
    task = _second_task()
    if route == "controlled":
        service = ControlledActionService(case.state, case.runtime.event_writer,
            config=case.runtime.config, project_root=case.runtime.project_root)
        source = service.writer.emit("flow.discovery.failed", actor="test", payload={})
        requested = service.writer.emit("control.action.requested", actor="operator", payload={})
        result = service.execute(action="candidate-rework-apply", requested_action="candidate-rework-apply",
            requested=requested, payload={**payload, "source_event_id": source.id,
                "candidate_rework_action": "retrigger", "rework_summary": {"gap_tasks": [task]}})
        assert result["ok"], result
        ready = next(e for e in reversed(case.log.read_all()) if e.type == "task_map.ready")
        case.runtime._maybe_start_writer_fanout(ready)
    else:
        event = ZfEvent(type="flow.gap_plan.ready", correlation_id="matrix-run",
                       payload={**payload, "gap_tasks": [task]})
        result = case.runtime._bridge_gap_plan_ready_to_task_map(event)
        assert result.action == "bridge", result
    assert _rejections(case), "gap route skipped cross-port admission"
    assert "acceptance_set_mismatch" in _rejections(case)[-1].payload["reason"]
    assert not case.runtime.task_store.list_all()
    assert not case.transport.sent


@pytest.mark.parametrize(("owner", "tier", "expected"), [
    ("candidate_verify", "e2e", {"verification_owner", "verification_tier"}),
    ("task_verify", "runtime", set()),
    (None, None, set()),
])
def test_explicit_same_ac_binding_matches_in_preflight_and_package(case, tmp_path, owner, tier, expected):
    from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
    from tests.test_plan_candidate_preflight import _matrix_metadata, _source_index

    row = next(p for p in case.ports if p["logical_name"] == "acceptance_matrix")["body"]["acceptance"][0]
    if owner is not None:
        row["verification_owner"] = owner
    if tier is not None:
        row["verification_tier"] = tier
    result = evaluate_plan_candidate_preflight(
        state_dir=case.state, project_root=tmp_path,
        reports=[{"report": {"task_map": case.body, "source_index": _source_index(),
                             "plan_ports": case.ports, "plan_md": "# Plan\n\nImplement TASK-1."}}],
        manifest={"stage_id": "prd-plan", "trigger_payload": {"flow_kind": "prd", "prd_ref": "docs/requirement.md"}},
        metadata=_matrix_metadata())
    fields = {e["field"] for e in result["errors"] if e["code"] == "acceptance_binding_mismatch"}
    assert fields == {f"acceptance_matrix.AC-1.{field}" for field in expected}
    admitted = _admit(case, _ready(case))
    if expected:
        assert admitted is None
        for field in fields:
            assert field in _rejections(case)[-1].payload["reason"]
    else:
        assert admitted is not None
        assert not _rejections(case)


@pytest.mark.parametrize("declare", [False, True])
def test_flow_required_and_produced_ports_need_a_task_map_selection_contract(case, declare):
    data = deepcopy(case.body)
    data["tasks"][0]["evidence_contract"] = {"plan_input_selection": {
        "schema_version": "task-plan-input-selection.v1",
        "implementation": ["task_execution_input", "requirement_spec"], "task-verify": ["task_execution_input"],
    }}
    case.ports += [
        {"logical_name": "execution_spec", "body": {"tasks": [{"task_id": "TASK-1"}]}},
        {"logical_name": "real_e2e_matrix", "body": {"rows": []}},
        {"logical_name": "task_execution_input", "body": {"schema_version": "task-scoped-plan-input.v1",
         "shared": {}, "tasks": [{"task_id": "TASK-1", "body": {"required": "the approved sample"}}]}},
    ]
    global_names = [port["logical_name"] for port in case.ports]
    case.runtime.config.workflow.flow_metadata["artifact_package"]["required_ports"] = [
        "requirement_spec", "task_map", "planning_result", "goal_claim_set", *global_names,
    ]
    if declare:
        # Existing producer spelling is canonicalized by the immutable Package.
        data["required_plan_ports"] = ["prd_ref", *global_names]
    else:
        data.pop("required_plan_ports")
    admitted = _admit(case, _ready(case, body=data))
    if declare:
        assert admitted is not None, [e.payload.get("reason") for e in _rejections(case)]
        assert admitted.payload["plan_artifact_package_digest"]
    else:
        assert admitted is None
        reason = _rejections(case)[-1].payload["reason"]
        assert "task_map.required_plan_ports" in reason and "unbound ports: requirement_spec, task_execution_input" in reason
