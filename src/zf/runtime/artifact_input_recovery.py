"""Current-input recovery projections; history never proves current model memory."""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.artifact_access import artifact_access_allowed
from zf.runtime.artifact_input_index import input_index, load_attempt_source_manifest
from zf.runtime.artifact_input_lifecycle import input_lifecycle
from zf.runtime.artifact_read_ledger import required_read_satisfied
from zf.runtime.artifact_read_requirements import required_reads_for_attempt
from zf.runtime.cli_command import zf_cli_cmd
from zf.runtime.recovery_attempt_facts import recovery_attempt_facts
from zf.runtime.recovery_checkpoint import maintenance_checkpoint_facts


def build_input_recovery(
    *, state_dir: Path, manifest: dict[str, Any], identity: Mapping[str, str],
    events: list, required: list[dict[str, Any]], reads: list[dict[str, Any]],
    checkpoint: dict[str, Any] | None = None, cursor: str | None = None,
) -> dict[str, Any]:
    """Called only after credential authorization or canonical runtime binding."""
    allowed = [(s["source_id"], s["artifact_id"], s["sha256"], s.get("allowed_paths") or ["$"])
               for s in manifest.get("sources", []) if artifact_access_allowed(s.get("access_scope"),
                    actor=identity.get("actor", ""), role=identity.get("role", ""), purpose=identity.get("purpose", ""))]
    visible = [row for row in required if any(row.get("source_id") == sid and row.get("artifact_id") == aid
               and (not row.get("artifact_sha256") or row["artifact_sha256"] == digest)
               and ("$" in paths or row.get("json_path", "$") in paths)
               for sid, aid, digest, paths in allowed)]
    own_reads = [r for r in reads if r.get("consumer_actor") == identity.get("actor")
                 and r.get("consumer_role") == identity.get("role")
                 and r.get("consumer_purpose") == identity.get("purpose")
                 and r.get("attempt_id") == manifest.get("attempt_id")
                 and all(not identity.get(key) or r.get(key) == identity[key]
                         for key in ("operation_id", "provider_session_id"))]
    missing = sum(not required_read_satisfied(own_reads, row) for row in visible)
    lifecycle = input_lifecycle(manifest, events)
    prefix = f"{zf_cli_cmd()} artifact"
    attempt = shlex.quote(str(manifest["attempt_id"]))
    result = {
        "schema_version": "attempt-input-recovery.v1", "status": "rebind_required" if lifecycle["requires_rebind"] else "bound",
        "authorizes_execution": False, "current_memory": "unknown", "history_scope": "this_consumer_only",
        "identity": {key: manifest.get(key, "") for key in (
            "workflow_run_id", "task_id", "attempt_id", "dispatch_id", "contract_revision", "task_map_generation", "target_commit")},
        "required_visible": len(visible), "historical_missing": missing,
        "reconstruction_required": len(visible), "checkpoint": checkpoint or {"status": "unavailable", "authorizes_continuation": False},
        "commands": {"index": f"{prefix} index --attempt {attempt}",
                     "recover": f"{prefix} recover --attempt {attempt}"},
        "instructions": "History and checkpoints do not prove current memory. Revalidate identity, workdir and lease; "
                        "consume all required bodies needed after compaction/session change. Use --resume only while current-session material remains available.",
    }
    from zf.runtime.work_record import current_work_record
    result["work_record"] = current_work_record(state_dir, events, manifest, identity.get("actor", ""))
    if result["work_record"].get("status") == "bound":
        op = shlex.quote(str(result["work_record"]["operation_id"]))
        result["commands"]["work_record_read"] = f"{prefix} work-record show --operation {op}"
    if not lifecycle["requires_rebind"]:
        result["commands"]["required_read"] = f"{prefix} read --attempt {attempt} --required --reason recovery"
    result["next_action"] = "request_controlled_rebind" if lifecycle["requires_rebind"] else "reconstruct_required_inputs"
    # The embedded index stays paginated; the recovery response itself, including
    # checkpoint metadata and command envelope, has the same byte budget.
    for limit in range(4, 0, -1):
        result["inputs"] = input_index(state_dir=state_dir, manifest=manifest, required=required, identity=dict(identity), reads=own_reads,
            cursor=cursor, limit=limit, lifecycle=lifecycle)
        result["next_cursor"] = result["inputs"]["next_cursor"]
        if len(json.dumps(result, ensure_ascii=False).encode()) + 1 <= 8192:
            return result
    raise ValueError("recovery metadata exceeds output bound")


def current_task_input_recovery(state_dir: Path, events: list, task: Any, *, actor: str = "") -> dict[str, Any]:
    """Resolve actual dispatch facts for recovery callers without issuing authority."""
    from zf.runtime.artifact_query import ArtifactQueryService
    from zf.runtime.artifact_read_liveness import live_attempt_ids
    from zf.runtime.task_run_binding import canonical_task_run
    from zf.runtime.workflow_operation import TERMINAL_OPERATION_STATUSES, reduce_workflow_operations

    unavailable = {"schema_version": "attempt-input-recovery.v1", "status": "unavailable",
                   "authorizes_execution": False, "current_memory": "unknown"}
    if task is None or not task.active_dispatch_id or not task.assigned_to or (actor and actor != task.assigned_to):
        return unavailable
    run_id, _ = canonical_task_run(task, events)
    if not run_id:
        return unavailable
    facts = recovery_attempt_facts(state_dir, task, run_id=run_id)
    if facts["status"] != "bound":
        return unavailable
    current = facts["identity"]
    operation = reduce_workflow_operations(events).get(str(current.get("operation_id") or ""), {})
    attempt = str(operation.get("active_attempt_id") or "")
    if (not attempt or operation.get("status") in TERMINAL_OPERATION_STATUSES
            or operation.get("task_id") != task.id or operation.get("workflow_run_id") != run_id
            or operation.get("role_instance") != task.assigned_to):
        return unavailable
    import re
    if (re.sub(r"[^A-Za-z0-9._-]+", "-", attempt).strip("-._") or "attempt") not in live_attempt_ids(events):
        return unavailable
    try:
        manifest = load_attempt_source_manifest(state_dir, attempt)
        expected = {"task_id": task.id, "workflow_run_id": run_id, "dispatch_id": task.active_dispatch_id}
        if any(manifest.get(key) != value for key, value in expected.items()):
            return unavailable
        required = required_reads_for_attempt(events, attempt, state_dir=state_dir,
            operation_id=str(current.get("operation_id") or ""))
        query = ArtifactQueryService(state_dir=state_dir, project_root=state_dir.parent)
        return build_input_recovery(state_dir=state_dir, manifest=manifest,
            identity={"actor": task.assigned_to, "role": str(current.get("role") or ""),
                      "purpose": str(manifest.get("read_purpose") or "")},
            events=events, required=required, reads=query._read_rows(attempt),
            checkpoint=maintenance_checkpoint_facts(state_dir, events, task_id=task.id, attempt_facts=facts))
    except (OSError, ValueError, KeyError):
        return unavailable


def render_input_recovery(value: Mapping[str, Any]) -> str:
    if value.get("status") == "unavailable":
        return "## Input recovery\nCurrent input binding unavailable; request controlled recovery, never reuse an old attempt's commands.\n"
    return "## Input recovery (projection, not execution authority)\n```json\n" + json.dumps(value, ensure_ascii=False) + "\n```\n"
