from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
import yaml

from zf.cli.flow import build_flow_intake, build_flow_submit_preview
from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_proposal import build_workflow_proposal
from zf.runtime.workflow_requests import (
    load_workflow_request,
    revise_workflow_request,
    workflow_request_path,
)
from zf.runtime.workflow_synthesis import (
    WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
    run_workflow_synthesis,
)
from zf.web.server import create_app


def _write_config(path: Path, *, lanes: int = 1) -> None:
    path.write_text(
        f"""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {{name: issue-demo}}
spec:
  lanes: {lanes}
  backend: mock
  issueRef: docs/intake/bug.md
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {{name: demo}}
spec:
  version: "1.0"
  project: {{name: demo, state_dir: .zf}}
""",
        encoding="utf-8",
    )


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    config_path = tmp_path / "zf.yaml"
    _write_config(config_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    app = create_app(
        state_dir,
        config=load_config(config_path),
        project_root=tmp_path,
    )
    return TestClient(app), state_dir


def _prd_client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    source = tmp_path / "docs" / "prd" / "request.md"
    source.parent.mkdir(parents=True)
    source.write_text("Build the confirmed product.\n", encoding="utf-8")
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        """\
apiVersion: zaofu.dev/v1
kind: PrdFlow
metadata: {name: prd-demo}
spec:
  lanes: 1
  backend: mock
  prdRef: docs/prd/request.md
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo, state_dir: .zf}
""",
        encoding="utf-8",
    )
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    app = create_app(
        state_dir,
        config=load_config(config_path),
        project_root=tmp_path,
    )
    return TestClient(app), state_dir


def _action_body(
    action_key: str,
    payload: dict,
) -> dict:
    return {
        "project_id": "default",
        "idempotency_key": action_key,
        "actor": "web",
        "payload": payload,
    }


def _write_generic_base_config(path: Path) -> None:
    path.write_text(
        yaml.safe_dump_all(
            [{
                "apiVersion": "zaofu.dev/v1",
                "kind": "ZfConfig",
                "metadata": {"name": "generic-web"},
                "spec": {
                    "version": "1.0",
                    "project": {"name": "generic-web", "state_dir": ".zf"},
                    "roles": [
                        {
                            "name": role,
                            "instance_id": role,
                            "backend": "mock",
                            "role_kind": "reader",
                        }
                        for role in (
                            "scoper",
                            "collector-a",
                            "collector-b",
                            "synthesizer",
                            "verifier",
                        )
                    ],
                    "workflow": {
                        "execution_profiles": {
                            "direct-v1": {"strategy": "direct"},
                        },
                    },
                },
            }],
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _generic_synthesis_candidate(request: dict) -> dict:
    return {
        "schema_version": WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
        "request_id": request["request_id"],
        "request_revision": request["revision"],
        "requirement_ref": request["requirement_spec_ref"],
        "requirement_digest": request["requirement_spec_digest"],
        "selected_flow_family": "Workflow",
        "short_flow_spec": {
            "flow_family": "Workflow",
            "intent": "research",
            "template": "evidence-synthesis-v1",
            "purpose": "Deliver a verified evidence synthesis.",
            "parameters": {
                "scoper_role": "scoper",
                "collector_roles": ["collector-a", "collector-b"],
                "synthesizer_role": "synthesizer",
                "verifier_role": "verifier",
                "artifact_name": "report",
                "artifact_kind": "report/markdown",
            },
        },
        "decision_rationale": "Use the registered research template.",
        "assumptions": [],
        "open_questions": [],
        "requested_roles": [
            "scoper",
            "collector-a",
            "collector-b",
            "synthesizer",
            "verifier",
        ],
        "requested_skills": [],
        "requested_profiles": ["direct-v1"],
        "completion_profile": {
            "id": "artifact_delivery",
            "delivery_policy": "report_only",
            "completion_threshold": "verified_artifacts",
            "required_artifacts": ["synthesize.report"],
        },
        "risk_hints": [],
    }


def test_generic_research_cli_preview_and_web_detail_share_proposal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-token")
    config_path = tmp_path / "zf.yaml"
    _write_generic_base_config(config_path)
    config = load_config(config_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    intake = build_flow_intake(
        kind="workflow",
        objective="Research and deliver one independently verified report.",
        backend="mock",
        project_id="generic-web",
        request_id="REQ-GENERIC-RESEARCH",
        acceptance=("The report answers the confirmed question.",),
        output=tmp_path / "docs" / "intake" / "REQ-GENERIC-RESEARCH.md",
    )
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    request = revise_workflow_request(
        state_dir,
        Path(intake["workflow_input_manifest_ref"]),
        actor="test",
        confirm=True,
        writer=writer,
    )
    synthesis = run_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id=request["request_id"],
        actor="test",
        candidate_result=_generic_synthesis_candidate(request),
    )
    preview = build_flow_submit_preview(
        config_path=config_path,
        intake_path=Path(intake["intake_ref"]),
        flow_kind="workflow",
        requested_by="test",
        reason="generic research proposal test",
        allow_missing_env=True,
        synthesis_result_ref=synthesis.result_ref,
    )

    assert preview["status"] in {"GO", "WARN"}
    assert preview["proposal"]["approval_status"] == "approvable"
    cli_proposal = preview["proposal"]
    app = create_app(
        state_dir,
        config=config,
        project_root=tmp_path,
    )
    client = TestClient(app)
    detail = client.get(
        "/api/projects/default/workflow-requests/REQ-GENERIC-RESEARCH"
    )

    assert detail.status_code == 200, detail.text
    web = detail.json()
    assert web["proposal"]["proposal_digest"] == (
        cli_proposal["proposal_digest"]
    )
    assert web["proposal"]["stage_graph"] == cli_proposal["stage_graph"]
    assert web["proposal"]["completion_profile"] == (
        cli_proposal["completion_profile"]
    )
    assert web["artifacts"]["config_diff"] == hydrate_sidecar_ref(
        state_dir,
        cli_proposal["config_diff_ref"],
    ).payload
    assert load_workflow_request(
        state_dir,
        "REQ-GENERIC-RESEARCH",
    )["proposal_digest"] == cli_proposal["proposal_digest"]
    replay = client.post(
        "/api/projects/default/workflow-submit",
        headers={"x-zf-web-token": "test-token"},
        json={
            "intake_ref": web["links"]["intake_ref"],
            "kind": "workflow",
            "apply": False,
            "allow_missing_env": True,
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["result"]["proposal"]["proposal_digest"] == (
        cli_proposal["proposal_digest"]
    )


def test_web_proposal_projection_and_exact_controlled_decisions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, state_dir = _client(tmp_path, monkeypatch)
    headers = {
        "x-zf-web-token": "test-token",
        "x-idempotency-key": "create-workflow-proposal",
    }
    proposed_response = client.post(
        "/api/projects/default/actions/workflow-request",
        headers=headers,
        json=_action_body(
            "create-workflow-proposal",
            {
                "request_id": "REQ-WEB-PROPOSAL",
                "kind": "issue",
                "objective": "Fix checkout expiry and add a regression test",
                "acceptance": ["Checkout remains valid for an active session."],
                "constraints": ["Do not change the public session API."],
                "backend": "mock",
                "allow_missing_env": True,
            },
        ),
    )

    assert proposed_response.status_code == 202, proposed_response.text
    proposed = proposed_response.json()
    assert proposed["status"] == "proposal_ready"
    request_id = proposed["request_id"]

    requests = client.get(
        "/api/projects/default/workflow-requests"
    ).json()
    assert requests["count"] == 1
    assert requests["items"][0]["objective"].startswith("Fix checkout")

    detail = client.get(
        f"/api/projects/default/workflow-requests/{request_id}"
    ).json()
    assert detail["requirement"]["acceptance"] == [
        "Checkout remains valid for an active session."
    ]
    assert detail["proposal"]["proposal_digest"] == proposed["proposal_digest"]
    assert detail["artifacts"]["short_flow_spec"]["documents"]
    assert detail["artifacts"]["effective_config"]["config"]
    assert detail["lifecycle"]["submitted"] is False

    submit_payload = {
        "request_id": request_id,
        "proposal_ref": proposed["proposal_ref"],
        "proposal_digest": proposed["proposal_digest"],
        "intake_ref": proposed["intake_ref"],
        "kind": "issue",
        "allow_missing_env": True,
    }
    unauthorized = client.post(
        "/api/projects/default/actions/workflow-submit",
        json=_action_body("approve-without-token", submit_payload),
    )
    assert unauthorized.status_code == 403

    stale = client.post(
        "/api/projects/default/actions/workflow-submit",
        headers={
            "x-zf-web-token": "test-token",
            "x-idempotency-key": "approve-stale",
        },
        json=_action_body(
            "approve-stale",
            {**submit_payload, "proposal_digest": "f" * 64},
        ),
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["status"] == "stale_proposal"

    config_before = (tmp_path / "zf.yaml").read_text(encoding="utf-8")
    approved = client.post(
        "/api/projects/default/actions/workflow-submit",
        headers={
            "x-zf-web-token": "test-token",
            "x-idempotency-key": "approve-current",
        },
        json=_action_body("approve-current", submit_payload),
    )
    replay = client.post(
        "/api/projects/default/actions/workflow-submit",
        headers={
            "x-zf-web-token": "test-token",
            "x-idempotency-key": "approve-current",
        },
        json=_action_body("approve-current", submit_payload),
    )

    assert approved.status_code == 202, approved.text
    assert replay.status_code == 202
    assert replay.json()["idempotency"]["status"] == "replayed"
    assert (tmp_path / "zf.yaml").read_text(encoding="utf-8") == config_before
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert len([
        event
        for event in events
        if event.type == "workflow.invoke.requested"
    ]) == 1
    submitted_detail = client.get(
        f"/api/projects/default/workflow-requests/{request_id}"
    ).json()
    assert submitted_detail["lifecycle"]["submitted"] is True
    assert submitted_detail["links"]["run_contract_ref"]


def test_web_clarification_prepares_current_prd_request_without_agent_roundtrip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, state_dir = _prd_client(tmp_path, monkeypatch)
    headers = {"x-zf-web-token": "test-token"}
    created = client.post(
        "/api/projects/default/workflow-intake",
        headers=headers,
        json={
            "request_id": "REQ-WEB-CLARIFY",
            "kind": "prd",
            "from": "docs/prd/request.md",
            "objective": "Build the confirmed product.",
            "backend": "mock",
        },
    )

    assert created.status_code == 200, created.text
    detail = client.get(
        "/api/projects/default/workflow-requests/REQ-WEB-CLARIFY"
    ).json()
    assert detail["status"] == "clarifying"
    assert detail["result"]["missing_required_fields"] == ["target_root"]
    assert detail["links"]["workflow_input_manifest_ref"] == detail["result"][
        "workflow_input_manifest_ref"
    ]

    clarified = client.post(
        "/api/projects/default/workflow-clarify",
        headers=headers,
        json={
            "request_id": "REQ-WEB-CLARIFY",
            "intake_ref": detail["links"]["intake_ref"],
            "target_root": str(tmp_path),
            "open_questions": [],
            "clarification_answers": [{
                "question": "Which target should receive the product?",
                "answer": str(tmp_path),
            }],
            "confirm": True,
            "requested_by": "web",
        },
    )

    assert clarified.status_code == 200, clarified.text
    assert clarified.json()["status"] == "ready"
    clarified_detail = client.get(
        "/api/projects/default/workflow-requests/REQ-WEB-CLARIFY"
    ).json()
    assert clarified_detail["requirement"]["clarification_answers"] == [{
        "question": "Which target should receive the product?",
        "answer": str(tmp_path),
    }]
    prepared = client.post(
        "/api/projects/default/workflow-submit",
        headers=headers,
        json={
            "request_id": "REQ-WEB-CLARIFY",
            "intake_ref": detail["links"]["intake_ref"],
            "kind": "prd",
            "apply": False,
            "allow_missing_env": True,
            "requested_by": "web",
        },
    )

    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert body["ok"] is True
    assert body["result"]["proposal"]["approval_status"] == "approvable"
    projection = load_workflow_request(state_dir, "REQ-WEB-CLARIFY")
    assert projection["status"] == "proposed"
    assert projection["proposal_ref"]["ref"]
    assert projection["proposal_digest"]


def test_web_partial_clarification_is_a_successful_persisted_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _state_dir = _prd_client(tmp_path, monkeypatch)
    headers = {"x-zf-web-token": "test-token"}
    created = client.post(
        "/api/projects/default/workflow-intake",
        headers=headers,
        json={
            "request_id": "REQ-WEB-PARTIAL-CLARIFY",
            "kind": "prd",
            "from": "docs/prd/request.md",
            "objective": "Build the confirmed product.",
            "backend": "mock",
        },
    )
    assert created.status_code == 200, created.text
    detail = client.get(
        "/api/projects/default/workflow-requests/REQ-WEB-PARTIAL-CLARIFY"
    ).json()

    clarification_payload = {
        "request_id": "REQ-WEB-PARTIAL-CLARIFY",
        "intake_ref": detail["links"]["intake_ref"],
        "target_root": str(tmp_path),
        "open_questions": ["Who approves the final proposal?"],
        "clarification_answers": [{
            "question": "Which target should receive the product?",
            "answer": str(tmp_path),
        }],
        "confirm": False,
        "requested_by": "web",
        "expected_revision": detail["result"]["revision"],
        "expected_requirement_digest": detail["result"][
            "requirement_spec_digest"
        ],
    }
    clarified = client.post(
        "/api/projects/default/workflow-clarify",
        headers=headers,
        json=clarification_payload,
    )

    assert clarified.status_code == 200, clarified.text
    body = clarified.json()
    assert body["ok"] is True
    assert body["status"] == "clarifying"
    assert body["result"]["revision"] == 2
    assert body["result"]["clarification_answer_count"] == 1
    assert body["result"]["open_questions"] == [
        "Who approves the final proposal?"
    ]
    stale = client.post(
        "/api/projects/default/workflow-clarify",
        headers=headers,
        json=clarification_payload,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["status"] == "stale_workflow_request"
    current = client.get(
        "/api/projects/default/workflow-requests/REQ-WEB-PARTIAL-CLARIFY"
    ).json()
    assert current["result"]["revision"] == 2
    assert current["requirement"]["clarification_answers"] == [{
        "question": "Which target should receive the product?",
        "answer": str(tmp_path),
    }]


def test_request_list_and_detail_share_run_admission_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, state_dir = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/projects/default/actions/workflow-request",
        headers={
            "x-zf-web-token": "test-token",
            "x-idempotency-key": "create-queued-workflow",
        },
        json=_action_body(
            "create-queued-workflow",
            {
                "request_id": "REQ-QUEUED",
                "kind": "issue",
                "objective": "Queue a bounded issue repair.",
                "backend": "mock",
                "allow_missing_env": True,
            },
        ),
    )
    assert response.status_code == 202, response.text

    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    source = writer.append(ZfEvent(
        type="workflow.invoke.requested",
        actor="test",
        task_id="TASK-QUEUED",
        correlation_id="RUN-QUEUED",
        payload={
            "request_id": "REQ-QUEUED",
            "run_id": "RUN-QUEUED",
            "workflow_run_id": "RUN-QUEUED",
        },
    ))
    base = {
        "schema_version": "run-admission.v1",
        "request_id": "REQ-QUEUED",
        "run_id": "RUN-QUEUED",
        "workflow_run_id": "RUN-QUEUED",
        "task_id": "TASK-QUEUED",
        "source_event_id": source.id,
    }
    writer.append(ZfEvent(
        type="run.admission.requested",
        actor="orchestrator",
        task_id="TASK-QUEUED",
        correlation_id="RUN-QUEUED",
        payload=base,
    ))
    writer.append(ZfEvent(
        type="run.admission.queued",
        actor="orchestrator",
        task_id="TASK-QUEUED",
        correlation_id="RUN-QUEUED",
        payload={
            **base,
            "reason": "project active Run capacity reached",
            "active_run_ids": ["RUN-ACTIVE"],
        },
    ))

    listed = client.get(
        "/api/projects/default/workflow-requests"
    ).json()["items"][0]
    detail = client.get(
        "/api/projects/default/workflow-requests/REQ-QUEUED"
    ).json()
    assert listed["status"] == "queued"
    assert listed["run_id"] == "RUN-QUEUED"
    assert listed["queue_position"] == 1
    assert detail["status"] == "queued"
    assert detail["result"]["queue_position"] == 1
    assert detail["lifecycle"]["admission"] == listed["run_admission"]


def test_web_run_controls_use_the_shared_controlled_action(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, state_dir = _client(tmp_path, monkeypatch)
    EventWriter(EventLog(state_dir / "events.jsonl")).append(ZfEvent(
        type="run.admission.admitted",
        actor="orchestrator",
        task_id="TASK-1",
        correlation_id="RUN-1",
        payload={
            "run_id": "RUN-1",
            "workflow_run_id": "RUN-1",
            "request_id": "REQ-1",
            "source_event_id": "evt-source",
            "policy_mode": "serial",
            "max_active_runs": 1,
        },
    ))
    headers = {
        "x-zf-web-token": "test-token",
        "x-idempotency-key": "pause-run-1",
    }

    paused = client.post(
        "/api/projects/default/actions/run-pause",
        headers=headers,
        json=_action_body(
            "pause-run-1",
            {"run_id": "RUN-1", "reason": "operator review"},
        ),
    )

    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    assert any(
        event.type == "run.paused"
        for event in EventLog(state_dir / "events.jsonl").read_all()
    )


def test_web_config_apply_uses_proposal_validation_sidecar(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, state_dir = _client(tmp_path, monkeypatch)
    candidate = tmp_path / "candidate.yaml"
    _write_config(candidate, lanes=2)
    requirement = (
        state_dir
        / "workflow-requests"
        / "REQ-CONFIG"
        / "requirements"
        / "revision-0001.json"
    )
    requirement.parent.mkdir(parents=True)
    requirement.write_text(
        json.dumps({
            "schema_version": "requirement-spec.v1",
            "request_id": "REQ-CONFIG",
            "revision": 1,
            "objective": "Increase the issue flow lane count.",
            "acceptance": ["The configured flow has two lanes."],
            "constraints": [],
            "open_questions": [],
            "confirmed": True,
        }),
        encoding="utf-8",
    )
    request = {
        "schema_version": "workflow.request.v1",
        "request_id": "REQ-CONFIG",
        "kind": "issue",
        "status": "ready",
        "revision": 1,
        "confirmed": True,
        "requirement_spec_ref": str(requirement),
        "requirement_spec_digest": hashlib.sha256(
            requirement.read_bytes()
        ).hexdigest(),
        "open_questions": [],
    }
    projection_path = workflow_request_path(state_dir, "REQ-CONFIG")
    projection_path.parent.mkdir(parents=True, exist_ok=True)
    projection_path.write_text(json.dumps(request), encoding="utf-8")
    proposal, proposal_ref = build_workflow_proposal(
        state_dir,
        request=request,
        base_config_path=tmp_path / "zf.yaml",
        candidate_config_path=candidate,
        preflight={"status": "GO", "blockers": []},
        flow_kind="issue",
    )
    payload = {
        "request_id": "REQ-CONFIG",
        "proposal_id": proposal["proposal_id"],
        "proposal_ref": proposal_ref,
        "proposal_digest": proposal["proposal_digest"],
        "validation_result_ref": proposal["validation_result_ref"],
        "approval_ref": "web:workflow-proposal:REQ-CONFIG",
        "idempotency_key": "apply-config-proposal",
    }

    applied = client.post(
        "/api/projects/default/actions/workflow-config-apply",
        headers={
            "x-zf-web-token": "test-token",
            "x-idempotency-key": "apply-config-proposal",
        },
        json=_action_body("apply-config-proposal", payload),
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "completed"
    assert "lanes: 2" in (tmp_path / "zf.yaml").read_text(encoding="utf-8")
    detail = client.get(
        "/api/projects/default/workflow-requests/REQ-CONFIG"
    ).json()
    assert detail["lifecycle"]["config_applied"] is True


def test_async_synthesis_operation_is_queryable_without_reposting(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _state_dir = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/projects/default/actions/workflow-request",
        headers={
            "x-zf-web-token": "test-token",
            "x-idempotency-key": "queue-workflow-synthesis",
        },
        json=_action_body(
            "queue-workflow-synthesis",
            {
                "request_id": "REQ-ASYNC-SYNTH",
                "kind": "issue",
                "objective": "Fix checkout expiry and add a regression test",
                "backend": "mock",
                "synthesis_backend": "mock",
                "allow_missing_env": True,
            },
        ),
    )

    assert response.status_code == 202, response.text
    queued = response.json()
    operation_id = queued["synthesis_operation_id"]
    assert queued["operation_status"] == "queued"

    detail = client.get(
        "/api/projects/default/workflow-requests/REQ-ASYNC-SYNTH"
    ).json()
    assert detail["operation"]["operation_id"] == operation_id
    assert detail["operation"]["queue_status"] == "queued"
    assert detail["result"]["run_id"] == detail["links"]["run_id"] == ""
    assert not detail["lifecycle"]["run_started"]

    operation = client.get(
        f"/api/projects/default/workflow-operations/{operation_id}"
    )
    assert operation.status_code == 200
    assert operation.json()["queue_status"] == "queued"

    cancelled = client.post(
        "/api/projects/default/actions/workflow-cancel",
        headers={
            "x-zf-web-token": "test-token",
            "x-idempotency-key": "cancel-workflow-synthesis",
        },
        json=_action_body(
            "cancel-workflow-synthesis",
            {
                "request_id": queued["request_id"],
                "operation_id": operation_id,
                "request_hash": queued["synthesis_request_hash"],
                "reason": "operator changed scope",
            },
        ),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    operation = client.get(
        f"/api/projects/default/workflow-operations/{operation_id}"
    ).json()
    assert operation["queue_status"] == "cancelled"
    detail = client.get(
        "/api/projects/default/workflow-requests/REQ-ASYNC-SYNTH"
    ).json()
    assert detail["status"] == detail["result"]["status"] == "cancelled", {
        "result": detail["result"], "operation": detail["operation"],
    }
    listing = client.get("/api/projects/default/workflow-requests").json()
    assert listing["items"][0]["status"] == "cancelled"

    revised = client.post(
        "/api/projects/default/workflow-clarify",
        headers={"x-zf-web-token": "test-token"},
        json={"request_id": queued["request_id"], "intake_ref": queued["intake_ref"],
              "objective": "Repair refreshed checkout scope", "confirm": True,
              "expected_revision": detail["result"]["revision"]},
    )
    assert revised.status_code < 300, revised.text
    detail = client.get(
        "/api/projects/default/workflow-requests/REQ-ASYNC-SYNTH"
    ).json()
    assert detail["result"]["revision"] > queued["request_revision"]
    assert detail["status"] != "cancelled"
