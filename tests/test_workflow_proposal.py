from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from zf.cli.flow_draft_support import orchestration_spec
from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_proposal import (
    WORKFLOW_PROPOSAL_SCHEMA,
    WorkflowProposalError,
    build_workflow_proposal,
    load_workflow_proposal,
    stable_json_digest,
)
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.workflow_requests import (
    load_workflow_request,
    workflow_request_path,
)
from zf.runtime.workflow_synthesis import (
    WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
    run_workflow_synthesis,
)


def _config(path: Path, *, lanes: int = 1) -> None:
    path.write_text(
        f"""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {{name: issue-demo}}
spec:
  lanes: {lanes}
  backend: mock
  issueRef: docs/issue.md
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
    load_config(path)


def _request(state_dir: Path, *, requirement_fields: dict | None = None) -> dict:
    requirement = state_dir / "workflow-requests" / "req-1" / "requirement.json"
    requirement.parent.mkdir(parents=True)
    requirement.write_text(
        json.dumps({
            "schema_version": "requirement-spec.v1",
            "objective": "Repair the confirmed issue",
            "request_id": "req-1",
            "revision": 1,
            **(requirement_fields or {}),
        }),
        encoding="utf-8",
    )
    import hashlib

    digest = hashlib.sha256(requirement.read_bytes()).hexdigest()
    projection = {
        "schema_version": "workflow.request.v1",
        "request_id": "req-1",
        "kind": (requirement_fields or {}).get("kind", "issue"),
        "status": "ready",
        "revision": 1,
        "requirement_spec_ref": str(requirement),
        "requirement_spec_digest": digest,
        "open_questions": [],
    }
    path = workflow_request_path(state_dir, "req-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(projection), encoding="utf-8")
    return projection


def _synthesis_result(request: dict, *, lanes: int = 1) -> dict:
    return {
        "schema_version": WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
        "request_id": request["request_id"],
        "request_revision": request["revision"],
        "requirement_ref": request["requirement_spec_ref"],
        "requirement_digest": request["requirement_spec_digest"],
        "selected_flow_family": "IssueFlow",
        "short_flow_spec": {
            "flow_family": "IssueFlow",
            "purpose": "Deliver the confirmed issue repair",
            "parameters": {
                "lanes": lanes,
                "strictness": "standard",
            },
        },
        "decision_rationale": "The requirement is a bounded issue repair.",
        "assumptions": [],
        "open_questions": [],
        "requested_roles": [],
        "requested_skills": [],
        "requested_profiles": ["direct-v1"],
        "completion_profile": {
            "delivery_policy": "report_only",
            "completion_threshold": "",
            "required_artifacts": [],
        },
        "risk_hints": [],
    }


def _generic_config(path: Path) -> None:
    semantic_workflow = orchestration_spec(tier="multi")["workflow"]
    semantic_workflow["execution_profiles"] = {
        "direct-v1": {"strategy": "direct"},
    }
    roles = [{
        "name": "orchestrator",
        "instance_id": "orchestrator",
        "backend": "mock",
        "role_kind": "reader",
        "triggers": [
            "dispatch.silent_stall",
            "orchestrator.rework.triage.requested",
            "orchestrator.semantic.checkpoint.requested",
        ],
    }, *[
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
    ]]
    path.write_text(
        yaml.safe_dump_all(
            [{
                "apiVersion": "zaofu.dev/v1",
                "kind": "ZfConfig",
                "metadata": {"name": "generic-research"},
                "spec": {
                    "version": "1.0",
                    "project": {
                        "name": "generic-research",
                        "state_dir": ".zf",
                    },
                    "roles": roles,
                    "workflow": semantic_workflow,
                },
            }],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    load_config(path)


def _generic_request(state_dir: Path) -> dict:
    requirement = (
        state_dir
        / "workflow-requests"
        / "req-research"
        / "requirement.json"
    )
    requirement.parent.mkdir(parents=True)
    requirement.write_text(
        json.dumps({
            "schema_version": "requirement-spec.v1",
            "request_id": "req-research",
            "revision": 1,
            "kind": "workflow",
            "objective": "Deliver a verified research report.",
            "acceptance": ["The report answers the confirmed question."],
            "constraints": [],
            "open_questions": [],
            "confirmed": True,
        }),
        encoding="utf-8",
    )
    import hashlib

    projection = {
        "schema_version": "workflow.request.v1",
        "request_id": "req-research",
        "kind": "workflow",
        "status": "ready",
        "revision": 1,
        "confirmed": True,
        "requirement_spec_ref": str(requirement),
        "requirement_spec_digest": hashlib.sha256(
            requirement.read_bytes()
        ).hexdigest(),
        "missing_required_fields": [],
        "open_questions": [],
    }
    path = workflow_request_path(state_dir, "req-research")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(projection), encoding="utf-8")
    return projection


def _generic_synthesis_result(request: dict) -> dict:
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


def test_generic_research_proposal_materializes_registered_graph_and_diff(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "zf.yaml"
    _generic_config(config_path)
    config = load_config(config_path)
    state_dir = tmp_path / ".zf"
    request = _generic_request(state_dir)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    synthesis = run_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id=request["request_id"],
        actor="test",
        candidate_result=_generic_synthesis_result(request),
    )

    proposal, descriptor = build_workflow_proposal(
        state_dir,
        request=load_workflow_request(state_dir, request["request_id"]),
        base_config_path=config_path,
        synthesis_result_ref=synthesis.result_ref,
        preflight={"status": "GO", "blockers": []},
        flow_kind="workflow",
        writer=writer,
    )

    nodes = {
        node["id"]: node for node in proposal["stage_graph"]["nodes"]
    }
    assert descriptor["ref"]
    assert proposal["flow_family"] == "Workflow"
    assert proposal["change_mode"] == "config_change"
    assert proposal["approval_status"] == "approvable"
    assert proposal["completion_profile"] == {
        "id": "artifact_delivery",
        "intent": "research",
        "template": "evidence-synthesis-v1",
        "generic_workflow_contract_digest": proposal["compiler_inputs"][
            "generic_workflow_contract_digest"
        ],
        "delivery_policy": "report_only",
        "completion_threshold": "verified_artifacts",
        "required_delivery_artifacts": [{
            "name": "report",
            "kind": "report/markdown",
            "source_ref": "synthesize.report",
            "required_for": "standard",
        }],
    }
    assert proposal["stage_graph"]["node_count"] == 5
    assert nodes["synthesize"]["dependencies"] == [
        "collect-1",
        "collect-2",
    ]
    assert nodes["synthesize"]["dependency_barrier_id"].startswith(
        "barrier:synthesize:"
    )
    assert {
        item["source"] for item in nodes["synthesize"]["input_ports"]
    } == {"collect-1.evidence-1", "collect-2.evidence-2"}
    assert nodes["verify"]["operation"] == "agent.verify"
    diff = hydrate_sidecar_ref(
        state_dir,
        proposal["config_diff_ref"],
    ).payload
    assert diff["changed"] is True
    assert "generic_workflows:" in diff["unified_diff"]
    assert "evidence-synthesis-v1" in diff["unified_diff"]
    effective = hydrate_sidecar_ref(
        state_dir,
        proposal["effective_config_ref"],
    ).payload["config"]
    policy = effective["workflow"]["orchestration"]
    assert policy["mode"] == "exception_advisor"
    assert policy["checkpoints"] == []
    assert policy["flow_policies"]["prd"]["checkpoints"] == [
        "plan_candidate",
    ]
    assert policy["flow_policies"]["prd"][
        "checkpoint_policies"
    ]["plan_candidate"] == "shadow"
    assert policy["flow_policies"]["workflow"]["mode"] == (
        "exception_advisor"
    )
    assert policy["flow_policies"]["research"]["mode"] == (
        "exception_advisor"
    )
    assert len(effective["workflow"]["_generic_workflows"]) == 1
    assert "autoresearch" not in json.dumps(
        proposal,
        sort_keys=True,
    ).lower()
    assert not list(tmp_path.glob(".zf.yaml.workflow-candidate-*.tmp"))


def test_workflow_proposal_is_immutable_and_deterministic(tmp_path: Path) -> None:
    config = tmp_path / "zf.yaml"
    _config(config)
    state_dir = tmp_path / ".zf"
    request = _request(state_dir)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)

    first, first_ref = build_workflow_proposal(
        state_dir,
        request=request,
        base_config_path=config,
        preflight={"status": "GO", "blockers": []},
        flow_kind="issue",
        writer=writer,
    )
    second, second_ref = build_workflow_proposal(
        state_dir,
        request=load_workflow_request(state_dir, "req-1"),
        base_config_path=config,
        preflight={"status": "GO", "blockers": []},
        flow_kind="issue",
        writer=writer,
    )

    assert first["proposal_digest"] == second["proposal_digest"]
    assert first_ref["ref"] == second_ref["ref"]
    assert first["change_mode"] == "run_parameters_only"
    assert first["approval_status"] == "approvable"
    assert first["validation_result_ref"]["kind"] == "workflow_preflight_snapshot"
    direct_profile = first["closure"]["execution_profiles"]["direct-v1"]
    assert direct_profile["digest"]
    assert direct_profile["profile"]["strategy"] == "direct"
    assert all(
        role["execution"]["default_profile"] == "direct-v1"
        for role in first["closure"]["roles"]
    )
    assert load_workflow_proposal(state_dir, first_ref) == first
    projection = load_workflow_request(state_dir, "req-1")
    assert projection["status"] == "proposed"
    assert projection["proposal_digest"] == first["proposal_digest"]
    assert [event.type for event in log.read_all()].count(
        "workflow.request.proposed"
    ) == 1


def test_workflow_proposal_detects_config_change_and_blocks_stop(
    tmp_path: Path,
) -> None:
    base = tmp_path / "zf.yaml"
    candidate = tmp_path / "candidate.yaml"
    _config(base, lanes=1)
    _config(candidate, lanes=2)
    state_dir = tmp_path / ".zf"
    request = _request(state_dir)

    proposal, descriptor = build_workflow_proposal(
        state_dir,
        request=request,
        base_config_path=base,
        candidate_config_path=candidate,
        preflight={
            "status": "STOP",
            "blockers": [{"severity": "STOP", "kind": "missing_capability"}],
        },
        flow_kind="issue",
    )

    assert descriptor["ref"]
    assert proposal["change_mode"] == "config_change"
    assert proposal["approval_status"] == "blocked"
    assert proposal["blockers"][0]["kind"] == "missing_capability"
    assert load_workflow_request(state_dir, "req-1")["status"] == "ready"


def test_workflow_proposal_digest_ignores_preflight_observation_timestamps(
    tmp_path: Path,
) -> None:
    config = tmp_path / "zf.yaml"
    _config(config)
    state_dir = tmp_path / ".zf"
    request = _request(state_dir)

    first, _ = build_workflow_proposal(
        state_dir,
        request=request,
        base_config_path=config,
        preflight={
            "status": "GO",
            "run_contract": {
                "preview": {"created_at": "2026-07-25T10:00:00+00:00"}
            },
        },
        flow_kind="issue",
    )
    reset = load_workflow_request(state_dir, "req-1")
    reset["status"] = "ready"
    reset.pop("proposal_ref", None)
    reset.pop("proposal_digest", None)
    reset.pop("proposal_revision", None)
    workflow_request_path(state_dir, "req-1").write_text(
        json.dumps(reset),
        encoding="utf-8",
    )
    second, _ = build_workflow_proposal(
        state_dir,
        request=load_workflow_request(state_dir, "req-1"),
        base_config_path=config,
        preflight={
            "status": "GO",
            "run_contract": {
                "preview": {"created_at": "2026-07-25T10:00:01+00:00"}
            },
        },
        flow_kind="issue",
    )

    assert first["proposal_digest"] == second["proposal_digest"]
    assert "created_at" not in first["preflight"]["run_contract"]["preview"]


def test_workflow_proposal_rejects_tampered_requirement(
    tmp_path: Path,
) -> None:
    config = tmp_path / "zf.yaml"
    _config(config)
    state_dir = tmp_path / ".zf"
    request = _request(state_dir)
    Path(request["requirement_spec_ref"]).write_text(
        '{"request_id":"req-1","revision":1,"tampered":true}',
        encoding="utf-8",
    )

    with pytest.raises(WorkflowProposalError, match="digest mismatch"):
        build_workflow_proposal(
            state_dir,
            request=request,
            base_config_path=config,
            preflight={"status": "GO", "blockers": []},
            flow_kind="issue",
        )


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("missing", "missing required fields"),
        ("unknown_field", "unknown fields"),
        ("unknown_schema", "schema is unsupported"),
        ("digest", "digest mismatch"),
    ],
)
def test_workflow_proposal_schema_and_digest_fail_closed(
    tmp_path: Path,
    mutation: str,
    error: str,
) -> None:
    config = tmp_path / "zf.yaml"
    _config(config)
    state_dir = tmp_path / ".zf"
    proposal, _ = build_workflow_proposal(
        state_dir,
        request=_request(state_dir),
        base_config_path=config,
        preflight={"status": "GO", "blockers": []},
        flow_kind="issue",
    )
    changed = json.loads(json.dumps(proposal))
    if mutation == "missing":
        changed.pop("closure")
    elif mutation == "unknown_field":
        changed["runtime_handler"] = "arbitrary-shell"
    elif mutation == "unknown_schema":
        changed["schema_version"] = "workflow-proposal.v99"
    else:
        changed["proposal_digest"] = "0" * 64
    if mutation != "digest":
        body = {
            key: value
            for key, value in changed.items()
            if key not in {"proposal_id", "proposal_digest"}
        }
        changed["proposal_digest"] = stable_json_digest(body)
        changed["proposal_id"] = (
            f"workflow-proposal:{changed['proposal_digest'][:24]}"
        )
    descriptor = write_immutable_json_sidecar(
        state_dir,
        changed,
        root="workflow/proposals/schema-tests",
        kind="workflow_proposal",
        schema_version=WORKFLOW_PROPOSAL_SCHEMA,
        created_by="test",
    )

    with pytest.raises(WorkflowProposalError, match=error):
        load_workflow_proposal(state_dir, descriptor)


def test_workflow_proposal_consumes_exact_admitted_synthesis(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "zf.yaml"
    _config(config_path)
    config = load_config(config_path)
    state_dir = tmp_path / ".zf"
    request = _request(state_dir)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    synthesis = run_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id=request["request_id"],
        actor="test",
        candidate_result=_synthesis_result(request),
    )

    proposal, _descriptor = build_workflow_proposal(
        state_dir,
        request=load_workflow_request(state_dir, request["request_id"]),
        base_config_path=config_path,
        synthesis_result_ref=synthesis.result_ref,
        preflight={
            "status": "GO",
            "blockers": [],
            "delivery_contract": {"strictness": "standard"},
        },
        flow_kind="issue",
        writer=writer,
    )

    assert proposal["approval_status"] == "approvable"
    assert proposal["synthesis_result_ref"] == synthesis.result_ref
    assert (
        proposal["short_flow_spec_ref"]
        == synthesis.result["short_flow_spec_ref"]
    )
    short_spec = hydrate_sidecar_ref(
        state_dir,
        proposal["short_flow_spec_ref"],
    ).payload
    assert proposal["run_parameters"] == short_spec["parameters"]
    assert proposal["decision_rationale"] == (
        "The requirement is a bounded issue repair."
    )


def test_workflow_proposal_blocks_synthesis_not_represented_by_config(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "zf.yaml"
    _config(config_path, lanes=1)
    config = load_config(config_path)
    state_dir = tmp_path / ".zf"
    request = _request(state_dir)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    synthesis = run_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id=request["request_id"],
        actor="test",
        candidate_result=_synthesis_result(request, lanes=2),
    )

    proposal, _descriptor = build_workflow_proposal(
        state_dir,
        request=load_workflow_request(state_dir, request["request_id"]),
        base_config_path=config_path,
        synthesis_result_ref=synthesis.result_ref,
        preflight={
            "status": "GO",
            "blockers": [],
            "delivery_contract": {"strictness": "standard"},
        },
        flow_kind="issue",
        writer=writer,
    )

    assert proposal["approval_status"] == "blocked"
    assert proposal["blockers"][0]["kind"] == (
        "workflow_synthesis_compile_mismatch"
    )
    assert "lanes requested 2, compiled 1" in proposal["blockers"][0]["message"]
    assert load_workflow_request(
        state_dir,
        request["request_id"],
    )["status"] == "ready"
