"""Bind a Reader checkpoint to its call and separate transport lease."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.artifact_input_lifecycle import input_lifecycle
from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.fanout_identity import fanout_current_status
from zf.runtime.reader_input_dispatch import reader_input_dispatch_matches
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash, reduce_workflow_operations


def reader_work_record_binding(
    state_dir: Path, events: list, operation: dict[str, Any], actor: str,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    """No Task reassignment, credential issuance or additional write permission."""
    if any(e.type == "event.malformed" and e.payload.get("ledger_integrity") == "unknown" for e in events):
        raise ValueError("Reader checkpoint requires a trustworthy ledger")
    if (operation.get("operation_type") != "fanout_reader_child"
            or operation.get("status") != "running" or operation.get("role_instance") != actor):
        raise ValueError("Reader checkpoint operation is not current")
    current = [op for op in reduce_workflow_operations(events).values()
               if op.get("status") == "running" and op.get("role_instance") == actor]
    if len(current) != 1 or current[0] != operation:
        raise ValueError("Reader checkpoint owner is ambiguous or stale")
    run = build_run_admission_projection(events).runs.get(operation.get("workflow_run_id"))
    if run is not None and run.status != "running":
        raise ValueError("Reader checkpoint Run is not running")
    body = hydrate_sidecar_ref(state_dir, operation["request_ref"]).payload
    if (operation_request_hash(body) != operation.get("request_hash")
            or any(not operation.get(k) or body.get(k) != operation[k] for k in (
                "workflow_run_id", "operation_id", "active_attempt_id", "role_instance", "lease_id"))):
        raise ValueError("Reader checkpoint request binding changed")
    request = body["request"]
    manifest = load_attempt_source_manifest(state_dir, operation["active_attempt_id"])
    if (request.get("source_manifest_digest") != canonical_json_sha256(manifest)
            or any(manifest.get(k) != operation.get(k) for k in ("workflow_run_id", "task_id"))
            or manifest.get("task_map_generation", "") != operation.get("task_map_generation", "")
            or not reader_input_dispatch_matches(operation, manifest, events)
            or input_lifecycle(manifest, events, state_dir=state_dir)["requires_rebind"]):
        raise ValueError("Reader checkpoint inputs require controlled rebind")
    if request.get("output_profile_id") == "candidate-verify":
        from zf.runtime.candidate_currentness import candidate_currentness_issues
        identity = request.get("result_identity") or {}
        if candidate_currentness_issues(state_dir, events, identity=identity,
                contract_ref=str(identity.get("contract_snapshot_ref") or ""),
                contract_digest=str(identity.get("contract_snapshot_digest") or "")):
            raise ValueError("Reader checkpoint frozen candidate is stale")
    fanout_id, child_id = request.get("fanout_id"), request.get("child_id")
    status = fanout_current_status(events, fanout_id)
    if not fanout_id or not child_id or not status.known or not status.current:
        raise ValueError("Reader checkpoint fanout is stale or unknown")
    if any(e.type in {"fanout.cancelled", "fanout.timed_out"}
           and e.payload.get("fanout_id") == fanout_id for e in events):
        raise ValueError("Reader checkpoint fanout is closed")
    child_events = [e for e in events if e.type in {
        "fanout.child.dispatched", "fanout.child.completed", "fanout.child.failed",
    } and e.payload.get("fanout_id") == fanout_id and e.payload.get("child_id") == child_id]
    latest = child_events[-1] if child_events else None
    expected = {"operation_id": operation["operation_id"], "role_instance": actor,
                "run_id": operation["dispatch_id"]}
    nested = (latest.payload.get("payload") or {}) if latest is not None else {}
    if (latest is None or latest.type != "fanout.child.dispatched" or latest.origin != "kernel"
            or any(not value or latest.payload.get(key) != value for key, value in expected.items())
            or nested.get("attempt_id") != operation["active_attempt_id"]
            or nested.get("request_hash") != operation["request_hash"]
            or ("request_hash" in latest.payload and latest.payload["request_hash"] != operation["request_hash"])):
        raise ValueError("Reader checkpoint child delivery is stale")
    rows = [r for r in TaskAttemptStore(state_dir / "task_attempts.json").current_rows()
            if r.get("operation_id") == operation["operation_id"] and r.get("instance_id") == actor]
    row = rows[0] if len(rows) == 1 else {}
    expected_attempt = {"run_id": operation["workflow_run_id"], "task_id": operation["task_id"],
                        "dispatch_id": operation["dispatch_id"], "instance_id": actor}
    try:
        expiry = datetime.fromisoformat(row["lease_expires_at"])
        valid_lease = expiry.tzinfo is not None and expiry > datetime.now(timezone.utc)
    except (KeyError, TypeError, ValueError):
        valid_lease = False
    if (row.get("status") != "sent" or not row.get("attempt_id") or not row.get("lease_id") or not valid_lease
            or any(row.get(k) != v for k, v in expected_attempt.items())
            or latest.payload.get("lease_id") != row["lease_id"]):
        raise ValueError("Reader checkpoint requires the current sent and unexpired transport lease")
    scratch = Path(str(request.get("result_scratch_ref") or ""))
    if scratch.is_absolute() or ".." in scratch.parts or not scratch.name:
        raise ValueError("Reader checkpoint has no signed result scratch")
    workdir = state_dir.resolve()
    binding = {
        "workflow_run_id": operation["workflow_run_id"], "task_id": operation["task_id"],
        "operation_id": operation["operation_id"], "attempt_id": manifest["attempt_id"],
        "dispatch_id": operation["dispatch_id"], "canonical_attempt_id": row["attempt_id"],
        "lease_id": row["lease_id"], "actor": actor, "workdir": str(workdir),
        "manifest_digest": canonical_json_sha256(manifest), "request_hash": operation["request_hash"],
        "record_scope": "reader_result_scratch", "result_scratch_ref": str(scratch),
        "fanout_id": fanout_id, "child_id": child_id,
    }
    return binding, manifest, workdir


def reader_checkpoint_entry(state_dir: Path, events: list, manifest: dict, actor: str) -> dict:
    """On-demand mechanical command shape; semantic checkpoint method stays in skills."""
    import shlex
    from zf.runtime.cli_command import zf_cli_cmd

    operations = [op for op in reduce_workflow_operations(events).values()
                  if op.get("active_attempt_id") == manifest.get("attempt_id")
                  and op.get("role_instance") == actor]
    if len(operations) != 1:
        return {}
    try:
        binding, current, _ = reader_work_record_binding(state_dir, events, operations[0], actor)
        if manifest != current:
            return {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return {
        "command": f"{zf_cli_cmd()} artifact work-record publish --operation {shlex.quote(binding['operation_id'])} --stdin --request-id '<unique-id>'",
        "body_fields": {"completed_actions": "list of evaluated criteria with evidence references",
                        "pending": "list of unresolved criteria/decisions", "next_step": "next action string",
                        "evidence_files": [], "report_draft": "optional unfinished report text"},
        "limits": {"max_bytes": 16384, "max_list_items": 16, "max_item_chars": 1024,
                   "max_next_step_chars": 2048, "max_report_draft_chars": 8192},
        "authorizes_execution": False,
        "instructions": "Keep result scratch unchanged. No secrets. Same request-id requires identical content. "
                        "History is unverified; preserve required reads and original final submit. "
                        "A checkpoint does not grant another turn, lease or verdict.",
    }


def _same_record_input(events: list, published: Any, previous: dict, current: dict) -> bool:
    if previous == current:
        return True
    transport = {"dispatch_id", "canonical_attempt_id", "lease_id"}
    if (set(previous) != set(current)
            or any(previous[k] != current[k] for k in current if k not in transport)):
        return False
    position = next((i for i, e in enumerate(events) if e.id == published.id), -1)
    if position < 0:
        return False
    # The original kernel publication was admitted under this exact transport.
    delivered = any(e.type == "fanout.child.dispatched" and e.origin == "kernel"
        and e.payload.get("operation_id") == previous["operation_id"]
        and e.payload.get("run_id") == previous["dispatch_id"]
        and e.payload.get("lease_id") == previous["lease_id"]
        for e in events[:position])
    attempt = any(e.type == "task.attempt.started" and e.origin == "kernel"
        and e.payload.get("attempt_id") == previous["canonical_attempt_id"]
        and e.payload.get("lease_id") == previous["lease_id"]
        and e.payload.get("operation_id") == previous["operation_id"]
        for e in events[:position])
    later = [e for e in events[position + 1:] if e.payload.get("operation_id") == current["operation_id"]]
    started = next((e for e in reversed(later) if e.type == "workflow.operation.started"), None)
    if not delivered or not attempt or started is None or started.origin != "kernel":
        return False
    return any(e.type == "workflow.operation.redrive_admitted" and e.origin == "kernel"
        and e.payload.get("recovery_decision_owner") == "kernel_replay"
        and e.payload.get("source_attempt_id") == current["attempt_id"]
        and e.payload.get("recovery_decision_event_id") == started.causation_id
        and all(e.payload.get(k) == current[k] for k in ("workflow_run_id", "task_id", "request_hash"))
        for e in later[:later.index(started)])


def current_reader_work_record(state_dir: Path, events: list, manifest: dict, actor: str) -> dict:
    """A projection for an already authorized input-recovery consumer."""
    unavailable = {"status": "unavailable", "authorizes_execution": False}
    operations = [op for op in reduce_workflow_operations(events).values()
                  if op.get("active_attempt_id") == manifest.get("attempt_id")
                  and op.get("role_instance") == actor]
    if len(operations) != 1:
        return unavailable
    try:
        binding, current, _ = reader_work_record_binding(state_dir, events, operations[0], actor)
        if current != manifest:
            return unavailable
        for event in reversed(events):
            if (event.type != "worker.work_record.published" or event.origin != "kernel"
                    or event.actor != actor or event.payload.get("operation_id") != binding["operation_id"]):
                continue
            record = hydrate_sidecar_ref(state_dir, event.payload["work_record_ref"]).payload
            previous = record.get("binding") or {}
            if (record.get("schema_version") != "worker-work-record.v1"
                    or not _same_record_input(events, event, previous, binding)):
                return unavailable
            if any(event.payload.get(k) != v for k, v in previous.items()):
                return unavailable
            for evidence in record["evidence_refs"]:
                hydrate_sidecar_ref(state_dir, evidence)
            return {"status": "bound", "authorizes_execution": False, "claims_status": "worker_report_unverified",
                    "event_id": event.id, "operation_id": binding["operation_id"],
                    "work_record_ref": event.payload["work_record_ref"],
                    "completed_count": len(record["completed_actions"]), "pending_count": len(record["pending"]),
                    "next_step": record["next_step"][:512], "next_step_complete": len(record["next_step"]) <= 512}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return unavailable
