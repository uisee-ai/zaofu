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
from zf.core.security.secret_provider import LocalSecretProvider, SecretKey
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.external_issue_clarification import (
    external_issue_triage_outcome_event,
    normalize_external_issue_disposition,
)


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
    key = SecretKey("local-user", str(tmp_path.resolve()), "github", "github.com")
    secrets = LocalSecretProvider(state_dir / "secrets" / "forge-credentials.json")
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
