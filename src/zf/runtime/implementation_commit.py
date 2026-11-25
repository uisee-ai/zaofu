"""Bind a successful Task Impl result to the dispatched Git workspace."""
from pathlib import Path
import re
from typing import Any, Mapping

from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_host_evidence import IDENTITY
from zf.runtime.task_host_manifest import current_task_dispatch, digest, git


def publication_retry_reason(commit: str, receipt_ref: str) -> str:
    return (f"Preauthorized candidate publication completed: source_commit={commit}; receipt={receipt_ref}. "
            "Reassess the exact commit and required self-checks; publication is not an Impl verdict.")


def published_retry_commit(state: Path, dispatch: Mapping[str, Any], events: list) -> str:
    """Use only the controlled predecessor publication consumed by this retry."""
    feedback = {row.get("blocking_event_id") for row in dispatch.get("rework_feedback") or []
                if isinstance(row, Mapping) and row.get("category") == "task_pipeline_operator_retry"}
    if not feedback:
        return ""
    by_id = {e.id: e for e in events}
    retries = [by_id[i] for i in feedback if i in by_id and by_id[i].type == "task.rework.requested"
               and by_id[i].origin == "kernel" and by_id[i].payload.get("source") == "task_pipeline_operator_retry"]
    if len(retries) != 1:
        return ""
    retry = retries[0]
    old = retry.payload
    generation = dispatch.get("operation_generation")
    if (type(generation) is not int or old.get("operation_generation") != generation - 1
            or any(old.get(k) != dispatch.get(k) for k in ("workflow_run_id", "task_id", "task_map_generation"))):
        return ""
    previous = events[:events.index(retry)]
    effects = [e for e in previous if e.type == "runtime.action.attempt.completed" and e.origin == "kernel"
               and e.payload.get("operation_id") == old["operation_id"]
               and e.payload.get("action") == "task-host-assist" and e.payload.get("kind") == "commit"
               and e.payload.get("status") == "completed"]
    if not effects:
        if old.get("publication_event_id"):
            raise ValueError("Impl retry is missing its host publication")
        return ""
    if len(effects) != 1:
        raise ValueError("ambiguous host publication for the previous Impl attempt")
    effect, = effects
    receipt_ref = effect.payload.get("receipt_ref")
    if not isinstance(receipt_ref, Mapping) or receipt_ref.get("kind") != "task_host_receipt":
        raise ValueError("Impl retry is missing its host publication receipt")
    start = by_id.get(effect.causation_id)
    receipt = hydrate_sidecar_ref(state, receipt_ref).payload
    if not isinstance(receipt, Mapping) or not isinstance(receipt.get("manifest"), Mapping):
        raise ValueError("invalid host publication receipt or manifest")
    # Older retries carry the Run Manager's complete publication reference in
    # their reason. A separate manual repair may advance the branch again.
    if not old.get("publication_event_id") and old.get("reason") != publication_retry_reason(
            receipt.get("source_commit", ""), receipt_ref["ref"]):
        return ""
    if old.get("publication_event_id") and old["publication_event_id"] != effect.id:
        raise ValueError("Impl retry references a different host publication event")
    manifest = receipt["manifest"]
    if (not start or start.origin != "kernel" or start.type != "runtime.action.attempt.started"
            or start.actor != effect.actor or start not in previous[:previous.index(effect)]
            or receipt_ref.get("source_event_id") != start.id
            or effect.task_id != old["task_id"] or start.task_id != effect.task_id
            or effect.correlation_id != old["workflow_run_id"] or start.correlation_id != effect.correlation_id
            or any(effect.payload.get(k) in (None, "") or effect.payload[k] != start.payload.get(k)
                   or receipt.get(k) != effect.payload[k]
                   for k in (*IDENTITY, "approval_ref", "action", "kind", "manifest_digest"))
            or any(receipt.get(k) != old.get(k)
                   for k in ("workflow_run_id", "task_id", "operation_generation", "task_map_generation"))
            or receipt.get("schema_version") != "task-host-receipt.v1" or receipt.get("status") != "completed"
            or receipt.get("manifest_digest") != digest({k: v for k, v in manifest.items() if k != "manifest_digest"})
            or manifest.get("kind") != "commit" or manifest.get("operation_id") != old["operation_id"]
            or manifest.get("contract_digest") != dispatch.get("contract_snapshot_digest")
            or manifest.get("branch") != dispatch.get("source_branch") or manifest.get("workdir") != dispatch.get("workdir")
            or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", str(receipt.get("source_commit") or ""))):
        raise ValueError("host publication receipt does not bind the current retry workspace and contract")
    return receipt["source_commit"]


def validate_impl_commit(state: Path, operation: Mapping[str, Any], result: Mapping[str, Any], events: list) -> None:
    if operation.get("task_pipeline_stage") != "impl":
        return
    dispatch = current_task_dispatch(events, dict(operation))
    root = Path(dispatch["workdir"])
    if (not root.is_absolute() or root != root.resolve() or not root.is_relative_to(state.resolve())
            or git(root, "rev-parse", "--show-toplevel") != str(root)
            or git(root, "symbolic-ref", "--short", "HEAD") != dispatch.get("source_branch")):
        raise ValueError("Impl commit requires the dispatched Task workspace and source branch")
    head = git(root, "rev-parse", "--verify", "HEAD^{commit}")
    published = published_retry_commit(state, dispatch, events)
    if published and published != head:
        raise ValueError(f"Dispatched HEAD {head} differs from its host publication {published}. "
                         "Report the changed workspace before claiming the published result.")
    for key in ("target_commit", "source_commit"):
        if key == "source_commit" and key not in result:
            continue
        submitted = result.get(key)
        if isinstance(submitted, str) and re.fullmatch(r"[0-9a-fA-F]{4,64}", submitted) and head.startswith(submitted.lower()):
            # A Git prefix must also resolve unambiguously in this repository.
            try:
                if git(root, "rev-parse", "--verify", "--end-of-options", submitted + "^{commit}") == head:
                    continue
            except ValueError:
                pass
        authority = "the authenticated host publication and Task HEAD" if published else "the dispatched Task HEAD"
        raise ValueError(f"{key} must resolve to {authority} {head}. Correct target_commit and self-check "
                         "commit references in this attempt's result scratch before submitting.")
