from __future__ import annotations

import base64
import json
from pathlib import Path

from issue_core.contracts import (
    AssessmentDecision,
    ForgeIssuePayload,
    ForgePublishResult,
    PublishedIssueRef,
)
from issue_core.draft import DraftRevisionConflict, IssueDraft
from issue_core.flow import IssueFlow
from issue_core.assessment import AssessmentFinding, ProjectAssessmentResult
from issue_core.environment import detect_project_version
from issue_core.normalizer import normalize_unstructured_report
from issue_core.publication import PublicationSnapshot, PublicationState
from issue_core.structure import TransientStructureError, structure_report
from zf.core.events import EventLog, EventWriter
import zf.runtime.self_issue_service as self_issue_service_module
from zf.runtime.self_issue_service import SelfIssueService


def test_free_form_report_is_split_into_editable_issue_sections() -> None:
    result = normalize_unstructured_report(
        "Kanban freezes\n\n"
        "Bug description:\nThe board stops updating.\n\n"
        "Steps:\n1. Open the board\n2. Click Triage\n\n"
        "Expected behavior:\nThe board should refresh.",
        environment={"os": "Linux", "version": "24.04"},
        project_version="host-1.2.3",
    )
    assert result["title"] == "Kanban freezes"
    assert result["bug_description"] == "The board stops updating."
    assert result["reproduction_steps"] == "1. Open the board\n2. Click Triage"
    assert result["expected_behavior"] == "The board should refresh."
    assert result["actual_behavior"] == ""
    assert result["environment"] == {"os": "Linux", "version": "24.04"}
    assert result["additional_context"] == ""
    assert result["understanding"]["field_provenance"]["additional_context"] == {
        "source": "user", "confidence": 0.0,
    }


def test_additional_context_starts_empty_even_when_raw_report_contains_a_section() -> None:
    result = normalize_unstructured_report(
        "Save freezes\n\nAdditional context:\nInternal note that must stay in raw evidence.",
    )
    assert result["additional_context"] == ""


def test_direct_capture_creates_draft_without_starting_assessment(tmp_path: Path) -> None:
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    service = SelfIssueService(
        state_dir,
        EventWriter(EventLog(state_dir / "events.jsonl")),
        project_root=tmp_path,
    )
    result = service.capture({
        "direct_report": True,
        "description": "The page is blank after clicking Save.",
        "target_binding": {"provider": "github", "project": "owner/repo"},
    })
    assert result["status"] == "draft_ready"
    assert result["start_evidence"] is False
    assert result["draft"]["assessment_status"] == "not_started"
    assert result["draft"]["additional_context"] == ""
    assert result["draft"]["environment"]["os"]
    raw_ref = result["draft"]["evidence_refs"][0]["ref"]
    assert (state_dir / raw_ref).is_file()
    limited = service.continue_limited({"draft_id": result["draft"]["draft_id"]})
    assert limited["status"] == "evidence_limited"
    assert limited["draft"]["assessment_status"] == "skipped"


def test_partial_browser_environment_falls_back_to_host_detector(
    tmp_path: Path, monkeypatch,
) -> None:
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    monkeypatch.setattr(
        self_issue_service_module,
        "detect_environment",
        lambda: {
            "os": "TestOS", "version": "9.1", "arch": "test64",
            "source": "host_detector",
        },
    )
    service = SelfIssueService(
        state_dir,
        EventWriter(EventLog(state_dir / "events.jsonl")),
        project_root=tmp_path,
    )
    result = service.capture({
        "direct_report": True,
        "description": "Partial browser metadata",
        "environment": {"os": "", "version": ""},
    })
    assert result["draft"]["environment"] == {
        "os": "TestOS", "version": "9.1", "arch": "test64",
        "source": "host_detector",
    }


def test_issue_draft_schema_requires_attachment_description() -> None:
    root = Path(__file__).parents[1]
    schema = json.loads((root / "schemas" / "issue-draft.v2.json").read_text(encoding="utf-8"))
    portable_schema = json.loads(
        (root / "skills" / "issue" / "schemas" / "issue-draft.v2.json").read_text(encoding="utf-8")
    )
    assert "attachment_context" in schema["required"]
    assert portable_schema == schema


def test_issue_core_contracts_are_host_neutral_and_revision_bound() -> None:
    decision = AssessmentDecision(
        draft_id="draft-1", draft_revision=2,
        decision="limited_report", actor_capability="reporter",
    )
    assert decision.to_dict()["schema_version"] == "assessment-decision.v1"
    payload = ForgeIssuePayload(
        project="owner/repo", title="Bug", body="Details", marker="m-1",
    )
    payload.validate()
    package = Path(__file__).parents[1] / "src" / "issue_core"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    assert "import zf" not in source
    assert "from zf" not in source


def test_portable_publication_state_machine_keeps_publish_and_connect_mutually_exclusive() -> None:
    snapshot = PublicationSnapshot("draft-1", 1, "github", "digest-1")
    state = PublicationState(snapshot)
    connect = state.request_publish(snapshot, connected=False, authorization_code="ABCD-1234")
    assert connect.status == "connect_required"
    assert connect.authorization_code == "ABCD-1234"
    publishing = connect.request_publish(snapshot, connected=True)
    assert publishing.status == "publishing"
    assert publishing.authorization_code == ""
    view = publishing.complete("https://github.com/owner/repo/issues/1")
    assert view.status == "view"
    assert view.issue_url.endswith("/1")
    assert view.request_publish(snapshot, connected=True) == view
    stale = view.request_publish(
        PublicationSnapshot("draft-1", 2, "github", "digest-2"), connected=True,
    )
    assert stale.status == "outcome_unknown"
    assert stale.reason == "stale_publication_snapshot"


def test_portable_draft_edit_is_compare_and_swap_revision_bound() -> None:
    draft = IssueDraft(
        draft_id="draft-1", title="Freeze", bug_description="Board freezes",
        reproduction_steps="Click Save", expected_behavior="Save succeeds",
        attachment_context="", environment={"os": "Linux", "version": "1"},
        project_version="2.0", additional_context="",
    )
    draft.validate()
    edited = draft.edit(expected_revision=1, title="Board freezes after Save")
    assert edited.revision == 2
    assert edited.title == "Board freezes after Save"
    try:
        draft.edit(expected_revision=2, additional_context="later")
    except DraftRevisionConflict:
        pass
    else:  # pragma: no cover - assertion keeps the contract explicit
        raise AssertionError("stale Draft edit must fail closed")


def test_portable_assessment_result_has_no_project_specific_categories() -> None:
    result = ProjectAssessmentResult(
        status="completed",
        summary="The failure is reproducible in the selected checkout.",
        findings=(AssessmentFinding(
            kind="reproduction", summary="Save action leaves the view unchanged",
            evidence_refs=("evidence://run/1",),
        ),),
    )
    value = result.to_dict()
    assert value["schema_version"] == "project-assessment-result.v1"
    assert value["findings"][0]["evidence_refs"] == ["evidence://run/1"]
    root = Path(__file__).parents[1]
    schema = json.loads(
        (root / "skills" / "issue" / "schemas" / "project-assessment-result.v1.json")
        .read_text(encoding="utf-8")
    )
    assert schema["properties"]["status"]["enum"] == ["completed", "failed", "not_run"]


def test_portable_issue_flow_requires_explicit_matching_assessment_decision() -> None:
    flow = IssueFlow()
    draft = flow.create_draft(
        draft_id="draft-flow", description="Save freezes\n\nExpected behavior:\nSave succeeds",
        environment={"os": "Linux", "version": "1"}, project_version="2.0",
    )
    limited = flow.record_assessment_decision(
        draft, decision="limited_report", actor_capability="reporter",
    )
    assert flow.assessment_is_dispatchable(draft, limited) is False
    assessment = flow.record_assessment_decision(
        draft, decision="project_assessment", actor_capability="reporter",
    )
    assert flow.assessment_is_dispatchable(draft, assessment) is True
    edited = draft.edit(expected_revision=1, additional_context="user note")
    assert flow.assessment_is_dispatchable(edited, assessment) is False
    publication = flow.publication(edited, provider="github", payload_digest="digest")
    assert publication.status == "previewed"


def test_portable_publication_state_preserves_partial_issue_and_can_reconcile() -> None:
    flow = IssueFlow()
    draft = flow.create_draft(
        draft_id="draft-partial", description="The page freezes",
        environment={"os": "Linux", "version": "1"}, project_version="2.0",
    )
    state = flow.publication(draft, provider="github", payload_digest="digest")
    publishing = state.request_publish(state.snapshot, connected=True)
    partial = publishing.mark_partial(
        "https://github.com/example/project/issues/9", "labels_pending",
    )
    assert partial.status == "partially_published"
    assert partial.issue_url.endswith("/issues/9")
    assert partial.complete(partial.issue_url).status == "view"


def test_portable_forge_contract_preserves_partial_issue_reference() -> None:
    result = ForgePublishResult(
        status="partially_published",
        issue=PublishedIssueRef(
            provider="github", project="owner/repo", number="9",
            url="https://github.com/owner/repo/issues/9",
        ),
        reason="labels_pending",
    )
    value = result.to_dict()
    assert value["status"] == "partially_published"
    assert value["issue"]["number"] == "9"


def test_portable_forge_contract_rejects_partial_without_remote_reference() -> None:
    result = ForgePublishResult(status="partially_published", reason="labels_pending")
    try:
        result.validate()
    except ValueError as exc:
        assert "remote Issue reference" in str(exc)
    else:
        raise AssertionError("partial publication without an Issue ref must fail closed")


def test_portable_issue_flow_auto_detects_environment_and_project_version(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'portable-app'\nversion = '7.8.9'\n", encoding="utf-8",
    )
    draft = IssueFlow().create_draft(
        draft_id="draft-detect", description="The page freezes", project_root=tmp_path,
    )
    assert draft.environment["os"]
    assert draft.environment["version"]
    assert draft.project_version == "7.8.9"


def test_direct_capture_accepts_empty_report_and_uses_project_detector(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '4.5.6'\n", encoding="utf-8",
    )
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    service = SelfIssueService(
        state_dir,
        EventWriter(EventLog(state_dir / "events.jsonl")),
        project_root=tmp_path,
    )
    result = service.capture({"direct_report": True, "description": ""})
    assert result["status"] == "draft_ready"
    assert result["draft"]["title"].startswith("Issue report — ")
    assert result["draft"]["bug_description"] == ""
    assert result["draft"]["actual_behavior"] == ""
    assert result["draft"]["project_version"] == "4.5.6"
    assert result["draft"]["analysis"]["project_version_detection"]["source"] == "pyproject.toml"
    assert result["draft"]["analysis"]["understanding"]["status"] == "fallback"
    assert result["draft"]["analysis"]["understanding"]["reason_code"] == (
        "structure_agent_unavailable"
    )


class _FixtureStructureAgent:
    def __init__(self, outputs: list[object]) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    def understand(self, description: str, **_: object) -> dict[str, object]:
        self.calls += 1
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return dict(output)  # type: ignore[arg-type]


def _structured_output() -> dict[str, object]:
    return {
        "title": "基础 wheel 缺少运行依赖，CLI 无法启动",
        "summary": "在全新的 Python 3.12 环境安装基础 wheel 后 CLI 启动失败。",
        "bug_description": "安装成功，但执行 CLI 时提示缺少 httpx。",
        "reproduction_steps": "1. 创建 Python 3.12 虚拟环境\n2. 安装基础 wheel\n3. 执行 CLI",
        "actual_behavior": "CLI 报 ModuleNotFoundError: httpx。",
        "expected_behavior": "基础 wheel 安装后 CLI 可以直接启动。",
        "attachment_context": "附有终端输出和 wheel METADATA。",
        "additional_context": "问题仅在不安装 dev extra 时出现。",
        "environment": {"os": "Linux", "version": "24.04", "python": "3.12"},
        "project_version": "0.0.7",
        "field_provenance": {
            name: {"source": "structure_agent", "confidence": 0.9}
            for name in (
                "title", "summary", "bug_description", "reproduction_steps",
                "actual_behavior", "expected_behavior", "attachment_context",
                "additional_context", "environment", "project_version",
            )
        },
    }


def test_structure_agent_understands_long_plain_language_without_field_markers(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    agent = _FixtureStructureAgent([_structured_output()])
    service = SelfIssueService(
        state_dir,
        EventWriter(EventLog(state_dir / "events.jsonl")),
        project_root=tmp_path,
        issue_understander=agent,
    )
    report = (
        "我在一个全新的 Python 3.12 虚拟环境安装基础 wheel，pip 说安装成功，"
        "但运行 CLI 就报缺少 httpx。附件里有终端输出和 METADATA，希望基础安装后能直接运行。"
    )
    result = service.capture({"direct_report": True, "description": report})
    draft = result["draft"]
    assert draft["title"] == "基础 wheel 缺少运行依赖，CLI 无法启动"
    assert draft["title"] != report
    assert draft["reproduction_steps"].startswith("1. 创建")
    assert draft["actual_behavior"].startswith("CLI 报")
    assert draft["analysis"]["understanding"]["status"] == "completed"
    assert draft["analysis"]["understanding"]["mode"] == "agent"


def test_structure_schema_repair_once_then_falls_back_without_guessing_title() -> None:
    agent = _FixtureStructureAgent([
        {"title": "whole report"},
        {"title": "still malformed"},
    ])
    result = structure_report(
        agent,
        "点击刷新以后页面没反应，过一会儿才更新。",
        environment={"os": "Linux", "version": "24.04"},
        project_version="1.0",
        local_timestamp="2026-09-18 10:00",
    )
    assert agent.calls == 2
    assert result.understanding_status == "fallback"
    assert result.reason_code == "structure_agent_schema_invalid"
    assert result.proposal["title"] == "Issue report — 2026-09-18 10:00"
    assert result.proposal["bug_description"].startswith("点击刷新")
    assert result.proposal["actual_behavior"] == ""


def test_structure_transient_failure_retries_at_most_twice() -> None:
    agent = _FixtureStructureAgent([
        TransientStructureError("timeout"),
        TransientStructureError("temporary 5xx"),
        _structured_output(),
    ])
    result = structure_report(
        agent,
        "report",
        environment={"os": "Linux", "version": "24.04"},
        project_version="1.0",
        local_timestamp="2026-09-18 10:00",
    )
    assert result.understanding_status == "completed"
    assert result.attempts == 3


def test_limited_draft_can_retry_understanding_while_unchanged(tmp_path: Path) -> None:
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    service = SelfIssueService(
        state_dir,
        EventWriter(EventLog(state_dir / "events.jsonl")),
        project_root=tmp_path,
    )
    captured = service.capture({
        "direct_report": True,
        "description": "安装 wheel 后运行 CLI 提示缺少 httpx。",
    })["draft"]
    assert captured["analysis"]["understanding"]["status"] == "fallback"
    service.issue_understander = _FixtureStructureAgent([_structured_output()])
    retried = service.retry_understanding({
        "draft_id": captured["draft_id"],
        "revision": captured["revision"],
    })
    assert retried["status"] == "understanding_completed"
    assert retried["draft"]["revision"] == 2
    assert retried["draft"]["title"] == "基础 wheel 缺少运行依赖，CLI 无法启动"


def test_limited_draft_can_retry_after_attachments_without_overwriting_edits(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    service = SelfIssueService(
        state_dir,
        EventWriter(EventLog(state_dir / "events.jsonl")),
        project_root=tmp_path,
    )
    captured = service.capture({
        "direct_report": True,
        "description": "安装 wheel 后运行 CLI 提示缺少 httpx。",
    })["draft"]
    attached = service.add_draft_attachment({
        "draft_id": captured["draft_id"],
        "revision": captured["revision"],
        "filename": "runtime.log",
        "content_type": "text/plain",
        "content_base64": base64.b64encode(b"missing httpx\n").decode("ascii"),
    })["draft"]
    assert attached["revision"] == 2
    assert attached["understanding_retry_allowed"] is True

    service.issue_understander = _FixtureStructureAgent([_structured_output()])
    retried = service.retry_understanding({
        "draft_id": attached["draft_id"],
        "revision": attached["revision"],
    })

    assert retried["status"] == "understanding_completed"
    assert retried["draft"]["revision"] == 3
    assert retried["draft"]["attachment_refs"][0]["filename"] == "runtime.log"
    assert retried["draft"]["environment"] == {
        "os": "Linux", "version": "24.04", "python": "3.12",
    }
    assert retried["draft"]["reported_expected_behavior"] == (
        "基础 wheel 安装后 CLI 可以直接启动。"
    )


def test_limited_draft_retry_remains_blocked_after_user_field_edit(tmp_path: Path) -> None:
    state_dir = tmp_path / ".state"
    state_dir.mkdir()
    service = SelfIssueService(
        state_dir,
        EventWriter(EventLog(state_dir / "events.jsonl")),
        project_root=tmp_path,
    )
    captured = service.capture({
        "direct_report": True,
        "description": "安装 wheel 后运行 CLI 提示缺少 httpx。",
    })["draft"]
    edited = service.update({
        "draft_id": captured["draft_id"],
        "revision": captured["revision"],
        "title": "用户修改后的标题",
    })["draft"]
    assert edited["understanding_retry_allowed"] is False

    service.issue_understander = _FixtureStructureAgent([_structured_output()])
    retried = service.retry_understanding({
        "draft_id": edited["draft_id"],
        "revision": edited["revision"],
    })

    assert retried["status"] == "understanding_retry_requires_unchanged_draft"
    assert retried["draft"]["title"] == "用户修改后的标题"


def test_project_detector_returns_unknown_without_project_metadata(tmp_path: Path) -> None:
    detected = detect_project_version(tmp_path)
    assert detected["value"] == "unknown"
    assert detected["source"] == "unknown"


def test_portable_issue_core_has_no_host_imports() -> None:
    package = Path(__file__).parents[1] / "src" / "issue_core"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    assert "import zf" not in source
    assert "from zf" not in source
