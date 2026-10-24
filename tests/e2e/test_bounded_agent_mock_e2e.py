"""Approval, real Kernel dispatch and credential-bound bounded Agent results."""
import json

import pytest

from tests.test_bounded_agent import approved_runtime
from tests.test_workflow_invoke_pattern_bridge import _run_until_sent
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential, ResultSubmitError
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.call_result_envelope import write_immutable_json_sidecar


def child_for(state, stage):
    manifests = [json.loads(p.read_text()) for p in (state / "fanouts").glob("*/manifest.json")]
    manifest = next(m for m in manifests if m["stage_id"] == stage)
    child, = manifest["children"]
    return child["payload"]


def consume_inputs(state, child):
    manifest = hydrate_sidecar_ref(state, child["attempt_source_manifest"]).payload
    for required in child.get("required_reads", []):
        read_attempt_artifact(state, manifest=manifest,
                              source_id=required["source_id"], artifact_id=required["artifact_id"],
                              json_path=required.get("json_path", "$"),
                              actor=child["role_instance"], role=child["role_instance"])


def submit(state, writer, child, semantic):
    token = provision_role_submit_credential(state, child["role_instance"], rotate=False)
    return SemanticResultSubmitService(state_dir=state, event_log=writer.event_log, event_writer=writer).submit(
        operation_id=child["operation_id"], semantic_result=semantic,
        role_instance=child["role_instance"], credential=token.read_text().strip(),
    )


def test_bounded_required_reads_cli_uses_immutable_operation_policy(tmp_path, monkeypatch, capsys):
    from zf.cli.main import main
    from zf.runtime.artifact_read_capability import (
        provision_role_artifact_read_credential, bind_attempt_artifact_read_capability,
    )

    state, writer, _, _, _, _, _ = approved_runtime(tmp_path, monkeypatch)
    child = child_for(state, "agent-execute")
    manifest = hydrate_sidecar_ref(state, child["attempt_source_manifest"]).payload
    token = provision_role_artifact_read_credential(
        state, "bounded-executor", role_name="bounded-executor", provider="mock",
    )
    bind_attempt_artifact_read_capability(
        state, operation_id=child["operation_id"], attempt_id=manifest["attempt_id"],
        role_instance="bounded-executor", manifest=manifest,
    )
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "bounded-executor")
    monkeypatch.setenv("ZF_ROLE_NAME", "bounded-executor")
    monkeypatch.setenv("ZF_ROLE_BACKEND", "mock")
    monkeypatch.setenv("ZF_ARTIFACT_READ_TOKEN_FILE", str(token))
    monkeypatch.setenv("ZF_ARTIFACT_PURPOSE", manifest.get("read_purpose", ""))
    capsys.readouterr()
    assert main(["artifact", "read", "--attempt", manifest["attempt_id"],
                 "--required", "--state-dir", str(state)]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == len(child["required_reads"])
    assert rows


def test_cancellation_fences_late_result_and_restart(tmp_path, monkeypatch):
    from zf.runtime.orchestrator import Orchestrator

    state, writer, invoke, runtime, transport, client, store = approved_runtime(tmp_path, monkeypatch)
    executor = child_for(state, "agent-execute")
    consume_inputs(state, executor)
    payload = {"run_id": invoke.payload["workflow_run_id"], "reason": "owner cancelled"}
    response = client.post("/api/actions/run-cancel", json=payload,
                           headers={"x-zf-web-token": "bounded-test-token"})
    assert response.status_code < 400, response.text
    for _ in range(3):
        runtime.run_once()
    assert "bounded-executor" in transport.stopped
    with pytest.raises(ResultSubmitError):
        submit(state, writer, executor, {"execution_status": "completed", "outputs": {"result": {"text": "late"}}})
    restored = Orchestrator(state, runtime.config, transport, project_root=tmp_path)
    restored._on_workflow_invoke_requested(invoke)
    restored.run_once()
    assert len(transport.sent) == 1
    assert store.get("TASK-BOUNDED").status != "done"
    assert not any(e.type == "artifact.delivery.verified" for e in writer.event_log.read_all())


@pytest.mark.parametrize("limit", ["tokens", "wall_clock"])
def test_bounded_operation_budget_stops_worker_and_rejects_late_result(tmp_path, monkeypatch, limit):
    from datetime import datetime
    from zf.runtime.workflow_budget_guard import enforce_active_workflow_budgets
    from zf.runtime.workflow_operation import load_workflow_operation

    state, writer, _, runtime, transport, _, store = approved_runtime(tmp_path, monkeypatch)
    executor = child_for(state, "agent-execute")
    operation = load_workflow_operation(writer.event_log, executor["operation_id"])
    if limit == "tokens":
        runtime.cost_tracker.record_usage("bounded-executor", 100001, 0, instance_id="bounded-executor",
                                          source_event_id="bounded-budget")
        emitted = enforce_active_workflow_budgets(runtime)
    else:
        emitted = enforce_active_workflow_budgets(
            runtime, now_epoch=datetime.fromisoformat(operation["started_at"]).timestamp() + 601,
        )
    exceeded = [e for e in emitted if e.type == "workflow.budget.exceeded"]
    assert any(limit in e.payload["exceeded_dimensions"] for e in exceeded)
    assert "bounded-executor" in transport.stopped
    with pytest.raises(ResultSubmitError):
        submit(state, writer, executor, {"outputs": {"result": {"text": "late"}}})
    assert store.get("TASK-BOUNDED").status != "done"


@pytest.mark.parametrize("verdict", ["passed", "rejected"])
def test_bounded_executor_cannot_finish_without_output_or_independent_verification(tmp_path, monkeypatch, verdict):
    state, writer, invoke, runtime, transport, _, store = approved_runtime(tmp_path, monkeypatch)
    assert invoke.payload["result_protocol_mode"] == "blocking"
    executor = child_for(state, "agent-execute")
    unread = json.loads((state / executor["result_scratch_ref"]).read_text())
    unread.update(execution_status="completed", summary="Claimed report without input consumption",
                  outputs={"result": {"summary": "Claimed requirement review", "findings": []}})
    with pytest.raises(ResultSubmitError):
        submit(state, writer, executor, unread)
    consume_inputs(state, executor)
    semantic = json.loads((state / executor["result_scratch_ref"]).read_text())
    with pytest.raises(ResultSubmitError):
        submit(state, writer, executor, semantic)
    assert len(transport.sent) == 1
    assert store.get("TASK-BOUNDED").status != "done"
    semantic.update(execution_status="completed", summary="Bounded report produced",
                    outputs={"result": {"summary": "Requirement reviewed", "findings": []}})
    submit(state, writer, executor, semantic)
    _run_until_sent(runtime, transport, 2)
    assert len(transport.sent) == 2
    assert transport.sent[1][0] == "bounded-verifier"
    assert store.get("TASK-BOUNDED").status != "done"
    verifier = child_for(state, "agent-verify")
    assert verifier["workflow_generation"] == executor["workflow_generation"]
    assert verifier["operation_id"] != executor["operation_id"]
    consume_inputs(state, verifier)
    verification = json.loads((state / verifier["result_scratch_ref"]).read_text())
    claims = hydrate_sidecar_ref(state, {"ref": verifier["goal_claim_set_ref"],
                                       "sha256": verifier["goal_claim_set_digest"]}).payload
    evidence = write_immutable_json_sidecar(
        state, {"verdict": verdict, "checks": ["report answers supplied requirement"]},
        root="bounded-verification", kind="verification_evidence", schema_version="bounded-evidence.v1",
        created_by="bounded-verifier",
    )
    verification.update(
        verdict=verdict, recommended_action="complete" if verdict == "passed" else "replan",
        goal_coverage=[{"goal_claim_id": claim["goal_claim_id"],
                        "status": "closed" if verdict == "passed" else "open",
                        "supporting_artifact_refs": [verification["artifacts"][0]["ref"]]}
                       for claim in claims["claims"]],
        verification_evidence_refs=[verification["artifacts"][0]["ref"]] if verdict == "passed" else [evidence["ref"]],
        open_gap_refs=[] if verdict == "passed" else [evidence["ref"]],
        outputs={"verification": {"verdict": verdict, "evidence_ref": evidence["ref"]}},
    )
    # A new process consumes the same durable identities and prepared result.
    from zf.runtime.orchestrator import Orchestrator
    runtime = Orchestrator(state, runtime.config, transport, project_root=tmp_path)
    submit(state, writer, verifier, verification)
    for _ in range(8):
        runtime.run_once()
    events = writer.event_log.read_all()
    assert bool([e for e in events if e.type == "artifact.delivery.verified"]) == (verdict == "passed")
    assert bool([e for e in events if e.type == "run.goal.completed"]) == (verdict == "passed")
    assert (store.get("TASK-BOUNDED").status == "done") == (verdict == "passed")
    assert sum(role == "bounded-executor" for role, *_ in transport.sent) == 1
