from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.runtime.self_issue_assessment_executor import (
    _AssessmentClaudeBackend,
    _AssessmentCodexBackend,
    _AssessmentObserver,
    AssessmentValidationError,
    maybe_schedule_web_assessment,
    _parse_report,
    _validated_assessment,
    run_self_issue_assessment,
)
from zf.runtime.self_issue_assessment_workspace import AssessmentWorkspace
from zf.runtime.self_issue_evidence_activity import (
    EvidenceActivityStore,
    read_evidence_activity,
)
from zf.runtime.self_issue_reproduction_ledger import (
    initialize_reproduction_ledger,
    read_reproduction_ledger,
    record_reproduction_result,
    reproduction_ledger_path,
    reserve_reproduction_attempt,
)
from zf.runtime.self_issue_service import SelfIssueService
from zf.web.headless_agent import HeadlessMessage, HeadlessTurnResult


def _answers() -> dict[str, object]:
    return {
        "title": "Assessment test", "bug_description": "A deterministic symptom.",
        "reproduction_steps": "Run the focused command.", "expected_behavior": "It passes.",
        "attachments_context": "", "environment": {"os": "Linux", "version": "24.04"},
        "zaofu_version": "0.0.3", "additional_context": "",
    }


def _report() -> dict[str, object]:
    return {
        "schema_version": "self-issue-assessment.v1",
        "classification": "runtime", "severity": "P1",
        "reproduction_status": "reproduced", "component": "runtime/worker",
        "impact_scope": "one worker run", "confidence": "high",
        "analysis": {
            "observations": ["focused reproduction failed"],
            "hypotheses": ["deadline handling"], "counter_evidence": [],
            "unknowns": [], "code_locations": ["src/zf/runtime/worker.py:1"],
            "duplicate_assessment": "none",
            "log_findings": [],
        },
        "recommended_next_action": "Add a focused regression test.",
    }


def _started(tmp_path: Path):
    state = tmp_path / ".state"
    state.mkdir()
    writer = EventWriter(EventLog(state / "events.jsonl"))
    service = SelfIssueService(state, writer, project_root=tmp_path)
    intake = service.capture({
        "target_binding": {"provider": "gitlab", "project": "a/b"},
    })["intake"]
    draft = service.submit_intake({
        "intake_id": intake["intake_id"], "answers": _answers(),
    })["draft"]
    guard = state / "processes" / "watcher.pid.json"
    guard.parent.mkdir(parents=True, exist_ok=True)
    guard.write_text(json.dumps({"owner_pid": os.getpid()}), encoding="utf-8")
    service.start_evidence({
        "draft_id": draft["draft_id"], "revision": draft["revision"],
    })
    started = service.claim_pending_assessment(owner_pid=os.getpid())
    assert started is not None
    return state, writer, service, started


def test_existing_orchestrator_role_assesses_with_full_workspace_access(
    tmp_path: Path, monkeypatch,
) -> None:
    state, writer, service, started = _started(tmp_path)
    skill = tmp_path / "skill" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("Read-only evidence assessment.\n", encoding="utf-8")
    monkeypatch.setattr(
        "zf.runtime.self_issue_assessment_executor.resolve_skill_source",
        lambda **_: skill,
    )
    observed: dict[str, object] = {}

    def workspace_builder(*, capsule, project_root, input_path, skill_root, state_dir):
        root = Path(capsule) / "workspace"
        root.mkdir()
        shutil.copy2(input_path, root / "evidence-input.json")
        return AssessmentWorkspace(root=root, manifest={"snapshot": "committed"})

    class FakeAgent:
        def __init__(self, **kwargs):
            observed["agent_kwargs"] = kwargs

        def run_turn(self, **kwargs):
            observed["turn"] = kwargs
            kwargs["on_message"](HeadlessMessage(type="tool_use", tool="Read"))
            assert "not a diagnosis Agent" in kwargs["message"]
            assert kwargs["permission_profile"] == "issue_investigation"
            return HeadlessTurnResult(
                ok=True, status="completed", backend="codex-headless",
                thread_id="thread", provider_session_id="session",
                reply=json.dumps(_report()), messages=[], usage={},
            )

    result = run_self_issue_assessment(
        state_dir=state, writer=writer,
        config=ZfConfig(project=ProjectConfig(name="test", state_dir=".state")),
        project_root=tmp_path, start_result=started, backend="codex-headless",
        surface="web", agent_factory=FakeAgent, workspace_builder=workspace_builder,
    )

    assert result["status"] == "evidence_completed"
    stored = service.drafts.get(started["draft"]["draft_id"])
    assert stored is not None
    assert stored.component == "unknown"
    projection = service.enrichment.get(stored.draft_id)
    assert projection is not None
    assert any(
        item.field_path == "component" and item.proposed_value == "runtime/worker"
        for item in projection.suggestions
    )
    assert stored.evidence_result_ref["kind"] == "self_issue_assessment"
    assert observed["agent_kwargs"]["timeout_s"] == 1200  # type: ignore[index]
    assert observed["turn"]["context"]["role"] == "orchestrator"  # type: ignore[index]


def test_observer_stops_the_twenty_first_tool_call(tmp_path: Path) -> None:
    state = tmp_path / ".state"
    state.mkdir()
    activity = EvidenceActivityStore(state, draft_id="draft-1", run_id="run-1")
    observer = _AssessmentObserver(activity)
    for _ in range(20):
        observer(HeadlessMessage(type="tool_use", tool="Read"))
    with pytest.raises(RuntimeError, match="tool budget exhausted"):
        observer(HeadlessMessage(type="tool_use", tool="Read"))


def test_codex_sandbox_failure_falls_back_to_claude_read_only(
    tmp_path: Path, monkeypatch,
) -> None:
    state, writer, service, started = _started(tmp_path)
    skill = tmp_path / "skill" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("Read-only evidence assessment.\n", encoding="utf-8")
    monkeypatch.setattr(
        "zf.runtime.self_issue_assessment_executor.resolve_skill_source",
        lambda **_: skill,
    )
    calls: list[str] = []

    def workspace_builder(*, capsule, input_path, **kwargs):
        root = Path(capsule) / "workspace"
        root.mkdir()
        shutil.copy2(input_path, root / "evidence-input.json")
        return AssessmentWorkspace(root=root, manifest={"snapshot": "committed"})

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        def run_turn(self, **kwargs):
            calls.append(kwargs["backend"])
            if kwargs["backend"] == "codex-headless":
                return HeadlessTurnResult(
                    ok=False, status="sandbox_unsupported", backend="codex-headless",
                    thread_id="thread", provider_session_id="", reply="",
                    messages=[], usage={}, error="host detail must not persist",
                )
            return HeadlessTurnResult(
                ok=True, status="completed", backend="claude-headless",
                thread_id="thread", provider_session_id="session",
                reply=json.dumps(_report()), messages=[], usage={},
            )

    result = run_self_issue_assessment(
        state_dir=state, writer=writer,
        config=ZfConfig(project=ProjectConfig(name="test", state_dir=".state")),
        project_root=tmp_path, start_result=started, backend="codex-headless",
        surface="web", agent_factory=FakeAgent, workspace_builder=workspace_builder,
    )

    assert result["status"] == "evidence_completed"
    assert calls == ["codex-headless", "claude-headless"]
    stored = service.drafts.get(started["draft"]["draft_id"])
    assert stored is not None and stored.evidence_status == "completed"
    assert "host detail" not in (state / "events.jsonl").read_text(encoding="utf-8")


def test_malformed_or_secret_bearing_reply_fails_without_applying_a_result(
    tmp_path: Path, monkeypatch,
) -> None:
    state, writer, service, started = _started(tmp_path)
    skill = tmp_path / "skill" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("safe", encoding="utf-8")
    monkeypatch.setattr(
        "zf.runtime.self_issue_assessment_executor.resolve_skill_source", lambda **_: skill,
    )

    def workspace_builder(*, capsule, **kwargs):
        root = Path(capsule) / "workspace"
        root.mkdir()
        return AssessmentWorkspace(root=root, manifest={})

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        def run_turn(self, **kwargs):
            return HeadlessTurnResult(
                ok=True, status="completed", backend="codex-headless",
                thread_id="thread", provider_session_id="session",
                reply="TOKEN=should-never-persist not-json", messages=[], usage={},
            )

    result = run_self_issue_assessment(
        state_dir=state, writer=writer,
        config=ZfConfig(project=ProjectConfig(name="test", state_dir=".state")),
        project_root=tmp_path, start_result=started, backend="codex-headless",
        surface="web", agent_factory=FakeAgent, workspace_builder=workspace_builder,
    )
    assert result["status"] == "evidence_failed"
    stored = service.drafts.get(started["draft"]["draft_id"])
    assert stored is not None
    assert stored.assessment_status == "failed"
    assert stored.evidence_status == "failed"
    assert not stored.evidence_result_ref
    assert "should-never-persist" not in json.dumps(stored.to_dict())
    assert "should-never-persist" not in (state / "events.jsonl").read_text(encoding="utf-8")
    activity = read_evidence_activity(state, started["draft"]["draft_id"])
    assert activity is not None
    assert any(
        entry["label"].startswith("assessment_invalid_json;")
        for entry in activity["entries"]
    )


def test_provider_failure_after_three_attempts_fails_without_fabricated_result(
    tmp_path: Path, monkeypatch,
) -> None:
    state, writer, service, started = _started(tmp_path)
    skill = tmp_path / "skill" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("safe", encoding="utf-8")
    monkeypatch.setattr(
        "zf.runtime.self_issue_assessment_executor.resolve_skill_source", lambda **_: skill,
    )
    ledger = reproduction_ledger_path(
        state,
        draft_id=started["draft"]["draft_id"],
        run_id=started["run_id"],
    )
    for index in range(1, 4):
        target = f"subject:tests/test_{index}.py"
        reserved = reserve_reproduction_attempt(ledger, target=target)
        record_reproduction_result(
            ledger,
            attempt=int(reserved["attempt"]),
            target=target,
            status="failed",
        )

    def workspace_builder(*, capsule, **kwargs):
        root = Path(capsule) / "workspace"
        root.mkdir(parents=True)
        return AssessmentWorkspace(root=root, manifest={})

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        def run_turn(self, **kwargs):
            return HeadlessTurnResult(
                ok=False, status="timeout", backend="claude-headless",
                thread_id="thread", provider_session_id="", reply="",
                messages=[], usage={}, error="unsafe provider detail",
            )

    result = run_self_issue_assessment(
        state_dir=state, writer=writer,
        config=ZfConfig(project=ProjectConfig(name="test", state_dir=".state")),
        project_root=tmp_path, start_result=started, backend="claude-headless",
        surface="web", agent_factory=FakeAgent, workspace_builder=workspace_builder,
    )

    assert result["status"] == "evidence_failed"
    stored = service.drafts.get(started["draft"]["draft_id"])
    assert stored is not None and stored.assessment_status == "failed"
    assert not stored.evidence_result_ref
    assert "unsafe provider detail" not in json.dumps(stored.to_dict())


def test_assessment_parser_rejects_prose_schema_drift_and_legacy_schema() -> None:
    with pytest.raises(AssessmentValidationError, match="assessment_invalid_json"):
        _parse_report("analysis: probably runtime")
    with pytest.raises(ValueError, match="canonical schema"):
        from zf.runtime.self_issue_evidence_run import normalize_assessment

        normalize_assessment({**_report(), "extra": "field"})
    with pytest.raises(ValueError, match="unsupported"):
        from zf.runtime.self_issue_evidence_run import normalize_assessment

        normalize_assessment({**_report(), "schema_version": "self-issue-diagnosis.v3"})


@pytest.mark.parametrize(
    ("report", "code"),
    [
        ({key: value for key, value in _report().items() if key != "component"},
         "assessment_missing_field: component"),
        ({**_report(), "confidence": "certain"},
         "assessment_invalid_enum: confidence"),
        ({**_report(), "unexpected": "unsafe value"}, "assessment_unknown_field"),
    ],
)
def test_assessment_validation_exposes_only_safe_failure_categories(
    report: dict[str, object], code: str,
) -> None:
    with pytest.raises(AssessmentValidationError, match=re.escape(code)):
        _validated_assessment(json.dumps(report))


def test_assessment_output_size_is_bounded() -> None:
    with pytest.raises(AssessmentValidationError, match="assessment_output_too_large"):
        _parse_report("x" * (64 * 1024 + 1))


def test_activity_numbers_reproductions_and_distinguishes_start_from_result(
    tmp_path: Path,
) -> None:
    activity = EvidenceActivityStore(tmp_path, draft_id="sid-1", run_id="sie-1")
    activity.start()
    ledger = initialize_reproduction_ledger(
        tmp_path, draft_id="sid-1", run_id="sie-1",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    observer = _AssessmentObserver(
        activity, reproduction_ledger=ledger, workspace_root=workspace,
    )
    command = "./run-reproduction subject tests/test_web.py::test_snapshot"
    observer(HeadlessMessage(type="tool_use", tool="Bash", input={"command": command}))
    observer(HeadlessMessage(
        type="tool_result",
        output=(
            'ZF_REPRODUCTION_EVENT {"attempt": 1, "max_attempts": 3, '
            '"status": "failed", '
            '"target": "subject:tests/test_web.py::test_snapshot"}'
        ),
    ))

    stored = read_evidence_activity(tmp_path, "sid-1")
    assert stored is not None
    labels = [entry["label"] for entry in stored["entries"]]
    assert labels[-2:] == [
        "Reproduction 1/3 started · subject:tests/test_web.py::test_snapshot",
        "Reproduction 1/3 failed · subject:tests/test_web.py::test_snapshot",
    ]


def test_turn_end_reconciles_runner_state_when_provider_omits_tool_messages(
    tmp_path: Path, monkeypatch,
) -> None:
    state, writer, _, started = _started(tmp_path)
    skill = tmp_path / "skill" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("safe", encoding="utf-8")
    monkeypatch.setattr(
        "zf.runtime.self_issue_assessment_executor.resolve_skill_source", lambda **_: skill,
    )

    def workspace_builder(*, capsule, input_path, **kwargs):
        root = Path(capsule) / "workspace"
        root.mkdir()
        shutil.copy2(input_path, root / "evidence-input.json")
        return AssessmentWorkspace(root=root, manifest={})

    class SilentToolAgent:
        def __init__(self, **kwargs):
            self.root = Path(kwargs["project_root"])

        def run_turn(self, **kwargs):
            state_path = self.root / ".assessment-runtime" / "reproductions.json"
            body = json.loads(state_path.read_text(encoding="utf-8"))
            body["attempts"].append({
                "attempt": 1,
                "target": "subject:tests/test_web.py::test_snapshot",
                "status": "passed",
            })
            state_path.write_text(json.dumps(body), encoding="utf-8")
            return HeadlessTurnResult(
                ok=True, status="completed", backend="claude-headless",
                thread_id="thread", provider_session_id="session",
                reply=json.dumps(_report()), messages=[], usage={},
            )

    result = run_self_issue_assessment(
        state_dir=state, writer=writer,
        config=ZfConfig(project=ProjectConfig(name="test", state_dir=".state")),
        project_root=tmp_path, start_result=started, backend="claude-headless",
        surface="web", agent_factory=SilentToolAgent, workspace_builder=workspace_builder,
    )

    ledger = reproduction_ledger_path(
        state,
        draft_id=started["draft"]["draft_id"],
        run_id=started["run_id"],
    )
    activity = read_evidence_activity(state, started["draft"]["draft_id"])
    assert result["status"] == "evidence_completed"
    assert read_reproduction_ledger(ledger)["attempts"][0]["status"] == "passed"
    assert activity is not None
    assert any(
        entry["label"].startswith("Reproduction 1/3 passed")
        for entry in activity["entries"]
    )


def test_resume_restores_the_same_run_reproduction_budget(tmp_path: Path) -> None:
    activity = EvidenceActivityStore(tmp_path, draft_id="sid-1", run_id="sie-1")
    activity.start()
    ledger = initialize_reproduction_ledger(
        tmp_path, draft_id="sid-1", run_id="sie-1",
    )
    first = reserve_reproduction_attempt(ledger, target="subject:tests/a.py")
    record_reproduction_result(
        ledger, attempt=int(first["attempt"]), target="subject:tests/a.py", status="failed",
    )
    second = reserve_reproduction_attempt(ledger, target="subject:tests/b.py")
    record_reproduction_result(
        ledger, attempt=int(second["attempt"]), target="subject:tests/b.py", status="timeout",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    observer = _AssessmentObserver(
        activity, reproduction_ledger=ledger, workspace_root=workspace,
    )
    observer(HeadlessMessage(
        type="tool_use", tool="Bash",
        input={"command": "./run-reproduction subject tests/test_web.py::test_snapshot"},
    ))
    stored = read_evidence_activity(tmp_path, "sid-1")
    assert stored is not None
    assert stored["entries"][-1]["label"].startswith("Reproduction 3/3 started")
    assert len(read_reproduction_ledger(ledger)["attempts"]) == 3


def test_assessment_backends_enforce_read_only_noninteractive_permissions() -> None:
    codex = _AssessmentCodexBackend().security_config("dangerous_full")
    assert codex == {"approvalPolicy": "never", "sandbox": "read-only"}
    args = _AssessmentClaudeBackend().build_args(
        thread_id="thread", permission_profile="dangerous_full",
        provider_session_id="", system_prompt="",
    )
    assert "dontAsk" in args
    assert "Edit,Write,NotebookEdit,WebFetch,WebSearch,Task" in args


def test_legacy_web_scheduler_shim_does_not_start_or_cancel_runtime_work(
    tmp_path: Path,
) -> None:
    response = maybe_schedule_web_assessment(
        "self-issue-evidence-interrupt",
        {
            "ok": True, "status": "evidence_interrupted", "run_id": "sie-1",
            "thread_id": "self-issue-assessment:sid-1:sie-1",
        },
        {}, state_dir=tmp_path, writer=None, config=None, project_root=tmp_path,  # type: ignore[arg-type]
    )

    assert response == {
        "ok": True, "status": "evidence_interrupted", "run_id": "sie-1",
        "thread_id": "self-issue-assessment:sid-1:sie-1",
    }
