"""Incremental, per-Task Candidate integration for Task Pipeline v4."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.locks import locked_path
from zf.core.verification.evidence import command_evidence
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidates import (
    CandidateRebuilder,
    CandidateResult,
    _candidate_reportable_status,
)
from zf.runtime.git_capture import git_env
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_pipeline_identity import task_pipeline_operation_identity
from zf.runtime.verification_commands import task_contract_verification_commands
from zf.runtime.workflow_operation import WorkflowOperationService


TASK_INTEGRATION_RECEIPT_SCHEMA = "task-integration-receipt.v1"
ROLLING_SMOKE_RECEIPT_SCHEMA = "rolling-smoke-command-receipt.v1"


def _rolling_smoke_command(command: str, worktree: Path) -> tuple[str, Path]:
    """Run npm ``--prefix`` commands from the prefix directory.

    npm's ``--prefix`` changes package resolution but does not change the
    process cwd. Commands recorded in task contracts are written from the
    repository root, so tools such as TypeScript would otherwise search the
    wrong directory for their config. Evidence retains the original command;
    execution removes the prefix flag after changing cwd.
    """

    try:
        tokens = shlex.split(str(command))
    except ValueError:
        return str(command), worktree
    if len(tokens) < 4 or tokens[0] != "npm":
        return str(command), worktree
    if tokens[1] == "--prefix" and len(tokens) > 3:
        prefix = tokens[2]
        rest = tokens[3:]
    elif tokens[1].startswith("--prefix="):
        prefix = tokens[1].split("=", 1)[1]
        rest = tokens[2:]
    else:
        return str(command), worktree
    if not prefix or not rest:
        return str(command), worktree
    prefix_path = Path(prefix)
    execution_root = prefix_path if prefix_path.is_absolute() else worktree / prefix_path
    try:
        execution_root = execution_root.resolve()
        execution_root.relative_to(worktree.resolve())
    except (OSError, ValueError):
        return str(command), worktree
    if not execution_root.is_dir():
        return str(command), worktree
    return " ".join(shlex.quote(item) for item in ("npm", *rest)), execution_root


def _rolling_smoke_environment_error(
    command: str,
    execution_root: Path,
) -> str | None:
    """Return a stable setup error before invoking a package manager.

    A candidate worktree without dependencies is an environment failure, not
    a product verification failure.  Keeping this distinction in the error
    makes Run Manager able to request setup/redrive instead of semantic
    rework.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or tokens[0] != "npm":
        return None
    if (execution_root / "package.json").is_file() and not (
        execution_root / "node_modules"
    ).is_dir():
        return f"candidate_environment_not_ready: dependencies missing at {execution_root}"
    return None


class CandidateIncrementalError(RuntimeError):
    """The per-Task integration contract could not be admitted."""


def hydrate_task_integration_receipt(
    rebuilder: CandidateRebuilder,
    descriptor: Mapping[str, Any],
    *,
    task_id: str = "",
    workflow_run_id: str = "",
    task_map_generation: str = "",
) -> dict[str, Any]:
    """Hydrate and currentness-check one immutable integration receipt."""

    body = hydrate_sidecar_ref(
        rebuilder.state_dir,
        dict(descriptor),
    ).payload
    if not isinstance(body, Mapping):
        raise CandidateIncrementalError("integration receipt is not an object")
    receipt = dict(body)
    required = (
        "workflow_run_id",
        "task_id",
        "task_map_generation",
        "pipeline_key",
        "integration_operation_id",
        "task_ref",
        "task_commit",
        "contract_revision",
        "candidate_generation",
        "expected_candidate_head",
        "new_candidate_head",
        "patch_identity",
        "command_registry_digest",
    )
    if any(not str(receipt.get(key) or "").strip() for key in required):
        raise CandidateIncrementalError("integration receipt is incomplete")
    expected = {
        "task_id": task_id,
        "workflow_run_id": workflow_run_id,
        "task_map_generation": task_map_generation,
    }
    for key, value in expected.items():
        if value and str(receipt.get(key) or "") != value:
            raise CandidateIncrementalError(
                f"integration receipt currentness mismatch: {key}"
            )
    index = rebuilder._task_index().get(str(receipt["task_id"]), {})
    if (
        not isinstance(index, Mapping)
        or str(index.get("task_ref") or "") != str(receipt["task_ref"])
        or str(index.get("source_commit") or "") != str(receipt["task_commit"])
    ):
        raise CandidateIncrementalError("integration receipt TaskRef is stale")
    refs = receipt.get("rolling_smoke_receipt_refs")
    if not isinstance(refs, list) or not refs:
        raise CandidateIncrementalError(
            "integration receipt has no rolling smoke evidence"
        )
    for item in refs:
        if not isinstance(item, Mapping):
            raise CandidateIncrementalError("rolling smoke receipt ref is invalid")
        hydrate_sidecar_ref(rebuilder.state_dir, dict(item))
    branch = str(receipt.get("candidate_branch") or "")
    if not branch:
        raise CandidateIncrementalError("integration receipt candidate branch missing")
    current = _resolve_commit(rebuilder.project_root, f"refs/heads/{branch}")
    integrated_head = str(receipt["new_candidate_head"])
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", integrated_head, current],
        cwd=rebuilder.project_root,
        capture_output=True,
        check=False,
        env=git_env(),
    )
    if ancestor.returncode != 0:
        raise CandidateIncrementalError(
            "integration receipt candidate head is not current ancestry"
        )
    return receipt


def integrate_task_candidate(
    rebuilder: CandidateRebuilder,
    *,
    task_id: str,
    workflow_run_id: str,
    task_map_generation: str,
    operation_generation: int,
    pipeline_key: str,
    dispatch_base_commit: str,
    contract_revision: str,
    event_writer: EventWriter,
    causation_id: str = "",
) -> CandidateResult:
    """Integrate one exact TaskRef under the existing Candidate lock/CAS."""

    pdd_id = rebuilder.pdd_id_for_task(task_id)
    branch = f"{rebuilder.config.runtime.git.candidate_branch_prefix}/{pdd_id}"
    base_commit = _resolve_commit(rebuilder.project_root, dispatch_base_commit)
    live_head, branch_exists = _candidate_head(
        rebuilder.project_root,
        branch=branch,
        base_commit=base_commit,
    )
    identity = task_pipeline_operation_identity(
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        task_map_generation=task_map_generation,
        stage="integration",
        stage_revision=TASK_INTEGRATION_RECEIPT_SCHEMA,
        operation_generation=operation_generation,
    )
    replay = _integrated_event(rebuilder, identity.operation_id)
    if replay is not None:
        replay_payload = dict(replay.payload)
        descriptor = replay_payload.get("receipt_ref")
        if not isinstance(descriptor, Mapping):
            raise CandidateIncrementalError(
                "integrated event has no immutable receipt ref"
            )
        receipt = hydrate_sidecar_ref(
            rebuilder.state_dir,
            dict(descriptor),
        ).payload
        if (
            not isinstance(receipt, Mapping)
            or str(receipt.get("integration_operation_id") or "")
            != identity.operation_id
            or _resolve_commit(rebuilder.project_root, f"refs/heads/{branch}")
            != str(receipt.get("new_candidate_head") or "")
        ):
            raise CandidateIncrementalError(
                "replayed integration receipt is not current"
            )
        return CandidateResult(
            status="integrated",
            event_type="integration.queue.integrated",
            payload=replay_payload,
        )
    request = {
        "workflow_run_id": workflow_run_id,
        "task_id": task_id,
        "task_pipeline_stage": "integration",
        "operation_generation": operation_generation,
        "task_map_generation": task_map_generation,
        "pipeline_key": pipeline_key or identity.pipeline_key,
        "candidate_generation": _candidate_generation(
            workflow_run_id,
            task_map_generation,
        ),
        "candidate_branch": branch,
        "expected_candidate_head": live_head,
        "dispatch_base_commit": base_commit,
        "contract_revision": contract_revision,
        "integration_strategy": rebuilder.config.runtime.git.candidate_strategy,
        "partial_candidate_auto_ship": "forbidden",
    }
    request = _pinned_integration_request(
        rebuilder,
        operation_id=identity.operation_id,
        current_request=request,
    )
    expected_head = _resolve_commit(
        rebuilder.project_root,
        str(request["expected_candidate_head"]),
    )
    operations = WorkflowOperationService(
        state_dir=rebuilder.state_dir,
        event_log=rebuilder.event_log,
        event_writer=event_writer,
    )
    ensured = operations.ensure_operation(
        workflow_run_id=workflow_run_id,
        operation_id=identity.operation_id,
        operation_type="task-stage",
        request=request,
        parent_stage_id="integration",
        task_id=task_id,
        role_instance="candidate-integrator",
        causation_id=causation_id,
        correlation_id=workflow_run_id,
    )
    if ensured.status == "settled":
        replay = _integrated_event(rebuilder, identity.operation_id)
        if replay is None:
            raise CandidateIncrementalError(
                "settled integration operation has no admitted receipt event"
            )
        return CandidateResult(
            status="integrated",
            event_type="integration.queue.integrated",
            payload=dict(replay.payload),
        )
    if ensured.status in {"blocked", "failed", "cancelled", "superseded"}:
        raise CandidateIncrementalError(
            f"integration operation is terminal: {ensured.status}"
        )
    if ensured.status == "divergent":
        raise CandidateIncrementalError("integration operation request diverged")

    _emit_once(
        rebuilder,
        event_writer,
        "task.integration_enqueued",
        operation_id=identity.operation_id,
        task_id=task_id,
        payload={
            **request,
            "operation_id": identity.operation_id,
            "operation_key": identity.operation_key,
            "queue_entry_id": identity.operation_id,
            "source_ref": _task_ref(rebuilder, task_id),
            "base_ref": expected_head,
        },
        causation_id=causation_id,
    )
    operations.mark_started(
        operation_id=identity.operation_id,
        request_hash=ensured.request_hash,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        dispatch_id=identity.operation_id,
        role_instance="candidate-integrator",
        active_attempt_id=identity.operation_id,
        lease_id=identity.operation_id,
        causation_id=causation_id,
        correlation_id=workflow_run_id,
    )
    integrating = _emit_once(
        rebuilder,
        event_writer,
        "integration.queue.integrating",
        operation_id=identity.operation_id,
        task_id=task_id,
        payload={
            **request,
            "operation_id": identity.operation_id,
            "operation_key": identity.operation_key,
            "queue_entry_id": identity.operation_id,
        },
        causation_id=causation_id,
    )

    try:
        with locked_path(rebuilder._manifest_path(pdd_id)):
            receipt, receipt_ref = _integrate_locked(
                rebuilder,
                task_id=task_id,
                workflow_run_id=workflow_run_id,
                task_map_generation=task_map_generation,
                operation_generation=operation_generation,
                operation_id=identity.operation_id,
                pipeline_key=pipeline_key or identity.pipeline_key,
                candidate_generation=request["candidate_generation"],
                branch=branch,
                branch_exists=branch_exists,
                expected_head=expected_head,
                base_commit=base_commit,
                contract_revision=contract_revision,
                source_event_id=integrating.id if integrating is not None else causation_id,
            )
    except Exception as exc:
        operations.block(
            operation_id=identity.operation_id,
            request_hash=ensured.request_hash,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            reason=f"candidate_incremental_failed:{type(exc).__name__}",
            details={"detail": str(exc)[:500]},
            causation_id=integrating.id if integrating is not None else causation_id,
            correlation_id=workflow_run_id,
        )
        failed = _emit_once(
            rebuilder,
            event_writer,
            "integration.queue.needs_review",
            operation_id=identity.operation_id,
            task_id=task_id,
            payload={
                **request,
                "operation_id": identity.operation_id,
                "queue_entry_id": identity.operation_id,
                "reason": f"{type(exc).__name__}: {exc}"[:500],
            },
            causation_id=integrating.id if integrating is not None else causation_id,
        )
        return CandidateResult(
            status="needs_review",
            event_type="integration.queue.needs_review",
            payload=dict(failed.payload) if failed is not None else {},
        )

    settled = operations.settle(
        operation_id=identity.operation_id,
        request_hash=ensured.request_hash,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        admitted_call_result_ref=receipt_ref,
        causation_id=integrating.id if integrating is not None else causation_id,
        correlation_id=workflow_run_id,
    )
    payload = {
        "schema_version": TASK_INTEGRATION_RECEIPT_SCHEMA,
        "workflow_run_id": workflow_run_id,
        "task_id": task_id,
        "task_map_generation": task_map_generation,
        "pipeline_key": pipeline_key or identity.pipeline_key,
        "operation_id": identity.operation_id,
        "operation_generation": operation_generation,
        "queue_entry_id": identity.operation_id,
        "candidate_generation": request["candidate_generation"],
        "candidate_branch": branch,
        "candidate_head": receipt["new_candidate_head"],
        "task_ref": receipt["task_ref"],
        "task_commit": receipt["task_commit"],
        "receipt_ref": receipt_ref,
        "receipt_digest": str(receipt_ref.get("sha256") or ""),
        "status": "integrated",
    }
    integrated = _emit_once(
        rebuilder,
        event_writer,
        "integration.queue.integrated",
        operation_id=identity.operation_id,
        task_id=task_id,
        payload=payload,
        causation_id=settled.id if settled is not None else (
            integrating.id if integrating is not None else causation_id
        ),
    )
    _emit_once(
        rebuilder,
        event_writer,
        "candidate.updated",
        operation_id=identity.operation_id,
        task_id=task_id,
        payload={
            **payload,
            "incremental": True,
            "integrated_task_id": task_id,
        },
        causation_id=integrated.id if integrated is not None else causation_id,
    )
    return CandidateResult(
        status="integrated",
        event_type="integration.queue.integrated",
        payload=payload,
    )


def _integrate_locked(
    rebuilder: CandidateRebuilder,
    *,
    task_id: str,
    workflow_run_id: str,
    task_map_generation: str,
    operation_generation: int,
    operation_id: str,
    pipeline_key: str,
    candidate_generation: str,
    branch: str,
    branch_exists: bool,
    expected_head: str,
    base_commit: str,
    contract_revision: str,
    source_event_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_head, current_exists = _candidate_head(
        rebuilder.project_root,
        branch=branch,
        base_commit=base_commit,
    )
    reconciled_head = expected_head
    advancement_receipt_refs: list[dict[str, Any]] = []
    if current_head != expected_head or current_exists != branch_exists:
        advancement_receipt_refs = _candidate_advancement_receipt_refs(
            rebuilder,
            workflow_run_id=workflow_run_id,
            task_map_generation=task_map_generation,
            candidate_generation=candidate_generation,
            branch=branch,
            expected_head=expected_head,
            current_head=current_head,
        )
        if not advancement_receipt_refs:
            raise CandidateIncrementalError("candidate_head_cas_mismatch")
        reconciled_head = current_head
        branch_exists = current_exists
    tasks = rebuilder.tasks_from_index(
        rebuilder.pdd_id_for_task(task_id),
        [task_id],
    )
    if len(tasks) != 1:
        raise CandidateIncrementalError("exact TaskRef is missing from task index")
    task_ref = tasks[0]
    stale = rebuilder._stale_task_refs(tasks)
    if stale:
        raise CandidateIncrementalError("TaskRef currentness check failed")
    if rebuilder.config.runtime.git.candidate_strategy != "cherry-pick":
        raise CandidateIncrementalError("v4 incremental integration requires cherry-pick")

    (
        integration_base_head,
        successor_base_adopted,
        supersedes_task_ids,
        adopted_base_commits,
    ) = _select_integration_base(
        rebuilder,
        task_id=task_id,
        task_source_commit=task_ref.source_commit,
        expected_candidate_head=reconciled_head,
        dispatch_base_commit=base_commit,
    )
    pdd_id = rebuilder.pdd_id_for_task(task_id)
    rebuilder._prepare_worktree(pdd_id, integration_base_head)
    worktree = rebuilder._worktree_path(pdd_id)
    declared_files = rebuilder._candidate_task_scope_files(
        integration_base_head,
        task_ref,
    )
    commits, skipped_commits = rebuilder._task_commits(
        integration_base_head,
        task_ref,
        declared_files=declared_files,
    )
    applied_commits: list[str] = []
    for commit in commits:
        status = rebuilder._apply_task_commit(
            worktree,
            commit,
            declared_files=declared_files,
        )
        if status == "applied":
            applied_commits.append(commit)
        else:
            skipped_commits.append(commit)
    new_head = _resolve_commit(worktree, "HEAD")
    environment = rebuilder._prepare_candidate_environment(
        worktree=worktree,
        commit=new_head,
    )
    if environment.get("status") != "ready":
        raise CandidateIncrementalError(
            f"candidate environment failed: {environment.get('detail')}"
        )
    smoke_refs, registry_digest = _run_rolling_smoke(
        rebuilder,
        task_id=task_id,
        worktree=worktree,
        target_commit=new_head,
        source_event_id=source_event_id,
    )
    dirty = _git(rebuilder.project_root, "-C", str(worktree), "status", "--porcelain")
    if _candidate_reportable_status(dirty):
        raise CandidateIncrementalError("rolling smoke left candidate worktree dirty")
    _update_candidate_ref(
        rebuilder.project_root,
        branch=branch,
        new_head=new_head,
        expected_head=reconciled_head,
        branch_exists=branch_exists,
    )
    patch_identity = _patch_identity(
        rebuilder.project_root,
        commits or [task_ref.source_commit],
    )
    receipt = {
        "schema_version": TASK_INTEGRATION_RECEIPT_SCHEMA,
        "workflow_run_id": workflow_run_id,
        "task_id": task_id,
        "task_map_generation": task_map_generation,
        "pipeline_key": pipeline_key,
        "integration_operation_id": operation_id,
        "operation_generation": operation_generation,
        "task_ref": task_ref.task_ref,
        "task_commit": task_ref.source_commit,
        "contract_revision": contract_revision,
        "candidate_generation": candidate_generation,
        "candidate_branch": branch,
        "expected_candidate_head": expected_head,
        "previous_candidate_head": reconciled_head,
        "candidate_head_reconciled": reconciled_head != expected_head,
        "candidate_advancement_receipt_refs": advancement_receipt_refs,
        "integration_base_head": integration_base_head,
        "successor_base_adopted": successor_base_adopted,
        "supersedes_task_ids": supersedes_task_ids,
        "adopted_base_commits": adopted_base_commits,
        "new_candidate_head": new_head,
        "integration_strategy": "cherry-pick",
        "patch_identity": patch_identity,
        "applied_commits": applied_commits,
        "skipped_commits": skipped_commits,
        "rolling_smoke_receipt_refs": smoke_refs,
        "command_registry_digest": registry_digest,
        "candidate_environment": environment,
        "status": "integrated",
    }
    descriptor = write_immutable_json_sidecar(
        rebuilder.state_dir,
        receipt,
        root="task-integration-receipts",
        kind="task_integration_receipt",
        schema_version=TASK_INTEGRATION_RECEIPT_SCHEMA,
        created_by="candidate-integrator",
        source_event_id=source_event_id,
    )
    _validate_receipt(
        rebuilder,
        descriptor,
        expected=receipt,
        branch=branch,
    )
    return receipt, descriptor


def _run_rolling_smoke(
    rebuilder: CandidateRebuilder,
    *,
    task_id: str,
    worktree: Path,
    target_commit: str,
    source_event_id: str,
) -> tuple[list[dict[str, Any]], str]:
    task = rebuilder.task_store.get(task_id)
    commands = (
        task_contract_verification_commands(task.contract)
        if task is not None
        else []
    )
    registry_digest = _digest(commands)
    selected = [
        command
        for command in commands
        if command.get("rolling_smoke") is True
    ]
    if not selected:
        raise CandidateIncrementalError("rolling_smoke_command_missing")
    refs: list[dict[str, Any]] = []
    from zf.runtime.candidates import _quality_gate_env

    for command in selected:
        started = time.monotonic()
        timeout = max(1, min(int(command.get("timeout_seconds") or 900), 3600))
        execution_command, execution_root = _rolling_smoke_command(
            str(command["command"]), worktree,
        )
        environment_error = _rolling_smoke_environment_error(
            str(command["command"]), execution_root,
        )
        if environment_error:
            raise CandidateIncrementalError(environment_error)
        try:
            result = subprocess.run(
                execution_command,
                shell=True,
                cwd=execution_root,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=_quality_gate_env(worktree),
            )
            evidence = command_evidence(
                command=str(command["command"]),
                exit_code=result.returncode,
                stdout=(result.stdout or "")[-100_000:],
                stderr=(result.stderr or "")[-100_000:],
            )
        except subprocess.TimeoutExpired as exc:
            evidence = command_evidence(
                command=str(command["command"]),
                exit_code=None,
                stdout=str(exc.stdout or "")[-100_000:],
                stderr=str(exc.stderr or "")[-100_000:],
                timed_out=True,
            )
        evidence.update({
            "schema_version": ROLLING_SMOKE_RECEIPT_SCHEMA,
            "command_id": str(command["id"]),
            "command_digest": str(command["command_digest"]),
            "command_registry_digest": registry_digest,
            "execution_root": str(execution_root),
            "target_commit": target_commit,
            "duration_ms": int((time.monotonic() - started) * 1000),
        })
        descriptor = write_immutable_json_sidecar(
            rebuilder.state_dir,
            evidence,
            root="rolling-smoke-receipts",
            kind="rolling_smoke_command_receipt",
            schema_version=ROLLING_SMOKE_RECEIPT_SCHEMA,
            created_by="candidate-integrator",
            source_event_id=source_event_id,
        )
        refs.append(descriptor)
        if evidence.get("exit_code") != 0:
            raise CandidateIncrementalError(
                f"rolling smoke failed: {command['id']}"
            )
    return refs, registry_digest


def _validate_receipt(
    rebuilder: CandidateRebuilder,
    descriptor: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
    branch: str,
) -> None:
    body = hydrate_task_integration_receipt(rebuilder, descriptor)
    if dict(body) != dict(expected):
        raise CandidateIncrementalError("integration receipt content diverged")
    current = _resolve_commit(rebuilder.project_root, f"refs/heads/{branch}")
    if current != str(body.get("new_candidate_head") or ""):
        raise CandidateIncrementalError("integration receipt candidate head is stale")


def _emit_once(
    rebuilder: CandidateRebuilder,
    event_writer: EventWriter,
    event_type: str,
    *,
    operation_id: str,
    task_id: str,
    payload: Mapping[str, Any],
    causation_id: str,
) -> ZfEvent | None:
    for event in reversed(rebuilder.event_log.read_all()):
        if event.type != event_type or event.task_id != task_id:
            continue
        body = event.payload if isinstance(event.payload, dict) else {}
        if str(body.get("operation_id") or "") == operation_id:
            return event
    return event_writer.append(ZfEvent(
        type=event_type,
        actor="zf-cli",
        origin="kernel",
        task_id=task_id,
        payload=dict(payload),
        causation_id=causation_id or None,
        correlation_id=str(payload.get("workflow_run_id") or "") or None,
    ))


def _integrated_event(
    rebuilder: CandidateRebuilder,
    operation_id: str,
) -> ZfEvent | None:
    for event in reversed(rebuilder.event_log.read_all()):
        if event.type != "integration.queue.integrated":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("operation_id") or "") == operation_id:
            return event
    return None


def _task_ref(rebuilder: CandidateRebuilder, task_id: str) -> str:
    entry = rebuilder._task_index().get(task_id, {})
    return str(entry.get("task_ref") or "") if isinstance(entry, dict) else ""


def _candidate_head(
    project_root: Path,
    *,
    branch: str,
    base_commit: str,
) -> tuple[str, bool]:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        env=git_env(),
    )
    if result.returncode == 0:
        return result.stdout.strip(), True
    return base_commit, False


def _pinned_integration_request(
    rebuilder: CandidateRebuilder,
    *,
    operation_id: str,
    current_request: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep a blocked operation's immutable request identity on redrive."""

    for event in reversed(rebuilder.event_log.read_all()):
        if event.type != "workflow.operation.requested":
            continue
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        if str(payload.get("operation_id") or "") != operation_id:
            continue
        descriptor = payload.get("request_ref")
        if not isinstance(descriptor, Mapping):
            return dict(current_request)
        try:
            body = hydrate_sidecar_ref(
                rebuilder.state_dir,
                dict(descriptor),
            ).payload
        except (OSError, TypeError, ValueError):
            return dict(current_request)
        persisted = body.get("request") if isinstance(body, Mapping) else None
        if not isinstance(persisted, Mapping):
            return dict(current_request)
        stable_fields = set(current_request) - {"expected_candidate_head"}
        if any(
            persisted.get(field) != current_request.get(field)
            for field in stable_fields
        ):
            return dict(current_request)
        expected_head = str(persisted.get("expected_candidate_head") or "")
        if not expected_head:
            return dict(current_request)
        try:
            _resolve_commit(rebuilder.project_root, expected_head)
        except CandidateIncrementalError:
            return dict(current_request)
        return dict(persisted)
    return dict(current_request)


def _candidate_advancement_receipt_refs(
    rebuilder: CandidateRebuilder,
    *,
    workflow_run_id: str,
    task_map_generation: str,
    candidate_generation: str,
    branch: str,
    expected_head: str,
    current_head: str,
) -> list[dict[str, Any]]:
    """Prove a stale CAS head advanced only through admitted integrations."""

    if (
        not expected_head
        or expected_head == current_head
        or not _commit_is_ancestor(
            rebuilder.project_root,
            ancestor=expected_head,
            descendant=current_head,
        )
    ):
        return []
    edges: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for event in rebuilder.event_log.read_all():
        if event.type != "candidate.updated" or event.origin != "kernel":
            continue
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        descriptor = payload.get("receipt_ref")
        if (
            str(payload.get("workflow_run_id") or "") != workflow_run_id
            or str(payload.get("task_map_generation") or "")
            != task_map_generation
            or str(payload.get("candidate_generation") or "")
            != candidate_generation
            or str(payload.get("candidate_branch") or "") != branch
            or not isinstance(descriptor, Mapping)
        ):
            continue
        try:
            receipt = hydrate_task_integration_receipt(
                rebuilder,
                dict(descriptor),
                task_id=str(event.task_id or ""),
                workflow_run_id=workflow_run_id,
                task_map_generation=task_map_generation,
            )
        except (CandidateIncrementalError, OSError, TypeError, ValueError):
            continue
        previous = str(
            receipt.get("previous_candidate_head")
            or receipt.get("expected_candidate_head")
            or ""
        )
        new_head = str(receipt.get("new_candidate_head") or "")
        if any((
            not previous,
            not new_head,
            previous == new_head,
            str(payload.get("candidate_head") or "") != new_head,
            str(payload.get("operation_id") or "")
            != str(receipt.get("integration_operation_id") or ""),
            not _commit_is_ancestor(
                rebuilder.project_root,
                ancestor=previous,
                descendant=new_head,
            ),
        )):
            continue
        edges.setdefault(previous, []).append((new_head, dict(descriptor)))

    cursor = expected_head
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    while cursor != current_head and cursor not in seen:
        seen.add(cursor)
        candidates = {
            (new_head, str(ref.get("sha256") or "")): ref
            for new_head, ref in edges.get(cursor, [])
            if new_head == current_head
            or _commit_is_ancestor(
                rebuilder.project_root,
                ancestor=new_head,
                descendant=current_head,
            )
        }
        if len(candidates) != 1:
            return []
        (next_head, _digest), descriptor = next(iter(candidates.items()))
        refs.append(descriptor)
        cursor = next_head
    return refs if cursor == current_head else []


def _select_integration_base(
    rebuilder: CandidateRebuilder,
    *,
    task_id: str,
    task_source_commit: str,
    expected_candidate_head: str,
    dispatch_base_commit: str,
) -> tuple[str, bool, list[str], list[str]]:
    """Choose a pinned base without silently discarding candidate ancestry."""

    if _commit_is_ancestor(
        rebuilder.project_root,
        ancestor=dispatch_base_commit,
        descendant=expected_candidate_head,
    ):
        return expected_candidate_head, False, [], []
    if not _commit_is_ancestor(
        rebuilder.project_root,
        ancestor=expected_candidate_head,
        descendant=dispatch_base_commit,
    ):
        raise CandidateIncrementalError(
            "dispatch_base_commit diverges from current candidate head"
        )

    task = rebuilder.task_store.get(task_id)
    evidence = (
        task.contract.evidence_contract
        if task is not None and isinstance(task.contract.evidence_contract, dict)
        else {}
    )
    supersedes_task_ids = list(dict.fromkeys(
        str(item).strip()
        for item in evidence.get("supersedes_task_ids", [])
        if str(item).strip()
    )) if isinstance(evidence.get("supersedes_task_ids"), list) else []
    expected_source_ref = f"git:{dispatch_base_commit}"
    task_successor_authorized = bool(
        supersedes_task_ids
        and _successor_contract_binds_base(
            rebuilder,
            task_id=task_id,
            expected_source_ref=expected_source_ref,
            supersedes_task_ids=supersedes_task_ids,
        )
    )
    generation_continuation_authorized = _generation_continuation_binds_base(
        rebuilder,
        task_id=task_id,
        expected_candidate_head=expected_candidate_head,
        dispatch_base_commit=dispatch_base_commit,
    )
    if not task_successor_authorized and not generation_continuation_authorized:
        raise CandidateIncrementalError(
            "newer dispatch_base_commit requires a successor contract bound "
            f"to {expected_source_ref} or an admitted generation continuation"
        )
    if not _commit_is_ancestor(
        rebuilder.project_root,
        ancestor=dispatch_base_commit,
        descendant=task_source_commit,
    ):
        raise CandidateIncrementalError(
            "successor TaskRef does not descend from dispatch_base_commit"
        )
    adopted_base_commits = [
        item
        for item in _git(
            rebuilder.project_root,
            "rev-list",
            "--reverse",
            f"{expected_candidate_head}..{dispatch_base_commit}",
        ).splitlines()
        if item
    ]
    if not adopted_base_commits:
        raise CandidateIncrementalError(
            "successor base adoption produced no immutable commit range"
        )
    return (
        dispatch_base_commit,
        True,
        supersedes_task_ids,
        adopted_base_commits,
    )


def _generation_continuation_binds_base(
    rebuilder: CandidateRebuilder,
    *,
    task_id: str,
    expected_candidate_head: str,
    dispatch_base_commit: str,
) -> bool:
    """Verify a generation-wide continuation without task supersession.

    Plan amendments can advance the shared writer base for every Task while
    preserving Task identities.  Accept that base only when the immutable
    package, Task Map, generation admission, and original candidate target all
    agree.  A bare descendant commit remains unauthorized.
    """

    task = rebuilder.task_store.get(task_id)
    if task is None or not isinstance(task.contract.evidence_contract, dict):
        return False
    evidence = task.contract.evidence_contract
    source_refs = evidence.get("source_refs")
    if not isinstance(source_refs, dict):
        return False
    workflow_run_id = str(evidence.get("workflow_run_id") or "").strip()
    task_map_generation = str(
        source_refs.get("task_map_generation") or ""
    ).strip()
    task_map_ref = str(source_refs.get("task_map_ref") or "").strip()
    package_ref = str(
        source_refs.get("plan_artifact_package_ref") or ""
    ).strip()
    package_digest = str(
        source_refs.get("plan_artifact_package_digest") or ""
    ).strip()
    if not all((
        workflow_run_id,
        task_map_generation,
        task_map_ref,
        package_ref,
        package_digest,
        expected_candidate_head,
        dispatch_base_commit,
    )):
        return False

    try:
        from zf.runtime.plan_artifact_package import (
            validate_plan_artifact_package_shape,
        )
        from zf.runtime.task_pipeline_contexts import (
            task_pipeline_generation_contexts,
        )

        package = hydrate_sidecar_ref(
            rebuilder.state_dir,
            {"ref": package_ref, "sha256": package_digest},
        ).payload
        if not isinstance(package, Mapping):
            return False
        validate_plan_artifact_package_shape(package)
        if (
            str(package.get("workflow_run_id") or "") != workflow_run_id
            or str(package.get("task_map_generation") or "")
            != task_map_generation
        ):
            return False
        prior_ref = str(package.get("supersedes_package_ref") or "").strip()
        prior_digest = str(
            package.get("supersedes_package_digest") or ""
        ).strip()
        if not prior_ref or not prior_digest:
            return False
        prior_package = hydrate_sidecar_ref(
            rebuilder.state_dir,
            {"ref": prior_ref, "sha256": prior_digest},
        ).payload
        if not isinstance(prior_package, Mapping):
            return False
        validate_plan_artifact_package_shape(prior_package)
        if str(prior_package.get("workflow_run_id") or "") != workflow_run_id:
            return False

        task_map_port = next(
            (
                port
                for port in [
                    *package.get("produced", []),
                    *package.get("inherited", []),
                ]
                if isinstance(port, Mapping)
                and str(port.get("logical_name") or "") == "task_map"
                and str(port.get("ref") or "") == task_map_ref
                and str(port.get("sha256") or "")
            ),
            None,
        )
        if task_map_port is None:
            return False
        task_map = hydrate_sidecar_ref(
            rebuilder.state_dir,
            {
                "ref": task_map_ref,
                "sha256": str(task_map_port["sha256"]),
            },
        ).payload
        if not isinstance(task_map, Mapping):
            return False
        metadata = task_map.get("metadata")
        if not isinstance(metadata, Mapping) or str(
            metadata.get("writer_dispatch_base_commit") or ""
        ).strip() != dispatch_base_commit:
            return False
        raw_task = next(
            (
                item
                for key in ("tasks", "task_items", "items")
                for item in task_map.get(key, [])
                if isinstance(item, Mapping)
                and str(item.get("task_id") or item.get("id") or "") == task_id
            ),
            None,
        )
        if raw_task is None:
            return False

        events = rebuilder.event_log.read_all()
        context = task_pipeline_generation_contexts(events).get(task_id)
        if context is None or any((
            str(context.get("workflow_run_id") or "") != workflow_run_id,
            str(context.get("task_map_generation") or "")
            != task_map_generation,
            str(context.get("task_map_ref") or "") != task_map_ref,
            str(context.get("plan_artifact_package_ref") or "") != package_ref,
            str(context.get("plan_artifact_package_digest") or "")
            != package_digest,
            str(context.get("dispatch_base_commit") or "")
            != dispatch_base_commit,
        )):
            return False
        generation_event_id = str(
            context.get("generation_admitted_event_id") or ""
        )
        generation_event = next(
            (event for event in events if event.id == generation_event_id),
            None,
        )
        if generation_event is None or generation_event.origin != "kernel":
            return False
        trigger_event_id = str(context.get("trigger_event_id") or "")
        trigger = next(
            (event for event in events if event.id == trigger_event_id),
            None,
        )
        if trigger is None or trigger.type != "task_map.ready":
            return False
        trigger_payload = (
            trigger.payload if isinstance(trigger.payload, Mapping) else {}
        )
        if any((
            str(trigger_payload.get("task_map_generation") or "")
            != task_map_generation,
            str(trigger_payload.get("plan_artifact_package_ref") or "")
            != package_ref,
            str(trigger_payload.get("plan_artifact_package_digest") or "")
            != package_digest,
        )):
            return False
        target_ref = str(
            trigger_payload.get("target_ref")
            or trigger_payload.get("candidate_base_commit")
            or trigger_payload.get("source_commit")
            or ""
        ).strip()
        return bool(
            target_ref
            and _resolve_commit(rebuilder.project_root, target_ref)
            == expected_candidate_head
        )
    except (CandidateIncrementalError, OSError, TypeError, ValueError):
        return False


def _successor_contract_binds_base(
    rebuilder: CandidateRebuilder,
    *,
    task_id: str,
    expected_source_ref: str,
    supersedes_task_ids: list[str],
) -> bool:
    """Verify direct or legacy plan-package-backed successor provenance."""

    task = rebuilder.task_store.get(task_id)
    if task is None:
        return False
    if str(task.contract.source_ref or "").strip() == expected_source_ref:
        return True

    evidence = task.contract.evidence_contract
    if not isinstance(evidence, dict):
        return False
    source_refs = evidence.get("source_refs")
    if not isinstance(source_refs, dict):
        return False
    package_ref = str(source_refs.get("plan_artifact_package_ref") or "").strip()
    package_digest = str(
        source_refs.get("plan_artifact_package_digest") or ""
    ).strip()
    task_map_ref = str(source_refs.get("task_map_ref") or "").strip()
    task_map_generation = str(source_refs.get("task_map_generation") or "").strip()
    if not package_ref or not package_digest or not task_map_ref:
        return False

    try:
        from zf.runtime.plan_artifact_package import (
            validate_plan_artifact_package_shape,
        )
        from zf.runtime.task_map_successor import (
            successor_base_errors,
            task_map_successor_base_commit,
            task_map_supersedes_task_ids,
        )

        package = hydrate_sidecar_ref(
            rebuilder.state_dir,
            {"ref": package_ref, "sha256": package_digest},
        ).payload
        if not isinstance(package, Mapping):
            return False
        validate_plan_artifact_package_shape(package)
        if task_map_generation and str(
            package.get("task_map_generation") or ""
        ) != task_map_generation:
            return False
        workflow_run_id = str(evidence.get("workflow_run_id") or "").strip()
        if workflow_run_id and str(
            package.get("workflow_run_id") or ""
        ) != workflow_run_id:
            return False
        task_map_port = next(
            (
                port
                for port in [
                    *package.get("produced", []),
                    *package.get("inherited", []),
                ]
                if isinstance(port, Mapping)
                and str(port.get("logical_name") or "") == "task_map"
                and str(port.get("ref") or "") == task_map_ref
                and str(port.get("sha256") or "")
            ),
            None,
        )
        if task_map_port is None:
            return False
        task_map = hydrate_sidecar_ref(
            rebuilder.state_dir,
            {
                "ref": task_map_ref,
                "sha256": str(task_map_port["sha256"]),
            },
        ).payload
        if not isinstance(task_map, Mapping):
            return False
        raw_task = next(
            (
                item
                for key in ("tasks", "task_items", "items")
                for item in task_map.get(key, [])
                if isinstance(item, dict)
                and str(item.get("task_id") or item.get("id") or "") == task_id
            ),
            None,
        )
        if raw_task is None or successor_base_errors(raw_task, task_id=task_id):
            return False
        expected_commit = expected_source_ref.removeprefix("git:")
        return (
            task_map_successor_base_commit(raw_task) == expected_commit
            and task_map_supersedes_task_ids(raw_task) == supersedes_task_ids
        )
    except (OSError, TypeError, ValueError):
        return False


def _commit_is_ancestor(
    project_root: Path,
    *,
    ancestor: str,
    descendant: str,
) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        env=git_env(),
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = result.stderr.strip() or result.stdout.strip()
    raise CandidateIncrementalError(
        f"git merge-base ancestry check failed: {detail}"
    )


def _update_candidate_ref(
    project_root: Path,
    *,
    branch: str,
    new_head: str,
    expected_head: str,
    branch_exists: bool,
) -> None:
    expected_ref = expected_head if branch_exists else "0" * 40
    result = subprocess.run(
        ["git", "update-ref", f"refs/heads/{branch}", new_head, expected_ref],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        env=git_env(),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CandidateIncrementalError(f"candidate_head_cas_mismatch: {detail}")


def _patch_identity(project_root: Path, commits: list[str]) -> str:
    patch_ids: list[str] = []
    for commit in commits:
        shown = subprocess.run(
            ["git", "show", "--pretty=format:", "--binary", commit],
            cwd=project_root,
            capture_output=True,
            check=False,
            env=git_env(),
        )
        if shown.returncode != 0:
            continue
        result = subprocess.run(
            ["git", "patch-id", "--stable"],
            cwd=project_root,
            input=shown.stdout,
            capture_output=True,
            check=False,
            env=git_env(),
        )
        if result.returncode == 0 and result.stdout.strip():
            patch_ids.append(result.stdout.decode("utf-8").split()[0])
    return _digest(patch_ids or commits)


def _resolve_commit(cwd: Path, ref: str) -> str:
    value = _git(cwd, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if not value:
        raise CandidateIncrementalError(f"git ref is missing: {ref}")
    return value


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=git_env(),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CandidateIncrementalError(
            f"git {' '.join(args)} failed: {detail}"
        )
    return result.stdout.strip()


def _candidate_generation(workflow_run_id: str, task_map_generation: str) -> str:
    return "cg-" + hashlib.sha256(
        f"{workflow_run_id}|{task_map_generation}".encode("utf-8")
    ).hexdigest()[:16]


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CandidateIncrementalError",
    "ROLLING_SMOKE_RECEIPT_SCHEMA",
    "TASK_INTEGRATION_RECEIPT_SCHEMA",
    "hydrate_task_integration_receipt",
    "integrate_task_candidate",
]
