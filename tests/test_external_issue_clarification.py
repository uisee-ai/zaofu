from pathlib import Path
from zf.core.events.model import ZfEvent
from zf.core.config.schema import (
    ExternalIssueIngressConfig,
    ProjectConfig,
    SelfIssueConfig,
    SelfIssueTargetConfig,
    WorkflowConfig,
    ZfConfig,
)
from zf.core.events import EventLog, EventWriter
from zf.core.issue_triage.models import IssueMirror, derived_triage_group
from zf.core.issue_triage.store import IssueMirrorStore
from zf.core.security.secret_provider import (
    LocalSecretProvider,
    SecretKey,
    forge_credential_store_path,
    forge_credential_workspace_id,
)
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import write_sidecar_text
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.external_issue_clarification import (
    _disposition_from_report_markdown,
    _producer_disposition,
    clarification_request_body,
    clarification_request_marker,
    external_issue_triage_outcome_event,
    normalize_external_issue_disposition,
)


def test_remote_clarification_request_is_stable_and_contains_only_reporter_questions() -> None:
    marker = clarification_request_marker("github:1:2", "sha256:revision", "TRIAGE-1", 1)
    body = clarification_request_body({
        "round": 1,
        "questions": [
            {"question": "What exact action reproduces this?", "required": True},
            {"question": "Which OS version is affected?", "required": False},
        ],
    }, marker=marker)
    assert marker in body
    assert "What exact action reproduces this? (required)" in body
    assert "Which OS version is affected? (optional)" in body
    assert "source_revision" not in body
    assert marker == clarification_request_marker("github:1:2", "sha256:revision", "TRIAGE-1", 1)
    assert marker != clarification_request_marker("github:1:2", "sha256:other", "TRIAGE-1", 1)
    assert "Clarification request 1." in body


def test_needs_info_accepts_all_current_reporter_only_questions_at_once() -> None:
    normalized, issues = normalize_external_issue_disposition({
        "outcome": "needs_info",
        "reason": "Several reporter-only facts are absent.",
        "questions": [
            {
                "id": f"fact_{index}", "type": "long_text",
                "question": f"Please provide missing fact {index}.", "required": True,
            }
            for index in range(8)
        ],
    })
    assert not issues
    assert normalized is not None
    assert len(normalized["questions"]) == 8


def _clarification_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test", state_dir=".zf"),
        self_issue=SelfIssueConfig(
            enabled=True,
            target_project="owner/repo",
            target_locked=True,
            targets={"github": SelfIssueTargetConfig(
                provider="github", project="owner/repo",
            )},
            ingress=ExternalIssueIngressConfig(enabled=True),
        ),
        workflow=WorkflowConfig(flow_metadata_by_kind={"workflow": {
            "generic_workflow_contract_digest": "a" * 64,
        }}),
    )


def test_needs_info_disposition_is_structured_and_bounded() -> None:
    disposition, issues = normalize_external_issue_disposition({
        "outcome": "needs_info",
        "reason": "缺少可重复的操作步骤",
        "questions": [{
            "id": "reproduction_steps",
            "type": "long_text",
            "question": "请提供完整复现步骤",
            "required": True,
        }],
    })
    assert issues == []
    assert disposition is not None
    assert disposition["schema_version"] == "external-issue-clarification.v1"
    assert disposition["questions"][0]["id"] == "reproduction_steps"


def test_needs_info_rejects_invalid_question_shape() -> None:
    disposition, issues = normalize_external_issue_disposition({
        "outcome": "needs_info",
        "reason": "missing",
        "questions": [{
            "id": "Bad ID",
            "type": "free_form",
            "question": "?",
        }],
    })
    assert disposition is None
    assert {item["code"] for item in issues} >= {
        "invalid_or_duplicate", "invalid_value",
    }


def test_needs_info_rejects_only_an_oversized_combined_question_payload() -> None:
    disposition, issues = normalize_external_issue_disposition({
        "outcome": "needs_info",
        "reason": "missing indispensable reporter facts",
        "questions": [
            {
                "id": f"missing_fact_{index}",
                "type": "long_text",
                "question": f"Fact {index}: " + "x" * 470,
            }
            for index in range(1, 111)
        ],
    })
    assert disposition is None
    assert {
        (item["field"], item["code"])
        for item in issues
    } >= {(
        "control_result.external_issue_disposition.questions",
        "payload_too_large",
    )}


def test_unverified_candidate_is_a_valid_bounded_disposition() -> None:
    disposition, issues = normalize_external_issue_disposition({
        "outcome": "unverified_candidate",
        "reason": "源码范围明确，但动态验证未运行",
        "questions": [],
        "confidence": "medium",
        "verification_status": "partial",
        "verification_gaps": ["runtime stopped"],
    })
    assert issues == []
    assert disposition is not None
    assert disposition["outcome"] == "unverified_candidate"
    assert disposition["verification_gaps"] == ["runtime stopped"]


def test_legacy_markdown_report_recovers_needs_info_disposition() -> None:
    disposition = _disposition_from_report_markdown(
        "## 结论\n`verdict`: `needs_info`; `recommendation`: `needs_rework`。"
    )
    assert disposition is not None
    assert disposition["outcome"] == "needs_info"
    assert disposition["questions"][0]["id"] == "missing_required_context"


def test_legacy_markdown_non_user_gap_becomes_unverified_candidate() -> None:
    disposition = _disposition_from_report_markdown(
        "## 结论\n`verdict`: `needs_info`; `recommendation`: `needs_rework`。"
        "当前运行时已停止，未能执行动态复现。"
    )
    assert disposition is not None
    assert disposition["outcome"] == "unverified_candidate"
    assert disposition["verification_status"] == "partial"


def test_markdown_ready_to_fix_marker_is_recovered() -> None:
    disposition = _disposition_from_report_markdown(
        "## Disposition\n"
        "`external_issue_disposition: ready_to_fix`\n"
        "The bounded target and evidence anchor are present."
    )
    assert disposition is not None
    assert disposition["outcome"] == "ready_to_fix"


def test_english_runtime_gap_becomes_unverified_candidate() -> None:
    disposition = _disposition_from_report_markdown(
        "## Classification\n`verdict`: `needs_info`; `recommendation`: `needs_rework`.\n"
        "The runtime is stopped and no live browser capture was possible."
    )
    assert disposition is not None
    assert disposition["outcome"] == "unverified_candidate"


def test_report_status_marker_with_runtime_gap_becomes_unverified_candidate() -> None:
    disposition = _disposition_from_report_markdown(
        "## Classification\n"
        "Triage remains **needs_info** rather than ready-to-fix.\n"
        "The runtime is stopped and no live browser capture was possible.\n"
        "`external_issue_triage_status`: `needs_info`"
    )
    assert disposition is not None
    assert disposition["outcome"] == "unverified_candidate"


def test_long_report_reason_keeps_recovered_candidate_valid() -> None:
    disposition = _disposition_from_report_markdown(
        "`external_issue_triage_status`: `needs_info`\n"
        "The runtime is stopped.\n" + "details " * 300
    )
    assert disposition is not None
    assert disposition["outcome"] == "unverified_candidate"
    assert len(disposition["verification_gaps"][0]) <= 500


def test_long_report_uses_bounded_gap_and_keeps_complete_report_ref(tmp_path: Path) -> None:
    report_text = (
        "## 结论\n`external_issue_triage_status`: `needs_info`\n"
        "运行时已停止。\n" + "details " * 300
    )
    report_ref = write_sidecar_text(
        tmp_path,
        "artifacts/external-issues/reports/full-report.md",
        report_text,
        kind="external_issue_report",
        schema_version="external-issue-report.v1",
        created_by="test",
    )
    disposition = _disposition_from_report_markdown(
        report_text, report_ref=report_ref,
    )
    assert disposition is not None
    assert disposition["outcome"] == "unverified_candidate"
    assert len(disposition["verification_gaps"][0]) <= 500
    assert disposition["report_ref"] == report_ref
    assert hydrate_sidecar_ref(tmp_path, disposition["report_ref"]).payload == report_text


def test_inline_long_report_is_promoted_to_complete_sidecar(
    tmp_path: Path, monkeypatch,
) -> None:
    import zf.runtime.external_issue_clarification as module

    report_text = (
        "## 结论\n`external_issue_triage_status`: `needs_info`\n"
        "The runtime is stopped.\n" + "details " * 300
    )
    control = write_immutable_json_sidecar(
        tmp_path,
        {"outputs": {"report": report_text}},
        root="external-issues/control",
        kind="call_control_result",
        schema_version="workflow-read-result.v1",
        created_by="test",
    )
    monkeypatch.setattr(
        module,
        "hydrate_call_result_envelope",
        lambda _state_dir, _descriptor: {"control_result": control},
    )
    result = module._producer_disposition(tmp_path, [{}])
    assert result is not None
    assert result["outcome"] == "unverified_candidate"
    assert len(result["verification_gaps"][0]) <= 500
    full_report = hydrate_sidecar_ref(tmp_path, result["report_ref"]).payload
    assert full_report == report_text


def test_output_artifact_markdown_report_recovers_disposition(
    tmp_path: Path, monkeypatch,
) -> None:
    import zf.runtime.external_issue_clarification as module

    report = write_sidecar_text(
        tmp_path,
        "artifacts/generic-workflow/report.md",
        "## 结论\n`verdict`: `needs_info`; `recommendation`: `needs_rework`。"
        "运行时已停止，未能执行动态复现。",
        kind="report/markdown",
        schema_version="generic-workflow-output.v1",
        created_by="test",
    )
    control = write_immutable_json_sidecar(
        tmp_path,
        {"output_artifacts": [report]},
        root="generic-workflow/control",
        kind="call_control_result",
        schema_version="workflow-read-result.v1",
        created_by="test",
    )
    monkeypatch.setattr(
        module,
        "hydrate_call_result_envelope",
        lambda _state_dir, _descriptor: {
            "control_result": control,
        },
    )
    result = _producer_disposition(tmp_path, [{}])
    assert result is not None
    assert result["outcome"] == "unverified_candidate"


def test_structured_disposition_inherits_report_ref_from_output_artifact(
    tmp_path: Path, monkeypatch,
) -> None:
    """A structured disposition must retain the paired report binding."""

    import zf.runtime.external_issue_clarification as module

    report = write_sidecar_text(
        tmp_path,
        "artifacts/generic-workflow/report.md",
        "## Verification\n`uv run pytest tests/test_issue.py -q --no-cov`\n",
        kind="report/markdown",
        schema_version="generic-workflow-output.v1",
        created_by="test",
    )
    control = write_immutable_json_sidecar(
        tmp_path,
        {
            "external_issue_disposition": {
                "outcome": "ready_to_fix",
                "reason": "范围与验证目标明确",
                "questions": [],
            },
            "output_artifacts": [report],
        },
        root="generic-workflow/control",
        kind="call_control_result",
        schema_version="workflow-read-result.v1",
        created_by="test",
    )
    monkeypatch.setattr(
        module,
        "hydrate_call_result_envelope",
        lambda _state_dir, _descriptor: {"control_result": control},
    )

    result = module._producer_disposition(tmp_path, [{}])

    assert result is not None
    assert result["outcome"] == "ready_to_fix"
    assert result["report_ref"] == report


def test_forbidden_needs_info_reason_is_downgraded_to_unverified_candidate(
    tmp_path: Path, monkeypatch,
) -> None:
    import zf.runtime.external_issue_clarification as module

    disposition = {
        "outcome": "needs_info",
        "reason": "动态 Playwright 未运行",
        "reason_code": "no_dynamic_playwright",
        "questions": [],
    }
    monkeypatch.setattr(module, "_producer_disposition", lambda *args: disposition)
    invoke = ZfEvent(
        type="workflow.invoke.requested", task_id="TASK-3", correlation_id="TRIAGE-3",
        payload={"workflow_run_id": "TRIAGE-3", "source": "external_issue",
                 "source_key": "github:1:4", "source_revision": "sha256:r"},
    )
    verified = ZfEvent(
        type="artifact.delivery.verified", task_id="TASK-3", correlation_id="TRIAGE-3",
        payload={"workflow_run_id": "TRIAGE-3", "artifact_delivery_result": {}},
    )
    result = module.external_issue_triage_outcome_event(tmp_path, [invoke], cause=verified)
    assert result is not None
    assert result.type == "external_issue.triage.unverified_candidate"


def test_verified_needs_info_projects_sidecar_and_round(tmp_path: Path, monkeypatch) -> None:
    import zf.runtime.external_issue_clarification as module

    disposition = {
        "schema_version": "external-issue-clarification.v1",
        "outcome": "needs_info",
        "reason": "缺少日志",
        "questions": [{
            "id": "logs",
            "type": "long_text",
            "question": "请提供日志",
            "required": True,
        }],
    }
    monkeypatch.setattr(module, "_producer_disposition", lambda *args: disposition)
    invoke = ZfEvent(
        type="workflow.invoke.requested",
        task_id="ISSUE-TASK-1",
        correlation_id="TRIAGE-1",
        payload={
            "workflow_run_id": "TRIAGE-1",
            "source": "external_issue",
            "source_key": "github:1:2",
            "source_revision": "sha256:revision",
        },
    )
    verified = ZfEvent(
        type="artifact.delivery.verified",
        task_id="ISSUE-TASK-1",
        correlation_id="TRIAGE-1",
        payload={"workflow_run_id": "TRIAGE-1", "artifact_delivery_result": {}},
    )
    result = external_issue_triage_outcome_event(
        tmp_path, [invoke], cause=verified,
    )
    assert result is not None
    assert result.type == "external_issue.triage.needs_info"
    assert result.payload["round"] == 1
    assert result.payload["clarification_ref"]["ref"].startswith(
        "artifacts/external-issues/clarifications/"
    )


def test_verified_needs_info_reads_disposition_nested_in_report(
    tmp_path: Path, monkeypatch,
) -> None:
    import zf.runtime.external_issue_clarification as module

    disposition = {
        "outcome": "needs_info",
        "reason": "缺少日志",
        "questions": [{
            "id": "logs", "type": "long_text", "question": "请提供日志",
            "required": True,
        }],
    }
    monkeypatch.setattr(
        module,
        "_producer_disposition",
        lambda *args: disposition,
    )
    invoke = ZfEvent(
        type="workflow.invoke.requested",
        task_id="TASK-2",
        correlation_id="TRIAGE-2",
        payload={
            "workflow_run_id": "TRIAGE-2",
            "source": "external_issue",
            "source_key": "github:1:3",
            "source_revision": "sha256:revision",
        },
    )
    verified = ZfEvent(
        type="artifact.delivery.verified",
        task_id="TASK-2",
        correlation_id="TRIAGE-2",
        payload={"workflow_run_id": "TRIAGE-2", "artifact_delivery_result": {}},
    )
    result = module.external_issue_triage_outcome_event(
        tmp_path, [invoke], cause=verified,
    )
    assert result is not None
    assert result.payload["reason"] == "缺少日志"


def test_clarification_submit_is_idempotent_and_retriages(
    tmp_path: Path, monkeypatch,
) -> None:
    from zf.runtime.external_issue_clarification_service import (
        ExternalIssueClarificationService,
    )
    from zf.runtime import external_issue_clarification_service as service_module

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    mirror = IssueMirror(
        issue_key="github:1:2", provider="github", repository_id="1",
        repository="owner/repo", number=2, node_id="node", html_url="https://github.com/owner/repo/issues/2",
        title="Need details", author_login="reporter", github_state="open",
        created_at="2026-09-02T00:00:00+00:00", updated_at="2026-09-02T00:00:00+00:00",
        labels=(), derived_group=derived_triage_group("open", ()),
        last_seen_at="2026-09-02T00:00:00+00:00",
    )
    IssueMirrorStore(state_dir).upsert(mirror, "body")
    clarification = write_immutable_json_sidecar(
        state_dir,
        {"schema_version": "external-issue-clarification.v1", "status": "needs_info",
         "source_revision": "sha256:old", "task_id": "TASK-1", "round": 1,
         "reason": "missing", "questions": [{"id": "logs", "type": "long_text",
         "question": "Logs?", "required": True}], "workflow_run_id": "TRIAGE-1",
         "source_key": "github:1:2", "outcome": "needs_info", "max_rounds": 3},
        root="external-issues/clarifications", kind="external_issue_clarification",
        schema_version="external-issue-clarification.v1", created_by="test",
    )
    writer = EventWriter(EventLog(state_dir / "events.jsonl"), default_origin="kernel")
    writer.append(ZfEvent(type="external_issue.triage.needs_info", task_id="TASK-1",
        payload={"source_key": "github:1:2", "workflow_run_id": "TRIAGE-1",
                 "clarification_ref": clarification}))
    key = SecretKey(
        "local-user",
        forge_credential_workspace_id(tmp_path),
        "github",
        "github.com",
    )
    secrets = LocalSecretProvider(forge_credential_store_path(state_dir))
    secrets.put(key, {"access_token": "token"})

    class FakeGitHub:
        def publish_issue_comment(self, project, issue_number, body, *, marker, access_token):
            assert project == "owner/repo" and issue_number == 2 and access_token == "token"
            return {"status": "published", "url": "https://github.com/comment", "id": "7", "replayed": "false"}

    class FakeIngress:
        def __init__(self, *args, **kwargs): pass
        def start_manual_triage(self, number):
            return type("Outcome", (), {"queued": True, "status": "queued", "task_id": "TASK-2", "workflow_run_id": "TRIAGE-2", "source_revision": "new"})()

    monkeypatch.setattr(service_module, "ExternalIssueIngressService", FakeIngress)
    service = ExternalIssueClarificationService(
        state_dir, _clarification_config(), project_root=tmp_path,
        writer=writer, github=FakeGitHub(),
    )
    result = service.submit({"issue_number": 2, "triage_id": "TRIAGE-1", "answers": {"logs": "trace"}})
    assert result["ok"] is True
    assert result["status"] == "published_and_retriaged"
    assert any(event.type == "external_issue.clarification.published" for event in writer.event_log.read_all())
