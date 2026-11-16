"""Read-only manifests for explicitly reviewed, quiescent task host assistance."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from zf.core.task.store import TaskStore
from zf.runtime.project_git_baseline import _file, _relative
from zf.runtime.run_admission import build_run_admission_projection, fold_terminal_run_scope
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import (
    current_task_contract_identity, descriptor_from_payload, hydrate_task_contract_snapshot,
)
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.workflow_git_readiness import git_read
from zf.runtime.workflow_operation import reduce_workflow_operations


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def git(root: Path, *args: str) -> str:
    result = git_read(root, *args)
    if result.returncode:
        raise ValueError(f"Git inspection failed: {result.stderr.strip()}")
    return result.stdout if "-z" in args else result.stdout.strip()


def binding(service, operation_id: str) -> tuple[dict, dict, dict, Path]:
    events = service.writer.event_log.read_all()
    operations = reduce_workflow_operations(events)
    op = operations.get(operation_id, {})
    if op.get("request_ref") and not op.get("task_pipeline_stage"):
        request = hydrate_sidecar_ref(service.state_dir, op["request_ref"]).payload["request"]
        if request.get("output_profile_id") == "candidate-verify":
            from zf.runtime.candidate_host_binding import binding as candidate_binding
            return candidate_binding(service, op, request, events, operations)
    abstained_verify = op.get("task_pipeline_stage") == "verify" and op.get("semantic_verdict") == "abstained"
    if (op.get("status") != "settled" or (op.get("semantic_verdict") not in {"blocked", "failed"} and not abstained_verify)
            or op.get("task_pipeline_stage") not in {"impl", "verify"}
            or not op.get("call_result_admitted_event_id")):
        raise ValueError("host assistance requires a settled admitted blocked task operation")
    if abstained_verify:
        result = hydrate_sidecar_ref(service.state_dir, op.get("admitted_control_result_ref") or {}).payload
        if (result.get("schema_version") != "verification-result.v1"
                or result.get("execution_status") != "failed" or result.get("verdict") != "abstained"
                or any(result.get(k) != op.get(k) for k in (
                    "operation_id", "task_id", "workflow_run_id", "task_map_generation", "operation_generation"))):
            raise ValueError("host assistance requires the admitted failed Verify execution")
    aliases, terminals = fold_terminal_run_scope(events)
    if aliases.get(op["workflow_run_id"], op["workflow_run_id"]) in terminals:
        raise ValueError("terminal Run cannot authorize host assistance")
    run = build_run_admission_projection(events).runs.get(op["workflow_run_id"])
    if run is None or run.status != "running":
        raise ValueError("host assistance requires a running admitted Run; pause is preserved")
    context = task_pipeline_generation_contexts(events).get(op["task_id"], {})
    if any(context.get(k) != op.get(k) for k in ("workflow_run_id", "task_map_generation")):
        raise ValueError("task generation is no longer current")
    if any(other.get("task_id") == op["task_id"] and other.get("operation_id") != operation_id
           and (other.get("status") in {"requested", "reserved", "running", "suspended"}
                or int(other.get("operation_generation") or 0) > int(op["operation_generation"]))
           for other in operations.values()):
        raise ValueError("another operation owns the task")
    request = hydrate_sidecar_ref(service.state_dir, op["request_ref"]).payload["request"]
    identity = request["result_identity"]
    contract = hydrate_task_contract_snapshot(service.state_dir, descriptor_from_payload(identity),
        expected={k: op[k] for k in ("workflow_run_id", "task_id", "task_map_generation")})
    task = TaskStore(service.state_dir / "kanban.json").get(op["task_id"])
    if task is None or any(str(contract.get(k) or "") != str(v or "")
                           for k, v in current_task_contract_identity(task).items()):
        raise ValueError("task contract is no longer current")
    dispatches = [e for e in events if e.type == "task.pipeline.stage.dispatched"
                  and e.payload.get("operation_id") == operation_id]
    if len(dispatches) != 1:
        raise ValueError("task operation lacks a unique dispatch workspace")
    dispatch = dispatches[0].payload
    root = Path(dispatch["workdir"])
    if root.is_symlink() or root.resolve() != root or not root.is_relative_to(service.state_dir.resolve()):
        raise ValueError("workspace must be the dispatched owned path under state_dir")
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("workspace is not the repository root")
    if git(root, "rev-parse", "--path-format=absolute", "--git-common-dir") != git(
            service.project_root, "rev-parse", "--path-format=absolute", "--git-common-dir"):
        raise ValueError("workspace does not share the project Git repository")
    branch = git(root, "symbolic-ref", "--short", "HEAD")
    if branch != identity.get("source_branch") or branch != dispatch.get("source_branch"):
        raise ValueError("workspace branch differs from dispatch authority")
    return op, request, contract, root


def source_files(root: Path) -> list[dict]:
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    return [_file(root, name) for name in sorted(set(names) - {""})]


def dependency_digest(service, root: Path) -> str:
    """Pin declared local dependency bytes, including ignored compiler files."""
    config = service.config.runtime.workdirs
    rows = []
    for name in sorted(set(config.required_paths + config.provision_paths)):
        path = _relative(root, name)
        if not path.exists():
            raise ValueError(f"declared dependency is missing: {name}")
        for item in sorted(path.rglob("*")) if path.is_dir() else [path]:
            relative = str(item.relative_to(root))
            if item.is_symlink():
                if not item.resolve().is_relative_to(path.resolve()):
                    raise ValueError("dependency symlink escapes the reviewed local dependency")
                rows.append((relative, "symlink", os.readlink(item)))
            elif item.is_file():
                rows.append((relative, item.stat().st_mode, hashlib.sha256(item.read_bytes()).hexdigest()))
            elif not item.is_dir():
                raise ValueError("dependency contains a non-regular resource")
    return digest(rows)


def preview(service, *, operation_id: str, kind: str, paths: list[str] | None = None,
            command_id: str = "") -> dict:
    op, request, contract, root = binding(service, operation_id)
    if kind not in {"commit", "verify"}:
        raise ValueError("host action kind must be commit or verify")
    if git(root, "diff", "--cached", "--name-only"):
        raise ValueError("host assistance refuses an existing staged index")
    names = sorted(set(paths or []))
    command = None
    if kind == "commit":
        if op.get("task_pipeline_stage") != "impl" or not names:
            raise ValueError("commit requires explicit Impl paths")
        hooks = Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))
        if (git_read(root, "config", "--get", "core.hooksPath").stdout.strip()
                or any(p.is_file() and os.access(p, os.X_OK) and not p.name.endswith(".sample")
                       for p in hooks.glob("*"))):
            raise ValueError("Git hooks require separate review; this host action does not bypass them")
        scopes = request.get("workdir_write_scopes") or []
        for name in names:
            _relative(root, name)
            if not any(name == p.rstrip("/") or name.startswith(p.rstrip("/") + "/") for p in scopes):
                raise ValueError(f"commit path outside assigned write scope: {name}")
            _file(root, name)
        changed = set(git(root, "diff", "--name-only", "-z").split("\0")) | set(
            git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"))
        if changed - {""} != set(names):
            raise ValueError("explicit paths must equal the entire task source delta")
    else:
        if names or git(root, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError("verification requires a clean committed target and no commit paths")
        commands = [c for c in contract["verification_commands"] if c["command_id"] == command_id]
        if len(commands) != 1:
            raise ValueError("verification command is not in the frozen contract")
        command = commands[0]
        from zf.runtime.verification_commands import verification_command_required_for_stage
        owner = ("candidate_verify" if contract.get("authority_scope") == "candidate" else
                 "impl_self_check" if op["task_pipeline_stage"] == "impl" else "task_verify")
        if not verification_command_required_for_stage(command, verification_owner=owner, task_id=op["task_id"]):
            raise ValueError("verification command belongs to a different stage")
        if owner != "impl_self_check" and request.get("target_commit") != git(root, "rev-parse", "HEAD"):
            raise ValueError("Verify target differs from the frozen target")
    body = {"schema_version": "task-host-manifest.v1", "kind": kind,
            "operation_id": operation_id,
            "identity": {k: op.get(k) for k in ("workflow_run_id", "task_id", "task_map_generation",
                "operation_generation", "active_attempt_id", "lease_id", "dispatch_id", "request_hash")},
            "contract_digest": request["result_identity"]["contract_snapshot_digest"],
            "workdir": str(root), "head": git(root, "rev-parse", "HEAD"),
            "branch": git(root, "rev-parse", "--abbrev-ref", "HEAD"),
            "paths": names, "command": command, "files": source_files(root),
            "dependency_digest": dependency_digest(service, root)}
    return {**body, "manifest_digest": digest(body)}
