from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import (
    ExternalIssueIngressConfig,
    ProjectConfig,
    SelfIssueConfig,
    SelfIssueTargetConfig,
    WorkflowConfig,
    ZfConfig,
)
from zf.core.events import EventLog
from zf.core.events.model import ZfEvent
from zf.core.issue_triage.models import IssueComment, IssueMirror, derived_triage_group
from zf.core.issue_triage.store import IssueMirrorStore
from zf.core.task.schema import TaskExecutionBinding
from zf.runtime.external_issue_ingress import ExternalIssueIngressService
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.task_contract_authority import TaskContractAuthorityService


class _Reconciler:
    def __init__(self, result: dict | None = None) -> None:
        self.result = result or {"ok": True, "status": "fresh", "changed": 1}
        self.calls: list[bool] = []

    def refresh(self, *, force: bool = False) -> dict:
        self.calls.append(force)
        return dict(self.result)

    def refresh_issue(self, issue_number: int) -> dict:
        self.calls.append(True)
        return {**self.result, "issue_number": issue_number}


def _config(*, auto_triage_new_only: bool = True) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="zaofu", state_dir=".zf"),
        self_issue=SelfIssueConfig(
            enabled=True,
            provider="github",
            authorization_domain="github.com",
            target_project="rzwu-uisee/zaofu-self-issue",
            target_locked=True,
            targets={
                "github": SelfIssueTargetConfig(
                    provider="github",
                    authorization_domain="github.com",
                    project="rzwu-uisee/zaofu-self-issue",
                    auth_mode="device_flow",
                ),
            },
            default_publication_mode="github",
            ingress=ExternalIssueIngressConfig(
                enabled=True,
                poll_interval_seconds=300,
                approval_label="zaofu:ready-to-fix",
                auto_triage_new_only=auto_triage_new_only,
            ),
        ),
        workflow=WorkflowConfig(
            flow_metadata_by_kind={
                "workflow": {
                    "generic_workflow_contract_digest": "a" * 64,
                    "completion_profile": "artifact_delivery",
                },
            },
        ),
    )


def _mirror(*, created_at: str, labels: tuple[str, ...] = ()) -> IssueMirror:
    return IssueMirror(
        issue_key="github:123:8",
        provider="github",
        repository_id="123",
        repository="rzwu-uisee/zaofu-self-issue",
        number=8,
        node_id="I_node_8",
        html_url="https://github.com/rzwu-uisee/zaofu-self-issue/issues/8",
        title="New ingress regression",
        author_login="reporter",
        github_state="open",
        created_at=created_at,
        updated_at=created_at,
        labels=labels,
        derived_group=derived_triage_group("open", labels),
        last_seen_at=created_at,
    )


def _service(
    tmp_path: Path,
    mirror: IssueMirror,
    *,
    auto_triage_new_only: bool = True,
) -> tuple[ExternalIssueIngressService, _Reconciler]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    IssueMirrorStore(state_dir).upsert(mirror, "Untrusted issue body")
    reconciler = _Reconciler()
    service = ExternalIssueIngressService(
        state_dir,
        _config(auto_triage_new_only=auto_triage_new_only),
        project_root=tmp_path,
        reconciler=reconciler,  # type: ignore[arg-type]
        now=lambda: datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    return service, reconciler


def test_new_issue_revision_queues_one_read_only_triage_run(tmp_path: Path) -> None:
    service, reconciler = _service(
        tmp_path,
        _mirror(created_at="2026-09-01T00:00:00+00:00"),
    )

    first = service.poll_once()
    second = service.poll_once()

    assert first.ok is True
    assert first.received == 1
    assert first.triage_queued == 1
    assert second.received == 0
    assert reconciler.calls == [False, False]
    events = EventLog(service.state_dir / "events.jsonl").read_all()
    assert sum(event.type == "external_issue.received" for event in events) == 1
    invokes = [event for event in events if event.type == "workflow.invoke.requested"]
    assert len(invokes) == 1
    assert invokes[0].payload["pattern_id"] == "external-issue-triage"
    assert invokes[0].payload["flow_kind"] == "workflow"
    assert invokes[0].payload["source_refs"]
    tasks = service.tasks.list_all()
    assert len(tasks) == 1
    assert tasks[0].execution_binding.owner == "workflow"
    assert tasks[0].contract.source_key == "github:123:8"
    assert tasks[0].contract.scope == []
    assert tasks[0].contract.exclusions


def test_same_revision_reuses_task_and_changed_revision_gets_retry_suffix(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path, _mirror(created_at="2026-09-01T00:00:00+00:00"))
    first = service.start_manual_triage(8)
    second = service.start_manual_triage(8)
    assert first.task_id == second.task_id
    assert first.workflow_run_id != second.workflow_run_id or second.queued is False

    mirror = service.mirrors.get(8)
    assert mirror is not None
    service.mirrors.upsert(mirror, "Changed issue body")
    changed = service.start_manual_triage(8)
    assert changed.task_id != first.task_id
    assert changed.task_id.endswith("-R1")


def test_issue_invoke_binds_runtime_flow_identity(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path,
        _mirror(created_at="2026-09-01T00:00:00+00:00"),
    )
    contract_path = service.state_dir / "config" / "run-contract.json"
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text(
        json.dumps({"contract_digest": "b" * 64}) + "\n",
        encoding="utf-8",
    )

    service.poll_once()

    invoke = next(
        event
        for event in EventLog(service.state_dir / "events.jsonl").read_all()
        if event.type == "workflow.invoke.requested"
    )
    assert invoke.payload["effective_config_digest"]
    assert invoke.payload["effective_config_ref"]["sha256"] == (
        invoke.payload["effective_config_digest"]
    )
    assert invoke.payload["run_contract_digest"] == "b" * 64
    assert invoke.payload["run_contract_ref"]
    assert not Path(invoke.payload["run_contract_ref"]).is_absolute()
    assert invoke.payload["run_contract_ref"].startswith("artifacts/run-contracts/")
    assert invoke.payload["goal_id"] == invoke.payload["workflow_run_id"]
    assert invoke.payload["workflow_generation"] == (
        invoke.payload["generic_workflow_contract_digest"]
    )
    assert invoke.payload["request_revision"] > 0
    assert invoke.payload["workflow_intent"] == "research"
    assert invoke.payload["workflow_template"] == "evidence-synthesis-v1"
    assert invoke.payload["completion_profile"] == "artifact_delivery"
    assert invoke.payload["goal_claim_set_ref"]
    assert len(invoke.payload["goal_claim_set_digest"]) == 64
    claim_event = next(
        event
        for event in EventLog(service.state_dir / "events.jsonl").read_all()
        if event.type == "goal.claim_set.pinned"
    )
    assert claim_event.payload["workflow_run_id"] == invoke.payload["workflow_run_id"]
    assert claim_event.payload["goal_claim_set_digest"] == (
        invoke.payload["goal_claim_set_digest"]
    )


def test_issue_invoke_rotates_software_baseline_to_artifact_contract(
    tmp_path: Path,
) -> None:
    service, _ = _service(
        tmp_path,
        _mirror(created_at="2026-09-01T00:00:00+00:00"),
    )
    (tmp_path / "zf.yaml").write_text("version: '1.0'\n", encoding="utf-8")
    contract_path = service.state_dir / "config" / "run-contract.json"
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text(json.dumps({
        "schema_version": "run-contract.v1",
        "contract_digest": "c" * 64,
        "workflow": {
            "completion_profile": "software_delivery",
            "generic_workflow_contract_digest": "",
        },
    }) + "\n", encoding="utf-8")

    identity = service._workflow_identity()
    snapshot = service.state_dir / identity["run_contract_ref"]
    body = json.loads(snapshot.read_text(encoding="utf-8"))
    workflow = body["contract"]["workflow"]
    assert workflow["completion_profile"] == "artifact_delivery"
    assert workflow["generic_workflow_contract_digest"] == "a" * 64
    assert workflow["workflow_generation"] == "a" * 64
    assert identity["run_contract_digest"] != "c" * 64


def test_issue_invoke_exposes_body_and_comments_as_controlled_inputs(
    tmp_path: Path,
) -> None:
    service, _ = _service(
        tmp_path,
        _mirror(created_at="2026-09-01T00:00:00+00:00"),
    )
    mirrored = service.mirrors.get(8)
    assert mirrored is not None
    mirrored, _ = service.mirrors.write_comments(
        mirrored,
        [
            IssueComment(
                id=1,
                node_id="IC_node_1",
                html_url=(
                    "https://github.com/rzwu-uisee/zaofu-self-issue/"
                    "issues/8#issuecomment-1"
                ),
                author_login="commenter",
                author_avatar_url="https://avatars.githubusercontent.com/u/1",
                body="Untrusted issue comment",
                created_at="2026-09-01T00:00:00+00:00",
                updated_at="2026-09-01T00:00:00+00:00",
            )
        ],
    )

    service.poll_once()

    invoke = next(
        event
        for event in EventLog(service.state_dir / "events.jsonl").read_all()
        if event.type == "workflow.invoke.requested"
    )
    refs = {
        str(item["ref"]): str(item["sha256"])
        for item in invoke.payload["artifact_refs"]
    }
    assert refs[mirrored.body_ref] == mirrored.body_digest.removeprefix("sha256:")
    assert refs[mirrored.comments_ref] == mirrored.comments_digest.removeprefix(
        "sha256:"
    )
    assert str(invoke.payload["source_refs"]["external_issue"]) in refs


def test_issue_invoke_materializes_github_image_attachment_refs(
    tmp_path: Path, monkeypatch,
) -> None:
    service, _ = _service(
        tmp_path,
        _mirror(created_at="2026-09-01T00:00:00+00:00"),
    )
    mirrored = service.mirrors.get(8)
    assert mirrored is not None
    body_path = service.state_dir / mirrored.body_ref
    body_path.write_text(
        "Screenshot: https://user-images.githubusercontent.com/1/shot.png\n",
        encoding="utf-8",
    )
    import hashlib
    mirrored = type(mirrored)(**{
        **mirrored.to_dict(),
        "body_digest": hashlib.sha256(body_path.read_bytes()).hexdigest(),
    })
    mirrored, _ = service.mirrors.upsert(
        mirrored, "Screenshot: https://user-images.githubusercontent.com/1/shot.png"
    )

    class _Response:
        headers = {"Content-Type": "image/png"}
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, limit): return b"png-bytes"

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: _Response())
    refs = service._issue_artifact_refs(
        mirrored, source_ref="artifacts/source.json", source_digest="source",
    )
    attachments = [item for item in refs if item.get("kind") == "external_issue_attachment"]
    assert len(attachments) == 1
    assert attachments[0]["sha256"] == hashlib.sha256(b"png-bytes").hexdigest()
    assert (service.state_dir / attachments[0]["ref"]).read_bytes() == b"png-bytes"


def test_historical_issue_is_mirrored_but_not_auto_triaged(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path,
        _mirror(created_at="2026-08-30T00:00:00+00:00"),
    )

    result = service.poll_once()

    assert result.ok is True
    assert result.received == 0
    assert service.tasks.list_all() == []
    assert not (service.state_dir / "events.jsonl").exists()


def test_operator_can_manually_queue_one_historical_issue_revision(tmp_path: Path) -> None:
    service, reconciler = _service(
        tmp_path,
        _mirror(created_at="2026-08-30T00:00:00+00:00"),
    )

    first = service.start_manual_triage(8)
    second = service.start_manual_triage(8)

    assert first.ok is True
    assert first.status == "queued"
    assert first.queued is True
    assert second.status == "already_queued"
    assert second.queued is False
    assert reconciler.calls == [True, True]
    events = EventLog(service.state_dir / "events.jsonl").read_all()
    received = next(event for event in events if event.type == "external_issue.received")
    assert received.actor == "web-operator"
    assert received.payload["admission_mode"] == "manual"
    invokes = [event for event in events if event.type == "workflow.invoke.requested"]
    assert len(invokes) == 1
    assert invokes[0].payload["requested_by"] == "web-operator"


def test_operator_can_retry_cancelled_historical_issue_revision(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path,
        _mirror(created_at="2026-08-30T00:00:00+00:00"),
    )

    first = service.start_manual_triage(8)
    events = EventLog(service.state_dir / "events.jsonl")
    writer = service.writer
    writer.emit(
        "run.cancelled",
        actor="web",
        task_id=first.task_id,
        payload={
            "run_id": first.workflow_run_id,
            "workflow_run_id": first.workflow_run_id,
        },
        correlation_id=first.workflow_run_id,
    )

    retry = service.start_manual_triage(8)

    assert retry.ok is True
    assert retry.queued is True
    # A cancelled retry on the same immutable Issue revision reactivates the
    # stable Task identity; only a changed revision receives ``-R<n>``.
    assert retry.task_id == first.task_id
    assert retry.workflow_run_id != first.workflow_run_id
    assert service.tasks.get(first.task_id).status == "backlog"
    queued = [
        event for event in events.read_all()
        if event.type == "external_issue.triage.queued"
    ]
    assert len(queued) == 2
    assert queued[1].payload["workflow_run_id"] == retry.workflow_run_id
    assert queued[1].payload["retry_of_workflow_run_id"] == first.workflow_run_id
    assert queued[1].payload["triage_attempt"] == 2


def test_operator_can_start_new_triage_after_terminal_disposition(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path,
        _mirror(created_at="2026-08-30T00:00:00+00:00"),
    )

    first = service.start_manual_triage(8)
    service.writer.emit(
        "external_issue.triage.ready_to_fix",
        actor="kernel",
        task_id=first.task_id,
        payload={
            "source_key": "github:123:8",
            "source_revision": first.source_revision,
            "workflow_run_id": first.workflow_run_id,
        },
        correlation_id=first.workflow_run_id,
    )

    retry = service.start_manual_triage(8)

    assert retry.ok is True
    assert retry.queued is True
    assert retry.workflow_run_id != first.workflow_run_id
    assert retry.task_id == first.task_id


def test_ready_label_is_only_an_approval_intent(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path,
        _mirror(
            created_at="2026-09-01T00:00:00+00:00",
            labels=("zaofu:ready-to-fix",),
        ),
    )

    service.poll_once()

    events = EventLog(service.state_dir / "events.jsonl").read_all()
    intent = next(
        event for event in events
        if event.type == "external_issue.fix_approval.intent"
    )
    assert intent.payload["status"] == "awaiting_exact_proposal"
    assert not any(
        event.type == "operator.action.requested" for event in events
    )


def test_failed_provider_sync_does_not_publish_ingress(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path,
        _mirror(created_at="2026-09-01T00:00:00+00:00"),
    )
    service.reconciler = _Reconciler({
        "ok": False,
        "status": "rate_limited",
        "changed": 0,
        "error": "rate limit",
    })  # type: ignore[assignment]

    result = service.poll_once()

    assert result.ok is False
    assert result.status == "rate_limited"
    assert not (service.state_dir / "events.jsonl").exists()


def test_ingress_config_is_provider_neutral_but_gitlab_adapter_is_disabled(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        """\
version: '1.0'
project: {name: ingress-config, state_dir: .zf}
self_issue:
  enabled: true
  provider: github
  authorization_domain: github.com
  target_locked: true
  target_project: owner/issues
  default_publication_mode: github
  targets:
    github: {authorization_domain: github.com, project: owner/issues, auth_mode: device_flow}
  ingress:
    enabled: true
    provider: github
    mode: poll
    poll_interval_seconds: 300
    target_root: .
  delivery:
    enabled: true
    provider: github
    repository: rzwu-uisee/zaofu-self-issue
    remote_url: https://github.com/rzwu-uisee/zaofu-self-issue.git
    base_branch: dev
    branch_prefix: review
    merge_strategy: squash
    pr_sync_mode: manual_refresh
    auto_close_issue: false
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.self_issue.provider == "github"
    assert config.self_issue.default_publication_mode == "github"
    assert config.self_issue.ingress.poll_interval_seconds == 300
    assert config.self_issue.ingress.target_root == "."
    assert config.self_issue.delivery.repository == "rzwu-uisee/zaofu-self-issue"
    assert config.self_issue.delivery.remote_url == (
        "https://github.com/rzwu-uisee/zaofu-self-issue.git"
    )
    assert config.self_issue.delivery.base_branch == "dev"
    assert config.self_issue.delivery.merge_strategy == "squash"
    assert config.self_issue.delivery.auto_close_issue is False

    text = config_path.read_text(encoding="utf-8").replace(
        "provider: github\n    mode: poll",
        "provider: gitlab\n    mode: poll",
    )
    config_path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="currently implements github only"):
        load_config(config_path)


def test_ingress_poll_interval_is_bounded(tmp_path: Path) -> None:
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        """\
version: '1.0'
project: {name: ingress-config}
self_issue:
  ingress: {poll_interval_seconds: 10}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="between 60 and 86400"):
        load_config(config_path)


def test_current_triage_contract_creates_exact_issue_fix_proposal(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "zf.yaml")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    mirror = _mirror(created_at="2026-09-01T00:00:00+00:00")
    IssueMirrorStore(state_dir).upsert(mirror, "Untrusted issue body")
    service = ExternalIssueIngressService(
        state_dir,
        config,
        project_root=root,
        reconciler=_Reconciler(),  # type: ignore[arg-type]
        now=lambda: datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    service.poll_once()
    task = service.tasks.list_all()[0]
    contract = asdict(task.contract)
    contract.update({
        "schema_version": "task-contract.v1",
        "behavior": "修复该 Issue 描述的确定性回归。",
        "verification": "运行聚焦回归测试并保存输出证据。",
        "verification_tiers": ["static", "runtime"],
        "scope": ["src/zf/runtime/external_issue_ingress.py"],
        "acceptance_criteria": ["复现先失败，修复后通过"],
        "owner_role": "issue-fix-lane-0",
        "spec_skip_reason": "现有 GitHub Issue 与分诊报告是本次小修复的来源合同",
    })
    contract["evidence_contract"] = {
        **contract["evidence_contract"],
        "external_issue_triage_status": "ready_to_fix",
        "external_issue_revision": contract["source_revision"],
    }
    TaskContractAuthorityService(
        task_store=service.tasks,
        event_writer=service.writer,
        state_dir=state_dir,
    ).replace(
        task,
        contract=contract,
        source="external_issue_triage",
        actor="external-issue-triage",
    )

    result = service.poll_once()

    assert result.ok is True
    proposals = [
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "operator.action.proposed"
    ]
    assert len(proposals) == 1
    proposal = proposals[0].payload["proposal"]
    assert proposal["action"] == "workflow-start"
    assert proposal["payload"]["route_id"] == "delivery:issue:default"
    assert "external_issue" in str(proposal["payload"])

    # A resolved proposal must not suppress approval for a later retry with
    # the same TaskContract/source revision.
    first_event = proposals[0]
    first_proposal = first_event.payload["proposal"]
    service.writer.append(
        ZfEvent(
            type="operator.action.resolved",
            actor="web",
            task_id=task.id,
            payload={
                "proposal_event_id": first_event.id,
                "proposal_id": first_proposal["proposal_id"],
                "status": "executed",
            },
        )
    )
    service.poll_once()
    proposals = [
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "operator.action.proposed"
    ]
    assert len(proposals) == 2
    retry_proposal = proposals[-1].payload["proposal"]
    assert retry_proposal["proposal_id"] != first_proposal["proposal_id"]
    assert retry_proposal["proposal_id"].endswith("-r2")


def test_ready_triage_reopens_task_after_stale_cancellation(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "zf.yaml")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    mirror = _mirror(created_at="2026-09-01T00:00:00+00:00")
    IssueMirrorStore(state_dir).upsert(mirror, "Untrusted issue body")
    service = ExternalIssueIngressService(
        state_dir, config, project_root=root, reconciler=_Reconciler(),
        now=lambda: datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    service.poll_once()
    task = service.tasks.list_all()[0]
    task = service.tasks.update(task.id, status="cancelled")
    assert task is not None
    queued = [
        event for event in service.writer.event_log.read_all()
        if event.type == "external_issue.triage.queued"
    ][-1]
    run_id = str(queued.payload["workflow_run_id"])
    service.writer.append(
        ZfEvent(
            type="external_issue.triage.ready_to_fix",
            task_id=task.id,
            correlation_id=run_id,
            payload={
                "workflow_run_id": run_id,
                "source_revision": queued.payload["source_revision"],
            },
        )
    )

    service.poll_once()

    reopened = service.tasks.get(task.id)
    assert reopened is not None
    assert reopened.status == "backlog"
    proposals = [
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "operator.action.proposed"
    ]
    assert len(proposals) == 1


def test_ready_triage_promotes_intake_contract_for_fix_proposal(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "zf.yaml")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    mirror = _mirror(created_at="2026-09-01T00:00:00+00:00")
    IssueMirrorStore(state_dir).upsert(mirror, "Untrusted issue body")
    service = ExternalIssueIngressService(
        state_dir, config, project_root=root, reconciler=_Reconciler(),
        now=lambda: datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    service.poll_once()
    task = service.tasks.list_all()[0]
    outcome = ZfEvent(
        type="external_issue.triage.ready_to_fix",
        task_id=task.id,
        correlation_id="TRIAGE-READY",
        payload={
            "source_revision": task.contract.source_revision,
            "verification_status": "partial",
            "verification_gaps": ["dynamic validation not run"],
        },
    )

    service._promote_ready_triage_task(outcome)

    promoted = service.tasks.get(task.id)
    assert promoted is not None
    assert promoted.contract.schema_version == "task-contract.v1"
    assert promoted.contract.evidence_contract["external_issue_triage_status"] == "ready_to_fix"
    assert promoted.contract.evidence_contract["verification_gaps"] == ["dynamic validation not run"]


def test_fix_proposal_backfills_report_verification_command(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "zf.yaml")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    mirror = _mirror(created_at="2026-09-01T00:00:00+00:00")
    IssueMirrorStore(state_dir).upsert(mirror, "Untrusted issue body")
    service = ExternalIssueIngressService(
        state_dir, config, project_root=root, reconciler=_Reconciler(),
        now=lambda: datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    service.poll_once()
    task = service.tasks.list_all()[0]
    report_ref = "artifacts/external-issues/reports/triage.md"
    report_path = state_dir / report_ref
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        "## Verification\n\n`uv run pytest tests/test_regression.py -q --no-cov`\n",
        encoding="utf-8",
    )
    contract = replace(
        task.contract,
        schema_version="task-contract.v1",
        verification="执行分诊报告中的验证命令并保存可审计证据。",
        evidence_contract={
            **task.contract.evidence_contract,
            "external_issue_triage_status": "ready_to_fix",
            "external_issue_revision": task.contract.source_revision,
        },
    )
    TaskContractAuthorityService(
        task_store=service.tasks,
        event_writer=service.writer,
        state_dir=state_dir,
    ).replace(task, contract=contract, source="test")
    service.writer.append(ZfEvent(
        type="external_issue.triage.ready_to_fix",
        task_id=task.id,
        payload={"report_ref": {"ref": report_ref}},
    ))

    updated = service._ensure_executable_verification(
        service.tasks.get(task.id) or task,
        causation_id="evt-test",
    )

    assert updated.contract.verification == "uv run pytest tests/test_regression.py -q --no-cov"


def test_semantic_triage_outcome_closes_run_admission(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, _mirror(created_at="2026-09-01T00:00:00+00:00"))
    service.poll_once()
    events = EventLog(service.state_dir / "events.jsonl")
    queued = next(event for event in events.read_all() if event.type == "external_issue.triage.queued")
    run_id = str(queued.payload["workflow_run_id"])
    service.writer.emit(
        "run.admission.admitted",
        actor="orchestrator",
        task_id=queued.task_id,
        payload={"run_id": run_id, "workflow_run_id": run_id, "request_id": run_id},
        correlation_id=run_id,
    )
    service.writer.emit(
        "external_issue.triage.ready_to_fix",
        actor="kernel",
        task_id=queued.task_id,
        payload={"workflow_run_id": run_id, "source_revision": queued.payload["source_revision"]},
        correlation_id=run_id,
    )

    service._reconcile_completed_triage_outcomes()

    final = events.read_all()
    projection = build_run_admission_projection(final)
    assert run_id not in projection.active_run_ids
    assert any(
        event.type == "run.completed"
        and str(event.payload.get("run_id") or "") == run_id
        for event in final
    )


def test_active_delivery_binding_suppresses_duplicate_fix_proposal(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path, _mirror(created_at="2026-09-01T00:00:00+00:00"))
    service.poll_once()
    events = EventLog(service.state_dir / "events.jsonl")
    queued = next(event for event in events.read_all() if event.type == "external_issue.triage.queued")
    task = service.tasks.get(queued.task_id)
    assert task is not None
    service._promote_ready_triage_task(ZfEvent(
        type="external_issue.triage.ready_to_fix",
        actor="kernel",
        task_id=task.id,
        payload={
            "workflow_run_id": queued.payload["workflow_run_id"],
            "source_revision": queued.payload["source_revision"],
        },
        correlation_id=str(queued.payload["workflow_run_id"]),
    ))
    task = service.tasks.get(task.id)
    assert task is not None
    request_id = "workflow-active-delivery"
    TaskContractAuthorityService(
        task_store=service.tasks,
        event_writer=service.writer,
        state_dir=service.state_dir,
    ).replace(
        task,
        contract=task.contract,
        execution_binding=TaskExecutionBinding(
            owner="workflow",
            request_id=request_id,
            request_revision=1,
            workflow_run_id=request_id,
        ),
        source="workflow_submit",
    )
    service.writer.emit(
        "run.admission.admitted",
        actor="orchestrator",
        task_id=task.id,
        payload={"run_id": request_id, "workflow_run_id": request_id, "request_id": request_id},
        correlation_id=request_id,
    )
    mirror = service.mirrors.get(8)
    assert mirror is not None

    result = service._ensure_fix_proposal(
        mirror,
        revision=str(queued.payload["source_revision"]),
        source_ref=str(task.contract.source_ref),
        source_digest="source",
        causation_id=queued.id,
    )

    assert result is None
    assert not any(event.type == "operator.action.proposed" for event in events.read_all())


def test_ready_triage_reopens_archived_intake_task(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "zf.yaml")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    mirror = _mirror(created_at="2026-09-01T00:00:00+00:00")
    IssueMirrorStore(state_dir).upsert(mirror, "Untrusted issue body")
    service = ExternalIssueIngressService(
        state_dir, config, project_root=root, reconciler=_Reconciler(),
        now=lambda: datetime(2026, 8, 31, tzinfo=timezone.utc),
    )
    service.poll_once()
    task = service.tasks.list_all()[0]
    service.tasks.update(task.id, status="done")
    outcome = ZfEvent(
        type="external_issue.triage.ready_to_fix",
        task_id=task.id,
        correlation_id="TRIAGE-READY-ARCHIVED",
        payload={"source_revision": task.contract.source_revision},
    )

    service._promote_ready_triage_task(outcome)

    reopened = service.tasks.get(task.id)
    assert reopened is not None
    assert reopened.status == "backlog"
    assert reopened.contract.schema_version == "task-contract.v1"
