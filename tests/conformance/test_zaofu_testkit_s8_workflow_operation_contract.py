"""Deterministic Workflow Synthesis enqueue/consumer identity contracts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_operation import load_workflow_operation
from zf.runtime.orchestrator import WorkflowRuntimeCoordinator
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services
from zf.runtime.workflow_requests import workflow_request_path
from zf.runtime.workflow_synthesis import (
    WorkflowSynthesisError,
    consume_workflow_synthesis_operations,
    enqueue_workflow_synthesis,
    run_workflow_synthesis,
)

from tests.test_workflow_synthesis import _context, _result


def test_synthesis_enqueue_then_fresh_consumer_keeps_request_identity(
    tmp_path: Path,
) -> None:
    """A reloaded consumer must not block an unchanged queued operation."""

    config, state_dir, writer, projection = _context(tmp_path)
    queued = enqueue_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="web",
        backend="mock",
        operation_context={"surface": "workflow-request", "mode": "queue"},
    )

    result = _result(projection, "IssueFlow", open_questions=["Confirm scope?"])
    # Two independent consumers reopen disk state: no inherited config,
    # EventLog cache, monkeypatch or in-memory enqueue result.
    child = r"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_synthesis import consume_workflow_synthesis_operations
root = Path(sys.argv[1])
config = load_config(root / 'zf.yaml')
class Agent:
    calls = 0
    def run_turn(self, **kwargs):
        assert kwargs['permission_profile'] == 'read_only'
        self.calls += 1
        return SimpleNamespace(ok=True, reply=sys.argv[2])
agent = Agent()
state = root / config.project.state_dir
consumed = consume_workflow_synthesis_operations(
    state_dir=state, project_root=root, config=config,
    writer=EventWriter(EventLog(state / 'events.jsonl')), agent=agent,
)
print(json.dumps({'consumed': consumed, 'calls': agent.calls}))
"""
    outcomes = []
    for _ in range(2):
        process = subprocess.run(
            [sys.executable, "-c", child, str(tmp_path), json.dumps(result)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        outcomes.append(json.loads(process.stdout))
    assert outcomes == [
        {"consumed": 1, "calls": 1},
        {"consumed": 0, "calls": 0},
    ]
    events = writer.event_log.read_all()
    assert sum(e.type == "workflow.operation.requested" for e in events) == 1
    assert sum(e.type == "workflow.operation.settled" for e in events) == 1
    assert all(
        e.payload["request_hash"] == queued.request_hash
        for e in events if e.type.startswith("workflow.operation.")
    )
    operation = load_workflow_operation(writer.event_log, queued.operation_id)
    assert operation is not None
    assert operation["status"] == "settled"
    assert operation["request_hash"] == queued.request_hash
    assert not any(
        event.type == "workflow.operation.blocked"
        and event.payload.get("reason") == "request_hash_divergence"
        for event in writer.event_log.read_all()
    )


def test_synthesis_semantic_request_change_is_fail_closed(
    tmp_path: Path,
) -> None:
    """A genuine request-context change must remain a visible divergence."""

    config, state_dir, writer, projection = _context(tmp_path)
    queued = enqueue_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="web",
        backend="mock",
        operation_context={"surface": "workflow-request", "mode": "queue"},
    )

    with pytest.raises(
        WorkflowSynthesisError,
        match="workflow synthesis operation request diverged",
    ):
        run_workflow_synthesis(
            state_dir=state_dir,
            project_root=tmp_path,
            config=config,
            writer=writer,
            request_id="req-synth",
            actor="web",
            backend="mock",
            operation_context={
                "surface": "workflow-request",
                "mode": "changed-semantic-input",
            },
            candidate_result=_result(projection, "IssueFlow"),
        )

    operation = load_workflow_operation(writer.event_log, queued.operation_id)
    assert operation is not None
    assert operation["status"] == "blocked"
    blocked = [
        event
        for event in writer.event_log.read_all()
        if event.type == "workflow.operation.blocked"
        and event.payload.get("operation_id") == queued.operation_id
    ]
    assert blocked
    assert blocked[-1].payload["reason"] == "request_hash_divergence"
    assert blocked[-1].payload["expected_request_hash"] == queued.request_hash
    assert blocked[-1].payload["actual_request_hash"] != queued.request_hash


def test_synthesis_consumer_catalog_drift_is_observable_before_provider(
    tmp_path: Path,
) -> None:
    """Changed allowed skills are a semantic input, not proof of ZF-TEST-047."""

    config, state_dir, writer, projection = _context(tmp_path)
    queued = enqueue_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="web",
        backend="mock",
    )
    late_skill = tmp_path / "skills" / "materialized-after-enqueue"
    late_skill.mkdir(parents=True)
    (late_skill / "SKILL.md").write_text("# materialized skill\n", encoding="utf-8")

    class Agent:
        calls = 0

        def run_turn(self, **_kwargs):
            self.calls += 1
            raise AssertionError("catalog drift must block before provider")

    agent = Agent()
    consumed = consume_workflow_synthesis_operations(
        state_dir=state_dir,
        project_root=tmp_path,
        config=load_config(tmp_path / "zf.yaml"),
        writer=writer,
        agent=agent,
        limit=1,
    )

    assert consumed == 1
    operation = load_workflow_operation(writer.event_log, queued.operation_id)
    assert operation is not None
    assert operation["status"] == "blocked"
    assert operation["request_hash"] == queued.request_hash
    assert agent.calls == 0
    blocked = [
        e for e in writer.event_log.read_all()
        if e.type == "workflow.operation.blocked"
    ]
    assert len(blocked) == 1
    assert blocked[0].payload["reason"] == "request_hash_divergence"
    assert blocked[0].payload["expected_request_hash"] == queued.request_hash
    assert blocked[0].payload["actual_request_hash"] != queued.request_hash


@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("nested_state", [False, True])
def test_locked_self_issue_runtime_keeps_synthesis_project_catalog_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locked: bool,
    nested_state: bool,
) -> None:
    """A product project's locked Self-Issue policy must not alter its catalog."""

    config, state_dir, writer, projection = _context(tmp_path)
    if nested_state:
        nested = tmp_path / "runtime" / "ledger"
        nested.mkdir(parents=True)
        (state_dir / "workflow-requests").rename(nested / "workflow-requests")
        requirement = nested / "workflow-requests" / "req-synth" / "requirement.json"
        projection["requirement_spec_ref"] = str(requirement)
        workflow_request_path(nested, "req-synth").write_text(json.dumps(projection))
        state_dir = nested
        config.project.state_dir = "runtime/ledger"
        writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    config.self_issue.enabled = True
    config.self_issue.target_locked = locked
    config.self_issue.target_project = "owner/zaofu-self-issue"
    queued = enqueue_workflow_synthesis(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        writer=writer,
        request_id="req-synth",
        actor="web",
        backend="mock",
    )

    runtime = WorkflowRuntimeCoordinator(
        state_dir,
        config,
        object(),
        project_root=tmp_path,
    )
    assert runtime.project_root == tmp_path.resolve()

    class Agent:
        calls = 0

        def run_turn(self, **kwargs):
            assert json.loads(kwargs["message"].split("Contract:\n", 1)[1])[
                "allowed_skills"
            ] == ["zf-workflow-synthesis"]
            self.calls += 1
            return SimpleNamespace(
                ok=True,
                reply=json.dumps(_result(projection, "IssueFlow")),
            )

    agent = Agent()

    def make_agent(*, state_dir, project_root):
        assert project_root == tmp_path.resolve()
        return agent

    monkeypatch.setattr("zf.web.headless_agent.KanbanHeadlessAgent", make_agent)
    result = run_standard_tick_services(
        runtime, state=TickServiceState(), now=0.0,
    )

    assert result.workflow_synthesis_consumed == 1
    operation = load_workflow_operation(writer.event_log, queued.operation_id)
    assert operation is not None
    blocked = [
        event for event in writer.event_log.read_all()
        if event.type == "workflow.operation.blocked"
        and event.payload.get("operation_id") == queued.operation_id
    ]
    diagnostic = blocked[-1].payload if blocked else {}
    assert operation["status"] == "settled", (
        f"runtime_project_root={runtime.project_root}; "
        f"expected_hash={diagnostic.get('expected_request_hash')}; "
        f"actual_hash={diagnostic.get('actual_request_hash')}"
    )
    assert operation["request_hash"] == queued.request_hash
    assert agent.calls == 1


def test_managed_self_issue_does_not_block_general_workflow_proposal(tmp_path: Path) -> None:
    """Workspace-managed triage must coexist with a project workflow proposal."""
    import yaml

    from zf.core.config.self_issue_policy import inject_self_issue_policy
    from zf.runtime.workflow_proposal import build_workflow_proposal
    from zf.runtime.workflow_requests import load_workflow_request
    from tests.test_self_issue_target import _locked_policy
    from tests.test_workflow_proposal import (
        _generic_config, _generic_request, _generic_synthesis_result,
    )

    config_path = tmp_path / "zf.yaml"
    _generic_config(config_path)
    documents = inject_self_issue_policy(
        list(yaml.safe_load_all(config_path.read_text())), _locked_policy(),
    )
    config_path.write_text(yaml.safe_dump_all(documents, sort_keys=False))
    config = load_config(config_path)
    assert {stage.id for stage in config.workflow.stages} >= {
        "external-issue-triage", "external-issue-triage-verify",
    }
    original_config = config_path.read_bytes()
    state_dir = tmp_path / config.project.state_dir
    request = _generic_request(state_dir)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    outcome = run_workflow_synthesis(
        state_dir=state_dir, project_root=tmp_path, config=config, writer=writer,
        request_id=request["request_id"], actor="test",
        candidate_result=_generic_synthesis_result(request),
    )
    proposal, _ = build_workflow_proposal(
        state_dir,
        request=load_workflow_request(state_dir, request["request_id"]),
        base_config_path=config_path, synthesis_result_ref=outcome.result_ref,
        preflight={"status": "GO", "blockers": []}, flow_kind="workflow", writer=writer,
    )
    assert proposal["approval_status"] == "approvable"
    assert {node["id"] for node in proposal["stage_graph"]["nodes"]} >= {
        "scope", "synthesize", "verify",
    }
    assert config_path.read_bytes() == original_config
