"""The current accepted research must survive both PRD workflow entry surfaces."""

from __future__ import annotations

import hashlib
import json

import pytest

from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.workflow_anchor import bind_workflow_request_to_task, mark_workflow_managed_task
from zf.runtime.workflow_inputs import hydrate_request_workflow_input_manifest
from zf.runtime.workflow_origin import build_workflow_origin_binding, workflow_origin_digest
from zf.runtime.workflow_requests import (
    adopt_workflow_research_result,
    bind_workflow_proposal,
    load_workflow_request,
    register_workflow_intake,
    revise_workflow_request,
)
from tests.test_workflow_inputs import _config
from tests.test_workflow_requests import _request_fixture


@pytest.mark.parametrize("surface", ["channel", "kanban_agent"])
def test_adopted_research_reaches_prd_inputs_and_stays_pinned_after_proposal(tmp_path, surface):
    state, source, writer = _request_fixture(tmp_path)
    manifest = json.loads(source.read_text())
    origin = build_workflow_origin_binding(
        source="web", project_id="demo", channel_id="channel-1" if surface == "channel" else "",
        conversation_id="kanban:demo" if surface == "kanban_agent" else "",
    )
    forged = {"source_field": "workflow.research.adopted", "ref": "research/forged.md"}
    source.write_text(json.dumps({
        **manifest, "kind": "prd", "origin_binding": origin,
        "artifact_refs": [*manifest.get("artifact_refs", []), forged],
    }))
    register_workflow_intake(state, source, actor="owner", writer=writer)
    request = revise_workflow_request(
        state, source, actor="owner", kind="prd", objective="Correct quantities before posting.",
        source_root=str(tmp_path), target_root=str(tmp_path), open_questions=[], confirm=True, writer=writer,
    )
    task = bind_workflow_request_to_task(
        mark_workflow_managed_task(Task(id="T-DRAFT", title="PRD")),
        request_id=request["request_id"], request_revision=request["revision"],
        origin_binding_digest=workflow_origin_digest(origin),
    )
    TaskStore(state / "kanban.json").add(task)

    def adopt(name: str, summary: str) -> tuple[str, str]:
        ref = f"research/{name}.md"
        body = state / ref
        body.parent.mkdir(exist_ok=True)
        body.write_text(summary + "\n")
        digest = hashlib.sha256(body.read_bytes()).hexdigest()
        adopt_workflow_research_result(
            state, request["request_id"], expected_revision=request["revision"],
            artifact_ref=ref, artifact_digest=digest, summary=summary, actor="owner",
            source_event_id="result-" + name, result_event_id="result-" + name,
            task_id=task.id, workflow_run_id="research-run", terminal_event_id="terminal-" + name,
            writer=writer,
        )
        return ref, digest

    first_ref, first_digest = adopt("field-guide-rC", "Adopt draft corrections for the receiving clerk.")
    prepared = load_workflow_request(state, request["request_id"])
    effective = hydrate_request_workflow_input_manifest(state, prepared)
    adopted = [row for row in effective["artifact_refs"] if isinstance(row, dict) and row.get("ref") == first_ref]
    assert forged not in effective["artifact_refs"]
    assert len(adopted) == 1
    assert adopted[0]["sha256"] == first_digest
    assert adopted[0]["result_event_id"] == "result-field-guide-rC"
    assert adopted[0]["request_revision"] == request["revision"]

    bind_workflow_proposal(
        state, request_id=request["request_id"], request_revision=request["revision"],
        proposal_ref={"ref": "proposals/frozen.json", "sha256": "a" * 64},
        proposal_digest="a" * 64, actor="owner", writer=writer,
    )
    newer_ref, _ = adopt("field-guide-rD", "Additional behavior under consideration.")
    # A current Request may receive additional context after its Proposal was frozen.
    reused = register_workflow_intake(state, source, actor="owner", writer=writer)
    assert hydrate_request_workflow_input_manifest(state, reused)["artifact_refs"] == effective["artifact_refs"]
    assert hydrate_request_workflow_input_manifest(state, prepared) == effective
    if surface == "channel":
        writer.emit("channel.created", actor="owner", payload={"channel_id": "channel-1", "name": "Receiving"})
        writer.emit("channel.state_update.posted", actor="runtime", payload={
            "channel_id": "channel-1", "thread_id": "main", "status": "research_completed",
            "summary": "Unadopted sibling report about shipping reminders.",
            "refs": {"artifact_refs": [{"kind": "research_report", "path": "research/sibling.md"}]},
        })
    service = ControlledActionService(
        state, writer, config=_config(state), project_root=tmp_path, actor="owner", source="kanban-agent",
    )
    requested = writer.emit("web.action.requested", actor="web", payload={"action": "workflow-invoke"})
    run = service.execute(
        action="workflow-invoke", requested_action="workflow-invoke", requested=requested,
        payload={
            "task_id": task.id, "request_id": request["request_id"],
            "request_revision": request["revision"], "artifact_refs": [forged],
            "pattern_id": "prd-draft", "expected_output": "A PRD for receiving inventory.",
        },
    )
    assert run["ok"], run
    prompt = (state / run["workflow_prompt_ref"]).read_text()
    assert first_digest in prompt and first_ref in prompt
    assert newer_ref not in prompt
    assert "research/sibling.md" not in prompt
    assert forged["ref"] not in prompt
    dispatched = json.loads((state / run["workflow_input_manifest_ref"]).read_text())
    assert adopted[0] in dispatched["artifact_refs"]


def test_bound_prd_to_planner_reader_task_and_plan_preview(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient
    from zf.runtime.plan_preview_artifacts import bind_plan_approval_preview
    from zf.runtime.task_workflow_plans import task_workflow_binding_digest
    from zf.runtime.workflow_request_acceptance import build_task_workflow_input_contract
    from zf.runtime.workflow_task_input_artifacts import materialize_channel_prd_reader_source
    from zf.web.server import create_app

    from tests.test_channel_prd_task_contract import _execute, _ready_prd_fixture, _task_payload

    ref = "Partner serialization handbook, edition C section 4.1"
    reason = "Existing reconciliation readers require strict JSON."
    need = {
        "id": "REQ-JSON", "status": "covered", "section": "Acceptance Criteria",
        "source_refs": [ref], "reason": reason, "acceptance_ids": ["AC-01"],
    }
    state, writer, service, authority = _ready_prd_fixture(
        tmp_path, implementation_start=True, adopted_requirement=need,
    )
    created = _execute(service, writer, "create-task", _task_payload(authority))
    assert created["ok"], created
    task = TaskStore(state / "kanban.json").get("TASK-CHANNEL-PRD")
    inputs = build_task_workflow_input_contract(task, task_contract_digest=task_workflow_binding_digest(task))
    criterion, = inputs["acceptance_criteria"]
    assert criterion["source_refs"] == [ref] and criterion["id"] in need["acceptance_ids"]
    assert inputs["exclusions"] == ["YAML output"]
    artifacts = state / "artifacts"
    artifacts.mkdir(exist_ok=True)
    reader = Path(materialize_channel_prd_reader_source(
        state_dir=state, workflow_dir=artifacts, input_contract=inputs,
    ))
    # A planner result fixture binds the actual compiled Task, the independent command
    # and the materialized source. The semantic planner itself is a real-provider tier.
    plan = artifacts / "plan.md"
    plan.write_text(f"# Strict JSON delivery\n\n{task.id} implements {criterion['id']}.\n")
    task_map = artifacts / "plan-tasks.json"
    task_map.write_text(json.dumps({"tasks": [{
        "task_id": task.id, "owner_role": "dev-format", "source_ref": criterion["source_refs"][0],
        "acceptance_criteria": [{"id": criterion["id"], "statement": criterion["criterion"]}],
        "validation": {"commands": task.contract.validation["commands"]},
    }]}))
    event_body = {
        "plan_id": "plan-adopt", "trace_id": "run-adopt", "task_map_ref": str(task_map), "task_count": 1,
        "plan_artifact_ref": str(plan), "authority_refs": [str(reader)],
    }
    bound = bind_plan_approval_preview(state, event_body, tmp_path, event_body)
    writer.emit("plan.approval.requested", actor="owner", payload=bound)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "a1-test-token")
    with TestClient(create_app(state, project_root=tmp_path, config=service.config)) as client:
        client.headers["x-zf-web-token"] = "a1-test-token"
        response = client.get("/api/projects/default/channels/ch-prd-task/prd-preview", params={
            "thread_id": "main", "revision": 1, "digest": authority["source_digest"],
        })
        assert response.status_code == 200, response.text
        prd = response.json()["markdown"]
        assert prd.rstrip() == reader.read_text().rstrip()
        assert ref in prd and reason in prd
        response = client.get("/api/plans/plan-adopt/preview")
        assert response.status_code == 200, response.text
        view = response.json()
        assert str(reader) in view["authority_refs"]
        row, = view["task_map_summary"]["tasks"]
        assert row["source_ref"] == ref and row["task_id"] == task.id
        assert row["owner_role"] == "dev-format"
        assert row["acceptance_criteria"][0]["id"] == "AC-01"
        assert "VC-01" in view["task_map_markdown"]
