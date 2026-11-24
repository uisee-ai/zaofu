"""Mechanical fences for an in-flight reader's controlled provider replacement."""

import fcntl
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent


def reader_dispatch_call_identity(payload: dict) -> dict:
    """Transport retries renew TaskAttempt fields but retain the nested Reader call."""
    nested = payload.get("payload") or {}
    if not isinstance(nested, dict):
        return {}
    for key in ("operation_id", "request_hash", "workflow_run_id"):
        if payload.get(key) and nested.get(key) and payload[key] != nested[key]:
            return {}
    return {**{key: payload.get(key) or nested.get(key) or "" for key in (
        "operation_id", "request_hash", "workflow_run_id")},
        "attempt_id": nested.get("attempt_id") or payload.get("attempt_id") or ""}


def reader_respawn_in_progress(state_dir: Path, role_instance: str) -> bool:
    """Observe the existing replacement lock without creating or unlinking it."""
    path = state_dir / "locks" / "respawns" / f"{role_instance}.lock"
    try:
        handle = path.open("rb")
    except OSError:
        return False
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def reader_recovery_fence(
    events: list[ZfEvent], dispatch: ZfEvent, dispatch_index: int
) -> int:
    """Fence only signals preceding delivery for this exact original dispatch."""
    original = dispatch.payload
    role = str(original.get("role_instance") or "")
    expected = {
        "recovered_dispatch_event_id": dispatch.id,
        "fanout_id": str(original.get("fanout_id") or ""),
        "run_id": str(original.get("run_id") or ""),
    }
    if not role or not all(expected.values()):
        return dispatch_index
    identity = reader_dispatch_call_identity(original)
    for key in ("operation_id", "request_hash"):
        if identity.get(key):
            expected[key] = str(identity[key])
    for index in range(len(events) - 1, dispatch_index, -1):
        event = events[index]
        if event.type != "worker.recovery.injected" or event.actor != role:
            continue
        if all(
            str(event.payload.get(k) or "") == value for k, value in expected.items()
        ):
            return index
    return dispatch_index


def reader_continuation_guidance(
    *, attempt_id: str, backend: str, cli: str, runtime=None, actor: str = "", operation_id: str = "",
) -> str:
    """Offer a bounded read continuation without asserting current memory."""
    import shlex

    from zf.runtime.artifact_workdir_view import recovery_hint

    files = recovery_hint(runtime, actor, attempt_id, operation_id) if runtime and backend == "codex" else ""
    entry = files or " Use artifact recover for this attempt."
    text = (
        " Continue the same pending operation from the available session and "
        "frozen inputs. Re-read any source you cannot still use; historical "
        "delivery alone does not prove current context after compaction. "
        "Reconcile unfinished tool calls and external effects before retrying."
        f"{entry} If the recovery view exposes work_record_read, "
        "read its digest-bound semantic checkpoint and assess pending criteria and "
        "report draft against current inputs. The record is unverified history, "
        "not a verdict or permission to skip required reads."
        " When historical_missing is zero, check the recovery view for delivery gaps. "
        "If none remain, inspect the signed request's existing result scratch for the "
        "draft and last unresolved step, even if no work record was published. Treat "
        "the draft as unverified. Use the source index to retrieve the exact clauses "
        "needed to check it. A fresh --required without a cursor starts at page one; "
        "use that only when a full reread is actually needed."
        " Continue consuming remaining pages in this turn until all required inputs "
        "are covered or a concrete blocker prevents progress. Do not stop merely "
        "after an arbitrary number of pages or to report partial progress. Once "
        "inputs are covered, complete the independent evaluation and validated "
        "result submission under the original briefing. Preserve permission, "
        "budget and evidence gates; input coverage is not an acceptance verdict."
    )
    if backend == "codex" and attempt_id and not files:
        attempt = shlex.quote(attempt_id)
        text += (
            f" For a lost required-read cursor, use `{cli} artifact read --attempt "
            f"{attempt} --required --resume` only if the earlier pages are still "
            "available in this session; otherwise reread the necessary pages."
        )
    return text


def reader_recovery_guidance(runtime: Any, role: Any, child: dict) -> str:
    """Prepare authenticated continuation before delivering a recovery prompt."""
    from zf.runtime.cli_command import zf_cli_cmd

    attempt_id = renew_reader_recovery_capability(runtime, role, child)
    return reader_continuation_guidance(
        attempt_id=attempt_id, backend=role.backend, cli=zf_cli_cmd(),
        runtime=runtime, actor=role.instance_id, operation_id=child.get("operation_id", ""),
    )


def renew_reader_recovery_capability(runtime: Any, role: Any, child: dict) -> str:
    """Renew the existing input grant only after checking its immutable carrier."""
    from zf.runtime.artifact_read_capability import bind_attempt_artifact_read_capability

    if not child.get("operation_id"):
        return str(child.get("attempt_id") or "")
    manifest, request_hash = current_reader_input(runtime, role, child)
    attempt_id = manifest["attempt_id"]
    bind_attempt_artifact_read_capability(runtime.state_dir, operation_id=child["operation_id"],
        attempt_id=attempt_id, role_instance=role.instance_id, manifest=manifest)
    child.update(attempt_id=attempt_id, request_hash=request_hash)
    return attempt_id


class ReaderAlreadySettled(ValueError):
    """The previous result is authoritative; the kernel can route it without another send."""


def current_reader_input(runtime: Any, role: Any, child: dict) -> tuple[dict, str]:
    """Validate the original dispatch again without rotating or granting a capability."""
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.workflow_operation import operation_request_hash, reduce_workflow_operations

    operation_id = str(child["operation_id"])
    events = runtime.event_log.read_all()
    dispatch = next((e for e in reversed(events) if e.id == child.get("event_id")), None)
    identity = reader_dispatch_call_identity(dispatch.payload) if dispatch else {}
    operation = reduce_workflow_operations(events).get(operation_id, {})
    if (dispatch and dispatch.origin == "kernel" and dispatch.actor == "zf-cli"
            and dispatch.payload.get("role_instance") == role.instance_id and identity.get("operation_id") == operation_id
            and identity.get("request_hash") and any(e.origin == "kernel" and
                e.type == "workflow.call.result.admitted" and e.actor == "zf-cli" and e.payload.get("admission_status") == "admitted"
                and e.payload.get("operation_id") == operation_id and e.payload.get("request_hash") == identity["request_hash"]
                and e.payload.get("attempt_id") == identity["attempt_id"] for e in events)):
        raise ReaderAlreadySettled("reader result already admitted; preserve its verdict")
    if (operation.get("operation_type") not in {"fanout_synth", "fanout_reader_child"}
            or operation.get("status") not in {"requested", "running"}
            or operation.get("role_instance") != role.instance_id
            or operation.get("workflow_run_id") != (child.get("workflow_run_id") or child.get("trace_id"))
            or identity.get("operation_id") != operation_id
            or operation.get("request_hash") != identity.get("request_hash")
            or (child.get("request_hash") and child["request_hash"] != identity.get("request_hash"))):
        raise ValueError("reader recovery capability has no matching active operation")
    if (dispatch is None or dispatch.origin != "kernel" or dispatch.actor != "zf-cli"
            or dispatch.type not in {"fanout.synth.dispatched", "fanout.child.dispatched"}
            or any(dispatch.payload.get(k) != child.get(k)
                   for k in ("operation_id", "run_id", "role_instance"))
            or runtime._fanout_dispatch_has_authoritative_result(events, dispatch)):
        raise ValueError("reader recovery capability has no current dispatch")
    nested = dispatch.payload.get("payload") or {}
    descriptor = dispatch.payload.get("attempt_source_manifest") or nested.get("attempt_source_manifest")
    if not isinstance(descriptor, dict):
        raise ValueError("reader recovery source manifest is missing")
    request = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload
    manifest = hydrate_sidecar_ref(runtime.state_dir, descriptor).payload
    attempt_id = str(manifest.get("attempt_id") or "")
    if (operation_request_hash(request) != operation["request_hash"]
            or descriptor.get("sha256") != request["request"].get("source_manifest_digest")
            or operation.get("active_attempt_id") != attempt_id
            or identity.get("attempt_id") != attempt_id
            or (child.get("attempt_id") and child["attempt_id"] not in {
                attempt_id, dispatch.payload.get("attempt_id")})):
        raise ValueError("reader recovery immutable input binding differs")
    if dispatch.payload.get("lease_id"):
        from zf.core.state.task_attempts import TaskAttemptStore

        rows = [row for row in TaskAttemptStore(runtime.state_dir / "task_attempts.json").current_rows()
                if row.get("instance_id") == role.instance_id and row.get("dispatch_id") == operation["dispatch_id"]]
        if len(rows) != 1 or rows[0].get("lease_id") != dispatch.payload["lease_id"] or rows[0].get("status") != "sent":
            raise ValueError("reader recovery transport lease changed")
        from datetime import datetime, timezone
        expiry = rows[0].get("lease_expires_at")
        if expiry and datetime.fromisoformat(expiry) <= datetime.now(timezone.utc):
            raise ValueError("reader recovery transport lease expired")
    return manifest, identity["request_hash"]
