"""Current-input recovery projections; history never proves current model memory."""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.artifact_access import artifact_access_allowed
from zf.runtime.artifact_input_index import input_index, load_attempt_source_manifest
from zf.runtime.artifact_input_lifecycle import input_lifecycle
from zf.runtime.artifact_read_briefing_policy import plan_read_page_bytes
from zf.runtime.artifact_read_ledger import required_read_satisfied
from zf.runtime.artifact_read_requirements import required_reads_for_attempt
from zf.runtime.cli_command import zf_cli_cmd
from zf.runtime.recovery_attempt_facts import recovery_attempt_facts
from zf.runtime.recovery_checkpoint import maintenance_checkpoint_facts


def _legacy_operation_binding(manifest: Mapping[str, Any], identity: Mapping[str, str], events: list) -> str:
    """Bind old operation-less history only through a unique canonical attempt."""
    from zf.runtime.workflow_operation import reduce_workflow_operations

    operations = [op for op in reduce_workflow_operations(events).values()
                  if op.get("active_attempt_id") == manifest.get("attempt_id")]
    if len(operations) != 1:
        return ""
    operation = operations[0]
    if (operation.get("operation_id") != identity.get("operation_id")
            or operation.get("role_instance") != identity.get("actor")
            or any(not manifest.get(key) or operation.get(key) != manifest[key]
                   for key in ("task_id", "workflow_run_id", "dispatch_id"))):
        return ""
    return str(operation["operation_id"])


def _history_operation_matches(row: Mapping[str, Any], manifest: Mapping[str, Any],
                               identity: Mapping[str, str], legacy_operation: str) -> bool:
    expected = identity.get("operation_id")
    if not expected or row.get("operation_id") == expected:
        return True
    # This is a read-only history projection, never a current-session witness.
    return bool(not row.get("operation_id") and legacy_operation == expected
                and all(manifest.get(key) and row.get(key) == manifest[key]
                        for key in ("workflow_run_id", "task_id", "dispatch_id")))


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
    legacy_operation = (_legacy_operation_binding(manifest, identity, events)
                        if identity.get("operation_id") and any(not r.get("operation_id") for r in reads) else "")
    own_reads = [r for r in reads if r.get("consumer_actor") == identity.get("actor")
                 and r.get("consumer_role") == identity.get("role")
                 and r.get("consumer_purpose") == identity.get("purpose")
                 and r.get("attempt_id") == manifest.get("attempt_id")
                 and (not identity.get("provider") or r.get("consumer_provider") == identity["provider"])
                 and _history_operation_matches(r, manifest, identity, legacy_operation)
                 and (not identity.get("provider_session_id")
                      or r.get("provider_session_id") == identity["provider_session_id"])]
    missing = sum(not required_read_satisfied(own_reads, row) for row in visible)
    lifecycle = input_lifecycle(manifest, events, state_dir=state_dir)
    prefix = f"{zf_cli_cmd()} artifact"
    attempt = shlex.quote(str(manifest["attempt_id"]))
    result = {
        "schema_version": "attempt-input-recovery.v1", "status": "rebind_required" if lifecycle["requires_rebind"] else "bound",
        "authorizes_execution": False, "current_memory": "unknown", "history_scope": "this_consumer_only",
        "identity": {key: manifest.get(key, "") for key in (
            "workflow_run_id", "task_id", "attempt_id", "dispatch_id", "contract_revision", "task_map_generation", "target_commit")},
        "required_visible": len(visible), "historical_missing": missing,
        "required_scope_count": len(visible), "checkpoint": checkpoint or {"status": "unavailable", "authorizes_continuation": False},
        "commands": {"index": f"{prefix} index --attempt {attempt}",
                     "recover": f"{prefix} recover --attempt {attempt}"},
        "instructions": "This report does not revoke existing execution authority and grants none. "
                        "Dispatch, identity, workdir, lease and runtime gates still govern execution. "
                        "current_memory stays unknown; required_scope_count is scope, historical_missing is "
                        "this consumer's read-gap count. Do not wait for memory/permission fields to change. "
                        "Rebind when required. Otherwise read a bound work record if available, consume missing "
                        "required bodies, then retrieve exact forgotten clauses through the index and continue. "
                        "History/checkpoints never prove current understanding. Zero historical_missing calls "
                        "for assessing semantic context gaps, not a full reset. Compaction or absent WRC alone "
                        "does not require repeating all inputs or completed checks. Deliver needed bodies fully "
                        "without truncation. Use --resume only while current-session material remains available.",
        "recovery_exit": {"after_complete_delivery": "continue_current_step",
                          "requires_current_execution_authority": True,
                          "requires_complete_untruncated_delivery": True,
                          "missing_environment_or_unknown_effect": "wait_for_specific_dependency"},
    }
    from zf.runtime.work_record import current_work_record
    result["work_record"] = current_work_record(state_dir, events, manifest, identity.get("actor", ""))
    if (manifest.get("handoff_authority_contract") or {}).get("operation_type") == "fanout_reader_child":
        from zf.runtime.reader_work_record import reader_checkpoint_entry
        entry = reader_checkpoint_entry(state_dir, events, manifest, identity.get("actor", ""))
        if entry:
            result["work_record_publish"] = entry
            from zf.runtime.task_handoff import predecessor_work_record
            try:
                binding = {key: str(manifest.get(key) or "") for key in ("workflow_run_id", "task_id")}
                from zf.runtime.workflow_operation import reduce_workflow_operations
                operation = next(op for op in reduce_workflow_operations(events).values()
                    if op.get("active_attempt_id") == manifest["attempt_id"] and op.get("role_instance") == identity.get("actor"))
                binding["operation_id"] = operation["operation_id"]
                predecessor_work_record(state_dir, manifest, binding)
                result["commands"]["predecessor_work_record_read"] = (
                    f"{prefix} work-record show --operation {shlex.quote(binding['operation_id'])} --predecessor")
            except (OSError, ValueError, KeyError, StopIteration):
                pass
    if result["work_record"].get("status") == "bound":
        op = shlex.quote(str(result["work_record"]["operation_id"]))
        result["commands"]["work_record_read"] = f"{prefix} work-record show --operation {op}"
    if not lifecycle["requires_rebind"]:
        if missing:
            command = f"{prefix} read --attempt {attempt} --required --resume --reason recovery"
            page_bytes = plan_read_page_bytes(manifest.get("read_purpose"), manifest.get("handoff_authority_contract"))
            if page_bytes:
                command += f" --format text --page-bytes {page_bytes}"
                result["instructions"] += (
                    " Return each full tool output to the model through ZF_ARTIFACT_TEXT_PAGE_END, including in loops. "
                    "Allow 16384 output tokens or reduce --page-bytes to 2048. Retry clipped pages at the same cursor. "
                    "For the next page keep the format and size, replacing --resume with the returned --cursor."
                )
            result["commands"]["required_read"] = command
        from zf.runtime.task_handoff_receipt import handoff_receipt_projection

        handoff_sources = [s for s in manifest.get("sources", []) if s.get("source_id") == "task-handoff"]
        if handoff_sources:
            receipt = handoff_receipt_projection(state_dir, events, str(manifest.get("task_id") or ""))
            if receipt.get("handoff_ref", {}).get("sha256") == handoff_sources[0].get("sha256"):
                result["handoff_receipt"] = {key: receipt[key] for key in ("status", "target_operation_id")}
                if receipt["status"] == "awaiting_receipt":
                    operation = shlex.quote(receipt["target_operation_id"])
                    digest = shlex.quote(handoff_sources[0]["sha256"])
                    result["commands"]["handoff_accept_after_read"] = f"{prefix} handoff-accept --operation {operation} --digest {digest}"
    result["next_action"] = ("request_controlled_rebind" if lifecycle["requires_rebind"] else
        "read_missing_required_inputs" if missing else "assess_context_gaps_then_continue")
    from zf.runtime.input_delivery_recovery import input_delivery_recovery

    delivery = input_delivery_recovery(state_dir, events, dict(identity), manifest["attempt_id"], own_reads)
    if delivery:
        result["provider_delivery"] = delivery
        if delivery.get("next_gap_cursor"):
            result["commands"]["next_delivery_gaps"] = (
                f"{prefix} recover --attempt {attempt} --delivery-cursor {delivery['next_gap_cursor']}")
        result["instructions"] += (" For observed input contracts, historical_missing is still the host read count; "
            "provider_delivery separately reports complete delivery evidence. Repair its missing selections "
            "using bounded pages and full tool output; result validate lists exact repair commands.")
        if not lifecycle["requires_rebind"] and delivery["status"] != "complete":
            result["next_action"] = "repair_provider_delivery" if delivery["status"] == "incomplete" else "diagnose_delivery_evidence"
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
                      "operation_id": str(current.get("operation_id") or ""),
                      "purpose": str(manifest.get("read_purpose") or "")},
            events=events, required=required, reads=query._read_rows(attempt),
            checkpoint=maintenance_checkpoint_facts(state_dir, events, task_id=task.id, attempt_facts=facts))
    except (OSError, ValueError, KeyError):
        return unavailable


def render_input_recovery(value: Mapping[str, Any]) -> str:
    if value.get("status") == "unavailable":
        return "## Input recovery\nCurrent input binding unavailable; request controlled recovery, never reuse an old attempt's commands.\n"
    return "## Input recovery (projection, not execution authority)\n```json\n" + json.dumps(value, ensure_ascii=False) + "\n```\n"
