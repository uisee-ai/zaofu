from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.runtime.self_issue_liveness import self_issue_runtime_status
from zf.runtime.self_issue_runtime import schedule_pending_self_issue_assessment
from zf.runtime.self_issue_service import SelfIssueService


def _answers() -> dict[str, object]:
    return {
        "title": "Runtime-owned Self-Issue assessment",
        "bug_description": "The board stopped updating.",
        "reproduction_steps": "Open the board and wait for the next event.",
        "expected_behavior": "The board updates.",
        "attachments_context": "",
        "environment": {"os": "Linux", "version": "24.04"},
        "zaofu_version": "0.0.3",
        "additional_context": "",
    }


def _service(tmp_path: Path) -> tuple[SelfIssueService, EventLog]:
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    (state_dir / "session.yaml").write_text("session_id: test\n", encoding="utf-8")
    event_log = EventLog(state_dir / "events.jsonl")
    service = SelfIssueService(
        state_dir, EventWriter(event_log), project_root=tmp_path,
    )
    return service, event_log


def _draft(service: SelfIssueService) -> dict:
    intake = service.capture({
        "target_binding": {"provider": "gitlab", "project": "a/b"},
    })["intake"]
    return service.submit_intake({
        "intake_id": intake["intake_id"], "answers": _answers(),
    })["draft"]


def _mark_live(state_dir: Path) -> None:
    guard = state_dir / "processes" / "watcher.pid.json"
    guard.parent.mkdir(parents=True, exist_ok=True)
    guard.write_text(json.dumps({"owner_pid": os.getpid()}), encoding="utf-8")


def test_runtime_status_requires_a_live_watcher_owner(tmp_path: Path) -> None:
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    assert self_issue_runtime_status(state_dir) == "unknown"

    (state_dir / "session.yaml").write_text("session_id: test\n", encoding="utf-8")
    assert self_issue_runtime_status(state_dir) == "stopped"

    _mark_live(state_dir)
    assert self_issue_runtime_status(state_dir) == "live"

    (state_dir / "processes" / "watcher.pid.json").write_text(
        json.dumps({"owner_pid": 999_999_999}), encoding="utf-8",
    )
    assert self_issue_runtime_status(state_dir) == "stopped"


def test_stopped_runtime_collects_static_evidence_without_semantic_assessment(
    tmp_path: Path,
) -> None:
    service, event_log = _service(tmp_path)
    draft = _draft(service)

    result = service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })

    assert result["status"] == "evidence_waiting_for_runtime"
    assert result["scheduled"] is False
    assert result["draft"]["runtime_status"] == "stopped"
    assert result["draft"]["evidence_status"] == "waiting_for_runtime"
    assert result["draft"]["evidence_collection_mode"] == "static_only"
    assert result["draft"]["assessment_status"] == "waiting_for_runtime"
    plan_ref = result["draft"]["investigation_plan_ref"]
    plan = json.loads((service.state_dir / plan_ref["ref"]).read_text(encoding="utf-8"))
    assert plan["schema_version"] == "investigation-plan.v1"
    assert plan["draft_revision"] == result["draft"]["revision"]
    assert plan["budget"] == {
        "active_runtime_seconds": 1200,
        "command_timeout_seconds": 180,
        "max_no_new_evidence_rounds": 2,
        "max_reproductions_per_round": 3,
        "max_rounds": 3,
        "max_tools_per_round": 20,
        "transient_retries": 2,
    }
    enrichment = service.enrichment.get(draft["draft_id"])
    assert enrichment is not None
    assert len(enrichment.evidences) == 1
    assert enrichment.evidences[0].integrity == "verified"
    assert enrichment.evidences[0].applicability == "current"
    assert "self_issue.assessment.claimed" not in {
        event.type for event in event_log.read_all()
    }


def test_limited_offline_report_is_publishable_and_discloses_the_gap(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    draft = _draft(service)
    waiting = service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })

    service.interrupt_evidence({"draft_id": draft["draft_id"]})
    limited = service.continue_limited({"draft_id": draft["draft_id"]})
    preview = service.preview({"draft_id": draft["draft_id"]})

    assert waiting["draft"]["assessment_status"] == "waiting_for_runtime"
    assert limited["draft"]["evidence_status"] == "completed"
    assert limited["draft"]["evidence_collection_mode"] == "limited"
    assert limited["draft"]["assessment_status"] == "skipped"
    assert limited["draft"]["assessment_confidence"] == "low"
    body = preview["preview"]["body"]
    assert "Evidence collection was limited." in body
    assert "Not collected because the user interrupted evidence collection." in body
    assert "A project assessment was not performed" in body


def test_runtime_scheduler_claims_each_pending_assessment_once(
    tmp_path: Path, monkeypatch,
) -> None:
    service, event_log = _service(tmp_path)
    draft = _draft(service)
    _mark_live(service.state_dir)
    requested = service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })
    assert requested["status"] == "assessment_requested"

    started_threads: list[object] = []

    class DeferredThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self) -> None:
            started_threads.append(self)

    monkeypatch.setattr("zf.runtime.self_issue_runtime.threading.Thread", DeferredThread)
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=".state"),
        roles=[RoleConfig(name="orchestrator", backend="claude-code")],
    )
    orchestrator = SimpleNamespace(
        state_dir=service.state_dir,
        event_writer=EventWriter(event_log),
        project_root=tmp_path,
        config=config,
    )

    assert schedule_pending_self_issue_assessment(orchestrator) is True
    assert schedule_pending_self_issue_assessment(orchestrator) is False
    assert len(started_threads) == 1
    stored = service.drafts.get(draft["draft_id"])
    assert stored is not None
    assert stored.assessment_status == "running"
    assert stored.assessment_claim_id.startswith("siac-")


def test_runtime_liveness_does_not_dispatch_assessment_without_user_choice(
    tmp_path: Path,
) -> None:
    service, event_log = _service(tmp_path)
    draft = _draft(service)
    waiting = service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })
    _mark_live(service.state_dir)

    checked = service.check_runtime({
        "draft_id": draft["draft_id"],
        "revision": waiting["draft"]["revision"],
    })

    assert checked["status"] == "runtime_live_assessment_choice_required"
    assert checked["draft"]["assessment_status"] == "not_started"
    assert not any(
        event.type == "self_issue.assessment.requested"
        for event in event_log.read_all()
    )

    selected = service.start_evidence({
        "draft_id": draft["draft_id"],
        "revision": checked["draft"]["revision"],
        "assessment_decision": "project_assessment",
        "actor_capability": "user",
    })
    assert selected["status"] == "assessment_requested"
    decisions = [
        event for event in event_log.read_all()
        if event.type == "self_issue.assessment.decision.recorded"
    ]
    assert decisions[-1].payload["decision"] == "project_assessment"
    assert decisions[-1].payload["draft_revision"] == checked["draft"]["revision"]


def test_live_runtime_can_explicitly_choose_limited_without_assessment_dispatch(
    tmp_path: Path,
) -> None:
    service, event_log = _service(tmp_path)
    _mark_live(service.state_dir)
    draft = service.capture({
        "direct_report": True,
        "description": "The board freezes after a refresh.",
    })["draft"]

    limited = service.continue_limited({
        "draft_id": draft["draft_id"],
        "revision": draft["revision"],
        "actor_capability": "user",
    })

    assert limited["status"] == "evidence_limited"
    assert limited["draft"]["assessment_status"] == "skipped"
    assert limited["draft"]["evidence_collection_mode"] == "limited"
    assert not any(
        event.type == "self_issue.assessment.requested"
        for event in event_log.read_all()
    )

    repeated = service.continue_limited({
        "draft_id": draft["draft_id"],
        "revision": limited["draft"]["revision"],
    })
    assert repeated["status"] == "evidence_limited"
    assert repeated["idempotent"] is True


def test_evidence_start_limited_decision_uses_limited_path(
    tmp_path: Path,
) -> None:
    service, event_log = _service(tmp_path)
    _mark_live(service.state_dir)
    draft = service.capture({
        "direct_report": True,
        "description": "The board freezes after a refresh.",
    })["draft"]

    limited = service.start_evidence({
        "draft_id": draft["draft_id"],
        "revision": draft["revision"],
        "assessment_decision": "limited_report",
    })

    assert limited["status"] == "evidence_limited"
    assert limited["draft"]["assessment_status"] == "skipped"
    assert not any(
        event.type == "self_issue.assessment.requested"
        for event in event_log.read_all()
    )


def test_limited_choice_cannot_race_an_already_requested_assessment(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    _mark_live(service.state_dir)
    draft = service.capture({
        "direct_report": True,
        "description": "The board freezes after a refresh.",
    })["draft"]
    requested = service.start_evidence({
        "draft_id": draft["draft_id"],
        "revision": draft["revision"],
        "assessment_decision": "project_assessment",
    })
    assert requested["status"] == "assessment_requested"

    limited = service.continue_limited({
        "draft_id": draft["draft_id"],
        "revision": requested["draft"]["revision"],
    })

    assert limited["ok"] is False
    assert limited["status"] == "evaluation_locked"


def test_direct_issue_capture_never_assesses_just_because_runtime_is_live(
    tmp_path: Path,
) -> None:
    service, event_log = _service(tmp_path)
    _mark_live(service.state_dir)
    result = service.capture({
        "direct_report": True,
        "description": "The project page does not refresh.",
    })
    assert result["status"] == "draft_ready"
    assert result["draft"]["assessment_status"] == "not_started"
    assert not any(
        event.type == "self_issue.assessment.requested"
        for event in event_log.read_all()
    )
