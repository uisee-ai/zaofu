"""Read-only, explicitly granted remeasurement of pre-freeze unattributed samples."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from zf.core.config.schema import RoleConfig
from zf.runtime.backend_session_reader import read_historical_codex_usage
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.codex_turn_boundary import timestamp
from zf.runtime.codex_turn_sample import scoped_rollout
from zf.runtime.milestone_provider_activity import frozen_provider_activity
from zf.runtime.provider_usage_reconciliation import build_disk_usage_event
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import OPERATION_EVENT_TYPES, reduce_workflow_operations

ACTION = "reassess_native_activity"
SOURCE = "retrospective-native-activity"
IDENTITY = ("workflow_run_id", "task_id", "operation_id", "attempt_id", "dispatch_id", "lease_id")


def _matches_delivery(sent, started, stage):
    p = stage.payload
    return bool(sent and started and sent.actor == "orchestrator"
        and all(p.get(k) and sent.payload.get(k) == p[k] for k in IDENTITY)
        and sent.payload.get("schema_version") == "task-attempt.v1"
        and sent.payload.get("identity_version") == "operation-v2"
        and sent.payload.get("placement_epoch") == str(p.get("placement_epoch"))
        and sent.payload.get("role_instance") == p.get("role_instance") == started.payload.get("role_instance")
        and started.payload.get("active_attempt_id") == p["attempt_id"]
        and started.payload.get("dispatch_id") == p["dispatch_id"] and started.payload.get("lease_id") == p["lease_id"]
        and started.payload.get("task_id") == p["task_id"]
        and started.payload.get("workflow_run_id") == p["workflow_run_id"]
        and started.payload.get("request_hash") == p.get("request_hash")
        and timestamp(sent.ts) <= timestamp(stage.ts) and timestamp(started.ts) <= timestamp(stage.ts))


def _candidates(events, run, missing):
    """Only the last eligible sample per actual turn is needed to measure its interval."""
    relevant = {e.payload.get("operation_id") for e in events if e.origin == "kernel"
                and e.type == "task.pipeline.stage.dispatched" and e.task_id in missing
                and e.payload.get("workflow_run_id") == run}
    history = defaultdict(list)
    sent, physical_roles, started, stages, current, latest = {}, {}, {}, {}, {}, {}
    for e in events:
        if e.origin != "kernel":
            continue
        p = e.payload
        role, operation = p.get("role_instance"), p.get("operation_id")
        if e.type == "task.attempt.sent":
            # A replacement physical delivery retires the previous role binding.
            current.pop(role, None)
            sent[role] = e
            physical_roles[p.get("attempt_id")] = role
            if operation in relevant:
                history[operation].append(e)
        elif e.type in {"task.attempt.failed", "task.attempt.succeeded", "task.attempt.superseded"}:
            role = physical_roles.get(p.get("attempt_id"))
            prior = sent.get(role)
            if prior and p.get("attempt_id") == prior.payload.get("attempt_id"):
                sent.pop(role)
                current.pop(role, None)
        elif operation in relevant and (e.type in OPERATION_EVENT_TYPES or e.type == "workflow.call.result.admitted"):
            history[operation].append(e)
            status = reduce_workflow_operations(history[operation]).get(operation, {})
            if (status.get("status") != "running" or status.get("divergent")
                    or status.get("call_result_admitted_event_id")):
                started.pop(operation, None)
            elif e.type == "workflow.operation.started" and status.get("active_attempt_id") == p.get("active_attempt_id"):
                started[operation] = e
            elif e.type == "workflow.operation.retry_started":
                started.pop(operation, None)  # an older start cannot prove a replacement attempt
        elif e.type == "task.pipeline.stage.dispatched" and e.actor == "orchestrator":
            if (p.get("workflow_run_id") == run and e.task_id in missing and e.task_id == p.get("task_id")
                    and all(p.get(k) for k in IDENTITY)):
                stages[e.id] = e
        elif e.type == "codex.hook.user_prompt_submit":
            current.pop(e.actor, None)
            stage = stages.get(e.causation_id)
            if stage and stage.payload.get("role_instance") == e.actor and p.get("cwd") == stage.payload.get("workdir"):
                current[e.actor] = (stage, e)
        elif e.type == "agent.usage" and e.actor in current:
            stage, hook = current[e.actor]
            op = started.get(stage.payload["operation_id"])
            delivery = sent.get(e.actor)
            if (p.get("usage_binding_status") == "unattributed" and not e.task_id
                    and not any(p.get(k) for k in IDENTITY)
                    and p.get("source") == "disk_reader" and p.get("capture_phase") == "lifecycle_poll"
                    and p.get("backend") == "codex" and p.get("usage_sample_id")
                    and p.get("provider_session_id") == hook.payload.get("session_id")
                    and 0 < timestamp(hook.ts) < timestamp(p.get("usage_timestamp")) <= timestamp(e.ts)
                    and _matches_delivery(delivery, op, stage)):
                latest[stage.id, hook.id] = (stage, hook, e, delivery, op)
    return latest.values()


def supplemental_activity(state, events, candidate, lifecycle, members, role):
    original = lifecycle["source"]
    frozen = hydrate_sidecar_ref(state, original).payload["summary"]
    missing = set((frozen.get("provider_activity") or {}).get("unobserved_task_ids") or ())
    if not missing:
        return None
    history = events[:events.index(candidate)]
    sample_counts = Counter(e.payload.get("usage_sample_id") for e in history
                            if e.type == "agent.usage" and e.origin == "kernel")
    groups = defaultdict(list)
    for row in _candidates(history, frozen["workflow_run_id"], missing):
        _, hook, usage, _, _ = row
        session = usage.payload["provider_session_id"]
        location = hook.payload.get("transcript_path")
        if (sample_counts[usage.payload["usage_sample_id"]] == 1 and isinstance(location, str)
                and scoped_rollout(state, usage.actor, session, Path(location))):
            groups[usage.actor, session, Path(location)].append(row)

    derived, proofs = {}, {}
    for (actor, session, path), rows in groups.items():
        times = Counter(row[2].payload["usage_timestamp"] for row in rows)
        samples = {}
        for _, _, usage, _, _ in rows:
            stamp = usage.payload["usage_timestamp"]
            if times[stamp] == 1:
                samples[stamp] = usage.payload["usage_sample_id"]
        reports = read_historical_codex_usage(state, actor, session, path, samples)
        for stage, hook, usage, sent, started in rows:
            p = usage.payload
            report = reports.get(p["usage_timestamp"])
            if not report or report.native_turn_sample.get("turn_id") != hook.payload.get("turn_id"):
                continue
            rebuilt = build_disk_usage_event(role=RoleConfig(name=actor, backend="codex"),
                                             config=None, usage=report).payload
            if any(rebuilt.get(k) != p.get(k) for k in ("usage_sample_id", "model", "usage", "provider_session_id")):
                continue
            derived[usage.id] = replace(usage, task_id=stage.task_id, payload={
                **p, **{k: sent.payload[k] for k in (*IDENTITY, "schema_version", "identity_version", "placement_epoch")},
                "native_turn_sample": report.native_turn_sample})
            proofs[usage.id] = {"usage_event_id": usage.id, "usage_sample_id": p["usage_sample_id"],
                                "prefix_sha256": report.native_turn_sample["rollout_prefix_sha256"],
                                "dispatch_event_id": stage.id, "submit_event_id": hook.id, "sent_event_id": sent.id,
                                "operation_started_event_id": started.id}
    if not derived:
        return None
    activity, rows = frozen_provider_activity([derived.get(e.id, e) for e in history], candidate, members)
    accepted = sorted({row["usage_event_id"] for intervals in rows.values() for row in intervals} & proofs.keys())
    if not accepted:
        return None
    summary = {"measurement": "retrospective_native_activity", "workflow_run_id": frozen["workflow_run_id"],
               "candidate_event_id": candidate.id, "freeze_at": candidate.ts, "candidate_generation": frozen["candidate_generation"],
               "target_commit": frozen["target_commit"], "original_lifecycle_digest": original["sha256"],
               "provider_activity": activity, "witnesses": [proofs[k] for k in accepted]}
    body = {"schema_version": "retrospective-native-activity.v1", "summary": summary, "authorizes_execution": False}
    ref = write_immutable_json_sidecar(state, body, root=SOURCE, kind=SOURCE, schema_version=body["schema_version"],
                                      created_by="kernel", source_event_id=candidate.id)
    source = {**ref, "source_id": SOURCE, "artifact_id": SOURCE, "allowed_paths": ["$.summary"],
              "access_scope": {"actors": [role], "purposes": ["candidate-verify"]}}
    windows = {f"retrospective-{ref['sha256']}-{i}": group for i, group in enumerate(activity["generations"])}
    return {"source": source, "windows": windows}
