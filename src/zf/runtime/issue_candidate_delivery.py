"""Human-controlled publication handoff for verified External Issue candidates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.issue_triage.models import IssueMirror
from zf.core.issue_triage.store import IssueMirrorStore
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.issue_version_binding import validate_delivery_version_context


HANDOFF_SCHEMA_VERSION = "candidate-publication-handoff.v1"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PR_PATH_RE = re.compile(r"^/([^/]+)/([^/]+)/pull/(\d+)$")
PullRequestTransport = Callable[[str, dict[str, str]], tuple[int, bytes]]
RemoteBaseReader = Callable[[str, str], str]
LifecycleLabelWriter = Callable[[IssueMirror, str], Mapping[str, Any]]


class CandidateDeliveryError(ValueError):
    """Fail-closed candidate delivery contract error."""


def _git_remote_base(remote_url: str, branch: str) -> str:
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", remote_url, f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CandidateDeliveryError(
            "Unable to read the configured remote base branch"
        ) from exc
    if result.returncode != 0:
        raise CandidateDeliveryError(
            result.stderr.strip() or "Unable to read the configured remote base branch"
        )
    fields = result.stdout.strip().split()
    sha = fields[0].lower() if len(fields) == 2 else ""
    if not _SHA_RE.fullmatch(sha):
        raise CandidateDeliveryError("Configured remote base branch was not found")
    return sha


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _github_access_token() -> str:
    token = str(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if token:
        return token if "\n" not in token and "\r" not in token else ""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    token = result.stdout.strip() if result.returncode == 0 else ""
    return token if "\n" not in token and "\r" not in token else ""


def _digest(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_candidate_event_matches(
    event: ZfEvent,
    *,
    task_id: str,
    workflow_run_id: str,
    candidate_identities: tuple[str, ...] = (),
    require_candidate_identity: bool = False,
) -> bool:
    """Bind candidate-level events to one Issue Fix Run.

    Candidate-level Kernel events intentionally have no ``task_id``.  Their
    durable parent identity is the workflow run carried in the aggregate
    payload/correlation id.  Task-bound legacy events remain supported, while
    taskless events fail closed unless the exact Issue Fix Run matches.
    """

    payload = event.payload if isinstance(event.payload, dict) else {}
    event_run_id = str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or payload.get("trace_id")
        or event.correlation_id
        or ""
    ).strip()
    if event.task_id:
        if event.task_id != task_id:
            return False
        if event_run_id and workflow_run_id and event_run_id != workflow_run_id:
            return False
    elif not workflow_run_id or event_run_id != workflow_run_id:
        return False

    flow_kind = str(payload.get("flow_kind") or payload.get("request_kind") or "")
    if flow_kind and flow_kind != "issue":
        return False

    event_identities = {
        str(payload.get(key) or "").strip()
        for key in ("pdd_id", "feature_id", "goal_id")
    }
    event_identities.discard("")
    if not event.task_id and require_candidate_identity and not event_identities:
        return False
    expected_identities = {value for value in candidate_identities if value}
    if expected_identities and event_identities.isdisjoint(expected_identities):
        return False
    return True


def _stdlib_transport(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()
    except (TimeoutError, ConnectionError, OSError) as exc:
        raise CandidateDeliveryError(
            "GitHub pull request lookup could not reach GitHub"
        ) from exc


class GitHubPullRequestReader:
    """Read public GitHub PR state without creating or mutating remote objects."""

    def __init__(self, transport: PullRequestTransport | None = None) -> None:
        self.transport = transport or _stdlib_transport

    def read(self, repository: str, number: int) -> dict[str, Any]:
        owner, name = repository.split("/", 1)
        base = (
            "https://api.github.com/repos/"
            f"{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}/pulls/{number}"
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ZaoFu-Issue-Candidate-Delivery",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        access_token = _github_access_token()
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        # The PR document and its reviews are independent GitHub requests.
        # Fetch them together so a slow reviews endpoint does not add another
        # full network timeout to the user-facing delivery action.
        reviews_url = base + "/reviews"
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="zf-pr-read") as pool:
            pr_future = pool.submit(self.transport, base, headers)
            reviews_future = pool.submit(self.transport, reviews_url, headers)
            status, raw = pr_future.result()
            try:
                reviews_result = reviews_future.result()
            except CandidateDeliveryError:
                # A non-successful PR response is already authoritative for
                # the operation. Preserve that error/status instead of
                # letting an independent reviews request mask it.
                if status == 200:
                    raise
                reviews_result = (0, b"")
            review_status, review_count = self._parse_reviews(*reviews_result)
        if status == 404:
            raise CandidateDeliveryError("GitHub pull request was not found")
        if status != 200:
            raise CandidateDeliveryError(f"GitHub pull request lookup failed: HTTP {status}")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CandidateDeliveryError("GitHub pull request response is invalid") from exc
        if not isinstance(value, dict):
            raise CandidateDeliveryError("GitHub pull request response is invalid")

        head = value.get("head") if isinstance(value.get("head"), dict) else {}
        base_value = value.get("base") if isinstance(value.get("base"), dict) else {}
        head_repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
        merged = bool(value.get("merged") or value.get("merged_at"))
        state = str(value.get("state") or "").lower()
        if merged:
            lifecycle = "merged"
        elif state == "closed":
            lifecycle = "pr_closed_without_merge"
        elif review_status == "changes_requested":
            lifecycle = "pr_changes_requested"
        elif review_status == "approved":
            lifecycle = "pr_approved"
        else:
            lifecycle = "pr_open"
        return {
            "number": int(value.get("number") or number),
            "url": str(value.get("html_url") or ""),
            "repository": str(head_repo.get("full_name") or ""),
            "head_ref": str(head.get("ref") or ""),
            "head_sha": str(head.get("sha") or "").lower(),
            "base_ref": str(base_value.get("ref") or ""),
            "base_sha": str(base_value.get("sha") or "").lower(),
            "state": state,
            "lifecycle": lifecycle,
            "review_status": review_status,
            "review_count": review_count,
            "merge_commit_sha": str(value.get("merge_commit_sha") or "").lower(),
            "merged_at": str(value.get("merged_at") or ""),
            "updated_at": str(value.get("updated_at") or ""),
        }

    def _parse_reviews(self, status: int, raw: bytes) -> tuple[str, int]:
        if status != 200:
            return "pending", 0
        try:
            values = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "pending", 0
        if not isinstance(values, list):
            return "pending", 0
        latest: dict[str, str] = {}
        for row in values:
            if not isinstance(row, dict):
                continue
            user = row.get("user") if isinstance(row.get("user"), dict) else {}
            login = str(user.get("login") or "")
            state = str(row.get("state") or "").upper()
            if login and state in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
                latest[login] = state
        states = set(latest.values())
        if "CHANGES_REQUESTED" in states:
            return "changes_requested", len(latest)
        if "APPROVED" in states:
            return "approved", len(latest)
        return "pending", len(latest)


class IssueCandidateDeliveryService:
    """Own review receipts, local review refs, and read-only PR receipts."""

    def __init__(
        self,
        *,
        state_dir: Path,
        project_root: Path,
        config: ZfConfig,
        pr_reader: GitHubPullRequestReader | None = None,
        remote_base_reader: RemoteBaseReader | None = None,
        lifecycle_label_writer: LifecycleLabelWriter | None = None,
        actor: str = "web-operator",
        defer_remote_sync: bool = False,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.config = config
        self.policy = config.self_issue.delivery
        self.project_root = Path(project_root).resolve()
        self.event_log = event_log_from_project(self.state_dir, config=config)
        self.writer = EventWriter(self.event_log)
        self.pr_reader = pr_reader or GitHubPullRequestReader()
        self.remote_base_reader = remote_base_reader or _git_remote_base
        self.lifecycle_label_writer = lifecycle_label_writer
        self.actor = str(actor or "web-operator")
        self.defer_remote_sync = bool(defer_remote_sync)

    def _reconcile_remote_lifecycle_label(
        self, issue: IssueMirror, state: str,
    ) -> None:
        """Delegate canonical label writes to the configured Forge adapter."""
        if self.lifecycle_label_writer is None:
            return
        # Coordinate with refresh and batch publication. The handoff/event
        # remains canonical; only a confirmed Forge result patches the mirror.
        from zf.runtime.external_issue_label_sync import _workspace_lifecycle_targets, try_intake_lock

        mirrors = IssueMirrorStore(self.state_dir)
        with try_intake_lock(mirrors.refresh_lock_path) as acquired:
            if not acquired:
                result = {"status": "reconcile_pending", "reason": "mirror_refresh_in_progress"}
            else:
                targets, _ = _workspace_lifecycle_targets(self, mirrors=[issue])
                expected = targets.get(issue.issue_key)
                if expected and expected[0] and expected[1] != state:
                    result = {"status": "reconcile_pending", "reason": "newer_issue_lifecycle"}
                else:
                    try:
                        result = self.lifecycle_label_writer(issue, state)
                    except Exception as exc:  # pragma: no cover - provider boundary
                        result = {"status": "outcome_unknown", "reason": type(exc).__name__}
                    targets, _ = _workspace_lifecycle_targets(self, mirrors=[issue])
                    if targets.get(issue.issue_key) != expected:
                        result = {"status": "reconcile_pending", "reason": "lifecycle_changed_during_sync"}
                if result.get("status") == "reconciled":
                    mirrors.project_lifecycle_label(
                        issue.issue_key, state, dict(result.get("label_colors") or {}),
                    )
        status = str(result.get("status") or "outcome_unknown")
        event_type = (
            "external_issue.lifecycle_label.reconciled"
            if status == "reconciled"
            else "external_issue.lifecycle_label.reconcile_failed"
        )
        self.writer.emit(
            event_type,
            actor="issue-candidate-delivery",
            payload={
                "source_key": issue.issue_key,
                "source_revision": str(result.get("source_revision") or ""),
                "repository": issue.repository,
                "issue_number": issue.number,
                "state": state,
                "status": status,
                "reason": str(result.get("reason") or ""),
            },
            correlation_id=f"external-issue:{issue.issue_key}",
        )

    def projection(self, issue: IssueMirror) -> dict[str, Any]:
        candidate = self._candidate(issue)
        handoff = self._read_handoff(issue.number)
        if handoff and candidate:
            handoff_matches_candidate = (
                str(handoff.get("candidate_head_sha") or "")
                == candidate["candidate_head_sha"]
                and str(handoff.get("source_revision") or "")
                == candidate["source_revision"]
            )
            if not handoff_matches_candidate:
                # A handoff is an approval receipt for one exact candidate,
                # not evidence that a later verified candidate is stale.
                handoff = {}
        return {
            "enabled": bool(self.policy.enabled),
            "configured_repository": self.policy.repository,
            "configured_base_branch": self.policy.base_branch,
            "candidate": candidate,
            "handoff": handoff,
            "status": str(handoff.get("status") or "") if handoff else "",
            "stale_reason": "",
        }

    def review(
        self,
        issue: IssueMirror,
        *,
        verdict: str,
        expected_candidate_sha: str,
        expected_source_revision: str,
        reason: str = "",
    ) -> dict[str, Any]:
        self._require_enabled()
        event_types = {
            "approve": "candidate.owner_review.approved",
            "changes_requested": "candidate.owner_review.changes_requested",
            "reject": "candidate.owner_review.declined",
        }
        if verdict not in event_types:
            raise CandidateDeliveryError("verdict must be approve, changes_requested, or reject")
        normalized_reason = str(reason or "").strip()[:2000]
        if verdict != "approve" and not normalized_reason:
            raise CandidateDeliveryError("Owner changes or rejection requires a review reason")
        with locked_path(self._lock_path(issue.number)):
            candidate = self._require_candidate(
                issue,
                expected_candidate_sha=expected_candidate_sha,
                expected_source_revision=expected_source_revision,
            )
            current = self._read_handoff(issue.number)
            status = {
                "approve": "approved_for_pr",
                "changes_requested": "owner_changes_requested",
                "reject": "owner_rejected",
            }[verdict]
            immutable_statuses = {
                "publication_prepared", "pr_open", "pr_changes_requested",
                "pr_approved", "pr_closed_without_merge", "merged",
            }
            if current.get("status") in immutable_statuses:
                same_receipt = (
                    (current.get("owner_review") or {}).get("verdict") == verdict
                    and current.get("candidate_head_sha") == candidate["candidate_head_sha"]
                    and current.get("source_revision") == candidate["source_revision"]
                )
                if same_receipt:
                    return {"ok": True, "idempotent": True, **current}
                raise CandidateDeliveryError(
                    "Owner review is immutable after publication preparation"
                )
            if (
                current.get("status") == status
                and current.get("candidate_head_sha") == candidate["candidate_head_sha"]
                and current.get("source_revision") == candidate["source_revision"]
            ):
                return {"ok": True, "idempotent": True, **current}
            handoff = self._base_handoff(issue, candidate)
            handoff.update({
                "status": status,
                "owner_review": {
                    "verdict": verdict,
                    "reviewer": self.actor,
                    "reason": normalized_reason,
                    "reviewed_at": _now(),
                },
                "updated_at": _now(),
            })
            descriptor = self._write_handoff(issue.number, handoff)
            event = self.writer.emit(
                event_types[verdict],
                actor=self.actor,
                task_id=candidate["task_id"],
                correlation_id=candidate["workflow_run_id"] or None,
                payload={
                    "schema_version": HANDOFF_SCHEMA_VERSION,
                    "provider": issue.provider,
                    "source_key": issue.issue_key,
                    "source_revision": candidate["source_revision"],
                    "issue_number": issue.number,
                    "candidate_ref": candidate["candidate_ref"],
                    "candidate_head_sha": candidate["candidate_head_sha"],
                    "candidate_base_sha": candidate["candidate_base_sha"],
                    "verdict": verdict,
                    "reason": normalized_reason,
                    **descriptor,
                },
            )
            handoff["owner_review"]["event_id"] = event.id
            descriptor = self._write_handoff(issue.number, handoff)
            if not self.defer_remote_sync:
                self._reconcile_remote_lifecycle_label(issue, status)
            return {"ok": True, "idempotent": False, **handoff, **descriptor}

    def prepare(
        self,
        issue: IssueMirror,
        *,
        expected_candidate_sha: str,
        expected_source_revision: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        with locked_path(self._lock_path(issue.number)):
            candidate = self._require_candidate(
                issue,
                expected_candidate_sha=expected_candidate_sha,
                expected_source_revision=expected_source_revision,
            )
            remote_base_sha = self.remote_base_reader(
                self.policy.remote_url,
                self.policy.base_branch,
            ).lower()
            if remote_base_sha != candidate["candidate_base_sha"]:
                raise CandidateDeliveryError(
                    "Configured remote base changed; rebuild and reverify the candidate"
                )
            handoff = self._read_handoff(issue.number)
            if (
                handoff.get("status") not in {"approved_for_pr", "publication_prepared"}
                or (handoff.get("owner_review") or {}).get("verdict") != "approve"
            ):
                raise CandidateDeliveryError("exact candidate requires Owner approval before publication")
            branch = (
                f"{self.policy.branch_prefix}/github-issue-{issue.number}-"
                f"{candidate['candidate_head_sha'][:8]}"
            )
            if (
                handoff.get("status") == "publication_prepared"
                and handoff.get("review_branch") == branch
            ):
                self._create_review_ref(branch, candidate["candidate_head_sha"])
                return {"ok": True, "idempotent": True, **handoff}
            self._create_review_ref(branch, candidate["candidate_head_sha"])
            body_ref = self._write_pr_body(issue, candidate, branch)
            push_command = (
                f"git push {self.policy.remote_url} "
                f"refs/heads/{branch}:refs/heads/{branch}"
            )
            pr_command = (
                f"gh pr create --repo {self.policy.repository} "
                f"--base {self.policy.base_branch} --head {branch} "
                f"--title {json.dumps('fix: resolve GitHub issue #' + str(issue.number))} "
                f"--body-file {self.state_dir / body_ref}"
            )
            handoff.update({
                "status": "publication_prepared",
                "review_branch": branch,
                "verified_remote_base_sha": remote_base_sha,
                "pr_body_ref": body_ref,
                "human_commands": {
                    "push": push_command,
                    "create_pr": pr_command,
                },
                "updated_at": _now(),
            })
            descriptor = self._write_handoff(issue.number, handoff)
            event = self.writer.emit(
                "candidate.publication.prepared",
                actor="zf-kernel",
                task_id=candidate["task_id"],
                correlation_id=candidate["workflow_run_id"] or None,
                payload={
                    "schema_version": HANDOFF_SCHEMA_VERSION,
                    "source_key": issue.issue_key,
                    "source_revision": candidate["source_revision"],
                    "issue_number": issue.number,
                    "candidate_ref": candidate["candidate_ref"],
                    "candidate_head_sha": candidate["candidate_head_sha"],
                    "candidate_base_sha": candidate["candidate_base_sha"],
                    "delivery_provider": self.policy.provider,
                    "delivery_repository": self.policy.repository,
                    "base_branch": self.policy.base_branch,
                    "review_branch": branch,
                    **descriptor,
                },
            )
            handoff["publication_event_id"] = event.id
            descriptor = self._write_handoff(issue.number, handoff)
            if not self.defer_remote_sync:
                self._reconcile_remote_lifecycle_label(issue, "publication_prepared")
            return {"ok": True, "idempotent": False, **handoff, **descriptor}

    def record_pr(self, issue: IssueMirror, *, url: str) -> dict[str, Any]:
        return self._sync_pr(issue, url=url, first_record=True)

    def refresh_pr(self, issue: IssueMirror) -> dict[str, Any]:
        handoff = self._read_handoff(issue.number)
        url = str((handoff.get("pull_request") or {}).get("url") or "")
        if not url:
            raise CandidateDeliveryError("No pull request has been recorded")
        return self._sync_pr(issue, url=url, first_record=False)

    def _sync_pr(
        self,
        issue: IssueMirror,
        *,
        url: str,
        first_record: bool,
    ) -> dict[str, Any]:
        self._require_enabled()
        repository, number = self._parse_pr_url(url)
        if repository.casefold() != self.policy.repository.casefold():
            raise CandidateDeliveryError("Pull request repository is not the configured delivery target")
        remote = self.pr_reader.read(self.policy.repository, number)
        with locked_path(self._lock_path(issue.number)):
            candidate = self._require_candidate(issue)
            handoff = self._read_handoff(issue.number)
            if handoff.get("status") not in {
                "publication_prepared", "pr_open", "pr_changes_requested",
                "pr_approved", "pr_closed_without_merge", "merged",
            }:
                raise CandidateDeliveryError("Prepare the approved review branch before recording a PR")
            expected = {
                "repository": self.policy.repository.casefold(),
                "head_ref": str(handoff.get("review_branch") or ""),
                "head_sha": candidate["candidate_head_sha"],
                "base_ref": self.policy.base_branch,
                "base_sha": candidate["candidate_base_sha"],
            }
            actual = {
                "repository": str(remote.get("repository") or "").casefold(),
                "head_ref": str(remote.get("head_ref") or ""),
                "head_sha": str(remote.get("head_sha") or ""),
                "base_ref": str(remote.get("base_ref") or ""),
                "base_sha": str(remote.get("base_sha") or ""),
            }
            mismatches = [key for key in expected if expected[key] != actual[key]]
            if mismatches:
                raise CandidateDeliveryError(
                    "Pull request identity mismatch: " + ", ".join(mismatches)
                )
            handoff.update({
                "status": remote["lifecycle"],
                "pull_request": {**remote, "synced_at": _now()},
                "version_context": {
                    **dict(handoff.get("version_context") or {}),
                    "pr_head": str(remote.get("head_sha") or "").lower(),
                    "verification_revision": str(remote.get("head_sha") or "").lower(),
                    "merge_sha": str(remote.get("merge_commit_sha") or "").lower(),
                },
                "updated_at": _now(),
            })
            valid, failures = validate_delivery_version_context(
                handoff["version_context"],
                require_pr=True,
                require_merge=remote["lifecycle"] == "merged",
            )
            if not valid:
                raise CandidateDeliveryError(
                    "Pull request revision binding failed: " + ", ".join(failures)
                )
            descriptor = self._write_handoff(issue.number, handoff)
            event_type = (
                "delivery.merged"
                if remote["lifecycle"] == "merged"
                else "forge.pull_request.recorded"
                if first_record
                else "forge.pull_request.synced"
            )
            event = self.writer.emit(
                event_type,
                actor="github-pr-reader" if not first_record else self.actor,
                task_id=candidate["task_id"],
                correlation_id=candidate["workflow_run_id"] or None,
                payload={
                    "schema_version": HANDOFF_SCHEMA_VERSION,
                    "source_key": issue.issue_key,
                    "source_revision": candidate["source_revision"],
                    "issue_number": issue.number,
                    "candidate_head_sha": candidate["candidate_head_sha"],
                    "candidate_base_sha": candidate["candidate_base_sha"],
                    "pr_head_revision": str(remote.get("head_sha") or "").lower(),
                    "merge_revision": str(remote.get("merge_commit_sha") or "").lower(),
                    "delivery_provider": self.policy.provider,
                    "delivery_repository": self.policy.repository,
                    "pull_request_number": number,
                    "pull_request_url": remote["url"],
                    "base_branch": remote["base_ref"],
                    "head_branch": remote["head_ref"],
                    "status": remote["lifecycle"],
                    "merge_commit_sha": remote["merge_commit_sha"],
                    **descriptor,
                },
            )
            handoff["pull_request_event_id"] = event.id
            descriptor = self._write_handoff(issue.number, handoff)
            if not self.defer_remote_sync:
                self._reconcile_remote_lifecycle_label(issue, str(remote["lifecycle"]))
            return {"ok": True, **handoff, **descriptor}

    def _candidate(self, issue: IssueMirror) -> dict[str, Any] | None:
        events = self.event_log.read_all()
        task_id = ""
        run_id = ""
        source_revision = ""
        evaluated_source_revision = ""
        candidate_source_revision = ""
        candidate_event: ZfEvent | None = None
        judge_event: ZfEvent | None = None
        candidate_identities: tuple[str, ...] = ()
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if (
                event.type == "external_issue.received"
                and str(payload.get("source_key") or "") == issue.issue_key
            ):
                source_revision = str(payload.get("source_revision") or "")
            elif (
                event.type == "external_issue.triage.queued"
                and str(payload.get("source_key") or "") == issue.issue_key
            ):
                task_id = str(event.task_id or "")
                run_id = str(payload.get("workflow_run_id") or "")
                source_revision = str(payload.get("source_revision") or source_revision)
                evaluated_source_revision = str(
                    payload.get("source_code_revision")
                    or payload.get("target_commit")
                    or evaluated_source_revision
                ).lower()
            elif task_id and event.task_id == task_id and event.type == "workflow.invoke.requested":
                if str(payload.get("flow_kind") or "") == "issue":
                    run_id = str(payload.get("workflow_run_id") or run_id)
            elif task_id and event.type == "candidate.ready" and issue_candidate_event_matches(
                event,
                task_id=task_id,
                workflow_run_id=run_id,
                require_candidate_identity=True,
            ):
                candidate_event = event
                candidate_source_revision = source_revision
                judge_event = None
                candidate_identities = tuple(
                    str(payload.get(key) or "").strip()
                    for key in ("pdd_id", "feature_id")
                    if str(payload.get(key) or "").strip()
                )
            elif (
                candidate_event is not None
                and event.type == "judge.passed"
                and issue_candidate_event_matches(
                    event,
                    task_id=task_id,
                    workflow_run_id=run_id,
                    candidate_identities=candidate_identities,
                )
            ):
                judge_event = event
        if candidate_event is None or judge_event is None:
            return None
        payload = candidate_event.payload if isinstance(candidate_event.payload, dict) else {}
        candidate_ref = str(payload.get("candidate_ref") or payload.get("branch") or "")
        base_sha = str(payload.get("candidate_base_commit") or payload.get("base_commit") or "").lower()
        head_sha = str(payload.get("candidate_head_commit") or payload.get("commit") or "").lower()
        if not candidate_ref or not _SHA_RE.fullmatch(base_sha) or not _SHA_RE.fullmatch(head_sha):
            return None
        pdd_id = str(payload.get("pdd_id") or "")
        manifest = self._candidate_manifest(pdd_id)
        changed_paths = sorted(self._collect_paths(manifest))
        commands = sorted(self._collect_commands(manifest))
        judge_payload = judge_event.payload if isinstance(judge_event.payload, dict) else {}
        risks = self._string_list(
            judge_payload.get("unresolved_risks") or judge_payload.get("risks")
        )
        return {
            "task_id": task_id,
            "workflow_run_id": run_id,
            "source_revision": candidate_source_revision,
            "current_source_revision": source_revision,
            "pdd_id": pdd_id,
            "candidate_ref": candidate_ref,
            "candidate_base_sha": base_sha,
            "candidate_head_sha": head_sha,
            "evaluated_source_revision": str(payload.get("evaluated_source_revision") or payload.get("source_code_revision") or payload.get("target_commit") or evaluated_source_revision or base_sha).lower(),
            "fix_based_on": str(payload.get("fix_based_on") or base_sha).lower(),
            "diff_ref": str(payload.get("diff_ref") or f"{base_sha}..{head_sha}"),
            "quality_status": str(payload.get("quality_status") or ""),
            "quality_gates_passed": self._string_list(payload.get("quality_gates_passed")),
            "quality_gates_failed": self._string_list(payload.get("quality_gates_failed")),
            "changed_paths": changed_paths,
            "verification_commands": commands,
            "unresolved_risks": risks,
            "candidate_event_id": candidate_event.id,
            "judge_event_id": judge_event.id,
        }

    def _require_candidate(
        self,
        issue: IssueMirror,
        *,
        expected_candidate_sha: str = "",
        expected_source_revision: str = "",
    ) -> dict[str, Any]:
        candidate = self._candidate(issue)
        if candidate is None:
            raise CandidateDeliveryError("Issue does not have a current verified candidate")
        if expected_candidate_sha and expected_candidate_sha != candidate["candidate_head_sha"]:
            raise CandidateDeliveryError("Candidate changed after the page was loaded")
        if expected_source_revision and expected_source_revision != candidate["source_revision"]:
            raise CandidateDeliveryError("Issue source revision changed after the page was loaded")
        return candidate

    def _base_handoff(self, issue: IssueMirror, candidate: dict[str, Any]) -> dict[str, Any]:
        version_context = {
            "reported_source_revision": candidate["source_revision"],
            "evaluated_against": candidate["evaluated_source_revision"],
            "fix_based_on": candidate["fix_based_on"],
            "candidate_head": candidate["candidate_head_sha"],
            "pr_head": "",
            "verification_revision": "",
            "merge_sha": "",
        }
        return {
            "schema_version": HANDOFF_SCHEMA_VERSION,
            "provider": issue.provider,
            "source_repository": issue.repository,
            "source_key": issue.issue_key,
            "source_revision": candidate["source_revision"],
            "issue_number": issue.number,
            "issue_url": issue.html_url,
            "issue_title": issue.title,
            "task_id": candidate["task_id"],
            "workflow_run_id": candidate["workflow_run_id"],
            "pdd_id": candidate["pdd_id"],
            "candidate_ref": candidate["candidate_ref"],
            "candidate_base_sha": candidate["candidate_base_sha"],
            "candidate_head_sha": candidate["candidate_head_sha"],
            "version_context": version_context,
            "evaluated_source_revision": candidate["evaluated_source_revision"],
            "fix_based_on": candidate["fix_based_on"],
            "diff_ref": candidate["diff_ref"],
            "changed_paths": candidate["changed_paths"],
            "verification_commands": candidate["verification_commands"],
            "quality_gates_passed": candidate["quality_gates_passed"],
            "unresolved_risks": candidate["unresolved_risks"],
            "candidate_event_id": candidate["candidate_event_id"],
            "judge_event_id": candidate["judge_event_id"],
            "delivery": {
                "provider": self.policy.provider,
                "repository": self.policy.repository,
                "remote_url": self.policy.remote_url,
                "base_branch": self.policy.base_branch,
                "merge_strategy": self.policy.merge_strategy,
                "auto_close_issue": self.policy.auto_close_issue,
            },
            "created_at": _now(),
        }

    def _create_review_ref(self, branch: str, candidate_sha: str) -> None:
        self._git("cat-file", "-e", f"{candidate_sha}^{{commit}}")
        ref = f"refs/heads/{branch}"
        existing = self._git_optional("rev-parse", "--verify", ref)
        if existing:
            if existing != candidate_sha:
                raise CandidateDeliveryError("Review branch already points to a different commit")
            return
        result = subprocess.run(
            ["git", "update-ref", ref, candidate_sha, "0" * 40],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise CandidateDeliveryError(result.stderr.strip() or "Unable to create review branch")

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.project_root,
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            raise CandidateDeliveryError(result.stderr.strip() or "Git command failed")
        return result.stdout.strip()

    def _git_optional(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.project_root,
            capture_output=True, text=True, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def _parse_pr_url(self, value: str) -> tuple[str, int]:
        try:
            parsed = urllib.parse.urlparse(str(value or "").strip())
            port = parsed.port
        except ValueError as exc:
            raise CandidateDeliveryError("Enter a valid GitHub pull request URL") from exc
        match = _PR_PATH_RE.fullmatch(parsed.path.rstrip("/"))
        if (
            parsed.scheme != "https" or parsed.hostname != "github.com"
            or port not in {None, 443} or parsed.username is not None
            or parsed.password is not None or match is None
        ):
            raise CandidateDeliveryError("Enter a valid GitHub pull request URL")
        return f"{match.group(1)}/{match.group(2)}", int(match.group(3))

    def _write_pr_body(
        self,
        issue: IssueMirror,
        candidate: dict[str, Any],
        branch: str,
    ) -> str:
        lines = [
            f"## Fix for GitHub Issue #{issue.number}", "",
            f"Source issue: {issue.html_url}",
            f"Candidate: `{candidate['candidate_head_sha']}`",
            f"Base: `{candidate['candidate_base_sha']}`",
            f"Evaluated against: `{candidate['evaluated_source_revision']}`",
            f"Fix based on: `{candidate['fix_based_on']}`",
            f"Review branch: `{branch}`", "", "### Changed paths", "",
        ]
        lines.extend(f"- `{path}`" for path in candidate["changed_paths"] or ["Not reported"])
        lines.extend(["", "### Verification", ""])
        lines.extend(f"- `{command}`" for command in candidate["verification_commands"] or ["See ZaoFu candidate evidence"])
        lines.extend(["", "### Unresolved risks", ""])
        lines.extend(f"- {risk}" for risk in candidate["unresolved_risks"] or ["None reported"])
        lines.extend(["", "This PR was prepared from a locally verified ZaoFu candidate. Merge remains a human-controlled GitHub action.", ""])
        relative = f"artifacts/issue-triage/delivery/github/{issue.number}/pr-body.md"
        atomic_write_text(self.state_dir / relative, "\n".join(lines))
        return relative

    def _handoff_path(self, issue_number: int) -> Path:
        return (
            self.state_dir / "artifacts" / "issue-triage" / "delivery"
            / "github" / str(issue_number) / "handoff.json"
        )

    def _lock_path(self, issue_number: int) -> Path:
        return self.state_dir / "locks" / "issue-candidate-delivery" / str(issue_number)

    def _read_handoff(self, issue_number: int) -> dict[str, Any]:
        path = self._handoff_path(issue_number)
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CandidateDeliveryError("Candidate publication handoff is invalid") from exc
        if not isinstance(value, dict) or value.get("schema_version") != HANDOFF_SCHEMA_VERSION:
            raise CandidateDeliveryError("Candidate publication handoff is invalid")
        return value

    def _write_handoff(self, issue_number: int, value: dict[str, Any]) -> dict[str, str]:
        path = self._handoff_path(issue_number)
        raw = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        atomic_write_text(path, raw)
        return {
            "handoff_ref": str(path.relative_to(self.state_dir)),
            "handoff_digest": _digest(value),
        }

    def _candidate_manifest(self, pdd_id: str) -> dict[str, Any]:
        if not pdd_id:
            return {}
        path = self.state_dir / "candidates" / pdd_id / "manifest.json"
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @classmethod
    def _collect_paths(cls, value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"changed_files", "files_touched"} and isinstance(child, list):
                    found.update(str(item) for item in child if str(item).strip())
                else:
                    found.update(cls._collect_paths(child))
        elif isinstance(value, list):
            for child in value:
                found.update(cls._collect_paths(child))
        return found

    @classmethod
    def _collect_commands(cls, value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            command = value.get("command")
            if isinstance(command, str) and command.strip():
                found.add(command.strip())
            for child in value.values():
                found.update(cls._collect_commands(child))
        elif isinstance(value, list):
            for child in value:
                found.update(cls._collect_commands(child))
        return found

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value] if value.strip() else []
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    def _require_enabled(self) -> None:
        if not self.policy.enabled:
            raise CandidateDeliveryError("Issue candidate delivery is disabled")


__all__ = [
    "CandidateDeliveryError",
    "GitHubPullRequestReader",
    "IssueCandidateDeliveryService",
]
