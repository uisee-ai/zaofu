"""Immutable task facts for a successor; receipt never grants execution rights."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from typing import Any

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, verify_sidecar_ref
from zf.runtime.workflow_operation import TERMINAL_OPERATION_STATUSES, reduce_workflow_operations

SCHEMA = "task-handoff.v1"
SOURCE_ID = "task-handoff"


def validate_handoff_body(body: Any, target: dict[str, str]) -> None:
    required = {"schema_version", "target", "source", "objective", "contract_revision", "remaining_acceptance",
                "workspace", "admitted_results", "work_record", "unknown_effects", "next_action", "references",
                "authorizes_execution", "business_completed"}
    if not isinstance(body, dict) or not required <= body.keys():
        raise ValueError("handoff semantic body is incomplete")
    if body["schema_version"] != SCHEMA or body["target"] != target:
        raise ValueError("handoff target is stale")
    if body["authorizes_execution"] is not False or body["business_completed"] is not False:
        raise ValueError("handoff cannot grant execution or business completion")
    if (not isinstance(body["workspace"], dict) or not isinstance(body["source"], dict)
            or not body["source"].get("operation_id") or not isinstance(body["next_action"], str)
            or not body["next_action"] or not all(isinstance(body[key], list) for key in (
                "remaining_acceptance", "admitted_results", "unknown_effects", "references"))):
        raise ValueError("handoff facts have an invalid shape")


def attach_task_handoff(runtime: Any, payload: dict[str, Any], contract: dict[str, Any]) -> None:
    """Pin predecessor facts before the successor operation request is frozen.

    Replay reuses the original body even if the workspace has since changed.
    First-stage and verify-only entry without a predecessor need no handoff.
    """
    target = {key: str(payload.get(key) or "") for key in (
        "workflow_run_id", "task_id", "operation_id",
    )}
    if not all(target.values()):
        raise ValueError("task handoff requires a complete target execution identity")
    key = hashlib.sha256(target["operation_id"].encode()).hexdigest()
    with locked_path(Path(runtime.state_dir) / "task-handoffs" / key):
        events = runtime.event_log.read_all()
        prior = [e for e in events if e.type == "task.handoff.prepared" and e.origin == "kernel"
                 and e.payload.get("target") == target]
        if prior:
            descriptor = prior[-1].payload["handoff_ref"]
            body = hydrate_sidecar_ref(runtime.state_dir, descriptor).payload
            validate_handoff_body(body, target)
        else:
            operations = reduce_workflow_operations(events)
            if target["operation_id"] in operations:
                return  # An existing immutable request cannot gain new inputs on replay.
            predecessors = [row for row in operations.values()
                if row.get("task_id") == target["task_id"] and row.get("workflow_run_id") == target["workflow_run_id"]
                and row.get("operation_id") != target["operation_id"] and row.get("task_pipeline_stage")]
            if not predecessors:
                return
            if any(row.get("status") not in TERMINAL_OPERATION_STATUSES for row in predecessors):
                raise ValueError("task handoff waits for predecessor operation settlement")
            order = {event.id: index for index, event in enumerate(events)}
            source = max(predecessors, key=lambda row: order.get(row.get("last_event_id"), -1))
            body = _body(runtime, payload, contract, target, source, events)
            validate_handoff_body(body, target)
            descriptor = write_immutable_json_sidecar(runtime.state_dir, body,
                root="task-handoffs/bodies", kind="task_handoff", schema_version=SCHEMA,
                created_by="kernel", source_event_id=str(source.get("last_event_id") or ""))
            runtime.event_writer.append(ZfEvent(type="task.handoff.prepared", origin="kernel",
                actor="orchestrator", task_id=target["task_id"], correlation_id=target["workflow_run_id"],
                payload={"target": target, "handoff_ref": descriptor, "source_operation_id": source["operation_id"]}))
        payload["task_handoff_ref"] = descriptor
        payload["input_refs"] = [*[row for row in payload.get("input_refs", [])
            if row.get("source_id") != SOURCE_ID],
            {**descriptor, "source_id": SOURCE_ID, "artifact_id": SOURCE_ID, "allowed_paths": ["$"]}]


def _body(runtime, payload, contract, target, source, events) -> dict[str, Any]:
    request_ref = source.get("request_ref")
    if not isinstance(request_ref, dict):
        raise ValueError("predecessor request body is missing")
    verify_sidecar_ref(runtime.state_dir, request_ref)
    references = [request_ref]
    change = _contract_change(runtime, target["task_id"], events, contract.get("contract_authority_revision"))
    if change.get("ref"):
        references.append(change["ref"])
    record = None
    for event in reversed(events):
        if (event.type == "worker.work_record.published" and event.origin == "kernel"
                and event.task_id == target["task_id"] and event.payload.get("operation_id") == source["operation_id"]
                and event.payload.get("attempt_id") == source.get("active_attempt_id")):
            ref = event.payload["work_record_ref"]
            record = hydrate_sidecar_ref(runtime.state_dir, ref).payload
            if record.get("binding", {}).get("operation_id") != source["operation_id"]:
                raise ValueError("predecessor work record identity mismatch")
            references.extend([ref, *record.get("evidence_refs", [])])
            break
    results = []
    for event in events:
        if (event.type == "workflow.call.result.admitted" and event.origin == "kernel" and event.task_id == target["task_id"]
                and event.payload.get("operation_id") == source["operation_id"]):
            ref = event.payload.get("control_result_ref")
            if isinstance(ref, dict):
                references.append(ref)
                results.append({"event_id": event.id, "semantic_verdict": event.payload.get("semantic_verdict"), "ref": ref})
    for descriptor in references:
        verify_sidecar_ref(runtime.state_dir, descriptor)
    workspace = Path(str(payload.get("workdir") or ""))
    observed = {}
    for field, arguments in (("head", ["rev-parse", "HEAD"]), ("dirty", ["status", "--porcelain", "--untracked-files=normal"])):
        try:
            result = subprocess.run(["git", *arguments], cwd=workspace, capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("handoff workspace inspection timed out") from exc
        if result.returncode:
            raise ValueError("handoff workspace cannot be inspected")
        observed[field] = bool(result.stdout) if field == "dirty" else result.stdout.strip()
    from zf.core.security.redaction import redact_obj
    from zf.runtime.task_execution_evidence import capture_execution_evidence, previous_handoff
    from zf.runtime.cli_command import zf_cli_cmd

    return redact_obj({
        "schema_version": SCHEMA, "target": target,
        "source": {key: source.get(key, "") for key in ("operation_id", "active_attempt_id", "dispatch_id", "status", "role_instance", "task_pipeline_stage", "reason", "semantic_verdict")},
        "objective": contract.get("behavior") or contract.get("title") or "",
        "contract_revision": contract.get("contract_revision", ""),
        "contract_change": change,
        "remaining_acceptance": contract.get("acceptance_criteria", []),
        "workspace": {"path": str(workspace), **observed, "candidate_status": "preserved_not_accepted",
                      "environment_qualification": "unverified"},
        "admitted_results": results, "work_record": record,
        "execution_evidence_ref": capture_execution_evidence(runtime.state_dir, source, events),
        "previous_handoff_ref": previous_handoff(runtime.state_dir, source, events),
        "execution_evidence_read": zf_cli_cmd() + " artifact handoff-evidence --operation " + target["operation_id"],
        "unknown_effects": _unconfirmed_continuations(events, target["task_id"], source.get("active_attempt_id")),
        "next_action": "Read current contract and handoff, acknowledge receipt, then continue the current authorized stage. "
                       "For historical command output, including controlled host probes after settlement, "
                       "use execution_evidence_read; select --event from its index "
                       "or --source-operation for a linked earlier attempt. Observations are not acceptance verdicts.",
        "references": references, "authorizes_execution": False, "business_completed": False,
    })


def _contract_change(runtime, task_id, events, expected_revision=None):
    """Keep old facts reachable without treating their approvals as current."""
    task = runtime.task_store.get(task_id)
    revision = getattr(task, "contract_authority_revision", "")
    if expected_revision is not None and expected_revision != revision:
        raise ValueError("contract authority changed during handoff preparation")
    for event in reversed(events):
        if (event.type != "task.contract.revision.applied" or event.origin != "kernel"
                or event.task_id != task_id
                or event.payload.get("contract_authority_revision") != revision):
            continue
        ref = event.payload.get("mutation_ref") or {
            "ref": event.payload.get("contract_mutation_ref", ""),
            "sha256": event.payload.get("contract_mutation_digest", ""),
            "kind": "task_contract_mutation", "content_type": "application/json",
        }
        if not ref.get("sha256"):
            raise ValueError("contract history requires a pinned digest")
        body = hydrate_sidecar_ref(runtime.state_dir, ref).payload
        if body.get("task_id") != task_id or body.get("authority_revision") != revision:
            raise ValueError("contract history identity mismatch")
        return {"ref": ref, "authority_revision": revision,
                "previous_authority_revision": body.get("previous_authority_revision", ""),
                "previous_snapshot_available": isinstance(body.get("previous_contract"), dict),
                "authorizes_execution": False}
    return {"status": "unavailable", "authorizes_execution": False}


def predecessor_work_record(state_dir: Path, manifest: dict, binding: dict) -> tuple[dict, dict]:
    """Read only the predecessor pinned for an already authorized current caller."""
    sources = [s for s in manifest.get("sources", []) if s.get("source_id") == SOURCE_ID]
    if len(sources) != 1 or not sources[0].get("sha256"):
        raise ValueError("current operation has no pinned predecessor handoff")
    body = hydrate_sidecar_ref(state_dir, sources[0]).payload
    validate_handoff_body(body, {key: binding[key] for key in ("workflow_run_id", "task_id", "operation_id")})
    record = body.get("work_record")
    if not isinstance(record, dict):
        raise ValueError("predecessor work record unavailable")
    expected = {key: binding[key] for key in ("workflow_run_id", "task_id")}
    expected.update(operation_id=body["source"]["operation_id"],
                    attempt_id=body["source"].get("active_attempt_id"),
                    dispatch_id=body["source"].get("dispatch_id"))
    if (not all(expected.values()) or record.get("schema_version") != "worker-work-record.v1"
            or record.get("authorizes_execution") is not False
            or any(record.get("binding", {}).get(key) != value for key, value in expected.items())):
        raise ValueError("predecessor work record identity mismatch")
    refs = [ref for ref in body["references"] if ref.get("kind") == "worker_work_record"]
    if len(refs) != 1 or hydrate_sidecar_ref(state_dir, refs[0]).payload != record:
        raise ValueError("predecessor work record does not match pinned reference")
    return refs[0], record


def _unconfirmed_continuations(events, task_id, attempt_id):
    """Delivery confirmation resolves only delivery uncertainty, not business work."""
    pending = {}
    for event in events:
        if (event.origin != "kernel" or event.task_id != task_id
                or event.payload.get("attempt_id") != attempt_id
                or event.payload.get("recovery_action") != "completion_nudge_requested"):
            continue
        key = (event.actor, event.payload.get("dispatch_id"), event.payload.get("input_read_progress"))
        if event.type == "worker.continuation.claimed":
            pending[key] = event.id
        elif event.type == "worker.continuation.reconciled" and event.payload.get("delivery_status") == "observed":
            pending = {key: value for key, value in pending.items() if value != event.payload.get("claim_id")}
        elif event.type == "worker.stuck.recovered" and event.payload.get("prompt_injected") is True:
            pending.pop(key, None)
    return list(pending.values())
