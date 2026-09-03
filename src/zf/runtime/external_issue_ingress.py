"""Server-owned External Issue polling, durable intake, and Triage queueing."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from zf.core.config.render import redact_config, renderable_config_to_primitive
from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.core.issue_triage.models import IssueMirror
from zf.core.issue_triage.store import IssueMirrorStore
from zf.core.state.atomic_io import atomic_write_text
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.core.task.contract_validation import validate_task_contract
from zf.core.workflow.flow_metadata import flow_metadata_for
from zf.integrations.forge.github_issues import GitHubIssueReconciler
from zf.runtime.call_result_envelope import (
    canonical_json_sha256,
    write_immutable_json_sidecar,
)
from zf.runtime.run_contract import write_run_contract_snapshot
from zf.runtime.workflow_anchor import mark_workflow_managed_task
from zf.runtime.workflow_start import WorkflowStartService
from zf.runtime.workflow_submission_binding import (
    pin_artifact_delivery_goal_claim_set,
)


SOURCE_SCHEMA_VERSION = "external-issue-source.v1"
INGRESS_STATE_SCHEMA_VERSION = "external-issue-ingress-state.v1"
TRIAGE_PATTERN_ID = "external-issue-triage"
_LOG = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class ExternalIssuePollResult:
    ok: bool
    status: str
    changed: int = 0
    received: int = 0
    triage_queued: int = 0
    error: str = ""


@dataclass(frozen=True)
class ExternalIssueManualTriageResult:
    ok: bool
    status: str
    issue_number: int
    queued: bool = False
    task_id: str = ""
    workflow_run_id: str = ""
    source_revision: str = ""
    error: str = ""


class ExternalIssueIngressService:
    """Reconcile one configured Forge repository into canonical intents.

    GitHub remains an edge adapter. This service materializes immutable,
    provider-neutral source revisions and is the deterministic boundary that
    creates the unique Intake Task and queues the read-only workflow.
    """

    def __init__(
        self,
        state_dir: Path,
        config: ZfConfig,
        *,
        project_root: Path,
        reconciler: GitHubIssueReconciler | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.config = config
        self.project_root = Path(project_root)
        self.policy = config.self_issue.ingress
        github_target = config.self_issue.targets.get("github")
        if self.policy.enabled and github_target is None:
            raise ValueError("GitHub issue ingress requires a configured GitHub target")
        self.repository = github_target.project if github_target else ""
        self.reconciler = reconciler or GitHubIssueReconciler(
            self.state_dir,
            self.repository,
            minimum_interval_seconds=self.policy.poll_interval_seconds,
        )
        self.now = now
        self.mirrors = IssueMirrorStore(self.state_dir)
        self.tasks = TaskStore(self.state_dir / "kanban.json")
        self.writer = EventWriter(
            event_log_from_project(self.state_dir, config=self.config),
            default_origin="kernel",
        )
        self.state_path = self.state_dir / "external-issues" / "ingress-state.json"

    def poll_once(self, *, force: bool = False) -> ExternalIssuePollResult:
        if not self.policy.enabled:
            return ExternalIssuePollResult(ok=True, status="disabled")
        activation = self._ensure_activation()
        refreshed = self.reconciler.refresh(force=force)
        if not refreshed.get("ok"):
            return ExternalIssuePollResult(
                ok=False,
                status=str(refreshed.get("status") or "failed"),
                changed=int(refreshed.get("changed") or 0),
                error=str(refreshed.get("error") or "GitHub issue sync failed"),
            )
        received = 0
        queued = 0
        for mirror in self.mirrors.list():
            if mirror.repository.casefold() != self.repository.casefold():
                continue
            source, revision = self._source_revision(mirror)
            descriptor = write_immutable_json_sidecar(
                self.state_dir,
                source,
                root=f"external-issues/{mirror.provider}/{mirror.repository_id}/{mirror.number}",
                kind="external_issue_source",
                schema_version=SOURCE_SCHEMA_VERSION,
                created_by="external-issue-ingress",
            )
            prior = self._received_revision(mirror.issue_key, revision)
            admitted_before = self._was_admitted(mirror.issue_key)
            created_after_activation = (
                _parse_timestamp(mirror.created_at)
                >= _parse_timestamp(str(activation["activated_at"]))
            )
            eligible = (
                not self.policy.auto_triage_new_only
                or created_after_activation
                or admitted_before
            )
            if not eligible:
                continue
            if prior is None:
                action = "opened" if not admitted_before else "updated"
                prior = self.writer.emit(
                    "external_issue.received",
                    actor="github-poller",
                    payload={
                        "schema_version": SOURCE_SCHEMA_VERSION,
                        "provider": mirror.provider,
                        "source_key": mirror.issue_key,
                        "source_revision": revision,
                        "source_ref": descriptor["ref"],
                        "source_digest": descriptor["sha256"],
                        "repository": mirror.repository,
                        "repository_id": mirror.repository_id,
                        "issue_number": mirror.number,
                        "action": action,
                        "project": self.config.project.name,
                        "target_root": self.policy.target_root,
                    },
                    correlation_id=f"external-issue:{mirror.issue_key}",
                )
                received += 1
                queued += int(
                    self._ensure_intake_and_triage(
                        mirror,
                        revision=revision,
                        source_ref=str(descriptor["ref"]),
                        source_digest=str(descriptor["sha256"]),
                        causation_id=prior.id,
                    )
                )
            proposal = self._ensure_fix_proposal(
                mirror,
                revision=revision,
                source_ref=str(descriptor["ref"]),
                source_digest=str(descriptor["sha256"]),
                causation_id=prior.id,
            )
            self._publish_approval_intent(
                mirror,
                revision=revision,
                causation_id=prior.id,
                proposal=proposal,
            )
        return ExternalIssuePollResult(
            ok=True,
            status=str(refreshed.get("status") or "fresh"),
            changed=int(refreshed.get("changed") or 0),
            received=received,
            triage_queued=queued,
        )

    def start_manual_triage(self, issue_number: int) -> ExternalIssueManualTriageResult:
        """Fetch and explicitly admit one Issue, including historical revisions."""
        if not self.policy.enabled:
            return ExternalIssueManualTriageResult(
                ok=False,
                status="disabled",
                issue_number=issue_number,
                error="GitHub Issue ingress is disabled",
            )
        self._ensure_activation()
        refreshed = self.reconciler.refresh_issue(issue_number)
        if not refreshed.get("ok"):
            return ExternalIssueManualTriageResult(
                ok=False,
                status=str(refreshed.get("status") or "failed"),
                issue_number=issue_number,
                error=str(refreshed.get("error") or "GitHub Issue lookup failed"),
            )
        mirror = self.mirrors.get(issue_number)
        if mirror is None or mirror.repository.casefold() != self.repository.casefold():
            return ExternalIssueManualTriageResult(
                ok=False,
                status="not_found",
                issue_number=issue_number,
                error=f"GitHub Issue #{issue_number} was not mirrored",
            )
        source, revision = self._source_revision(mirror)
        descriptor = write_immutable_json_sidecar(
            self.state_dir,
            source,
            root=f"external-issues/{mirror.provider}/{mirror.repository_id}/{mirror.number}",
            kind="external_issue_source",
            schema_version=SOURCE_SCHEMA_VERSION,
            created_by="external-issue-ingress",
        )
        prior = self._received_revision(mirror.issue_key, revision)
        if prior is None:
            prior = self.writer.emit(
                "external_issue.received",
                actor="web-operator",
                payload={
                    "schema_version": SOURCE_SCHEMA_VERSION,
                    "provider": mirror.provider,
                    "source_key": mirror.issue_key,
                    "source_revision": revision,
                    "source_ref": descriptor["ref"],
                    "source_digest": descriptor["sha256"],
                    "repository": mirror.repository,
                    "repository_id": mirror.repository_id,
                    "issue_number": mirror.number,
                    "action": "opened" if not self._was_admitted(mirror.issue_key) else "updated",
                    "admission_mode": "manual",
                    "project": self.config.project.name,
                    "target_root": self.policy.target_root,
                },
                correlation_id=f"external-issue:{mirror.issue_key}",
            )
        queued = self._ensure_intake_and_triage(
            mirror,
            revision=revision,
            source_ref=str(descriptor["ref"]),
            source_digest=str(descriptor["sha256"]),
            causation_id=prior.id,
            requested_by="web-operator",
            reason="人工选择 GitHub Issue 并启动只读 Triage",
            allow_retry=True,
        )
        # Read the identity that was actually persisted.  Computing the id
        # before admission can be stale when polling and manual admission race
        # or when this call discovers an already queued run.
        persisted_run = self._latest_triage_run_id(mirror.issue_key, revision)
        return ExternalIssueManualTriageResult(
            ok=True,
            status="queued" if queued else "already_queued",
            issue_number=mirror.number,
            queued=queued,
            task_id=self._latest_triage_task_id(mirror.issue_key, revision),
            workflow_run_id=persisted_run,
            source_revision=revision,
        )

    def _ensure_activation(self) -> dict[str, Any]:
        if self.state_path.exists():
            value = json.loads(self.state_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(value, dict) or not value.get("activated_at"):
                raise ValueError("invalid External Issue ingress state")
            if str(value.get("repository") or "").casefold() != self.repository.casefold():
                raise ValueError("External Issue ingress repository changed")
            return value
        value = {
            "schema_version": INGRESS_STATE_SCHEMA_VERSION,
            "provider": self.policy.provider,
            "repository": self.repository,
            "activated_at": _iso(self.now()),
        }
        atomic_write_text(
            self.state_path,
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        return value

    def _source_revision(self, mirror: IssueMirror) -> tuple[dict[str, Any], str]:
        source = {
            "schema_version": SOURCE_SCHEMA_VERSION,
            "provider": mirror.provider,
            "provider_host": "github.com",
            "source_key": mirror.issue_key,
            "repository": mirror.repository,
            "repository_id": mirror.repository_id,
            "issue_number": mirror.number,
            "node_id": mirror.node_id,
            "url": mirror.html_url,
            "title": mirror.title,
            "state": mirror.github_state,
            "author": mirror.author_login,
            "labels": list(mirror.labels),
            "assignees": list(mirror.assignees),
            "created_at": mirror.created_at,
            "updated_at": mirror.updated_at,
            "closed_at": mirror.closed_at,
            "body_ref": mirror.body_ref,
            "body_digest": mirror.body_digest,
            "comments_ref": mirror.comments_ref,
            "comments_digest": mirror.comments_digest,
            "project": self.config.project.name,
            "target_root": self.policy.target_root,
            "trust": "untrusted_external_input",
        }
        return source, "sha256:" + canonical_json_sha256(source)

    def _received_revision(self, source_key: str, revision: str):
        for event in reversed(self.writer.event_log.read_all()):
            if event.type != "external_issue.received":
                continue
            if (
                str(event.payload.get("source_key") or "") == source_key
                and str(event.payload.get("source_revision") or "") == revision
            ):
                return event
        return None

    def _was_admitted(self, source_key: str) -> bool:
        return any(
            event.type == "external_issue.received"
            and str(event.payload.get("source_key") or "") == source_key
            for event in self.writer.event_log.read_all()
        )

    def _ensure_intake_and_triage(
        self,
        mirror: IssueMirror,
        *,
        revision: str,
        source_ref: str,
        source_digest: str,
        causation_id: str,
        requested_by: str = "external-issue-ingress",
        reason: str = "自动只读分诊新的 External Issue revision",
        allow_retry: bool = False,
    ) -> bool:
        if self._triage_was_queued(
            mirror.issue_key, revision, allow_retry=allow_retry,
        ):
            return False
        run_id = self._triage_run_id(mirror.issue_key, revision)
        task_id = _task_id(mirror.issue_key, run_id=run_id)
        task = self.tasks.get(task_id)
        if task is None:
            task = mark_workflow_managed_task(Task(
                id=task_id,
                key=f"GH-{mirror.number}",
                title=f"分诊 GitHub Issue #{mirror.number}: {mirror.title}",
                status="backlog",
                assigned_to=None,
                contract=TaskContract(
                    schema_version="external-issue-intake.v1",
                    locale="zh-CN",
                    behavior="只读分析 External Issue，并形成可审查的修复合同；不得修改源码。",
                    verification="核对复现、范围、风险、验收标准与验证命令，并保留证据引用。",
                    verification_tiers=["manual_evidence"],
                    acceptance="生成绑定当前 source revision 的分诊证据与修复候选合同",
                    acceptance_criteria=[
                        "判断 duplicate、needs_info 或可修复",
                        "给出可验证的复现步骤与受控范围",
                        "不修改源码、不启动 Fix Run",
                    ],
                    exclusions=["源码写入", "push", "PR", "merge", "部署", "关闭远端 Issue"],
                    source_key=mirror.issue_key,
                    source_ref=source_ref,
                    source_revision=revision,
                    source_mode="external_issue_ingress",
                    source_title=mirror.title,
                    owner_role=TRIAGE_PATTERN_ID,
                    evidence_contract={
                        "external_issue_source_ref": source_ref,
                        "external_issue_source_digest": source_digest,
                        "external_issue_revision": revision,
                    },
                ),
            ))
            created, _ = self.tasks.add_many([task])
            if created:
                self.writer.emit(
                    "task.created",
                    actor="external-issue-intake",
                    task_id=task_id,
                    payload={
                        "task_id": task_id,
                        "title": task.title,
                        "source": "external_issue",
                        "source_key": mirror.issue_key,
                        "source_revision": revision,
                    },
                    causation_id=causation_id,
                    correlation_id=f"external-issue:{mirror.issue_key}",
                )
        triage_attempt = self._triage_attempt(mirror.issue_key, revision)
        previous_run_id = self._triage_retry_of(
            mirror.issue_key, revision, allow_retry=allow_retry,
        )
        workflow_payload = {
            "task_id": task_id,
            "pattern_id": TRIAGE_PATTERN_ID,
            "workflow_run_id": run_id,
            "run_id": run_id,
            "requested_by": requested_by,
            "reason": reason,
            "source": "external_issue",
            "source_refs": {"external_issue": source_ref},
            "artifact_refs": self._issue_artifact_refs(
                mirror,
                source_ref=source_ref,
                source_digest=source_digest,
            ),
            "flow_kind": "workflow",
            "route_id": "general:external-issue-triage",
            "source_key": mirror.issue_key,
            "source_revision": revision,
            "expected_output": "triage report and proposed TaskContract; no source writes",
            "target_ref": self.policy.target_root,
            "triage_attempt": triage_attempt,
        }
        if previous_run_id:
            workflow_payload.update({
                "retry_of_workflow_run_id": previous_run_id,
            })
        workflow_payload.update(self._workflow_identity())
        self._pin_external_issue_goal_claim_set(
            workflow_payload,
            run_id=run_id,
            source_ref=source_ref,
            source_digest=source_digest,
            source_revision=revision,
            task_id=task_id,
        )
        queued = self.writer.emit(
            "workflow.invoke.requested",
            actor="external-issue-intake",
            task_id=task_id,
            payload=workflow_payload,
            causation_id=causation_id,
            correlation_id=run_id,
        )
        queue_payload = {
            "source_key": mirror.issue_key,
            "source_revision": revision,
            "workflow_run_id": run_id,
            "invoke_event_id": queued.id,
            "status": "triage_queued",
            "triage_attempt": triage_attempt,
        }
        if previous_run_id:
            queue_payload.update({
                "retry_of_workflow_run_id": previous_run_id,
            })
        self.writer.emit(
            "external_issue.triage.queued",
            actor="external-issue-intake",
            task_id=task_id,
            payload=queue_payload,
            causation_id=queued.id,
            correlation_id=run_id,
        )
        return True

    @staticmethod
    def _issue_artifact_refs(
        mirror: IssueMirror,
        *,
        source_ref: str,
        source_digest: str,
    ) -> list[dict[str, str]]:
        """Expose every digest-bound Issue input to the controlled reader.

        The immutable source snapshot intentionally stores the body and
        comments as sidecar refs.  They must also be explicit workflow
        artifacts; otherwise the attempt manifest can read only the envelope
        and the worker cannot resolve its semantic payload through the
        artifact catalog.
        """

        refs = [{"ref": source_ref, "sha256": source_digest}]
        for ref, digest in (
            (mirror.body_ref, mirror.body_digest),
            (mirror.comments_ref, mirror.comments_digest),
        ):
            normalized_digest = str(digest or "").removeprefix("sha256:")
            if ref and normalized_digest:
                refs.append({"ref": ref, "sha256": normalized_digest})
        return refs

    def _workflow_identity(self) -> dict[str, Any]:
        """Bind External Issue invokes to the runtime's immutable identities.

        Ordinary workflow submission binds these refs before emitting
        ``workflow.invoke.requested``.  External Issue ingress is another
        kernel-owned entry point, so it must publish the same identity fields
        for run admission and Flow-role activation to verify.
        """

        effective_config = {
            "schema_version": "effective-config-snapshot.v1",
            "source": "runtime-loaded-zf-config",
            "config": redact_config(
                renderable_config_to_primitive(self.config)
            ),
        }
        effective_ref = write_immutable_json_sidecar(
            self.state_dir,
            effective_config,
            root="orchestrator-agent/effective-configs",
            kind="effective_config_snapshot",
            schema_version="effective-config-snapshot.v1",
            created_by="external-issue-ingress",
        )
        contract_digest = ""
        contract_snapshot: dict[str, Any] = {}
        try:
            contract_path = self.state_dir / "config" / "run-contract.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            if isinstance(contract, dict):
                contract_digest = str(contract.get("contract_digest") or "")
                if contract_digest:
                    contract_snapshot = write_run_contract_snapshot(
                        self.state_dir, contract
                    )
        except (OSError, json.JSONDecodeError):
            # ``zf start`` creates the active contract.  Keep the request
            # fail-closed when it is unavailable rather than inventing one.
            pass
        return {
            "effective_config_ref": effective_ref,
            "effective_config_digest": str(effective_ref.get("sha256") or ""),
            "run_contract_ref": str(contract_snapshot.get("ref") or ""),
            "run_contract_digest": contract_digest,
        }

    def _pin_external_issue_goal_claim_set(
        self,
        payload: dict[str, Any],
        *,
        run_id: str,
        source_ref: str,
        source_digest: str,
        source_revision: str,
        task_id: str,
    ) -> None:
        """Create the Goal binding required by the artifact verifier."""

        metadata = flow_metadata_for(self.config, "workflow")
        source_generation = str(source_digest or source_revision).strip()
        if len(source_generation) != 64:
            source_generation = hashlib.sha256(
                source_generation.encode("utf-8")
            ).hexdigest()
        generation = str(
            metadata.get("generic_workflow_contract_digest") or ""
        ).strip()
        if len(generation) != 64:
            # Lightweight/test configurations and older projects may not
            # enable Goal completion claims.  Preserve the ingress contract
            # in that mode; when Goal is enabled, fail closed because the
            # artifact verifier cannot admit an unbound run.
            if bool(getattr(getattr(self.config, "goal", None), "enabled", False)):
                raise RuntimeError(
                    "external Issue Triage is missing a valid generic workflow "
                    "contract digest"
                )
            return
        payload.update({
            "goal_id": run_id,
            "workflow_generation": generation,
            "request_revision": max(1, int(source_generation[:12], 16)),
            "workflow_intent": str(metadata.get("intent") or "research"),
            "workflow_template": str(
                metadata.get("workflow_template") or "evidence-synthesis-v1"
            ),
            "completion_profile": str(
                metadata.get("completion_profile") or "artifact_delivery"
            ),
            "required_delivery_artifacts": [
                dict(item)
                for item in metadata.get("required_delivery_artifacts") or []
                if isinstance(item, dict)
            ],
            "generic_workflow_contract_digest": generation,
            "requirement_spec_ref": source_ref,
            "requirement_spec_digest": source_digest,
        })
        pin_artifact_delivery_goal_claim_set(
            state_dir=self.state_dir,
            project_root=self.project_root,
            preview={"payload": payload},
            writer=self.writer,
            correlation_id=run_id,
            task_id=task_id,
        )

    def _triage_was_queued(
        self, source_key: str, revision: str, *, allow_retry: bool = False,
    ) -> bool:
        events = self.writer.event_log.read_all()
        queued = self._triage_queue_events(events, source_key, revision)
        if not queued or not allow_retry:
            return bool(queued)
        latest_run_id = str(queued[-1].payload.get("workflow_run_id") or "")
        if not latest_run_id:
            return True
        terminal_types = {
            "run.cancelled", "run.abandoned", "run.failed",
            "run.goal.blocked", "workflow.invoke.rejected", "run.admission.rejected",
        }
        return not any(
            event.type in terminal_types
            and str(
                event.payload.get("workflow_run_id")
                or event.payload.get("run_id")
                or event.correlation_id
                or ""
            ) == latest_run_id
            for event in events
        )

    @staticmethod
    def _triage_queue_events(
        events: list[Any], source_key: str, revision: str,
    ) -> list[Any]:
        return [
            event for event in events
            if event.type == "external_issue.triage.queued"
            and str(event.payload.get("source_key") or "") == source_key
            and str(event.payload.get("source_revision") or "") == revision
        ]

    def _latest_triage_run_id(self, source_key: str, revision: str) -> str:
        queued = self._triage_queue_events(
            self.writer.event_log.read_all(), source_key, revision,
        )
        return str(queued[-1].payload.get("workflow_run_id") or "") if queued else ""

    def _latest_triage_task_id(self, source_key: str, revision: str) -> str:
        queued = self._triage_queue_events(
            self.writer.event_log.read_all(), source_key, revision,
        )
        return str(queued[-1].task_id or "") if queued else ""

    def _triage_retry_of(
        self, source_key: str, revision: str, *, allow_retry: bool,
    ) -> str:
        if not allow_retry:
            return ""
        events = self.writer.event_log.read_all()
        queued = self._triage_queue_events(events, source_key, revision)
        if not queued:
            return ""
        previous_run_id = str(queued[-1].payload.get("workflow_run_id") or "")
        if not previous_run_id:
            return ""
        terminal_types = {
            "run.cancelled", "run.abandoned", "run.failed",
            "run.goal.blocked", "workflow.invoke.rejected", "run.admission.rejected",
        }
        return previous_run_id if any(
            event.type in terminal_types
            and str(
                event.payload.get("workflow_run_id")
                or event.payload.get("run_id")
                or event.correlation_id
                or ""
            ) == previous_run_id
            for event in events
        ) else ""

    def _triage_attempt(self, source_key: str, revision: str) -> int:
        return len(self._triage_queue_events(
            self.writer.event_log.read_all(), source_key, revision,
        )) + 1

    def _triage_run_id(self, source_key: str, revision: str) -> str:
        events = self.writer.event_log.read_all()
        attempts = sum(
            1 for event in events
            if event.type == "external_issue.triage.queued"
            and str(event.payload.get("source_key") or "") == source_key
            and str(event.payload.get("source_revision") or "") == revision
        )
        suffix = "" if attempts == 0 else f":retry:{attempts}"
        return "TRIAGE-" + hashlib.sha256(
            f"{source_key}:{revision}{suffix}".encode("utf-8")
        ).hexdigest()[:16].upper()

    def _publish_approval_intent(
        self,
        mirror: IssueMirror,
        *,
        revision: str,
        causation_id: str,
        proposal: dict[str, Any] | None,
    ) -> None:
        if self.policy.approval_label.casefold() not in {
            label.casefold() for label in mirror.labels
        }:
            return
        proposal_id = str((proposal or {}).get("proposal_id") or "")
        status = "awaiting_web_approval" if proposal_id else "awaiting_exact_proposal"
        if any(
            event.type == "external_issue.fix_approval.intent"
            and str(event.payload.get("source_key") or "") == mirror.issue_key
            and str(event.payload.get("source_revision") or "") == revision
            and str(event.payload.get("proposal_id") or "") == proposal_id
            and str(event.payload.get("status") or "") == status
            for event in self.writer.event_log.read_all()
        ):
            return
        self.writer.emit(
            "external_issue.fix_approval.intent",
            actor="github-poller",
            task_id=self._latest_triage_task_id(mirror.issue_key, revision) or None,
            payload={
                "source_key": mirror.issue_key,
                "source_revision": revision,
                "label": self.policy.approval_label,
                "proposal_id": proposal_id,
                "proposal_digest": str((proposal or {}).get("proposal_digest") or ""),
                "status": status,
                "reason": (
                    "polling observes label state but not a trusted actor authorization; "
                    "approve the exact proposal in ZaoFu Web"
                ),
            },
            causation_id=causation_id,
            correlation_id=f"external-issue:{mirror.issue_key}",
        )

    def _ensure_fix_proposal(
        self,
        mirror: IssueMirror,
        *,
        revision: str,
        source_ref: str,
        source_digest: str,
        causation_id: str,
    ) -> dict[str, Any] | None:
        task_id = self._latest_triage_task_id(mirror.issue_key, revision)
        task = self.tasks.get(task_id) if task_id else None
        if task is None:
            return None
        if (
            task.contract.source_key != mirror.issue_key
            or task.contract.source_revision != revision
            or task.contract.source_ref != source_ref
        ):
            return None
        evidence_contract = (
            task.contract.evidence_contract
            if isinstance(task.contract.evidence_contract, dict)
            else {}
        )
        if (
            task.contract.schema_version == "external-issue-intake.v1"
            or evidence_contract.get("external_issue_triage_status")
            != "ready_to_fix"
            or str(evidence_contract.get("external_issue_revision") or "")
            != revision
        ):
            return None
        if validate_task_contract(
            task,
            config=self.config,
            project_root=self.project_root,
        ):
            return None
        result = WorkflowStartService(
            self.state_dir,
            self.config,
            project_root=self.project_root,
        ).propose(
            self.writer,
            {
                "task_id": task.id,
                "route_id": "delivery:issue:default",
                "objective": f"修复 GitHub Issue #{mirror.number}: {mirror.title}",
                "reason": "分诊合同已完成，等待操作者批准本地 Fix Run",
                "source_refs": {"external_issue": source_ref},
                "artifact_refs": self._issue_artifact_refs(
                    mirror,
                    source_ref=source_ref,
                    source_digest=source_digest,
                ),
                "parameters": {
                    "source_key": mirror.issue_key,
                    "source_revision": revision,
                    "delivery_policy": "ship_candidate",
                },
            },
            actor="external-issue-intake",
            origin="external_issue",
            causation_id=causation_id,
            correlation_id=f"external-issue:{mirror.issue_key}",
        )
        return result if result.get("status") == "proposal_ready" else None


class ExternalIssuePoller:
    """One daemon loop owned by the `zf web` process."""

    def __init__(self, service: ExternalIssueIngressService) -> None:
        self.service = service
        self.interval_seconds = service.policy.poll_interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "ExternalIssuePoller":
        if self._thread is not None:
            return self
        self._thread = threading.Thread(
            target=self._run,
            name="zf-external-issue-poller",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.service.poll_once()
            except Exception:
                # Sync state and events carry expected diagnostics. A malformed
                # local state must not terminate the Web server process.
                _LOG.exception("External Issue polling iteration failed")
            self._stop.wait(self.interval_seconds)


def build_external_issue_poller(
    state_dir: Path,
    config: ZfConfig | None,
    *,
    project_root: Path,
) -> ExternalIssuePoller | None:
    if config is None or not config.self_issue.ingress.enabled:
        return None
    if config.self_issue.ingress.provider != "github":
        return None
    return ExternalIssuePoller(ExternalIssueIngressService(
        state_dir,
        config,
        project_root=project_root,
    ))


def _task_id(source_key: str, *, run_id: str) -> str:
    digest = hashlib.sha256(
        f"{source_key}:{run_id}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"ISSUE-{digest}"


__all__ = [
    "ExternalIssueIngressService",
    "ExternalIssueManualTriageResult",
    "ExternalIssuePollResult",
    "ExternalIssuePoller",
    "build_external_issue_poller",
]
