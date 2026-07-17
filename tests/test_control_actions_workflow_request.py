from __future__ import annotations

from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.events import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.control_actions import ControlledActionService


def _service(tmp_path: Path) -> tuple[ControlledActionService, EventLog]:
    config_ref = tmp_path / "zf.yaml"
    config_ref.write_text("""\
apiVersion: zaofu.dev/v1
kind: IssueFlow
metadata: {name: issue-demo}
spec:
  lanes: 1
  backend: mock
  issueRef: docs/intake/bug.md
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo, state_dir: .zf}
""", encoding="utf-8")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    service = ControlledActionService(
        state_dir,
        EventWriter(log),
        config=load_config(config_ref),
        project_root=tmp_path,
        actor="operator",
        source="kanban-agent",
        surface="web",
    )
    return service, log


def _execute(service: ControlledActionService, action: str, payload: dict) -> dict:
    return service._execute_action(
        action=action,
        requested_action=action,
        payload=payload,
        requested=ZfEvent(type="control.action.requested", actor="test", payload=payload),
    )


def test_request_is_proposed_before_explicit_submit(tmp_path: Path) -> None:
    service, log = _service(tmp_path)

    proposed = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": "Fix session expiry and add a regression test",
        "backend": "mock",
        "allow_missing_env": True,
    })

    assert proposed["ok"] is True
    assert proposed["status"] == "proposal_ready"
    assert Path(proposed["intake_ref"]).exists()
    assert "workflow.invoke.requested" not in [event.type for event in log.read_all()]

    submitted = _execute(service, "workflow-submit", {
        "intake_ref": proposed["intake_ref"],
        "kind": "issue",
        "allow_missing_env": True,
    })

    assert submitted["ok"] is True
    types = [event.type for event in log.read_all()]
    assert "workflow.request.proposed" in types
    assert "workflow.request.approved" in types
    assert "workflow.submit.accepted" in types
    assert "workflow.invoke.requested" in types

    replay = _execute(service, "workflow-submit", {
        "intake_ref": proposed["intake_ref"],
        "kind": "issue",
        "allow_missing_env": True,
    })
    assert replay["ok"] is True
    assert len([
        event for event in log.read_all()
        if event.type == "workflow.invoke.requested"
    ]) == 1


def test_vague_request_requires_clarification_and_never_invokes(tmp_path: Path) -> None:
    service, log = _service(tmp_path)

    result = _execute(service, "workflow-request", {
        "kind": "issue",
        "objective": "",
        "open_questions": ["Which checkout path is affected?"],
        "backend": "mock",
        "allow_missing_env": True,
    })

    assert result["ok"] is False
    assert result["status"] == "clarification_required"
    blocker_kinds = {item["kind"] for item in result["blockers"]}
    assert "workflow_request_required_fields_missing" in blocker_kinds
    assert "workflow_request_open_questions" in blocker_kinds
    assert "workflow.invoke.requested" not in [event.type for event in log.read_all()]


def test_initialized_idea_to_product_proposes_request_then_submit(tmp_path: Path) -> None:
    service, log = _service(tmp_path)

    result = _execute(service, "idea-to-product", {
        "objective": "Fix session expiry and add a regression test",
        "kind": "issue",
        "backend": "mock",
        "allow_missing_env": True,
    })

    assert result["ok"] is True
    proposal = [
        event for event in log.read_all() if event.type == "operator.action.proposed"
    ][-1]
    assert [item["action"] for item in proposal.payload["proposals"]] == [
        "workflow-request",
        "workflow-submit",
    ]
