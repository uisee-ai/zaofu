"""Join sealed provider samples to exact frozen member dispatches, without I/O."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import re

from zf.core.events import ZfEvent
from zf.runtime.codex_turn_boundary import timestamp
from zf.runtime.codex_turn_sample import native_uuid


def _key(e: ZfEvent) -> tuple:
    return tuple(e.payload[k] if isinstance(e.payload.get(k), str) else "" for k in (
        "workflow_run_id", "task_id", "operation_id", "attempt_id", "lease_id", "dispatch_id"
    ))


def _common_window(rows: list[dict]) -> dict:
    """Half-open intervals; never add lane peaks measured at different times."""
    changes = defaultdict(lambda: [[], []])
    for index, row in enumerate(rows):
        changes[row["observed_from"]][1].append(index)
        changes[row["observed_through"]][0].append(index)
    times = sorted(changes)
    active: set[int] = set()
    best = {"observed_concurrent_tasks": 0}
    for start, end in zip(times, times[1:]):
        remove, add = changes[start]
        active.difference_update(remove)
        active.update(add)
        owners = [rows[i] for i in sorted(active)]
        # An identity collision cannot prove independent activity for either row.
        identities = ("task_id", "role_instance", "session_id")
        counts = {k: Counter(row[k] for row in owners) for k in identities}
        distinct = [row for row in owners if all(counts[k][row[k]] == 1 for k in identities)]
        if len(distinct) > best["observed_concurrent_tasks"]:
            best = {"observed_concurrent_tasks": len(distinct), "from": start, "through": end,
                    "stage_counts": dict(sorted(Counter(row["stage"] for row in distinct).items())),
                    "usage_event_ids": sorted(row["usage_event_id"] for row in distinct)}
    return best


def frozen_provider_activity(events: list[ZfEvent], freeze: ZfEvent, members: list[dict]) -> tuple[dict, dict]:
    by_id = {e.id: e for e in events}
    run, generation = freeze.payload["workflow_run_id"], freeze.payload["task_map_generation"]
    member_by_task = {m["task_id"]: m for m in members}
    dispatches, claims = {}, {}
    submits = defaultdict(list)
    samples = []
    for e in events:
        p = e.payload
        if e.origin != "kernel":
            continue
        if e.type == "codex.hook.user_prompt_submit":
            parent = by_id.get(e.causation_id)
            if (parent and parent.origin == "kernel" and parent.actor == "orchestrator"
                    and parent.type in {"task.dispatched", "task.pipeline.stage.dispatched"}
                    and parent.payload.get("workflow_run_id") == run and parent.task_id in member_by_task
                    and parent.payload.get("role_instance") == e.actor and parent.payload.get("workdir") == p.get("cwd")
                    and native_uuid(p.get("session_id")) and native_uuid(p.get("turn_id"))):
                submits[(e.actor, p["session_id"], p["turn_id"])].append((parent, e))
            continue
        if p.get("workflow_run_id") != run or e.task_id not in member_by_task or not all(_key(e)) or p["task_id"] != e.task_id:
            continue
        if e.type == "task.attempt.delivery_claimed" and e.actor == "orchestrator":
            claims[_key(e)] = e
        if e.type == "agent.usage" and isinstance(p.get("native_turn_sample"), dict):
            samples.append(e)
        if e.type == "task.pipeline.stage.dispatched" and e.actor == "orchestrator":
            key = _key(e)
            # Duplicate occurrences for the same identity are ambiguous.
            dispatches[key] = None if key in dispatches else e

    turns = {}
    conflicts = set()
    for u in samples:
        data, k = u.payload, _key(u)
        d = dispatches.get(k)
        if d is None:
            continue
        p = d.payload
        task = member_by_task[d.task_id]
        gen = task.get("task_map_generation", generation)
        verification = by_id.get(task.get("verification_event_id"))
        package = freeze.payload.get("plan_artifact_package_digest") if gen == generation else None
        if (verification and verification.origin == "kernel" and verification.task_id == d.task_id
                and verification.payload.get("workflow_run_id") == run
                and verification.payload.get("task_map_generation") == gen):
            verified_package = verification.payload.get("plan_artifact_package_digest")
            if verified_package and package and verified_package != package:
                continue
            package = package or verified_package
        if (not package or p.get("plan_artifact_package_digest") != package or p.get("task_map_generation") != gen
                or not p.get("request_hash") or not p.get("stage_id") or p["stage_id"] != p.get("task_pipeline_stage")
                or not isinstance(p.get("workdir"), str) or not p["workdir"] or not p.get("workspace_generation") or not p.get("placement_epoch")
                or str(p["placement_epoch"]) != data.get("placement_epoch") or p.get("role_instance") != u.actor):
            continue
        native = data["native_turn_sample"]
        sid, tid = native.get("session_id"), native.get("turn_id")
        digest = hashlib.sha256(p["workdir"].encode()).hexdigest()
        if (data.get("schema_version") != "task-attempt.v1" or data.get("identity_version") != "operation-v2"
                or data.get("source") != "disk_reader" or data.get("capture_phase") != "lifecycle_poll"
                or data.get("backend") != "codex" or not data.get("usage_sample_id")
                or not isinstance(data.get("model"), str) or not data["model"]
                or native.get("schema_version") != "native-turn-sample.v1" or not native_uuid(sid) or not native_uuid(tid)
                or data.get("provider_session_id") != sid or native.get("workspace_sha256") != digest
                or native.get("native_usage_at") != data.get("usage_timestamp")):
            continue
        observed = [h for parent, h in submits.get((u.actor, sid, tid), ())
                    if _key(parent) == k and h.payload.get("cwd") == p["workdir"] and 0 < timestamp(parent.ts) <= timestamp(h.ts)]
        if len(observed) != 1:
            continue
        h = observed[0]
        start, sample = timestamp(native.get("native_started_at")), timestamp(data.get("usage_timestamp"))
        dispatched = timestamp(d.ts)
        claim = claims.get(k)
        # Transport may start its turn before the post-send dispatch occurrence.
        earliest = timestamp(claim.ts) if claim and 0 < timestamp(claim.ts) <= dispatched else dispatched
        anchor = max(start, dispatched, timestamp(h.ts))
        if not (0 < earliest <= start <= anchor < sample <= timestamp(u.ts) <= timestamp(freeze.ts)):
            continue
        turn = sid, tid
        model = data["model"]
        model_ref = {"model_sha256": hashlib.sha256(model.encode()).hexdigest()}
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_:.+-]*(?:/[A-Za-z0-9][A-Za-z0-9_:.+-]*)?", model) and len(model) <= 128:
            model_ref["model"] = model
        row = {"task_id": d.task_id, "stage": p["stage_id"], "role_instance": u.actor, **model_ref,
               "session_id": sid, "turn_id": tid, "operation_id": k[2], "attempt_id": k[3], "dispatch_id": k[5],
               "workspace_sha256": digest, "workspace_generation": p["workspace_generation"],
               "task_map_generation": gen, "plan_artifact_package_digest": package, "dispatch_event_id": d.id,
               "submit_event_id": h.id, "usage_event_id": u.id,
               "observed_from": datetime.fromtimestamp(anchor, timezone.utc).isoformat(timespec="microseconds"),
               "observed_through": datetime.fromtimestamp(sample, timezone.utc).isoformat(timespec="microseconds")}
        if start < dispatched:
            row["delivery_claim_event_id"] = claim.id
        prior = turns.get(turn)
        if prior and (prior["dispatch_event_id"], prior["observed_from"]) != (d.id, row["observed_from"]):
            conflicts.add(turn)
        if prior is None or row["observed_through"] > prior["observed_through"]:
            turns[turn] = row

    tasks = defaultdict(list)
    groups = defaultdict(list)
    for identity, row in turns.items():
        if identity not in conflicts:
            tasks[row["task_id"]].append(row)
            groups[(row["task_map_generation"], row["plan_artifact_package_digest"])].append(row)
    return ({
        "measurement": "Observed native turn through bound usage, measured separately per frozen Plan package.",
        "status": "observed" if groups else "unknown",
        "unobserved_task_ids": sorted(set(member_by_task) - tasks.keys()),
        "generations": [{"task_map_generation": gen, "plan_artifact_package_digest": pkg,
                         "peak_window": _common_window(rows)} for (gen, pkg), rows in sorted(groups.items())],
    }, tasks)
