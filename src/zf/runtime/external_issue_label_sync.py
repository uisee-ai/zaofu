"""Bounded lifecycle label writes and atomic mirror publication."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
import subprocess
from typing import TYPE_CHECKING, Any, Iterator, Mapping

from zf.core.events import ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.issue_triage.lifecycle_states import REMOTE_LIFECYCLE_STATE_SET
from zf.core.issue_triage.models import IssueMirror
from zf.core.state.locks import FileLock, lock_path_for
from zf.core.state.rotation import list_archives

if TYPE_CHECKING:
    from pathlib import Path
    from zf.runtime.external_issue_ingress import ExternalIssueIngressService
    from zf.runtime.issue_candidate_delivery import IssueCandidateDeliveryService

CANCELLATION_STATES = {
    "external_issue.triage.queued_cancelled": "triage_cancelled",
    "external_issue.fix.queued_cancelled": "fix_cancelled",
}

def _workspace_event_logs(service: ExternalIssueIngressService | IssueCandidateDeliveryService) -> list[Any]:
    """Read event ledgers for every registered project in this workspace.

    Issue mirrors can be opened from a different project than the one that
    owns their workflow.  Label reconciliation must therefore inspect the
    owner's ledger as well as the viewer's ledger.  Project roots are resolved
    through the registry/config resolver; stale registry rows are ignored.
    """

    logs: list[Any] = [service.writer.event_log]
    seen = {str(service.state_dir.resolve())}
    from zf.core.workspace.project_resolver import ProjectResolver
    from zf.core.workspace.registry import WorkspaceRegistry

    registry = WorkspaceRegistry()
    resolver = ProjectResolver(registry)
    configured = getattr(service, "repository", "")
    if not configured:
        delivery = getattr(getattr(service, "config", None), "self_issue", None)
        configured = str(getattr(getattr(delivery, "delivery", None), "repository", "") or "")
    configured = configured.casefold()
    for row in registry.list_projects():
        try:
            context = resolver.resolve(row.project_id).context
            target = context.config.self_issue.targets.get("github")
            project = str(getattr(target, "project", "") or "")
            if not project:
                project = str(getattr(context.config.self_issue.delivery, "repository", "") or "")
            if configured and project and project.casefold() != configured:
                continue
            key = str(context.state_dir.resolve())
            if key in seen or not context.state_dir.exists():
                continue
            seen.add(key)
            logs.append(event_log_from_project(context.state_dir, config=context.config))
        except (OSError, ValueError):
            continue
    return logs


def _relevant_events(log: Any, issue_keys: set[str]) -> list[ZfEvent]:
    """Decode only ledger lines causally related to the mirrored Issues.

    Large workspaces contain hundreds of thousands of unrelated events. The
    lifecycle reducer needs source-key lines first, then the Task/Run-bound
    lines they introduce. Raw JSONL filtering keeps refresh bounded without
    weakening EventLog's decoder/signature validation.
    """
    path = getattr(log, "path", None)
    if path is None:
        return log.read_all()
    files = list(list_archives(path.parent / path.stem, suffix=".jsonl")) + [path]
    if not any(file_path.exists() for file_path in files):
        return log.read_all()
    needles = set(issue_keys)
    selected: dict[str, ZfEvent] = {}
    for _ in range(3):
        changed = False
        if not needles:
            break
        try:
            # Explicitly suppress the filename prefix.  Once a workspace has
            # rotated event logs, rg scans multiple files and otherwise emits
            # ``path:json`` lines; EventLog.decode_line then rejects every
            # line, making a real local queue look like a remote-only label.
            command = ["rg", "-F", "--no-heading", "--no-line-number", "--no-filename"]
            for needle in needles:
                command.extend(("-e", needle))
            command.extend(("--", *(str(path) for path in files)))
            scan = subprocess.run(command, capture_output=True, text=True, check=False)
            raw_lines = scan.stdout.splitlines()
        except (OSError, ValueError):
            raw_lines = []
            for file_path in files:
                try:
                    raw_lines.extend(file_path.read_text(encoding="utf-8").splitlines())
                except (OSError, UnicodeError):
                    continue
        next_needles: set[str] = set()
        for raw in raw_lines:
            try:
                event = log.decode_line(raw)
            except Exception:
                continue
            if event is None or event.id in selected:
                continue
            selected[event.id] = event
            changed = True
            payload = event.payload if isinstance(event.payload, dict) else {}
            for value in (
                event.task_id, payload.get("task_id"), payload.get("parent_task_id"),
                payload.get("owner_task_id"), payload.get("workflow_run_id"),
                payload.get("run_id"), payload.get("trace_id"), event.correlation_id,
            ):
                value = str(value or "")
                if value:
                    next_needles.add(value)
        needles.update(next_needles)
        if not changed:
            break
    return sorted(selected.values(), key=lambda event: event.ts)


def _workspace_lifecycle_targets(
    service: ExternalIssueIngressService | IssueCandidateDeliveryService,
    *, mirrors: list[IssueMirror] | None = None,
) -> tuple[dict[str, tuple[ZfEvent | None, str]], set[str]]:
    """Use the same event projection as Web, never our previous sync outputs."""
    from zf.runtime.issue_lifecycle_projection import _workflow_projection, issue_lifecycle_event_index

    if mirrors is None:
        mirrors = [item for item in service.mirrors.list()
                   if item.repository.casefold() == service.repository.casefold()]
    selected = {}
    recorded = set()
    issue_keys = {mirror.issue_key for mirror in mirrors}
    logs = _workspace_event_logs(service)
    with ThreadPoolExecutor(max_workers=min(4, len(logs) or 1), thread_name_prefix="issue-ledger-scan") as pool:
        scanned = list(pool.map(lambda log: (log, _relevant_events(log, issue_keys)), logs))
    for log, events in scanned:
        indexed = issue_lifecycle_event_index(events, issue_keys)
        by_id = {event.id: event for rows in indexed.values() for event in rows}
        recorded.update(by_id)
        for mirror in mirrors:
            projected = _workflow_projection(indexed.get(mirror.issue_key, []), mirror, prefer_remote=False)
            if not projected or not projected.get("state_event_id"):
                continue
            event = by_id.get(projected["state_event_id"])
            if event is None:
                continue
            # New admission wins over old-run cleanup/late review events.
            timestamp = projected.get("admitted_at") or projected["state_event_at"]
            rank = (bool(projected.get("admitted_at")), datetime.fromisoformat(timestamp).timestamp(), str(log.path))
            prior = selected.get(mirror.issue_key)
            if prior is None or rank > prior[0]:
                selected[mirror.issue_key] = (rank, event, projected["state"], set(projected["observed_states"]))
    targets = {key: (event, state) for key, (_, event, state, _) in selected.items()
               if state in REMOTE_LIFECYCLE_STATE_SET}
    for mirror in mirrors:
        remote = set(mirror.labels) & REMOTE_LIFECYCLE_STATE_SET
        target = targets.get(mirror.issue_key)
        if target and target[1] == "mirrored" and remote - {"open", "mirrored"}:
            # Receiving a mirror alone does not own an already-running or
            # delivered workflow whose events may live outside this workspace.
            del targets[mirror.issue_key]
        elif target and target[0] and remote:
            observed = selected[mirror.issue_key][3] | {"open", "closed", "mirrored"}
            if not remote <= observed and (
                datetime.fromisoformat(target[0].ts).timestamp()
                <= datetime.fromisoformat(mirror.updated_at).timestamp()
            ):
                # A remote state absent from our local causal history and
                # observed after our last transition is not proven stale.
                # Do not roll back a delivery owned outside known ledgers.
                del targets[mirror.issue_key]
        if mirror.issue_key in targets:
            continue
        if len(remote) == 1:
            state = next(iter(remote))
            # Repository state is upstream-owned, not inferred from a Run.
            if state in {"open", "closed"}:
                state = mirror.github_state
            targets[mirror.issue_key] = (None, state)
    return targets, recorded


@contextmanager
def try_intake_lock(path: Path) -> Iterator[bool]:
    """Only acquisition contention is busy; errors inside the body still fail."""
    lock = FileLock(lock_path_for(path), timeout_seconds=0)
    try:
        lock.__enter__()
    except TimeoutError:
        yield False
        return
    try:
        yield True
    finally:
        lock.__exit__(None, None, None)


def reconcile_lifecycle_labels(
    service: ExternalIssueIngressService, requested: ZfEvent | None = None,
) -> Mapping[str, Any]:
    """Converge all lifecycle states under the caller's controlled intake lock.

    Network workers only call the provider. Confirmed labels and colors are
    published once by the main thread; failed writes remain retryable.
    """
    if service.lifecycle_label_writer is None:
        return {"status": "disabled", "reason": "lifecycle_label_writer_missing"}
    targets, recorded = _workspace_lifecycle_targets(service)
    if requested is not None:
        key = str(requested.payload.get("source_key") or "")
        if requested.type not in CANCELLATION_STATES or not key:
            return {"status": "disabled", "reason": "cancellation_source_missing"}
        if requested.id not in recorded:
            return {"status": "disabled", "reason": "cancellation_not_recorded"}
        target = targets.get(key)
        if not target or not target[0] or target[0].id != requested.id:
            return {"status": "superseded", "reason": "newer_issue_admission"}
        targets = {key: target}

    with try_intake_lock(service.mirrors.refresh_lock_path) as acquired:
        if not acquired:
            return {"status": "syncing", "reason": "mirror_refresh_in_progress"}
        mirrors = {item.issue_key: item for item in service.mirrors.list()}
        jobs = [(mirrors[key], event, state) for key, (event, state) in targets.items()
                if key in mirrors and (
                    set(mirrors[key].labels) & REMOTE_LIFECYCLE_STATE_SET != {state}
                    or not mirrors[key].label_colors.get(state)
                )]
        if requested is not None and not jobs:
            return {"status": "reconciled", "reason": "unchanged"}

        def write(job):
            mirror, _, desired = job
            try:
                return service.lifecycle_label_writer(mirror, desired)
            except Exception as exc:
                return {"status": "outcome_unknown", "reason": type(exc).__name__}

        if not jobs:
            return {"status": "reconciled", "reason": "no_pending_lifecycle_labels"}
        with ThreadPoolExecutor(max_workers=min(4, len(jobs)), thread_name_prefix="issue-label-sync") as pool:
            results = list(pool.map(write, jobs))
        # A different project can admit or advance a Run while the provider
        # request is in flight. Do not publish that stale response locally;
        # leave an auditable pending write for the next reconciliation pass.
        current_targets, _ = _workspace_lifecycle_targets(service)
        projections: dict[str, tuple[str, dict[str, str]]] = {}
        for index, ((mirror, event, desired), result) in enumerate(zip(jobs, results)):
            current = current_targets.get(mirror.issue_key)
            if current != (event, desired):
                result = {"status": "reconcile_pending", "reason": "lifecycle_changed_during_sync"}
                results[index] = result
            service._reconcile_remote_lifecycle_label(
                mirror, desired,
                revision=str(event.payload.get("source_revision") or "") if event else "",
                causation_id=event.id if event else "",
                write_result=result, projection_batch=projections,
            )
        service.mirrors.project_lifecycle_labels(projections)
        if requested is not None:
            return results[0]
        failed = sum(result.get("status") != "reconciled" for result in results)
        return {"status": "reconcile_pending" if failed else "reconciled", "pending": failed}


# Compatibility for the existing controlled cancellation action.
reconcile_cancellations = reconcile_lifecycle_labels
