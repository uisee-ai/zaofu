"""Operator-reviewed host effects; never confer a task or product verdict."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from collections.abc import Callable

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.environment_exec import resource_locks
from zf.runtime.environment_resources import environment_lock_plan
from zf.runtime.provider_turn_liveness import active_codex_turn_from_events
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json, write_sidecar_text
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.task_host_manifest import binding, dependency_digest, digest, git, preview, source_files
from zf.runtime.task_host_quiescence import unrelated_task_turn_isolated
from zf.runtime.workflow_git_readiness import git_environment

ACTION = "task-host-assist"


def _commit(root: Path, manifest: dict, *,
            source_snapshot: Callable[[Path], list[dict]] | None = None) -> dict:
    # Only internal repair publication supplies a base-bound delta snapshot.
    # External manifests cannot select or disable the default full-source gate.
    snapshot = source_snapshot or source_files
    paths = manifest["paths"]
    # No alternate index, forced add, hook bypass, amendment, merge or push.
    def run(*args):
        result = subprocess.run(["git", "-C", str(root), *args], env=git_environment(),
                                capture_output=True, text=True, timeout=60, check=False)
        if result.returncode:
            raise ValueError("reviewed Git operation failed: " + result.stderr.strip())
    run("add", "--", *paths)
    if set(git(root, "diff", "--cached", "--name-only", "-z").split("\0")) - {""} != set(paths):
        raise ValueError("staged paths changed; preserve index for operator inspection")
    if snapshot(root) != manifest["files"] or git(root, "rev-parse", "HEAD") != manifest["head"]:
        raise ValueError("source changed during staging; preserve index for operator inspection")
    run("commit", "-m", "feat: publish reviewed task implementation", "-m",
        "Host-Assist-Manifest: " + manifest["manifest_digest"], "--only", "--", *paths)
    head = git(root, "rev-parse", "HEAD")
    if (git(root, "rev-parse", "HEAD^") != manifest["head"]
            or snapshot(root) != manifest["files"]
            or git(root, "status", "--porcelain", "--untracked-files=all")):
        raise ValueError("Git postcondition failed; inspect committed effect before recovery")
    return {"status": "completed", "source_commit": head, "paths": paths}


def _verify(root: Path, manifest: dict) -> tuple[dict, str]:
    command = manifest["command"]
    timeout = int(command.get("timeout_seconds") or 0)
    if not 0 < timeout <= 900:
        raise ValueError("host probe requires a declared timeout of 1..900 seconds")
    with tempfile.TemporaryDirectory(prefix="zf-host-probe-") as temporary:
        # Operator credentials and provider configuration never enter the probe.
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": temporary,
               "TMPDIR": temporary, "LANG": "C.UTF-8"}
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(["/bin/sh", "-c", command["command"]], cwd=root,
                env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                code = 124
            output.seek(0)
            text = output.read().decode("utf-8", errors="replace")
    stable = (git(root, "rev-parse", "HEAD") == manifest["head"]
              and source_files(root) == manifest["files"]
              and not git(root, "status", "--porcelain", "--untracked-files=all"))
    return {"status": "completed" if code == 0 and stable else "failed",
            "exit_code": code, "source_stable": stable, "target_commit": manifest["head"],
            "command_id": command["command_id"], "command": command["command"],
            "command_digest": command["command_digest"],
            "execution_origin": "operator_authorized_host"}, text


def apply(service, manifest: dict, *, approval_ref: str, reviewed_digest: str, verifier=None,
          readonly_snapshot: bool = False, recollection_base: dict | None = None) -> dict:
    if not approval_ref.strip() or reviewed_digest != manifest.get("manifest_digest"):
        raise ValueError("explicit approval_ref and exact reviewed manifest digest required")
    body = {k: v for k, v in manifest.items() if k != "manifest_digest"}
    if digest(body) != reviewed_digest or manifest.get("schema_version") != "task-host-manifest.v1":
        raise ValueError("host manifest digest or schema is invalid")
    if readonly_snapshot and (manifest["kind"] != "verify" or verifier is None):
        raise ValueError("read-only snapshot requires an internal snapshot verifier")
    state = service.state_dir
    with locked_path(state / "locks" / "task-host-assist"):
        prior = [e for e in service.writer.event_log.read_all()
                 if e.payload.get("action") == ACTION and e.payload.get("manifest_digest") == reviewed_digest]
        completed = [e for e in prior if e.type == "runtime.action.attempt.completed"]
        if completed:
            receipt = completed[-1].payload["receipt_ref"]
            return {"status": "already_recorded", "receipt_ref": receipt,
                    "result": hydrate_sidecar_ref(state, receipt).payload}
        if any(e.type == "runtime.action.attempt.started" for e in prior):
            raise ValueError("prior host effect is unresolved; inspect it, do not rerun")
        op, _, _, root = binding(service, manifest["operation_id"])
        events = service.writer.event_log.read_all()
        turn = active_codex_turn_from_events(events, str(op.get("role_instance") or ""))
        if turn and not unrelated_task_turn_isolated(service, op, root, turn, events):
            raise BlockingIOError("host assistance requires a closed provider turn")
        # Publication reads dependencies; isolated snapshots also only read the
        # host workspace. Neither needs to evict idle provider environment readers.
        mode = fcntl.LOCK_SH if manifest["kind"] == "commit" or readonly_snapshot else fcntl.LOCK_EX
        with resource_locks(environment_lock_plan(root), mode):
            current = preview(service, operation_id=manifest["operation_id"], kind=manifest["kind"],
                              paths=manifest["paths"], command_id=(manifest.get("command") or {}).get("command_id", ""))
            if recollection_base is not None:
                from zf.runtime.host_probe_recollection import recollection_manifest
                from zf.runtime.pause_lifecycle import is_dispatch_paused
                if is_dispatch_paused(state, events=events):
                    raise ValueError("probe recollection cannot bypass dispatch pause")
                policy = service.config.runtime.host_verification
                checked = recollection_manifest(service, current, events, policy)
                if (not readonly_snapshot or verifier is None or current != recollection_base
                        or checked != manifest):
                    raise ValueError("independent probe recollection is no longer authorized")
            elif current != manifest:
                raise ValueError("host manifest is stale; review a new preview")
            fields = {"action": ACTION, "manifest_digest": reviewed_digest,
                      "operation_id": manifest["operation_id"], **manifest["identity"],
                      "approval_ref": approval_ref, "kind": manifest["kind"]}
            fields['manifest_ref'] = write_immutable_json_sidecar(state, manifest,
                root='task-host-manifests', kind='task_host_manifest',
                schema_version='task-host-manifest.v1', created_by=service.actor)
            if recollection_base is not None:
                fields.update(recollection_of_start_id=manifest['recollection_of_start_id'],
                              original_manifest_digest=manifest['original_manifest_digest'])
                if manifest.get('recollection_of_receipt_id'):
                    fields['recollection_of_receipt_id'] = manifest['recollection_of_receipt_id']
            start = service.writer.append(ZfEvent(
                type="runtime.action.attempt.started", origin="kernel", actor=service.actor,
                task_id=fields["task_id"], correlation_id=fields["workflow_run_id"], payload=fields,
            ))
            prefix = f"artifacts/task-host-actions/{reviewed_digest}"
            try:
                log_ref = None
                if manifest["kind"] == "commit":
                    result = _commit(root, manifest)
                else:
                    result, output = (verifier or _verify)(root, manifest)
                    if dependency_digest(service, root) != manifest["dependency_digest"]:
                        result.update(status="failed", source_stable=False)
                    log_ref = write_sidecar_text(state, prefix + "/output.txt", output,
                        kind="host_probe_output", schema_version="host-probe-output.v1",
                        created_by=service.actor, source_event_id=start.id, required=True)
                receipt = {"schema_version": "task-host-receipt.v1", **fields, **result,
                           "manifest": manifest, "output_ref": log_ref,
                           "product_verdict": "not_evaluated"}
                descriptor = write_sidecar_json(state, prefix + "/receipt.json", receipt,
                    kind="task_host_receipt", schema_version="task-host-receipt.v1",
                    created_by=service.actor, source_event_id=start.id, required=True)
                service.writer.append(ZfEvent(
                    type="runtime.action.attempt.completed", origin="kernel", actor=service.actor,
                    task_id=fields["task_id"], correlation_id=fields["workflow_run_id"], causation_id=start.id,
                    payload={**fields, "status": result["status"], "receipt_ref": descriptor},
                ))
                return {"status": result["status"], "receipt_ref": descriptor, "result": receipt}
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                service.writer.append(ZfEvent(
                    type="runtime.action.attempt.failed", origin="kernel", actor=service.actor,
                    task_id=fields["task_id"], causation_id=start.id, correlation_id=fields["workflow_run_id"],
                    payload={**fields, "reason": str(exc), "effect_status": "inspect_before_retry"},
                ))
                raise ValueError(f"host effect requires inspection before retry: {exc}") from exc


def execute_action(service, *, requested, action, requested_action, payload):
    try:
        result = apply(service, payload["manifest"], approval_ref=payload.get("approval_ref", ""),
                       reviewed_digest=payload.get("reviewed_digest", ""))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return service._failed(requested=requested, action=action, requested_action=requested_action,
            task_id=None, reason=str(exc), status_code=409, status="host_assistance_blocked")
    service._completed(requested=requested, event=requested, action=action, requested_action=requested_action,
        task_id=payload["manifest"]["identity"]["task_id"], status=result["status"],
        extra={"receipt_ref": result["receipt_ref"]})
    return {"ok": True, "action": action, **result}
