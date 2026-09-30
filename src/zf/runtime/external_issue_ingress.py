"""Server-owned External Issue polling, durable intake, and Triage queueing."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter, monotonic
from typing import Any, Callable, Mapping

from zf.core.config.render import redact_config, renderable_config_to_primitive
from zf.core.config.schema import ZfConfig
from zf.core.config.loader import ConfigError
from zf.core.events import EventWriter, ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.issue_triage.models import IssueMirror
from zf.core.issue_triage.lifecycle_states import (
    REMOTE_LIFECYCLE_STATE_SET,
    validate_lifecycle_state,
)
from zf.core.issue_triage.store import IssueMirrorStore
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.task.schema import Task, TaskContract, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.core.task.contract_validation import validate_task_contract
from zf.core.workflow.flow_metadata import flow_metadata_for
from zf.integrations.forge.github_issues import GitHubIssueReconciler
from zf.runtime.call_result_envelope import (
    canonical_json_sha256,
    write_immutable_json_sidecar,
)
from zf.runtime.run_contract import (
    _stable_contract_body,
    build_run_contract,
    stable_json_sha256,
    write_run_contract_snapshot,
)
from zf.runtime.workflow_anchor import mark_workflow_managed_task
from zf.runtime.task_contract_authority import (
    TaskContractAuthorityConflict,
    TaskContractAuthorityService,
)
from zf.runtime.workflow_start import WorkflowStartService
from zf.runtime.run_admission import request_admission_view
from zf.runtime.workflow_intake import _extract_verification_commands
from zf.runtime.verification_commands import normalize_verification_commands
from zf.runtime.workflow_submission_binding import (
    pin_artifact_delivery_goal_claim_set,
)
from zf.runtime.issue_execution import serialize_issue_admission
from zf.runtime.self_issue_target import SELF_ISSUE_TARGET_ROOT
from zf.runtime.external_issue_attachment_cache import load_capture, save_capture
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_bytes
from zf.runtime.external_issue_label_sync import (
    _workspace_lifecycle_targets,
    reconcile_cancellations,
    reconcile_lifecycle_labels,
    try_intake_lock,
)


SOURCE_SCHEMA_VERSION = "external-issue-source.v1"
INGRESS_STATE_SCHEMA_VERSION = "external-issue-ingress-state.v1"
TRIAGE_PATTERN_ID = "external-issue-triage"
_LOG = logging.getLogger(__name__)

# A remote lifecycle label is the public cross-project authority.  Polling
# must never turn an Issue that has already advanced (or stopped) back into a
# new Triage run merely because this project's local event ledger is missing
# that run.  Only the initial ``open`` label is eligible for automatic intake;
# all later states require their existing remote projection or an explicit
# developer action (for example Start Triage).
_AUTO_TRIAGE_INITIAL_REMOTE_STATE = "open"
_GITHUB_ATTACHMENT_HOSTS = frozenset({
    "github.com", "user-images.githubusercontent.com", "camo.githubusercontent.com",
})
_ATTACHMENT_URL = re.compile(r"https://[^\s)<>\"']+")
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

LifecycleLabelWriter = Callable[[IssueMirror, str], Mapping[str, Any]]
ClarificationCommentWriter = Callable[[IssueMirror, str, str], Mapping[str, Any]]


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
    workflow_project_id: str = ""
    workflow_project_name: str = ""
    execution_project_root: str = ""


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
        lifecycle_label_writer: LifecycleLabelWriter | None = None,
        clarification_comment_writer: ClarificationCommentWriter | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        from zf.runtime.issue_settings import current_issue_policy
        config = replace(config, self_issue=current_issue_policy(project_root, config.self_issue))
        self.state_dir = Path(state_dir)
        self.config = config
        self.policy = config.self_issue.ingress
        self.project_root = Path(project_root).resolve()
        self._execution_bound = False
        github_target = config.self_issue.targets.get("github")
        if self.policy.enabled and github_target is None:
            raise ValueError("GitHub issue ingress requires a configured GitHub target")
        self.repository = github_target.project if github_target else ""
        self.reconciler = reconciler or GitHubIssueReconciler(
            self.state_dir,
            self.repository,
            minimum_interval_seconds=self.policy.poll_interval_seconds,
        )
        # The callback is deliberately optional: tests and local/offline
        # deployments remain provider-neutral, while the real GitHub poller
        # supplies a bounded remote-label adapter.  No canonical store is
        # mutated by this hook; it only records the remote side effect.
        self.lifecycle_label_writer = lifecycle_label_writer
        self.clarification_comment_writer = clarification_comment_writer
        self._pass_reconciled_states: set[tuple[str, str]] | None = None
        self.now = now
        self.mirrors = IssueMirrorStore(self.state_dir)
        self.tasks = TaskStore(self.state_dir / "kanban.json")
        self.writer = EventWriter(
            event_log_from_project(self.state_dir, config=self.config),
            default_origin="kernel",
        )
        self.state_path = self.state_dir / "external-issues" / "ingress-state.json"

    def _reconcile_remote_lifecycle_label(
        self,
        mirror: IssueMirror,
        state: str,
        *,
        revision: str = "",
        causation_id: str = "",
        write_result: Mapping[str, Any] | None = None,
        projection_batch: dict[str, tuple[str, dict[str, str]]] | None = None,
    ) -> Mapping[str, Any]:
        """Best-effort remote label convergence with durable retry evidence.

        Polling calls this on every pass, so a transient GitHub failure is
        retried naturally without treating a remote label as local truth. The
        append-only events make pending/failed writes visible to operators;
        the callback remains the only component allowed to perform the Forge
        mutation.
        """
        if self.lifecycle_label_writer is None:
            return {"status": "disabled", "reason": "lifecycle_label_writer_missing"}
        try:
            desired = validate_lifecycle_state(state)
        except ValueError:
            return {"status": "label_reconcile_failed", "reason": "invalid_lifecycle_state"}
        write_key = (mirror.issue_key, desired)
        if self._pass_reconciled_states is not None and write_result is None:
            # Events already persist the desired state. Publish the final
            # workspace projection once, after this processing pass finishes.
            return {"status": "deferred", "reason": "lifecycle_batch"}
        if self._pass_reconciled_states is not None and write_key in self._pass_reconciled_states:
            return {"status": "reconciled", "reason": "already_reconciled_this_pass"}
        remote_states = {
            label for label in mirror.labels
            if label in REMOTE_LIFECYCLE_STATE_SET
        }
        if write_result is None and remote_states == {desired} and mirror.label_colors.get(desired):
            return {"status": "reconciled", "reason": "unchanged"}
        identity = {
            "source_key": mirror.issue_key,
            "source_revision": revision,
            "repository": mirror.repository,
            "issue_number": mirror.number,
            "state": desired,
        }
        result: Mapping[str, Any]
        try:
            result = write_result if write_result is not None else self.lifecycle_label_writer(mirror, desired)
        except Exception as exc:  # pragma: no cover - adapter boundary
            result = {"status": "outcome_unknown", "reason": type(exc).__name__}
        status = str(result.get("status") or "outcome_unknown")
        reason = str(result.get("reason") or "")
        if status == "reconciled":
            # The Forge mutation is confirmed. Reflect that result in the
            # mirror immediately instead of waiting for a possibly cached or
            # rate-limited GitHub read. The next upstream refresh remains the
            # authority and can correct any later external change.
            colors = dict(result.get("label_colors") or {})
            if projection_batch is not None:
                projection_batch[mirror.issue_key] = (desired, colors)
            else:
                self.mirrors.project_lifecycle_label(mirror.issue_key, desired, colors)
        if self._pass_reconciled_states is not None:
            self._pass_reconciled_states.add(write_key)
        event_type = (
            "external_issue.lifecycle_label.reconciled"
            if status == "reconciled"
            else "external_issue.lifecycle_label.reconcile_failed"
        )
        # Avoid an event storm when a poller repeatedly sees the same stable
        # result, but always append a changed failure/status so a later retry
        # has an auditable edge in the ledger.
        events = self.writer.event_log.read_all()
        prior = next(
            (
                event for event in reversed(events)
                if event.type == event_type
                and str(event.payload.get("source_key") or "") == mirror.issue_key
                and str(event.payload.get("source_revision") or "") == revision
                and str(event.payload.get("state") or "") == desired
                and str(event.payload.get("status") or "") == status
                and str(event.payload.get("reason") or "") == reason
            ),
            None,
        )
        if prior is not None:
            return result
        self.writer.emit(
            event_type,
            actor="external-issue-lifecycle-label",
            task_id=self._latest_triage_task_id(mirror.issue_key, revision) or None,
            payload={**identity, "status": status, "reason": reason},
            causation_id=causation_id or None,
            correlation_id=f"external-issue:{mirror.issue_key}",
        )
        return result

    @staticmethod
    def _remote_lifecycle_state(mirror: IssueMirror) -> str | None:
        """Return the single lifecycle label currently owned by the Forge.

        Lifecycle labels are intentionally read from the remote mirror before
        local intake/reducer work.  A poller may be running in a different
        project/state directory from the one that advanced the Issue; in
        that case the remote label is still authoritative and must suppress
        a duplicate automatic Triage admission.
        """

        for raw in mirror.labels:
            state = str(raw or "").strip()
            if state in REMOTE_LIFECYCLE_STATE_SET:
                return state
        return None

    def poll_once(self, *, force: bool = False, full: bool = False) -> ExternalIssuePollResult:
        started = perf_counter()
        try:
            return self._poll_once(force=force, full=full)
        finally:
            elapsed = perf_counter() - started
            _LOG.log(logging.WARNING if elapsed >= 5 else logging.INFO,
                     "Issue refresh including intake total=%.3fs", elapsed)

    def _poll_once(self, *, force: bool, full: bool) -> ExternalIssuePollResult:
        if not self.config.self_issue.enabled or not self.policy.enabled:
            return ExternalIssuePollResult(ok=True, status="disabled")
        refreshed = self.refresh_mirror(force=force, full=full)
        if not refreshed.get("ok"):
            return ExternalIssuePollResult(
                ok=False,
                status=str(refreshed.get("status") or "failed"),
                changed=int(refreshed.get("changed") or 0),
                error=str(refreshed.get("error") or "GitHub issue sync failed"),
            )
        if refreshed.get("status") == "syncing":
            return ExternalIssuePollResult(ok=True, status="syncing")
        return self.process_mirrors(changed=int(refreshed.get("changed") or 0))

    def refresh_mirror(self, *, force: bool = False, full: bool = False) -> dict[str, Any]:
        # Pin initial eligibility before the first upstream fetch.
        self._ensure_activation()
        return self.reconciler.refresh(force=force, **({"full": True} if full else {}))

    @serialize_issue_admission
    def process_mirrors(self, *, changed: int = 0, issue_numbers: set[int] | None = None) -> ExternalIssuePollResult:
        with try_intake_lock(self.state_dir / "external-issues" / "intake") as acquired:
            if not acquired:
                return ExternalIssuePollResult(ok=True, status="syncing", changed=changed)
            self._pass_reconciled_states = set()
            try:
                return self._process_mirrors_locked(changed=changed, issue_numbers=issue_numbers)
            finally:
                self._pass_reconciled_states = None

    def _process_mirrors_locked(self, *, changed: int, issue_numbers: set[int] | None = None) -> ExternalIssuePollResult:
        activation = getattr(self, "_routing_activation", None) or self._ensure_activation()
        self._reconcile_completed_triage_outcomes()
        workspace_targets, _ = _workspace_lifecycle_targets(self)
        local_event_ids = {event.id for event in self.writer.event_log.read_all()}
        from zf.runtime.issue_execution import IssueExecutionRoutes
        routes = None if self._execution_bound else IssueExecutionRoutes(self)
        forwarded: dict[str, tuple[Any, set[int]]] = {}
        received = 0
        queued = 0
        for mirror in self.mirrors.list():
            if issue_numbers is not None and mirror.number not in issue_numbers:
                continue
            if routes is not None and mirror.repository.casefold() == self.repository.casefold():
                target_ctx = routes.target(mirror, automatic=True)
                if target_ctx is None:
                    continue
                if target_ctx.state_dir.resolve() != self.state_dir.resolve():
                    target = routes.service_for(target_ctx, mirror)
                    key = str(target.state_dir.resolve())
                    forwarded.setdefault(key, (target, set()))[1].add(mirror.number)
                    continue
            if mirror.repository.casefold() != self.repository.casefold():
                continue
            remote_lifecycle_state = self._remote_lifecycle_state(mirror)
            workspace_target = workspace_targets.get(mirror.issue_key)
            if (
                workspace_target and workspace_target[0]
                and workspace_target[0].id not in local_event_ids
                and workspace_target[1] != "mirrored"
            ):
                # An owner's admission remains authoritative even if its
                # remote label write failed and the viewer has no label yet.
                continue
            # ``open`` (or no lifecycle label) is the only state that may be
            # admitted automatically.  Every other remote state represents
            # an already-admitted, paused, stopped, review or delivery lane;
            # preserve it until an explicit developer action changes it.
            if (
                mirror.github_state.casefold() == "closed"
                or (
                    remote_lifecycle_state is not None
                    and remote_lifecycle_state != _AUTO_TRIAGE_INITIAL_REMOTE_STATE
                )
            ):
                continue
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
                        "target_root": SELF_ISSUE_TARGET_ROOT,
                    "execution_project_root": str(self.project_root),
                    },
                    correlation_id=f"external-issue:{mirror.issue_key}",
                )
                received += 1
                queued += int(
                    0 if self._is_latest_clarification_request(mirror) else
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
        for target, numbers in forwarded.values():
            target._routing_activation = activation
            result = target.process_mirrors(issue_numbers=numbers)
            if not result.ok or result.status == "syncing":
                return result
            received += result.received
            queued += result.triage_queued
        labels = reconcile_lifecycle_labels(self)
        return ExternalIssuePollResult(
            ok=True,
            status=str(labels["status"]) if labels.get("status") in {"syncing", "reconcile_pending"} else "fresh",
            changed=changed,
            received=received,
            triage_queued=queued,
        )

    def reconcile_queue_cancellation(self, event: ZfEvent) -> Mapping[str, Any]:
        """Publish a durable cancellation without overwriting a newer admission."""
        with try_intake_lock(self.state_dir / "external-issues" / "intake") as acquired:
            if not acquired:
                return {"status": "syncing", "reason": "intake_in_progress"}
            return self._reconcile_queue_cancellation_locked(event)

    def reconcile_queue_cancellations(self) -> Mapping[str, Any]:
        """Compatibility entrypoint for all pending lifecycle projections."""
        return self.reconcile_lifecycle_labels()

    def reconcile_lifecycle_labels(self) -> Mapping[str, Any]:
        """Retry all lifecycle projections without enabling automatic intake."""
        with try_intake_lock(self.state_dir / "external-issues" / "intake") as acquired:
            if not acquired:
                return {"status": "syncing", "reason": "intake_in_progress"}
            return reconcile_lifecycle_labels(self)

    def _reconcile_queue_cancellation_locked(self, event: ZfEvent) -> Mapping[str, Any]:
        return reconcile_cancellations(self, event)

    def _reconcile_completed_triage_outcomes(self) -> None:
        """Backfill semantic triage outcomes missed by an earlier runtime tick.

        Artifact delivery is durable and may be admitted just before Web/API
        restarts.  Re-running the poller must therefore project a completed
        External Issue report into ``needs_info``/``unverified_candidate``
        exactly once instead of leaving the mirror stuck in ``triaging``.
        """

        from zf.runtime.external_issue_clarification import (
            external_issue_triage_outcome_event,
        )

        events = self.writer.event_log.read_all()
        # A previous poll may have persisted the semantic outcome just before
        # a process crash (or a contract promotion CAS failure).  Re-run the
        # idempotent promotion for such outcomes so the task cannot remain in
        # ``awaiting_fix_approval`` forever after a Web/API restart.
        for event in events:
            if event.type in {
                "external_issue.triage.needs_info",
                "external_issue.triage.needs_info_exhausted",
                "external_issue.triage.ready_to_fix",
                "external_issue.triage.unverified_candidate",
            }:
                self._complete_triage_run(event)
                if event.type in {
                    "external_issue.triage.needs_info",
                    "external_issue.triage.needs_info_exhausted",
                }:
                    self._publish_clarification_request(event)
            if event.type == "external_issue.triage.ready_to_fix":
                self._promote_ready_triage_task(event)
        external_runs = {
            str((event.payload or {}).get("workflow_run_id") or event.correlation_id or "")
            for event in events
            if event.type == "workflow.invoke.requested"
            and str((event.payload or {}).get("source") or "") == "external_issue"
        }
        projected_runs = {
            str((event.payload or {}).get("workflow_run_id") or event.correlation_id or "")
            for event in events
            if event.type in {
                "external_issue.triage.needs_info",
                "external_issue.triage.needs_info_exhausted",
                "external_issue.triage.ready_to_fix",
                "external_issue.triage.unverified_candidate",
            }
        }
        candidates: dict[str, Any] = {}
        for event in events:
            if event.type != "artifact.delivery.verified":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            run_id = str(payload.get("workflow_run_id") or event.correlation_id or "")
            if not run_id or run_id not in external_runs or run_id in projected_runs:
                continue
            candidates[run_id] = event
        for event in candidates.values():
            outcome = external_issue_triage_outcome_event(
                self.state_dir, events, cause=event,
            )
            if outcome is None:
                continue
            self.writer.append(outcome)
            events.append(outcome)
            self._complete_triage_run(outcome)
            if outcome.type in {
                "external_issue.triage.needs_info",
                "external_issue.triage.needs_info_exhausted",
            }:
                self._publish_clarification_request(outcome)
            if outcome.type == "external_issue.triage.ready_to_fix":
                self._promote_ready_triage_task(outcome)

    def _is_latest_clarification_request(self, mirror: IssueMirror) -> bool:
        """Do not re-triage our own question comment as if it were reporter input."""

        try:
            comments = self.mirrors.read_comments(mirror)
        except (OSError, ValueError, TypeError):
            return False
        if not comments:
            return False
        from zf.runtime.external_issue_clarification import REQUEST_MARKER_PREFIX

        return REQUEST_MARKER_PREFIX in comments[-1].body

    def _publish_clarification_request(self, outcome: Any) -> None:
        """Publish Need-info questions to the remote Issue exactly once."""

        payload = outcome.payload if isinstance(outcome.payload, Mapping) else {}
        source_key = str(payload.get("source_key") or "").strip()
        triage_id = str(
            payload.get("workflow_run_id") or outcome.correlation_id or ""
        ).strip()
        mirror = self._mirror_for_source_key(source_key)
        if mirror is None or not triage_id:
            return
        from zf.runtime.external_issue_clarification import (
            clarification_request_body,
            clarification_request_marker,
            hydrate_external_issue_clarification,
        )

        round_number = int(payload.get("round") or 1)
        marker = clarification_request_marker(
            source_key,
            str(payload.get("source_revision") or ""),
            triage_id,
            round_number,
        )
        marker_id = marker.removeprefix("<!-- zaofu-needs-info-request:").removesuffix(" -->")
        events = self.writer.event_log.read_all()
        if any(
            event.type == "external_issue.clarification.request_published"
            and str((event.payload or {}).get("marker_id") or "") == marker_id
            for event in events
        ):
            return
        clarification = hydrate_external_issue_clarification(
            self.state_dir, payload.get("clarification_ref"),
        )
        if clarification is None:
            return
        body = clarification_request_body(clarification, marker=marker)
        result: Mapping[str, Any]
        if self.clarification_comment_writer is not None:
            try:
                result = self.clarification_comment_writer(mirror, body, marker)
            except Exception as exc:  # pragma: no cover - adapter boundary
                result = {"status": "outcome_unknown", "reason": type(exc).__name__}
        else:
            from zf.integrations.forge.github import GitHubComProvider
            from zf.runtime.issue_candidate_delivery import _github_access_token

            token = _github_access_token()
            if not token:
                result = {"status": "authorization_required", "reason": "github_credential_missing"}
            else:
                result = GitHubComProvider().publish_issue_comment(
                    mirror.repository,
                    mirror.number,
                    body,
                    marker=marker,
                    access_token=token,
                )
        status = str(result.get("status") or "outcome_unknown")
        reason = str(result.get("reason") or "")
        event_type = (
            "external_issue.clarification.request_published"
            if status == "published"
            else "external_issue.clarification.request_pending"
        )
        if any(
            event.type == event_type
            and str((event.payload or {}).get("marker_id") or "") == marker_id
            and str((event.payload or {}).get("status") or "") == status
            and str((event.payload or {}).get("reason") or "") == reason
            for event in events
        ):
            return
        self.writer.emit(
            event_type,
            actor="external-issue-clarification",
            task_id=str(outcome.task_id or "") or None,
            payload={
                "source_key": source_key,
                "source_revision": str(payload.get("source_revision") or ""),
                "workflow_run_id": triage_id,
                "issue_number": mirror.number,
                "round": round_number,
                "marker_id": marker_id,
                "status": status,
                "reason": reason,
                "comment_url": str(result.get("url") or ""),
            },
            causation_id=str(getattr(outcome, "id", "") or "") or None,
            correlation_id=triage_id,
        )

    def _complete_triage_run(self, outcome: Any) -> None:
        """Close a semantic Triage outcome and release Project Run capacity."""

        payload = outcome.payload if isinstance(outcome.payload, dict) else {}
        run_id = str(
            payload.get("workflow_run_id")
            or payload.get("run_id")
            or outcome.correlation_id
            or ""
        ).strip()
        if not run_id:
            return
        outcome_type = str(getattr(outcome, "type", "") or "")
        lifecycle_state = {
            "external_issue.triage.needs_info": "needs_info",
            "external_issue.triage.needs_info_exhausted": "needs_info_exhausted",
            "external_issue.triage.unverified_candidate": "unverified_candidate",
            # The decision gate stays remote-visible until the exact Fix
            # proposal is approved and a Fix request is accepted.
            "external_issue.triage.ready_to_fix": "awaiting_fix_approval",
        }.get(outcome_type)
        if lifecycle_state:
            source_key = str(payload.get("source_key") or "").strip()
            revision = str(payload.get("source_revision") or "").strip()
            mirror = self._mirror_for_source_key(source_key)
            if mirror is not None:
                self._reconcile_remote_lifecycle_label(
                    mirror,
                    lifecycle_state,
                    revision=revision,
                    causation_id=str(getattr(outcome, "id", "") or ""),
                )
        events = self.writer.event_log.read_all()
        if any(
            event.type in {
                "run.goal.completed",
                "run.goal.blocked",
                "run.completed",
                "run.failed",
                "run.cancelled",
                "run.abandoned",
            }
            and str(
                (event.payload or {}).get("workflow_run_id")
                or (event.payload or {}).get("run_id")
                or event.correlation_id
                or ""
            ) == run_id
            for event in events
        ):
            return
        self.writer.append(ZfEvent(
            type="run.completed",
            actor="external-issue-intake",
            task_id=str(outcome.task_id or "") or None,
            payload={
                "schema_version": "run-admission.v1",
                "run_id": run_id,
                "workflow_run_id": run_id,
                "request_id": run_id,
                "status": "completed",
                "validation_status": "passed",
                "source": "external_issue_triage_outcome",
                "outcome_event_id": str(outcome.id or ""),
                "outcome_type": str(outcome.type or ""),
            },
            causation_id=str(outcome.id or "") or None,
            correlation_id=run_id,
        ))

    def _promote_ready_triage_task(self, outcome: Any) -> None:
        """Promote the intake contract after an admitted ready-to-fix result.

        External Issue reader children are intentionally report-only.  The
        report's ready disposition is the kernel-owned handoff point: retain
        the existing source binding, mark the contract as a normal fix
        contract, and let the regular delivery proposal path take over.
        """
        task = self.tasks.get(str(outcome.task_id or ""))
        if task is None or task.contract.schema_version != "external-issue-intake.v1":
            return
        payload = outcome.payload if isinstance(outcome.payload, dict) else {}
        evidence = dict(task.contract.evidence_contract or {})
        evidence.update({
            "external_issue_triage_status": "ready_to_fix",
            "external_issue_revision": str(payload.get("source_revision") or task.contract.source_revision),
            "verification_status": str(payload.get("verification_status") or ""),
            "verification_gaps": list(payload.get("verification_gaps") or []),
        })
        if isinstance(payload.get("report_ref"), dict):
            evidence["external_issue_report_ref"] = dict(payload["report_ref"])
        verification = "执行分诊报告中的验证命令并保存可审计证据。"
        validation: dict[str, Any] = {}
        verification_tiers = list(task.contract.verification_tiers or ["manual_evidence"])
        report_ref = evidence.get("external_issue_report_ref")
        if isinstance(report_ref, dict):
            report_path = Path(
                str(report_ref.get("ref") or report_ref.get("path") or "").strip()
            )
            if report_path and not report_path.is_absolute():
                report_path = self.state_dir / report_path
            try:
                report_text = report_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                report_text = ""
            commands = _extract_verification_commands(report_text)
            if commands:
                verification = commands[0]
                validation = {
                    "commands": normalize_verification_commands(commands),
                }
                verification_tiers = ["runtime"]
        contract = replace(
            task.contract,
            schema_version="task-contract.v1",
            behavior="修复 External Issue 报告中的有界问题，并保持源码 revision 绑定。",
            verification=verification,
            validation=validation,
            verification_tiers=verification_tiers,
            acceptance="完成有界修复并通过报告中声明的验证目标。",
            acceptance_criteria=[
                "修复目标限定在 External Issue 报告声明的源码范围",
                "验证命令通过或将明确缺口记录为证据",
            ],
            evidence_contract=evidence,
            spec_skip_reason="External Issue Triage 已形成绑定当前源码 revision 的 ready_to_fix 合同。",
        )
        try:
            TaskContractAuthorityService(
                task_store=self.tasks,
                event_writer=self.writer,
                state_dir=self.state_dir,
            ).replace(
                task,
                contract=contract,
                # The triage Run is complete.  Its workflow Request must not
                # be carried into the separate delivery proposal: doing so
                # makes WorkflowStart treat the proposal as an active replay
                # and reject it before emitting operator.action.proposed.
                execution_binding=TaskExecutionBinding(),
                source="external_issue_triage_ready_to_fix",
                actor="external-issue-intake",
                causation_id=str(outcome.id or ""),
                correlation_id=str(outcome.correlation_id or ""),
                task_updates=(
                    {
                        "status": "backlog",
                        "completed_at": None,
                        "cancelled_at": None,
                    }
                    if task.status in {"done", "cancelled"}
                    else None
                ),
                reopen_terminal=task.status in {"done", "cancelled"},
            )
        except TaskContractAuthorityConflict:
            # A concurrent runtime projection may win the CAS between the
            # read above and the mutation.  If it already applied this exact
            # promotion, treat the retry as successful; otherwise surface the
            # conflict to the poller instead of silently changing state.
            latest = self.tasks.get(task.id)
            if latest is None or latest.contract.schema_version != "task-contract.v1":
                raise

    @serialize_issue_admission
    def start_manual_triage(
        self,
        issue_number: int,
        *,
        actor: str = "web-operator",
        expected_source_revision: str = "",
        refresh: bool = True,
        defer_remote_sync: bool = False,
        defer_attachments: bool = False,
        lock_timeout_seconds: float = 60,
    ) -> ExternalIssueManualTriageResult:
        """Fetch and explicitly admit one Issue, including historical revisions."""
        if not self._execution_bound:
            from zf.runtime.issue_execution import IssueExecutionRoutes
            # A missing mirror needs one bounded fetch before repository-scoped routing.
            if self.mirrors.get(issue_number) is None:
                refreshed = self.reconciler.refresh_issue(issue_number)
                if not refreshed.get("ok"):
                    return ExternalIssueManualTriageResult(ok=False, status="not_found", issue_number=issue_number,
                        error=str(refreshed.get("error") or "GitHub Issue lookup failed"))
                refresh = False
            mirror = self.mirrors.get(issue_number)
            if mirror is not None:
                routes = IssueExecutionRoutes(self)
                target = routes.service_for(routes.target(mirror, automatic=False), mirror)
                if target is not self:
                    if expected_source_revision and self._source_revision(mirror)[1] != expected_source_revision:
                        return ExternalIssueManualTriageResult(ok=False, status="revision_conflict", issue_number=issue_number,
                            error="Issue source revision changed; refresh the Feishu card")
                    return target.start_manual_triage(issue_number, actor=actor,
                        expected_source_revision="", refresh=refresh,
                        defer_remote_sync=defer_remote_sync, defer_attachments=defer_attachments,
                        lock_timeout_seconds=lock_timeout_seconds)
        try:
            with locked_path(
                self.state_dir / "external-issues" / "intake",
                timeout_seconds=lock_timeout_seconds,
            ):
                return self._start_manual_triage_locked(
                    issue_number,
                    actor=actor,
                    expected_source_revision=expected_source_revision,
                    refresh=refresh,
                    defer_remote_sync=defer_remote_sync,
                    defer_attachments=defer_attachments,
                )
        except TimeoutError:
            return ExternalIssueManualTriageResult(
                ok=False,
                status="syncing",
                issue_number=issue_number,
                error="Issue intake is already updating this repository",
            )

    def _start_manual_triage_locked(
        self,
        issue_number: int,
        *,
        actor: str = "web-operator",
        expected_source_revision: str = "",
        refresh: bool = True,
        defer_remote_sync: bool = False,
        defer_attachments: bool = False,
    ) -> ExternalIssueManualTriageResult:
        actor = str(actor or "web-operator")
        # This is an explicit operator admission of one Issue, not the
        # background GitHub ingress loop.  Keep the total Self-Issue policy
        # as the safety gate, but do not require ``ingress.enabled`` here;
        # otherwise an operator cannot restart a previously cancelled queue
        # while automatic synchronization is intentionally disabled.
        if not self.config.self_issue.enabled:
            return ExternalIssueManualTriageResult(
                ok=False,
                status="disabled",
                issue_number=issue_number,
                error="Self-Issue is disabled",
            )
        self._ensure_activation()
        if refresh:
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
        expected_source_revision = str(expected_source_revision or "").strip()
        if expected_source_revision and expected_source_revision != revision:
            return ExternalIssueManualTriageResult(
                ok=False,
                status="revision_conflict",
                issue_number=mirror.number,
                source_revision=revision,
                error="Issue source revision changed; refresh the Feishu card",
            )
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
                actor=actor,
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
                    "target_root": SELF_ISSUE_TARGET_ROOT,
                    "execution_project_root": str(self.project_root),
                },
                correlation_id=f"external-issue:{mirror.issue_key}",
            )
        queued = self._ensure_intake_and_triage(
            mirror,
            revision=revision,
            source_ref=str(descriptor["ref"]),
            source_digest=str(descriptor["sha256"]),
            causation_id=prior.id,
            requested_by=actor,
            reason="人工选择 GitHub Issue 并启动只读 Triage",
            allow_retry=True,
            defer_remote_sync=defer_remote_sync,
            defer_attachments=defer_attachments,
        )
        # Read the identity that was actually persisted.  Computing the id
        # before admission can be stale when polling and manual admission race
        # or when this call discovers an already queued run.
        persisted_run = self._latest_triage_run_id(mirror.issue_key, revision)
        from zf.runtime.issue_execution import project_identity
        return ExternalIssueManualTriageResult(
            **project_identity(self.project_root, self.config),
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
            "author": mirror.author_login,
            # Lifecycle labels are a projection written by this workflow.  If
            # they participate in the immutable Issue-content revision, every
            # state transition looks like new reporter input and can enqueue a
            # fresh Triage attempt on the next GitHub poll.  Metadata labels
            # remain part of the source contract because maintainers may use
            # them to change classification or priority.
            "labels": [
                label for label in mirror.labels
                if label not in REMOTE_LIFECYCLE_STATE_SET
            ],
            "assignees": list(mirror.assignees),
            "created_at": mirror.created_at,
            # ``updated_at`` changes when GitHub applies a lifecycle label or
            # metadata-only housekeeping.  Body/comments/attachment digests
            # above are the content revision; including this transport
            # timestamp would make our own label write look like new reporter
            # input and enqueue Triage on every poll.  Binary attachments are
            # derived evidence and are intentionally outside this revision.
            "body_ref": mirror.body_ref,
            "body_digest": mirror.body_digest,
            "comments_ref": mirror.comments_ref,
            "comments_digest": mirror.comments_digest,
            "project": self.config.project.name,
            "target_root": SELF_ISSUE_TARGET_ROOT,
            "trust": "untrusted_external_input",
        }
        return source, "sha256:" + canonical_json_sha256(source)

    def _canonical_source_revision(self) -> str:
        """Return the committed ZaoFu revision used for this triage run."""

        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

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

    def _mirror_for_source_key(self, source_key: str) -> IssueMirror | None:
        if not source_key:
            return None
        return next(
            (
                mirror for mirror in self.mirrors.list()
                if mirror.issue_key == source_key
            ),
            None,
        )

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
        defer_remote_sync: bool = False,
        defer_attachments: bool = False,
    ) -> bool:
        if self._triage_was_queued(
            mirror.issue_key, revision, allow_retry=allow_retry,
        ):
            return False
        run_id = self._triage_run_id(mirror.issue_key, revision)
        task_id = self._task_id_for_revision(mirror.issue_key, revision)
        source_code_revision = self._canonical_source_revision()
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
                        "source_code_root": str(self.project_root),
                        "source_code_revision": source_code_revision,
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
        elif task.status in {"cancelled", "done"}:
            # Terminal tasks are archived by TaskStore. A retry on the same
            # Issue revision keeps the stable Task identity but reactivates it
            # before the new workflow dispatch.
            previous_status = task.status
            contract_evidence = dict(task.contract.evidence_contract or {})
            contract_evidence.update({
                "source_code_root": str(self.project_root),
                "source_code_revision": source_code_revision,
            })
            try:
                mutation = TaskContractAuthorityService(
                    task_store=self.tasks,
                    event_writer=self.writer,
                    state_dir=self.state_dir,
                ).replace(
                    task,
                    contract=replace(
                        task.contract,
                        evidence_contract=contract_evidence,
                    ),
                    source="external_issue_triage_retry",
                    actor="external-issue-intake",
                    causation_id=causation_id,
                    correlation_id=f"external-issue:{mirror.issue_key}",
                    task_updates={
                        "status": "backlog",
                        "assigned_to": None,
                        "active_dispatch_id": "",
                        "completed_at": None,
                        "cancelled_at": None,
                        "blocked_reason": "",
                    },
                    audit_payload={
                        "source_key": mirror.issue_key,
                        "source_revision": revision,
                    },
                    reopen_terminal=True,
                )
                task = mutation.task
            except TaskContractAuthorityConflict:
                task = self.tasks.get(task_id) or task
                if task.status in {"cancelled", "done"}:
                    return False
            self.writer.emit(
                "task.status_changed",
                actor="external-issue-intake",
                task_id=task_id,
                payload={
                    "from": previous_status,
                    "to": "backlog",
                    "source": "external_issue_retry",
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
                include_remote_attachments=not defer_attachments,
            ),
            "flow_kind": "workflow",
            "route_id": "general:external-issue-triage",
            "source_key": mirror.issue_key,
            "source_revision": revision,
            "expected_output": "triage report and proposed TaskContract; no source writes",
            # ``target_root`` is a config symbol; workflow target refs are
            # resolved relative to the canonical project_root above.
            "target_ref": ".",
            # The Issue digest above identifies the external input revision;
            # these fields identify the committed ZaoFu source being assessed.
            # Keeping both prevents an environment/version supplied in the
            # Issue form from being mistaken for the code revision.
            "target_root": SELF_ISSUE_TARGET_ROOT,
            "execution_project_root": str(self.project_root),
            "target_commit": source_code_revision,
            "source_code_revision": source_code_revision,
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
            "execution_project_root": str(self.project_root),
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
        if not defer_remote_sync:
            self._reconcile_remote_lifecycle_label(
                mirror,
                "triage_queued",
                revision=revision,
                causation_id=queued.id,
            )
        return True

    def _issue_artifact_refs(
        self,
        mirror: IssueMirror,
        *,
        source_ref: str,
        source_digest: str,
        include_remote_attachments: bool = True,
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
        if include_remote_attachments:
            refs.extend(self._issue_attachment_refs(mirror))
        return refs

    def _issue_attachment_refs(self, mirror: IssueMirror) -> list[dict[str, Any]]:
        """Materialize safe GitHub image/video links as evidence sidecars."""

        cache_key = (mirror.body_digest, mirror.comments_digest)
        cached = load_capture(self.state_dir, cache_key, self.now())
        if cached is not None:
            return [dict(item) for item in cached]

        texts: list[str] = []
        for ref in (mirror.body_ref, mirror.comments_ref):
            if not ref:
                continue
            try:
                hydrated = hydrate_sidecar_ref(self.state_dir, {
                    "ref": ref,
                    "sha256": (
                        mirror.body_digest if ref == mirror.body_ref else mirror.comments_digest
                    ).removeprefix("sha256:"),
                })
                payload = hydrated.payload
                if isinstance(payload, str):
                    texts.append(payload)
                elif isinstance(payload, list):
                    texts.extend(
                        str(item.get("body") or "")
                        for item in payload if isinstance(item, dict)
                    )
            except (OSError, ValueError, TypeError):
                continue
        urls: list[str] = []
        for text in texts:
            for raw in _ATTACHMENT_URL.findall(text):
                try:
                    parsed = urllib.parse.urlparse(raw.rstrip(".,"))
                    port = parsed.port
                except ValueError:
                    continue
                if (
                    parsed.scheme == "https"
                    and parsed.hostname in _GITHUB_ATTACHMENT_HOSTS
                    and port in {None, 443}
                    and parsed.username is None
                    and parsed.password is None
                    and (parsed.hostname != "github.com" or parsed.path.startswith("/user-attachments/"))
                ):
                    value = raw.rstrip(".,")
                    if value not in urls:
                        urls.append(value)
        descriptors: list[dict[str, Any]] = []
        for url in urls[:8]:
            try:
                request = urllib.request.Request(
                    url,
                    headers={"Accept": "*/*", "User-Agent": "ZaoFu-Issue-Triage"},
                )
                with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
                    content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0]
                    if not content_type.startswith(("image/", "video/")):
                        continue
                    raw = response.read(_MAX_ATTACHMENT_BYTES + 1)
                if len(raw) > _MAX_ATTACHMENT_BYTES:
                    continue
                digest = hashlib.sha256(raw).hexdigest()
                suffix = (
                    ".png" if content_type == "image/png"
                    else ".jpg" if content_type in {"image/jpeg", "image/jpg"}
                    else ".mp4" if content_type == "video/mp4"
                    else ".webm" if content_type == "video/webm"
                    else ".bin"
                )
                descriptor = write_sidecar_bytes(
                    self.state_dir,
                    f"artifacts/external-issues/attachments/{digest}{suffix}",
                    raw,
                    kind="external_issue_attachment",
                    schema_version="external-issue-attachment.v1",
                    created_by="external-issue-ingress",
                    content_type=content_type,
                    preview=urllib.parse.urlparse(url).path.rsplit("/", 1)[-1][:120],
                )
                descriptor.update({"source_url": url, "capture_source": "github_issue"})
                descriptors.append(descriptor)
            except (OSError, urllib.error.URLError, ValueError):
                _LOG.info("Skipping unavailable GitHub attachment", exc_info=True)
        save_capture(self.state_dir, cache_key, self.now(), descriptors,
                     complete=len(descriptors) == len(urls[:8]))
        return descriptors

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
            # A configured execution project may not have started its runtime yet.
            # Bind its immutable Triage contract now, so a queued request can resume.
            contract = json.loads(contract_path.read_text(encoding="utf-8")) if contract_path.is_file() else {"workflow": {}}
            if isinstance(contract, dict):
                contract_digest = str(contract.get("contract_digest") or "")
                if not isinstance(contract.get("workflow"), dict):
                    if contract_digest:
                        contract_snapshot = write_run_contract_snapshot(
                            self.state_dir, contract
                        )
                    return {
                        "effective_config_ref": effective_ref,
                        "effective_config_digest": str(effective_ref.get("sha256") or ""),
                        "run_contract_ref": str(contract_snapshot.get("ref") or ""),
                        "run_contract_digest": contract_digest,
                    }
                workflow_metadata = flow_metadata_for(self.config, "workflow")
                contract_config = self.config
                contract_config_path = self.project_root / "zf.yaml"
                # Bind the execution project's generic Triage workflow, never
                # a software-delivery baseline or the user's report source.
                if (
                    str(workflow_metadata.get("completion_profile") or "")
                    != "artifact_delivery"
                    or not str(
                        workflow_metadata.get("generic_workflow_contract_digest")
                        or ""
                    )
                ):
                    try:
                        from zf.core.config.loader import load_config

                        canonical_config = load_config(contract_config_path)
                        canonical_metadata = flow_metadata_for(
                            canonical_config, "workflow"
                        )
                        if canonical_metadata.get("generic_workflow_contract_digest"):
                            workflow_metadata = canonical_metadata
                            contract_config = canonical_config
                    except (OSError, ValueError, ConfigError):
                        # Unit/test configurations may deliberately use a
                        # minimal zf.yaml.  Keep the already-loaded config in
                        # that case; the route still fails closed if it has no
                        # valid generic contract digest.
                        pass
                expected_profile = str(
                    workflow_metadata.get("completion_profile") or "artifact_delivery"
                )
                expected_generation = str(
                    workflow_metadata.get("generic_workflow_contract_digest") or ""
                )
                # Never reuse the mutable baseline for an External Issue
                # child.  It may describe a previous software-delivery run;
                # this route must snapshot the generic artifact-delivery
                # Workflow on every admission.
                if (self.project_root / "zf.yaml").is_file():
                    contract = build_run_contract(
                        contract_config,
                        config_path=contract_config_path,
                        project_root=self.project_root,
                        state_dir=self.state_dir,
                        metadata_flow_kind="workflow",
                    )
                    # The mutable compatibility contract may have been built
                    # by ``zf start`` for a software-delivery flow before the
                    # External Issue route was selected.  Keep this ingress
                    # snapshot authoritative for the generic Workflow that
                    # is actually being dispatched; otherwise the verifier
                    # admits against the stale software profile/generation.
                    contract_workflow = dict(contract.get("workflow") or {})
                    contract_workflow.update({
                        "completion_profile": expected_profile,
                        "generic_workflow_contract_digest": expected_generation,
                        "workflow_generation": expected_generation,
                    })
                    contract["workflow"] = contract_workflow
                    required = workflow_metadata.get("required_delivery_artifacts")
                    if isinstance(required, list):
                        contract["required_delivery_artifacts"] = [
                            dict(item) for item in required
                            if isinstance(item, Mapping)
                        ]
                    contract["contract_digest"] = stable_json_sha256(
                        _stable_contract_body(contract)
                    )
                    contract_digest = str(contract.get("contract_digest") or "")
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
            "external_issue.triage.queued_cancelled",
            "run.cancelled", "run.abandoned", "run.failed",
            "run.goal.blocked", "run.goal.completed",
            "workflow.invoke.rejected", "run.admission.rejected",
            # A disposition is a terminal Triage decision.  Allowing an
            # explicit Start Triage after it is what lets an operator rerun
            # the same Issue against a newly fixed canonical checkout,
            # without reusing the old Triage run.
            "external_issue.triage.ready_to_fix",
            "external_issue.triage.unverified_candidate",
            # A needs_info disposition is a completed Triage attempt.  An
            # operator may explicitly start a fresh attempt after correcting
            # the source/evidence binding; clarification submission remains
            # the automatic retry path.
            "external_issue.triage.needs_info",
            "external_issue.triage.needs_info_exhausted",
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
            "external_issue.triage.queued_cancelled",
            "run.cancelled", "run.abandoned", "run.failed",
            "run.goal.blocked", "run.goal.completed",
            "workflow.invoke.rejected", "run.admission.rejected",
            "external_issue.triage.ready_to_fix",
            "external_issue.triage.unverified_candidate",
            "external_issue.triage.needs_info",
            "external_issue.triage.needs_info_exhausted",
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

    def _task_id_for_revision(self, source_key: str, revision: str) -> str:
        """Keep retries on one revision attached to one Task identity.

        A changed Issue revision gets a deterministic ``-R<n>`` suffix while
        repeated Start Triage calls for the same revision reuse the Task.
        """

        revisions: list[str] = []
        for event in self.writer.event_log.read_all():
            if event.type != "external_issue.received":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("source_key") or "") != source_key:
                continue
            value = str(payload.get("source_revision") or "")
            if value and value not in revisions:
                revisions.append(value)
        try:
            revision_index = revisions.index(revision)
        except ValueError:
            revision_index = len(revisions)
        digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:12].upper()
        suffix = f"-R{revision_index}" if revision_index else ""
        return f"ISSUE-{digest}{suffix}"

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
        if proposal_id:
            self._reconcile_remote_lifecycle_label(
                mirror,
                "awaiting_fix_approval",
                revision=revision,
                causation_id=causation_id,
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
        evidence_contract = (
            task.contract.evidence_contract
            if isinstance(task.contract.evidence_contract, dict)
            else {}
        )
        # ``TaskContract.source_revision`` is the internal task-capsule
        # revision (for example ``source-r...``), while the immutable Issue
        # revision is stored as ``external_issue_revision``.  Comparing the
        # two directly strands every promoted task before proposal creation.
        external_issue_revision = str(
            evidence_contract.get("external_issue_revision")
            or task.contract.source_revision
            or ""
        )
        if (
            task.contract.source_key != mirror.issue_key
            or external_issue_revision != revision
            or task.contract.source_ref != source_ref
        ):
            return None
        if (
            task.contract.schema_version == "external-issue-intake.v1"
            or evidence_contract.get("external_issue_triage_status")
            != "ready_to_fix"
            or str(evidence_contract.get("external_issue_revision") or "")
            != revision
        ):
            return None
        task = self._ensure_executable_verification(task, causation_id=causation_id)
        if validate_task_contract(
            task,
            config=self.config,
            project_root=self.project_root,
        ):
            return None
        # A ready-to-fix disposition is not executable merely because the
        # prose contract validates.  The delivery Request must carry a real
        # checkout and a bounded writable scope; otherwise admission can
        # create a worker workdir containing only .gitkeep and fail much
        # later with an opaque ``source_root``/``target_root`` error.
        allowed_paths = list(dict.fromkeys(
            str(path).strip()
            for path in (
                list(task.contract.scope or [])
                + list(task.contract.affected_files or [])
                + list(task.contract.exclusive_files or [])
            )
            if str(path).strip()
        ))
        if not allowed_paths:
            return None
        source_root = str(self.project_root.resolve(strict=False))
        target_root = source_root
        verification_commands = normalize_verification_commands(
            task.contract.verification,
            validation=(
                task.contract.validation
                if isinstance(task.contract.validation, dict)
                else {}
            ),
        )
        # A prose sentence in ``verification`` is not an executable check.
        # Canonical validation.commands may contain structured commands; the
        # legacy string form must at least start with a supported command
        # runner before it is admitted to a Fix Request.
        canonical_commands = (
            task.contract.validation.get("commands")
            if isinstance(task.contract.validation, dict)
            else None
        )
        if not verification_commands or (
            not isinstance(canonical_commands, list)
            and not re.match(
                r"^(?:npm|pnpm|yarn|bun|node|python|python3|uv|pytest|npx|docker|curl|go|cargo|deno)\b",
                str(task.contract.verification or "").strip(),
            )
        ):
            return None
        if task.status in {"cancelled", "done"}:
            # A stale cancellation from an older attempt may have projected
            # onto the stable Task after a newer attempt reached ready_to_fix.
            # Reopen only when the latest triage Run itself has a ready result;
            # a user-cancelled queued Run must remain terminal.
            events = self.writer.event_log.read_all()
            latest_run_id = self._latest_triage_run_id(mirror.issue_key, revision)
            has_ready_result = any(
                event.type == "external_issue.triage.ready_to_fix"
                and str(
                    event.payload.get("workflow_run_id")
                    or event.correlation_id
                    or ""
                ) == latest_run_id
                for event in events
            )
            if not latest_run_id or not has_ready_result:
                return None
            try:
                TaskContractAuthorityService(
                    task_store=self.tasks,
                    event_writer=self.writer,
                    state_dir=self.state_dir,
                ).replace(
                    task,
                    contract=task.contract,
                    source="external_issue_triage_ready_to_fix_reopen",
                    actor="external-issue-intake",
                    causation_id=causation_id,
                    correlation_id=f"external-issue:{mirror.issue_key}",
                    task_updates={
                        "status": "backlog",
                        "completed_at": None,
                        "cancelled_at": None,
                    },
                    reopen_terminal=True,
                )
                task = self.tasks.get(task.id) or task
            except TaskContractAuthorityConflict:
                task = self.tasks.get(task.id) or task
                if task.status in {"cancelled", "done"}:
                    return None
        # Tasks promoted by an earlier runtime may already use the
        # ``task-contract.v1`` schema, so the promotion path above is skipped.
        # Still detach the completed triage Request before building the Fix
        # proposal; otherwise preview treats the new delivery as an active
        # Request replay and returns ``workflow_request_active``.
        binding = task.execution_binding
        if binding.request_id or binding.workflow_run_id:
            admission = request_admission_view(
                self.writer.event_log.read_all(),
                request_id=str(binding.request_id or ""),
                run_id=str(binding.workflow_run_id or ""),
            )
            # A live delivery Request owns the Task and must not be detached
            # by the poller.  Only clear a binding left by a terminal,
            # completed Triage attempt.
            if not bool(admission.get("terminal")):
                return None
            try:
                TaskContractAuthorityService(
                    task_store=self.tasks,
                    event_writer=self.writer,
                    state_dir=self.state_dir,
                ).replace(
                    task,
                    contract=task.contract,
                    execution_binding=TaskExecutionBinding(),
                    source="external_issue_triage_ready_to_fix_clear_request",
                    actor="external-issue-intake",
                    causation_id=causation_id,
                    correlation_id=f"external-issue:{mirror.issue_key}",
                )
                task = self.tasks.get(task.id) or task
            except TaskContractAuthorityConflict:
                task = self.tasks.get(task.id) or task
                if task.execution_binding.request_id or task.execution_binding.workflow_run_id:
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
                    "source_root": source_root,
                    "target_root": target_root,
                    "allowed_paths": allowed_paths,
                    "verification_commands": verification_commands,
                },
                # Keep the roots at the Request boundary as well as inside
                # parameters.  The latter is the canonical route input; the
                # former makes the proposal self-describing to approval UIs
                # and prevents adapters from silently substituting a
                # project-local placeholder.
                "source_root": source_root,
                "target_root": target_root,
                "scope": allowed_paths,
            },
            actor="external-issue-intake",
            origin="external_issue",
            causation_id=causation_id,
            correlation_id=f"external-issue:{mirror.issue_key}",
        )
        return result if result.get("status") == "proposal_ready" else None

    def _ensure_executable_verification(
        self,
        task: Task,
        *,
        causation_id: str,
    ) -> Task:
        """Bind executable checks from the immutable triage report.

        Older ready-to-fix tasks were promoted with a prose-only verification
        sentence.  The writer fanout admission contract intentionally rejects
        that shape, so backfill the commands declared by the report before
        creating a delivery proposal.  This is idempotent and only replaces a
        prose/empty value when the report supplies a real command.
        """
        current = str(task.contract.verification or "").strip()
        evidence = task.contract.evidence_contract
        if not isinstance(evidence, dict):
            return task
        descriptor = evidence.get("external_issue_report_ref")
        if not isinstance(descriptor, dict):
            # Tasks promoted by an older runtime may predate the report-ref
            # field even though the authoritative ready_to_fix event carries
            # it.  Recover that immutable ref from the event ledger so a
            # cancelled/retried Task can still produce a valid Fix contract.
            for event in reversed(self.writer.event_log.read_all()):
                if event.type != "external_issue.triage.ready_to_fix":
                    continue
                if str(event.task_id or "") != task.id:
                    continue
                candidate = (event.payload or {}).get("report_ref")
                if isinstance(candidate, dict):
                    descriptor = candidate
                    break
        if not isinstance(descriptor, dict):
            return task
        ref = str(descriptor.get("ref") or descriptor.get("path") or "").strip()
        if not ref:
            return task
        report_path = Path(ref)
        if not report_path.is_absolute():
            report_path = self.state_dir / report_path
        try:
            report_text = report_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return task
        commands = _extract_verification_commands(report_text)
        if not commands or current in commands:
            return task
        service = TaskContractAuthorityService(
            task_store=self.tasks,
            event_writer=self.writer,
            state_dir=self.state_dir,
        )
        # Reopen/clear-request can update the same contract between the
        # initial read and this backfill.  Retry once against the freshly
        # loaded authority so a ready-to-fix delivery cannot carry the old
        # prose-only verification into writer fanout.
        candidate = task
        for _ in range(2):
            contract = replace(candidate.contract, verification=commands[0])
            try:
                service.replace(
                    candidate,
                    contract=contract,
                    source="external_issue_triage_report_verification_backfill",
                    actor="external-issue-intake",
                    causation_id=causation_id,
                    correlation_id=f"external-issue:{candidate.contract.source_key}",
                )
                return self.tasks.get(task.id) or candidate
            except TaskContractAuthorityConflict:
                candidate = self.tasks.get(task.id) or candidate
                if str(candidate.contract.verification or "").strip() in commands:
                    return candidate
        return self.tasks.get(task.id) or candidate


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
        last_poll: float | None = None
        while not self._stop.is_set():
            try:
                enabled = self.refresh_policy()
                if enabled and (last_poll is None or monotonic() - last_poll >= self.interval_seconds):
                    result = self.service.poll_once()
                    if result.status != "syncing":
                        last_poll = monotonic()
            except Exception:
                # Sync state and events carry expected diagnostics. A malformed
                # local state must not terminate the Web server process.
                _LOG.exception("External Issue polling iteration failed")
            # Observe settings even when auto-intake is disabled or its sync
            # interval is long. Never change policy during an in-flight pass.
            self._stop.wait(2)

    def refresh_policy(self) -> bool:
        from zf.runtime.issue_settings import current_issue_policy
        policy = current_issue_policy(self.service.project_root, self.service.config.self_issue)
        self.service.config = replace(self.service.config, self_issue=policy)
        self.service.policy = policy.ingress
        self.interval_seconds = policy.ingress.poll_interval_seconds
        self.service.reconciler.minimum_interval_seconds = self.interval_seconds
        return policy.enabled and policy.ingress.enabled


def build_external_issue_poller(
    state_dir: Path,
    config: ZfConfig | None,
    *,
    project_root: Path,
) -> ExternalIssuePoller | None:
    if config is None or "github" not in config.self_issue.targets:
        return None
    if config.self_issue.ingress.provider != "github":
        return None

    return ExternalIssuePoller(ExternalIssueIngressService(
        state_dir,
        config,
        project_root=project_root,
        lifecycle_label_writer=build_github_lifecycle_label_writer(),
    ))


def build_github_lifecycle_label_writer() -> LifecycleLabelWriter:
    """Compose the GitHub label adapter for ingress and candidate delivery."""
    # Import concrete provider/auth code only at composition time.  The core
    # lifecycle model remains usable by non-GitHub projects and offline tests.
    from zf.integrations.forge.github import GitHubComProvider
    from zf.runtime.issue_candidate_delivery import _github_access_token

    github = GitHubComProvider()

    def writer(mirror: IssueMirror, state: str) -> Mapping[str, Any]:
        token = _github_access_token()
        if not token:
            return {
                "status": "authorization_required",
                "reason": "github_credential_missing",
            }
        result = github.reconcile_lifecycle_label(
            mirror.repository,
            mirror.number,
            state,
            access_token=token,
        )
        return {"status": result.status, "reason": result.reason, "label_colors": result.label_colors}

    return writer


__all__ = [
    "ExternalIssueIngressService",
    "ExternalIssueManualTriageResult",
    "ExternalIssuePollResult",
    "ExternalIssuePoller",
    "build_github_lifecycle_label_writer",
    "build_external_issue_poller",
]
