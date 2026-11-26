"""Expose unresolved, authenticated container failures to semantic diagnosis."""
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.recovery_step import IDENTITY_FIELDS
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_host_manifest import digest
from zf.runtime.host_verification_policy import policy_receipt_matches


def bound_unknown_host_effect(event: ZfEvent, prior: dict[str, ZfEvent], approval_ref: str) -> bool:
    """An authenticated start/failure proves uncertainty, never execution success."""
    p = event.payload
    start = prior.get(event.causation_id)
    return bool(event.origin == "kernel" and event.type == "runtime.action.attempt.failed"
        and p.get("action") == "task-host-assist" and p.get("kind") == "verify"
        and p.get("effect_status") == "inspect_before_retry"
        and p.get("approval_ref") == approval_ref
        and start and start.origin == "kernel" and start.type == "runtime.action.attempt.started"
        and all(p.get(key) and p[key] == start.payload.get(key) for key in (
            "action", "kind", "workflow_run_id", "task_id", "operation_id", "manifest_digest", "approval_ref")))


def bound_container_probe_receipt(
    state_dir: Path, event: ZfEvent, prior: dict[str, ZfEvent], policy: Any,
) -> dict | None:
    """Validate current-policy execution facts for diagnosis or independent retry."""
    p = event.payload
    if (event.origin != "kernel" or event.type != "runtime.action.attempt.completed"
            or p.get("action") != "task-host-assist" or p.get("kind") != "verify"
            or p.get("approval_ref") != policy.approval_ref):
        return None
    start = prior.get(event.causation_id)
    if start is None or start.origin != "kernel" or start.type != "runtime.action.attempt.started":
        return None
    try:
        receipt = hydrate_sidecar_ref(state_dir, p["receipt_ref"]).payload
        if not isinstance(receipt, dict) or not isinstance(receipt.get("manifest"), dict):
            return None
        manifest = receipt["manifest"]
        identity = manifest["identity"]
        if not isinstance(identity, dict) or not isinstance(manifest.get("command"), dict):
            return None
        for key in ("workflow_run_id", "task_id", "operation_id", "manifest_digest", "approval_ref", "kind"):
            if not p.get(key) or p[key] != start.payload.get(key) or p[key] != receipt.get(key):
                return None
        for key, value in identity.items():
            if p.get(key) != value or receipt.get(key) != value or start.payload.get(key) != value:
                return None
        for key in ('recollection_of_start_id', 'original_manifest_digest', 'recollection_of_receipt_id'):
            if any(value != manifest.get(key) for value in (p.get(key), start.payload.get(key), receipt.get(key))):
                return None
        if (receipt.get("schema_version") != "task-host-receipt.v1"
                or receipt.get("execution_origin") != "preauthorized_container"
                or not policy_receipt_matches(state_dir, policy, receipt)
                or receipt.get("source_stable") is not True
                or type(receipt.get("exit_code")) is not int
                or receipt.get("status") not in {"completed", "failed"}
                or receipt.get("status") != p.get("status")
                or receipt.get("target_commit") != manifest.get("head")
                or not receipt.get("target_commit")
                or manifest.get("operation_id") != p["operation_id"]
                or manifest.get("manifest_digest") != p["manifest_digest"]
                or digest({k: v for k, v in manifest.items() if k != "manifest_digest"}) != p["manifest_digest"]
                or receipt.get("command") not in policy.commands
                or any(receipt.get(key) != manifest["command"].get(key)
                       for key in ("command", "command_id", "command_digest"))):
            return None
        return receipt
    except (OSError, ValueError, KeyError, TypeError):
        return None


def probe_obligation(receipt: dict) -> tuple[str, ...]:
    command = receipt["manifest"]["command"]
    return tuple(str(command.get(key) or "") for key in ("command_id", "tier", "command_digest", "command"))


def pending_host_probe_diagnoses(state_dir: Path, events: list[ZfEvent], config: Any) -> list[dict]:
    policy = getattr(config.runtime, "host_verification", None)
    if not policy or not policy.enabled:
        return []
    prior, failures, targets, terminal = {}, {}, {}, set()
    unknown = {}
    for event in events:
        p = event.payload
        run = p.get("workflow_run_id") or event.correlation_id
        if event.origin == "kernel":
            if event.type in {"run.goal.completed", "run.goal.failed", "run.aborted"}:
                terminal.add(run)
            if event.type == "candidate.ready" and p.get("commit"):
                targets[run] = p["commit"]
        receipt = bound_container_probe_receipt(state_dir, event, prior, policy)
        if bound_unknown_host_effect(event, prior, policy.approval_ref):
            unknown.setdefault(p["manifest_digest"], event)
        if receipt is not None:
            # Only completion of that effect, or its explicit independent
            # recollection, discharges the pending diagnosis. The original
            # unknown event remains unchanged and is never declared successful.
            for manifest_digest, failed in list(unknown.items()):
                if event.causation_id == failed.causation_id or (
                        receipt.get('recollection_of_start_id') == failed.causation_id
                        and receipt.get('original_manifest_digest') == manifest_digest
                        and receipt['manifest'].get('recollection_of_start_id') == failed.causation_id
                        and receipt['command'] in policy.recollect_commands):
                    unknown.pop(manifest_digest)
            key = (run, p["task_id"], receipt["target_commit"], probe_obligation(receipt))
            if receipt["exit_code"] == 0 and receipt["status"] == "completed":
                failures.pop(key, None)
            else:
                # Preserve the first unresolved occurrence; identical retries
                # do not create an endless sequence of diagnostic requests.
                failures.setdefault(key, (event, receipt))
        prior[event.id] = event
    for manifest, event in unknown.items():
        p = event.payload
        run = p["workflow_run_id"]
        target = p.get("target_commit") or targets.get(run, "")
        failures[(run, p["task_id"], target, manifest)] = (event, None)
    store = TaskStore(state_dir / "kanban.json")
    actions, seen = [], set()
    for (run, task_id, target, _), (event, receipt) in failures.items():
        scope = (run, task_id, target, event.payload["manifest_digest"])
        task = store.get(task_id)
        if (scope in seen or run in terminal or targets.get(run, target) != target
                or task is None or task.status in {"done", "cancelled"}):
            continue
        p = event.payload
        seen.add(scope)
        actions.append({
            "schema_version": "run-manager.pending-action.v1",
            "action": "diagnose-attention", "safe_resume_action": "diagnose_attention",
            "checkpoint_id": f"host-probe:{event.id}", "request_id": f"host-probe:{event.id}",
            **{key: p[key] for key in IDENTITY_FIELDS if p.get(key)},
            "target_commit": target, "source_event_id": event.id, "source_event_type": event.type,
            "host_manifest_digest": p["manifest_digest"], "target_operation_id": p["operation_id"],
            "source_event_ids": [event.id], "failure_event_ids": [event.id],
            "fingerprint": "host-probe:" + p["manifest_digest"],
            "failure_class": "unknown", "owner_route": "run_manager", "action_policy": "needs_diagnosis",
            "intervention_class": "diagnose", "suggested_route": "run_manager_resident_agent",
            "source_ref": p["receipt_ref"]["ref"] if receipt else "events.jsonl",
            "evidence_refs": [p["receipt_ref"]] if receipt else [],
            "summary": "预授权容器命令失败；读取绑定回执及原始输出，判断产品、依赖或运行器原因并提出有界恢复。重复派发不是恢复证据，不直接推断需要源码修复。",
            "expected_output": ["run.manager.agent.recommendation"],
            "continuation_expected_events": ["run.manager.agent.recommendation"],
            "effect_expected_events": ["run.manager.agent.recommendation"],
            "expected_downstream_events": ["run.manager.resident.prompted"],
        })
    return actions
