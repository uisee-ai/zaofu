from __future__ import annotations


import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, sidecar_path
from zf.runtime.workflow_requests import (
    load_workflow_request,
    workflow_request_path,
)
from zf.runtime.workflow_synthesis import (
    WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
    WorkflowSynthesisError,
    consume_workflow_synthesis_operations,
    enqueue_workflow_synthesis,
    run_workflow_synthesis,
)
from zf.runtime.workflow_synthesis_proposal import build_synthesis_proposal
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    load_workflow_operation,
)


def _context(tmp_path: Path, *, kind: str = "issue"):
    config_path = tmp_path / "zf.yaml"
    role_names = (
        ["planner"]
        if kind != "workflow"
        else [
            "scoper",
            "collector-a",
            "collector-b",
            "synthesizer",
            "verifier",
        ]
    )
    roles_yaml = "\n".join(
        (
            f"  - name: {name}\n"
            f"    instance_id: {name}\n"
            "    backend: mock\n"
            "    role_kind: reader\n"
            + (
                "    skills: [zf-workflow-synthesis]\n"
                if name == "planner"
                else ""
            )
        ).rstrip()
        for name in role_names
    )
    config_path.write_text(
        f"""\
version: "1.0"
project: {{name: demo, state_dir: .zf}}
roles:
{roles_yaml}
workflow:
  execution_profiles:
    direct-v1:
      strategy: direct
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    state_dir = tmp_path / ".zf"
    requirement = {
        "schema_version": "requirement-spec.v1",
        "request_id": "req-synth",
        "revision": 1,
        "kind": kind,
        "objective": "Deliver the requested behavior",
        "target_root": "src",
        "source_root": "legacy",
        "acceptance": ["focused regression passes"],
        "constraints": ["read current project config"],
        "open_questions": [],
        "confirmed": True,
    }
    requirement_path = (
        state_dir / "workflow-requests" / "req-synth" / "requirement.json"
    )
    requirement_path.parent.mkdir(parents=True)
    requirement_path.write_text(
        json.dumps(requirement, sort_keys=True),
        encoding="utf-8",
    )
    digest = hashlib.sha256(requirement_path.read_bytes()).hexdigest()
    projection = {
        "schema_version": "workflow.request.v1",
        "request_id": "req-synth",
        "project_id": "demo",
        "kind": kind,
        "status": "ready",
        "revision": 1,
        "requirement_spec_ref": str(requirement_path),
        "requirement_spec_digest": digest,
        "missing_required_fields": [],
        "open_questions": [],
        "confirmed": True,
    }
    request_path = workflow_request_path(state_dir, "req-synth")
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(projection), encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    return config, state_dir, EventWriter(log), projection


def _result(projection: dict, family: str, **updates) -> dict:
    result = {
        "schema_version": WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
        "request_id": projection["request_id"],
        "request_revision": projection["revision"],
        "requirement_ref": projection["requirement_spec_ref"],
        "requirement_digest": projection["requirement_spec_digest"],
        "selected_flow_family": family,
        "short_flow_spec": {
            "flow_family": family,
            "purpose": "Deliver the confirmed requirement",
            "parameters": {"lanes": 1, "strictness": "standard"},
        },
        "decision_rationale": "The selected family matches the request shape.",
        "assumptions": [],
        "open_questions": [],
        "requested_roles": ["planner"],
        "requested_skills": ["zf-workflow-synthesis"],
        "requested_profiles": ["direct-v1"],
        "completion_profile": {
            "delivery_policy": "report_only",
            "completion_threshold": "all_required",
            "required_artifacts": [],
        },
        "risk_hints": [],
    }
    result.update(updates)
    return result


def _research_result(projection: dict, **updates) -> dict:
    result = {
        "schema_version": WORKFLOW_SYNTHESIS_RESULT_SCHEMA,
        "request_id": projection["request_id"],
        "request_revision": projection["revision"],
        "requirement_ref": projection["requirement_spec_ref"],
        "requirement_digest": projection["requirement_spec_digest"],
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
        "decision_rationale": (
            "The confirmed request is an artifact-only research delivery."
        ),
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
    result.update(updates)
    return result


def test_research_synthesis_expands_only_registered_generic_template(
    tmp_path: Path,
) -> None:
    config, state_dir, writer, projection = _context(
        tmp_path,
        kind="workflow",
    )

    outcome = run_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="test",
        candidate_result=_research_result(projection),
    )

    short_spec = hydrate_sidecar_ref(
        state_dir,
        outcome.result["short_flow_spec_ref"],
    ).payload
    generic = short_spec["generic_workflow_spec"]
    tasks = {item["name"]: item for item in generic["tasks"]}
    assert outcome.result["selected_flow_family"] == "Workflow"
    assert short_spec["intent"] == "research"
    assert short_spec["template"] == "evidence-synthesis-v1"
    assert tasks["synthesize"]["dependencies"] == [
        "collect-1",
        "collect-2",
    ]
    assert tasks["verify"]["operation"] == "agent.verify"
    assert generic["completionProfile"]["id"] == "artifact_delivery"
    assert {
        item["operation"] for item in generic["tasks"]
    } <= {"agent.read", "agent.synthesize", "agent.verify"}
    assert "autoresearch" not in json.dumps(
        outcome.result,
        sort_keys=True,
    ).lower()


def test_bounded_single_synthesis_keeps_executor_and_independent_verifier(tmp_path):
    config, state_dir, writer, projection = _context(tmp_path, kind="workflow")
    candidate = _research_result(projection)
    candidate["short_flow_spec"].update(
        intent="bounded_execution", template="bounded-agent-v1",
        parameters={"executor_role": "synthesizer", "verifier_role": "verifier", "deadline_seconds": 120},
    )
    candidate["requested_roles"] = ["synthesizer", "verifier"]
    candidate["completion_profile"]["required_artifacts"] = ["agent-execute.result"]
    outcome = run_workflow_synthesis(
        state_dir=state_dir, project_root=tmp_path, config=config, writer=writer,
        request_id="req-synth", actor="test", candidate_result=candidate,
    )
    spec = hydrate_sidecar_ref(state_dir, outcome.result["short_flow_spec_ref"]).payload
    tasks = spec["generic_workflow_spec"]["tasks"]
    assert [task["role"] for task in tasks] == ["synthesizer", "verifier"]
    assert [task["deadlineSeconds"] for task in tasks] == [120, 120]
    assert spec["generic_workflow_spec"]["completionProfile"]["independentVerify"] is True
    assert not any(e.type == "workflow.invoke.requested" for e in writer.event_log.read_all())


def test_generic_synthesis_proposal_uses_generated_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / ".zf"
    descriptor = write_immutable_json_sidecar(
        state_dir,
        {
            "parameters": {},
            "generic_workflow_spec": {"entry": "scope"},
        },
        root="workflow/synthesis/test/flow-specs",
        kind="workflow_short_flow_spec",
        schema_version="workflow-short-flow-spec.v1",
        created_by="test",
    )
    captured: dict[str, object] = {}

    def fake_preview(**kwargs):
        captured.update(kwargs)
        return {"status": "GO"}

    monkeypatch.setattr(
        "zf.cli.flow.build_flow_submit_preview",
        fake_preview,
    )

    result = build_synthesis_proposal(
        state_dir=state_dir,
        result={
            "selected_flow_family": "Workflow",
            "short_flow_spec_ref": descriptor,
        },
        result_ref={"ref": "synthesis.json", "sha256": "abc"},
        operation_context={
            "config_ref": str(tmp_path / "zf.yaml"),
            "intake_ref": str(tmp_path / "intake.md"),
        },
        actor="test",
    )

    assert result == {"status": "GO"}
    assert captured["pattern_id"] == "scope"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda body: body["short_flow_spec"].update(
                {"template": "arbitrary-research-v9"}
            ),
            "unknown registered workflow template",
        ),
        (
            lambda body: body["short_flow_spec"]["parameters"].update(
                {"verifier_role": "root"}
            ),
            "unknown role",
        ),
        (
            lambda body: body.update({
                "requested_roles": [
                    "scoper",
                    "collector-a",
                    "collector-b",
                    "synthesizer",
                ],
            }),
            "parameter roles must be declared",
        ),
        (
            lambda body: body["short_flow_spec"]["parameters"].update(
                {"handler": "arbitrary-shell"}
            ),
            "unsupported fields",
        ),
    ],
)
def test_research_synthesis_rejects_unregistered_or_unbound_capability(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    config, state_dir, writer, projection = _context(
        tmp_path,
        kind="workflow",
    )
    candidate = _research_result(projection)
    mutate(candidate)

    with pytest.raises(WorkflowSynthesisError, match=message):
        run_workflow_synthesis(
            state_dir=state_dir,
            project_root=tmp_path,
            config=config,
            writer=writer,
            request_id="req-synth",
            actor="test",
            candidate_result=candidate,
        )


def test_research_synthesis_open_question_stays_in_clarification(
    tmp_path: Path,
) -> None:
    config, state_dir, writer, projection = _context(
        tmp_path,
        kind="workflow",
    )
    candidate = _research_result(projection)
    candidate["open_questions"] = ["Which evidence window is authoritative?"]

    outcome = run_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="test",
        candidate_result=candidate,
    )

    assert outcome.request_projection["status"] == "clarifying"
    assert "workflow.request.proposed" not in {
        event.type for event in writer.event_log.read_all()
    }


@pytest.mark.parametrize(
    ("kind", "family"),
    [
        ("issue", "IssueFlow"),
        ("prd", "PrdFlow"),
        ("refactor", "RefactorFlow"),
    ],
)
def test_mock_synthesis_admits_registered_flow_and_settles_operation(
    tmp_path: Path,
    kind: str,
    family: str,
) -> None:
    config, state_dir, writer, projection = _context(tmp_path, kind=kind)

    outcome = run_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="test",
        candidate_result=_result(projection, family),
    )

    assert outcome.result["selected_flow_family"] == family
    assert outcome.result["short_flow_spec_digest"]
    assert hydrate_sidecar_ref(
        state_dir,
        outcome.result["short_flow_spec_ref"],
    ).payload["flow_family"] == family
    assert load_workflow_request(
        state_dir,
        "req-synth",
    )["synthesis_digest"] == outcome.result_ref["sha256"]
    types = [event.type for event in writer.event_log.read_all()]
    assert "workflow.operation.requested" in types
    assert "workflow.operation.started" in types
    assert "workflow.operation.settled" in types
    assert "workflow.synthesis.admitted" in types


def test_synthesis_rejects_replay_after_effective_policy_changes(tmp_path: Path) -> None:
    from dataclasses import replace

    config, state_dir, writer, projection = _context(tmp_path)
    run_workflow_synthesis(
        state_dir=state_dir, project_root=tmp_path, config=config, writer=writer,
        request_id="req-synth", actor="test", candidate_result=_result(projection, "IssueFlow"),
    )
    changed = replace(config, roles=[replace(config.roles[0], model="changed-model")])
    with pytest.raises(WorkflowSynthesisError, match="request diverged"):
        run_workflow_synthesis(
            state_dir=state_dir, project_root=tmp_path, config=changed, writer=writer,
            request_id="req-synth", actor="test", candidate_result=_result(projection, "IssueFlow"),
        )
    assert sum(e.type == "workflow.synthesis.admitted" for e in writer.event_log.read_all()) == 1


def test_real_headless_caller_passes_read_only_contract_to_agent(
    tmp_path: Path,
) -> None:
    config, state_dir, writer, projection = _context(tmp_path)

    class FakeAgent:
        def __init__(self):
            self.call = {}

        def run_turn(self, **kwargs):
            self.call = kwargs
            return SimpleNamespace(
                ok=True,
                reply=json.dumps(_result(projection, "IssueFlow")),
            )

    agent = FakeAgent()
    outcome = run_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="test",
        backend="codex-headless",
        agent=agent,
    )

    assert outcome.result["selected_flow_family"] == "IssueFlow"
    assert agent.call["permission_profile"] == "read_only"
    assert "Do not emit expanded config" in agent.call["message"]
    assert '"effective_policy_digest": "sha256:' in agent.call["message"]
    assert '"template_contracts"' in agent.call["message"]
    assert '"native_delegation": null' in agent.call["message"]
    operation = load_workflow_operation(writer.event_log, outcome.operation_id)
    request = hydrate_sidecar_ref(state_dir, operation["request_ref"]).payload["request"]
    assert request["effective_policy_digest"].startswith("sha256:")


def test_cancelled_running_synthesis_discards_late_provider_result(
    tmp_path: Path,
) -> None:
    config, state_dir, writer, projection = _context(tmp_path)
    queued = enqueue_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="test",
        backend="mock",
    )

    class CancellingAgent:
        def run_turn(self, **kwargs):
            operation = load_workflow_operation(
                writer.event_log,
                queued.operation_id,
            ) or {}
            WorkflowOperationService(
                state_dir=state_dir,
                event_log=writer.event_log,
                event_writer=writer,
            ).cancel(
                operation_id=queued.operation_id,
                request_hash=queued.request_hash,
                workflow_run_id=str(
                    operation.get("workflow_run_id") or ""
                ),
                reason="operator cancelled",
                correlation_id="req-synth",
            )
            return SimpleNamespace(
                ok=True,
                reply=json.dumps(_result(projection, "IssueFlow")),
            )

    with pytest.raises(
        WorkflowSynthesisError,
        match="terminal: cancelled",
    ):
        run_workflow_synthesis(
            state_dir=state_dir,
            project_root=tmp_path,
            config=config,
            writer=writer,
            request_id="req-synth",
            actor="test",
            backend="mock",
            agent=CancellingAgent(),
        )

    operation = load_workflow_operation(
        writer.event_log,
        queued.operation_id,
    )
    assert operation is not None
    assert operation["status"] == "cancelled"
    event_types = [event.type for event in writer.event_log.read_all()]
    assert "workflow.synthesis.result.discarded" in event_types
    assert "workflow.operation.settled" not in event_types
    assert "workflow.synthesis.admitted" not in event_types
    assert not load_workflow_request(
        state_dir,
        "req-synth",
    ).get("synthesis_digest")


def test_blocking_question_returns_request_to_clarification(
    tmp_path: Path,
) -> None:
    config, state_dir, writer, projection = _context(tmp_path)
    candidate = _result(projection, "IssueFlow")
    candidate["open_questions"] = ["Which API route is authoritative?"]

    outcome = run_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="test",
        candidate_result=candidate,
    )

    assert outcome.request_projection["status"] == "clarifying"
    assert outcome.request_projection["open_questions"] == [
        "Which API route is authoritative?"
    ]
    assert "workflow.request.proposed" not in [
        event.type for event in writer.event_log.read_all()
    ]


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"request_revision": 2}, "revision is stale"),
        ({"selected_flow_family": "ResearchFlow"}, "unsupported workflow"),
        ({"requested_roles": ["root"]}, "unknown role"),
        ({"requested_skills": ["unregistered"]}, "unknown skill"),
        ({"requested_profiles": ["native-v9"]}, "unknown profile"),
        ({"expanded_config": {"roles": []}}, "unsupported fields"),
        (
            {
                "short_flow_spec": {
                    "flow_family": "IssueFlow",
                    "parameters": {"handler": "arbitrary"},
                },
            },
            "unsupported fields",
        ),
        (
            {
                "short_flow_spec": {
                    "flow_family": "IssueFlow",
                    "parameters": {"lanes": "many"},
                },
            },
            "lanes must be an integer",
        ),
        (
            {
                "completion_profile": {
                    "delivery_policy": "report_only",
                    "completion_threshold": "",
                    "required_artifacts": "report.md",
                },
            },
            "required_artifacts must be a list",
        ),
    ],
)
def test_malicious_or_stale_synthesis_fails_closed(
    tmp_path: Path,
    update: dict,
    message: str,
) -> None:
    config, state_dir, writer, projection = _context(tmp_path)
    candidate = _result(projection, "IssueFlow")
    candidate.update(update)

    with pytest.raises(WorkflowSynthesisError, match=message):
        run_workflow_synthesis(
            state_dir=state_dir,
            project_root=tmp_path,
            config=config,
            writer=writer,
            request_id="req-synth",
            actor="test",
            candidate_result=candidate,
        )

    assert load_workflow_request(state_dir, "req-synth")["status"] == "ready"
    assert "workflow.operation.failed" in [
        event.type for event in writer.event_log.read_all()
    ]


def test_provider_unavailable_fails_without_modifying_config(
    tmp_path: Path,
) -> None:
    config, state_dir, writer, _ = _context(tmp_path)
    before = (tmp_path / "zf.yaml").read_bytes()

    class UnavailableAgent:
        def run_turn(self, **kwargs):
            return SimpleNamespace(
                ok=False,
                error="provider command unavailable",
                status="unavailable",
            )

    with pytest.raises(WorkflowSynthesisError, match="provider failed"):
        run_workflow_synthesis(
            state_dir=state_dir,
            project_root=tmp_path,
            config=config,
            writer=writer,
            request_id="req-synth",
            actor="test",
            backend="codex-headless",
            agent=UnavailableAgent(),
        )

    assert (tmp_path / "zf.yaml").read_bytes() == before
    assert load_workflow_request(state_dir, "req-synth")["status"] == "ready"


def test_synthesis_rejects_tampered_requirement_before_agent_call(
    tmp_path: Path,
) -> None:
    config, state_dir, writer, projection = _context(tmp_path)
    Path(projection["requirement_spec_ref"]).write_text(
        '{"request_id":"req-synth","revision":1,"tampered":true}',
        encoding="utf-8",
    )

    with pytest.raises(WorkflowSynthesisError, match="digest mismatch"):
        run_workflow_synthesis(
            state_dir=state_dir,
            project_root=tmp_path,
            config=config,
            writer=writer,
            request_id="req-synth",
            actor="test",
            candidate_result=_result(projection, "IssueFlow"),
        )

    assert writer.event_log.read_all() == []


def test_running_operation_resumes_with_same_identity(
    tmp_path: Path,
) -> None:
    config, state_dir, writer, projection = _context(tmp_path)
    queued = enqueue_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="test",
        backend="mock",
    )
    WorkflowOperationService(
        state_dir=state_dir,
        event_log=writer.event_log,
        event_writer=writer,
    ).mark_started(
        operation_id=queued.operation_id,
        request_hash=queued.request_hash,
        workflow_run_id="workflow-request:req-synth:r1",
    )

    class RecoveryAgent:
        def __init__(self) -> None:
            self.calls = 0

        def run_turn(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(
                ok=True,
                reply=json.dumps(_result(projection, "IssueFlow")),
            )

    agent = RecoveryAgent()
    consumed = consume_workflow_synthesis_operations(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        agent=agent,
    )

    assert consumed == 1
    assert agent.calls == 1
    assert load_workflow_operation(
        writer.event_log,
        queued.operation_id,
    )["status"] == "settled"
    assert any(
        event.type == "workflow.synthesis.retried"
        for event in writer.event_log.read_all()
    )


def test_prepared_result_checkpoint_recovers_without_provider_recall(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, state_dir, writer, projection = _context(tmp_path)

    def crash_before_settle(self, **kwargs):
        raise SystemExit("simulated crash before settle")

    monkeypatch.setattr(
        WorkflowOperationService,
        "settle",
        crash_before_settle,
    )
    with pytest.raises(SystemExit, match="simulated crash"):
        run_workflow_synthesis(
            state_dir=state_dir,
            project_root=tmp_path,
            config=config,
            writer=writer,
            request_id="req-synth",
            actor="test",
            candidate_result=_result(projection, "IssueFlow"),
        )
    operation_id = next(
        event.payload["operation_id"]
        for event in writer.event_log.read_all()
        if event.type == "workflow.operation.requested"
    )
    assert load_workflow_operation(
        writer.event_log,
        operation_id,
    )["status"] == "running"

    monkeypatch.undo()

    class UnexpectedAgent:
        def run_turn(self, **kwargs):
            raise AssertionError("prepared result must avoid provider recall")

    outcome = run_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="test",
        backend="",
        agent=UnexpectedAgent(),
        resume_running=True,
    )

    assert outcome.replayed is False
    assert load_workflow_operation(
        writer.event_log,
        operation_id,
    )["status"] == "settled"


def test_stale_request_revision_supersedes_queued_operation(
    tmp_path: Path,
) -> None:
    config, state_dir, writer, _projection = _context(tmp_path)
    queued = enqueue_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="test",
        backend="mock",
    )
    request_path = workflow_request_path(state_dir, "req-synth")
    current = json.loads(request_path.read_text(encoding="utf-8"))
    current["revision"] = 2
    request_path.write_text(json.dumps(current), encoding="utf-8")

    consumed = consume_workflow_synthesis_operations(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
    )

    assert consumed == 1
    operation = load_workflow_operation(
        writer.event_log,
        queued.operation_id,
    )
    assert operation["status"] == "superseded"
    assert "revision superseded" in operation["reason"]


def test_unreadable_operation_request_fails_without_poisoning_consumer(
    tmp_path: Path,
) -> None:
    config, state_dir, writer, _projection = _context(tmp_path)
    queued = enqueue_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="test",
        backend="mock",
    )
    operation = load_workflow_operation(writer.event_log, queued.operation_id)
    assert operation is not None
    request_ref = operation["request_ref"]
    sidecar_path(state_dir, request_ref["ref"]).unlink()

    consumed = consume_workflow_synthesis_operations(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
    )

    assert consumed == 1
    failed = load_workflow_operation(writer.event_log, queued.operation_id)
    assert failed is not None
    assert failed["status"] == "failed"
    failure = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.synthesis.failed"
        and event.payload.get("operation_id") == queued.operation_id
    )
    assert failure.payload["phase"] == "request_hydration"
    assert failure.payload["next_action"] == (
        "repair request sidecar and submit a new revision"
    )
    assert consume_workflow_synthesis_operations(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
    ) == 0


def test_proposal_materialization_failure_has_bounded_retries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, state_dir, writer, projection = _context(tmp_path)
    queued = enqueue_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="test",
        backend="mock",
    )

    class Agent:
        def __init__(self) -> None:
            self.calls = 0

        def run_turn(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(
                ok=True,
                reply=json.dumps(_result(projection, "IssueFlow")),
            )

    def fail_proposal(**kwargs):
        raise RuntimeError("proposal projection unavailable")

    monkeypatch.setattr(
        "zf.runtime.workflow_synthesis._build_synthesis_proposal",
        fail_proposal,
    )
    agent = Agent()

    for _attempt in range(3):
        assert consume_workflow_synthesis_operations(
            state_dir=state_dir,
            project_root=tmp_path,
            config=config,
            writer=writer,
            agent=agent,
        ) == 1

    operation = load_workflow_operation(writer.event_log, queued.operation_id)
    assert operation is not None
    assert operation["status"] == "failed"
    failures = [
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.synthesis.proposal.failed"
        and event.payload.get("operation_id") == queued.operation_id
    ]
    assert [event.payload["attempt"] for event in failures] == [1, 2, 3]
    assert failures[-1].payload["next_action"] == (
        "inspect proposal materialization failure"
    )
    assert agent.calls == 1


def test_consumer_lock_contention_defers_without_reporting_failure(tmp_path):
    from zf.core.state.locks import locked_path

    config, state, writer, _ = _context(tmp_path)
    queued = enqueue_workflow_synthesis(
        state_dir=state, project_root=tmp_path, config=config, writer=writer,
        request_id="req-synth", actor="test", backend="mock",
    )
    before = [e.id for e in writer.event_log.read_all()]
    with locked_path(state / "projections/workflow-synthesis-consumer.guard"):
        assert consume_workflow_synthesis_operations(
            state_dir=state, project_root=tmp_path, config=config, writer=writer,
        ) == 0
    assert before == [e.id for e in writer.event_log.read_all()]
    assert load_workflow_operation(writer.event_log, queued.operation_id)["status"] == "requested"
