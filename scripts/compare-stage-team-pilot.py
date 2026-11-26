#!/usr/bin/env python3
"""Compare two captured §8 windows. Reads evidence only; never starts a Run."""

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path

from zf.core.events.log import EventLog
from zf.runtime.automation_metrics import cross_cutting_scorecard
from zf.runtime.recovery_case import build_recovery_metrics
from zf.runtime.run_scope import events_for_run
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref


def read_capture(root, descriptor):
    path = (root / descriptor["path"]).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("capture outside the experiment directory")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != descriptor["sha256"]:
        raise ValueError(f"capture digest mismatch: {path.name}")
    return json.loads(data)


def time(value):
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        raise ValueError("timestamps must include timezone")
    return ts


def snapshot(state, event):
    ref = event.payload.get("effective_config_ref")
    if not ref or not event.payload.get("workflow_proposal_ref"):
        raise ValueError("invoke lacks approved Proposal/config binding")
    loaded = hydrate_sidecar_ref(state, ref)
    if not loaded.ok or loaded.payload.get("schema_version") != "effective-config-snapshot.v1":
        raise ValueError("invalid effective config snapshot")
    return loaded.payload


_LOCAL_POLICY_FIELDS = {
    "session.tmux_session", "workflow._flow_metadata.target_root",
    "runtime.autoresearch_resident.worktree_root", "runtime.autoresearch_resident.output_root",
    "runtime.host_verification.approval_ref", "runtime.task_publication.approval_ref",
}


def common_policy_digest(snapshot_body, local_bindings=None):
    """Only the experimental consumer switches and capture-local project identity may differ."""
    config = deepcopy(snapshot_body["config"])
    for field, frozen in (local_bindings or {}).items():
        if field not in _LOCAL_POLICY_FIELDS and not field.startswith("workflow._flow_metadata.artifact_worker_cgroups."):
            raise ValueError("unsupported local identity in comparison")
        keys = field.split(".")
        target = config
        for key in keys[:-1]:
            target = target[key]
        if not isinstance(frozen, str) or not frozen or target.get(keys[-1]) != frozen:
            raise ValueError("local identity differs from preapproved capture")
        target[keys[-1]] = f"<isolated:{field}>"
    project = config.get("project", {})
    for name in ("name", "state_dir"):
        project.pop(name, None)
    runtime = config["runtime"]
    manager = runtime["run_manager"]
    for owner, name, switches in ((manager, "resident_agent", ("enabled",)),
                                  (manager, "reflect", ("enabled",)),
                                  (runtime, "autoresearch_resident", ("enabled", "self_repair_consumer"))):
        model = owner.get(name, {})
        for switch in switches:
            model.pop(switch, None)
        if not model:
            owner.pop(name, None)
    controls = {"config": config, "compiled_graph": snapshot_body.get("compiled_graph")}
    return hashlib.sha256(json.dumps(controls, sort_keys=True).encode()).hexdigest()


def receipt_binding(body, freeze, *, terminal_bound=False):
    if not freeze:
        return "observed_unbound"
    ids = [body[key] for key in ("candidate_event_id", "candidate_snapshot_event_id") if body.get(key)]
    scope = freeze.payload
    head = scope.get("candidate_head_commit") or scope.get("target_commit")
    if (any(value != freeze.id for value in ids)
            or any(body.get(k) and head and body[k] != head for k in
                   ("target_commit", "verified_target_commit", "candidate_head_commit"))
            or any(body.get(k) and scope.get(k) and body[k] != scope[k] for k in
                   ("freeze_id", "task_map_generation", "candidate_ref"))):
        return "older_or_different_target"
    if terminal_bound or ids or (body.get("freeze_id") and body["freeze_id"] == scope.get("freeze_id")):
        return "current_freeze"
    return "observed_unbound"


def terminal_bound_receipts(events, run, end, freeze):
    """The production delivery receipt may only carry claim/target; require the full existing completion proof."""
    from zf.runtime.goal_completion_receipt import build_goal_completion_receipt, GoalCompletionReceiptError

    if not freeze or not any(e.type == "run.goal.completed" and e.origin == "kernel" for e in events):
        return set()
    try:
        proof = build_goal_completion_receipt(events, run_id=run, generated_at=end.ts)
    except GoalCompletionReceiptError:
        return set()
    if proof["candidate"]["event_id"] != freeze.id:
        return set()
    return {proof["goal_closure"]["source_event_id"], proof["delivery"]["event_id"]} - {""}


def signed_judge_sources(events, run, proven):
    from zf.runtime.goal_closure_result import GoalClosureResultError, validate_goal_closure_result

    admissions = {}
    signed = {}
    completion = next((e.id for e in reversed(events) if e.type == "run.goal.completed" and e.origin == "kernel"), None)
    for event in events:
        if event.origin != "kernel":
            continue
        body = event.payload
        if (event.type == "workflow.call.result.admitted" and body.get("admission_status") == "admitted"
                and body.get("control_result_schema") == "goal-closure-result.v1"):
            ref = body.get("envelope_ref") or {}
            if isinstance(ref, dict) and ref.get("ref") and ref.get("sha256"):
                admissions[(ref["ref"], ref["sha256"])] = event
        if event.type != "goal.closure.synthesized":
            continue
        if event.id in proven:
            signed[event.id] = completion
            continue
        result = body.get("goal_closure_result")
        if (not isinstance(result, dict) or result.get("workflow_run_id") != run
                or body.get("goal_closure_result_error")):
            continue
        try:
            validate_goal_closure_result(result)
        except GoalClosureResultError:
            continue
        ref = body.get("admitted_call_result_ref") or {}
        if not isinstance(ref, dict):
            continue
        admission = admissions.get((ref.get("ref"), ref.get("sha256")))
        if (admission and admission.payload.get("semantic_verdict") == result["verdict"]
                and (not body.get("operation_id") or body["operation_id"] == admission.payload.get("operation_id"))):
            signed[event.id] = admission.id
    return signed


def intervention_candidates(ledger, window, approve, end, run):
    """Include Web requests whose public audit envelope has no top-level Run ID."""
    ids = {e.id for e in window}
    scoped, unbound = set(), set()
    owner_actions = {"operator.intent.approved", "replan.owner_decision.approved",
                     "replan.owner_decision.rejected", "replan.owner_decision.deferred"}
    for event in ledger[ledger.index(approve) + 1:ledger.index(end) + 1]:
        if event.type == "web.action.requested" and event.actor == "web" and event.origin in {"", "kernel"}:
            body = event.payload
            request = body.get("request") or {}
            target = body.get("workflow_run_id") or body.get("run_id") or request.get("workflow_run_id") or request.get("run_id")
            if target == run or event.id in ids:
                scoped.add(event.id)
            elif not target:
                unbound.add(event.id)
        elif event.id in ids and (event.actor in {"operator", "owner", "human"} or event.type in owner_actions):
            scoped.add(event.id)
    return scoped, unbound


def task_receipts(rows, by_id, state, run):
    """Count the integrated task once, preserving both canonical and compatibility IDs."""
    found = {}
    rejected = []
    positions = {event_id: seq for seq, event_id in enumerate(by_id)}
    for row in rows:
        event = by_id[row["id"]]
        body = event.payload
        task = row["task_id"]
        if event.type == "task.done":
            cause = by_id.get(event.causation_id)
            ref = body.get("integration_receipt_ref")
            if not (task and body.get("authority") == "task_integration_receipt" and cause
                    and cause.type == "integration.queue.integrated" and cause.origin == "kernel"
                    and cause.id == body.get("integration_event_id") and positions[cause.id] < positions[event.id]
                    and isinstance(ref, dict)
                    and ref.get("sha256") and ref["sha256"] == body.get("integration_receipt_digest")
                    and cause.payload.get("receipt_ref") == ref
                    and cause.payload.get("task_id") == task and cause.payload.get("workflow_run_id") == run):
                rejected.append(event.id)
                continue
            try:
                evidence = hydrate_sidecar_ref(state, ref)
            except (SidecarRefError, OSError):
                rejected.append(event.id)
                continue
            expected = {"workflow_run_id": run, "task_id": task, "task_map_generation": body.get("task_map_generation"),
                        "candidate_generation": body.get("candidate_generation"), "new_candidate_head": body.get("candidate_head")}
            if not (evidence.ok and isinstance(evidence.payload, dict)
                    and all(v and evidence.payload.get(k) == v for k, v in expected.items())
                    and all(cause.payload.get(k) == body.get(k) for k in ("candidate_head", "candidate_generation", "task_map_generation"))):
                rejected.append(event.id)
                continue
        key = task or event.id
        if key in found:
            found[key].setdefault("corroborating_event_ids", []).append(event.id)
        else:
            found[key] = row
    return list(found.values()), rejected


def compare_arm(root, arm, controls, protocol, mode, authorized_at):
    state = (root / arm["state_dir"]).resolve()
    if not state.is_relative_to(root.resolve()):
        raise ValueError("state directory must be inside the experiment capture")
    if not (state / "events.jsonl").resolve().is_relative_to(state) or not (state / "events.jsonl").is_file():
        raise ValueError("missing captured event ledger")
    run = arm["run_id"]
    ledger = EventLog(state / "events.jsonl").read_all()
    events = events_for_run(ledger, run_id=run,
                            include_legacy_single_run=False)
    audit = read_capture(root, arm["audit"])
    if (audit["run_id"] != run or audit["evidence_mode"] != mode
            or audit["controls_sha256"] != controls["sha256"]):
        raise ValueError("audit/run/controlled-input binding mismatch")
    by_id = {e.id: e for e in events}
    invoke, approve, end = [by_id[audit[k]] for k in ("invoke", "plan_approval", "window_end")]
    if (invoke.type != "workflow.invoke.requested" or approve.type != "plan.approved"
            or approve.origin == "worker" or events.index(invoke) >= events.index(approve)
            or events.index(approve) >= events.index(end) or time(invoke.ts) < authorized_at):
        raise ValueError("invalid approved experiment window")
    if mode == "real_provider":
        from zf.runtime.workflow_proposal import load_workflow_proposal
        proposal = load_workflow_proposal(state, invoke.payload["workflow_proposal_ref"])
        if (invoke.payload.get("workflow_proposal_digest") != proposal["proposal_digest"]
                or invoke.payload.get("effective_config_ref") != proposal["effective_config_ref"]
                or invoke.payload.get("request_id") != proposal["request_id"]
                or invoke.payload.get("request_revision") != proposal["request_revision"]):
            raise ValueError("historical approved config is not bound to this invoke")
        if not any(e.type == "workflow.invoke.accepted" and e.origin == "kernel" and e.causation_id == invoke.id
                   for e in events[:events.index(approve)]):
            raise ValueError("real window lacks kernel acceptance of the bound invoke")
        if not audit.get("provider_host_receipts"):
            raise ValueError("real window requires captured provider/host provenance")
        for evidence in audit["provider_host_receipts"]:
            body = read_capture(root, evidence)
            if (body.get("run_id") != run or not body.get("provider") or not body.get("trace_ref")
                    or not body.get("model") or not body.get("host_capture_ref")):
                raise ValueError("incomplete real provider/host provenance")
    snapshot_body = snapshot(state, invoke)
    config = snapshot_body["config"]
    oa = config["workflow"]["orchestration"]
    runtime = config["runtime"]
    rm = runtime["run_manager"]["resident_agent"]
    diagnosis = runtime.get("autoresearch_resident", {})
    frozen_controls = common_policy_digest(snapshot_body, (protocol.get("local_bindings") or {}).get(arm["group"]))
    if not oa.get("oa_profile") or oa.get("normal_frontier_owner") != "wrc":
        raise ValueError("both arms require configured OA and WRC")
    if rm["enabled"] != (arm["group"] == "A"):
        raise ValueError("resident RM group differs from frozen configuration")
    reflect = runtime["run_manager"].get("reflect", {})
    consumers = (["resident_rm"] if rm["enabled"] else []) + (["on_demand_reflect"] if reflect.get("enabled") else [])
    if diagnosis.get("enabled") and diagnosis.get("self_repair_consumer"):
        consumers.append("autoresearch")
    if arm["group"] == "B" and not consumers:
        raise ValueError("B requires configured diagnosis consumer")
    window = events[events.index(approve) + 1:events.index(end) + 1]
    ids = {e.id: e for e in window}
    recorded, unbound_web = intervention_candidates(ledger, window, approve, end, run)
    # Off-ledger restarts are visible only to an independent host audit.
    manual = audit.get("interventions")
    if manual is not None and (not isinstance(manual, list)
            or any(not isinstance(row, dict) or not row.get("action") or not row.get("evidence")
                   or not time(approve.ts) <= time(row["at"]) <= time(end.ts) for row in manual)):
        raise ValueError("invalid intervention receipt")
    if manual is not None:
        manual_keys = [r.get("event_id") or (r["at"], r["action"], r["evidence"]) for r in manual]
        if len(manual_keys) != len(set(manual_keys)):
            raise ValueError("duplicate intervention in audit")
    handoffs = audit.get("model_handoff_event_ids")
    if handoffs is not None:
        model_events = {"workflow.operation.started", "workflow.operation.retry_started", "run.manager.agent.recommendation",
                        "run.manager.autoresearch.consumed", "autoresearch.loop.started", "run.manager.reflect.completed"}
        if (len(handoffs) != len(set(handoffs))
                or any(i not in ids or ids[i].type not in model_events for i in handoffs)):
            raise ValueError("duplicate or out-of-window model handoff")
    waits = audit.get("unowned_waits")
    if waits is not None:
        durations = []
        for wait in waits:
            opened, closed = ids[wait["opened"]], ids[wait["closed"]]
            seconds = (time(closed.ts) - time(opened.ts)).total_seconds()
            if seconds < 0 or not wait["evidence"]:
                raise ValueError("invalid unowned wait interval")
            durations.append(seconds)
    case = audit.get("recovery_case_id")
    fault = ids.get(audit.get("fault"))
    effects = [e for e in window if case and fault and e.origin == "kernel"
               and e.type == "run.manager.action.effect.passed" and e.payload.get("recovery_case_id") == case
               and e.payload.get("status") == "passed"
               and (observed := ids.get(e.payload.get("observed_event_id")))
               and observed.id == e.causation_id and observed.type == e.payload.get("observed_event_type")
               and observed.type in protocol["effective_actions"].get(e.payload.get("action"), [])
               and events.index(fault) < events.index(observed) < events.index(e)]
    same_case = fault and (fault.payload.get("recovery_case_id") == case or any(
        e.origin == "kernel" and e.payload.get("recovery_case_id") == case
        and fault.id in (e.payload.get("source_event_ids") or []) for e in window))
    first = effects[0] if same_case and effects else None
    freeze = next((e for e in reversed(window) if e.type == "candidate.ready" and e.origin == "kernel"
                   and e.payload.get("schema_version") == "candidate-freeze-receipt.v1"), None)

    proven = terminal_bound_receipts(events[:events.index(end)+1], run, end, freeze)

    def receipts(*types):
        rows = []
        for e in window:
            if e.origin != "kernel" or e.type not in types:
                continue
            body = e.payload
            projection = body.get("goal_closure_result") or {}
            bindings = {receipt_binding(p, freeze, terminal_bound=e.id in proven) for p in (body, projection)}
            binding = ("older_or_different_target" if "older_or_different_target" in bindings else
                       "current_freeze" if "current_freeze" in bindings else "observed_unbound")
            rows.append({"id": e.id, "type": e.type,
                         "status": projection.get("verdict") or body.get("status"), "binding": binding,
                         "task_id": e.task_id or body.get("task_id")})
        return rows

    judge = receipts("goal.closure.synthesized", "judge.passed", "judge.failed")
    sources = signed_judge_sources(events[:events.index(end)+1], run, proven)
    signed = {r["id"]: r for r in judge if r["id"] in sources}
    judge_receipts = []
    unconfirmed_judge_events = []
    for row in judge:
        e = by_id[row["id"]]
        if e.type == "goal.closure.synthesized":
            if e.id not in signed:
                unconfirmed_judge_events.append(e.id)
                continue
            row["admission_evidence"] = sources[e.id]
        elif (e.causation_id and e.causation_id == e.payload.get("source_event_id")
                and by_id.get(e.causation_id) and by_id[e.causation_id].type == "goal.closure.synthesized"):
            if e.causation_id in signed:
                signed[e.causation_id].setdefault("compatibility_event_ids", []).append(e.id)
            else:
                unconfirmed_judge_events.append(e.id)
            continue
        judge_receipts.append(row)
    delivery = receipts("run.delivery.settled", "ship.completed")
    settled = {r["id"]: r for r in delivery if r["type"] == "run.delivery.settled"}
    ship_counts = {}
    for row in delivery:
        if row["type"] == "ship.completed":
            ship = by_id[row["id"]]
            key = (ship.causation_id, ship.payload.get("source_commit"))
            ship_counts[key] = ship_counts.get(key, 0) + 1
    delivery_receipts = []
    for row in delivery:
        ship = by_id[row["id"]]
        matches = [v for eid, v in settled.items() if ship.type == "ship.completed" and (
            by_id[eid].payload.get("ship_event_id") == ship.id or (
                not by_id[eid].payload.get("ship_event_id") and ship.causation_id and by_id[eid].causation_id == ship.causation_id
                and ship_counts[(ship.causation_id, ship.payload.get("source_commit"))] == 1
                and ship.payload.get("source_commit") and ship.payload["source_commit"] == by_id[eid].payload.get("target_commit")
                and (by_id.get(ship.causation_id) and by_id[ship.causation_id].type == "run.delivery.requested")))]
        if len(matches) == 1:
            matches[0].setdefault("ship_event_ids", []).append(ship.id)
        else:
            delivery_receipts.append(row)

    coverage = audit.get("coverage")
    if coverage:
        if not coverage.get("evaluation") or not coverage["event_ids"] or any(i not in ids for i in coverage["event_ids"]):
            raise ValueError("coverage evaluation requires this window's evidence")
    captures = {name: audit.get(name) for name in ("provider_profiles", "capacity", "permissions",
               "target_baseline", "fault_scenario", "acceptance_contract", "requirements")}
    if any(value is None or value == "" for value in captures.values()) or captures != protocol["controls"]:
        raise ValueError("captured controls differ from the approved experiment protocol")
    confirmed_manual = {str(row.get("event_id")) for row in manual or [] if row.get("event_id")}
    if confirmed_manual - (ids.keys() | recorded | unbound_web):
        raise ValueError("intervention references another window")
    consumer_receipts = {"resident_rm": receipts("run.manager.agent.recommendation.consumed"),
                         "on_demand_reflect": receipts("run.manager.reflect.completed"),
                         "autoresearch": receipts("run.manager.autoresearch.consumed")}
    unseen_owner = sorted((recorded | unbound_web) - confirmed_manual)
    host_observed = mode == "deterministic_replay"
    if mode == "real_provider" and audit.get("host_observation"):
        host = read_capture(root, audit["host_observation"])
        host_observed = (host.get("run_id") == run and host.get("plan_approval") == approve.id
                         and host.get("window_end") == end.id and bool(host.get("collector"))
                         and time(host["from"]) <= time(approve.ts) and time(host["through"]) >= time(end.ts)
                         and host.get("gaps") == [] and bool(host.get("sources"))
                         and all(read_capture(root, ref).get("run_id") == run for ref in host["sources"]))
    completed, unconfirmed_tasks = task_receipts(receipts("task.done", "task.done.accepted"), by_id, state, run)
    return {"group": arm["group"], "run_id": run, "window": [approve.id, end.id], "controls": captures,
            "frozen_controls_digest": frozen_controls,
            "approved_config_digest": invoke.payload["effective_config_ref"]["sha256"],
            "oa": oa["oa_profile"], "resident_rm_enabled": rm["enabled"], "configured_consumers": consumers,
            "observed_consumer_receipts": consumer_receipts,
            "task_completion_events": completed, "unconfirmed_task_event_ids": unconfirmed_tasks, "coverage": coverage,
            "judge_receipts": judge_receipts, "unconfirmed_judge_event_ids": unconfirmed_judge_events,
            "judge_attempt_failures": receipts("goal.closure.synthesis.failed"),
            "delivery_receipts": delivery_receipts,
            "terminal_receipts": receipts("run.goal.completed", "run.goal.blocked", "run.failed"),
            "owner_events_for_review": unseen_owner, "recorded_interventions": len(manual) if manual is not None else None,
            "host_observation_complete": host_observed, "unbound_web_action_ids": sorted(unbound_web),
            "post_approval_interventions": len(manual) if manual is not None and not unseen_owner and host_observed else None,
            "model_handoff_event_ids": handoffs, "model_handoffs": len(handoffs) if handoffs is not None else None,
            "unowned_wait_seconds": durations if waits is not None else None,
            "first_effective_recovery": ({"fault": fault.id, "receipt": first.id, "action": first.payload["action"],
                 "seconds": (time(first.ts) - time(fault.ts)).total_seconds()} if first else None),
            "recovery_effects": build_recovery_metrics(window)["counts"],
            "automation_signals": cross_cutting_scorecard(window)}


def compare(path):
    pair = json.loads(path.read_text())
    mode = pair["evidence_mode"]
    if mode not in {"deterministic_replay", "real_provider"}:
        raise ValueError("unknown evidence_mode")
    root = path.parent
    controls = pair["controls"]
    protocol = read_capture(root, controls)
    if not protocol.get("effective_actions"):
        raise ValueError("experiment must define effective recovery evidence before launch")
    if not pair.get("experiment_id") or not pair.get("authorization_ref"):
        raise ValueError("experiment identity/authorization is missing")
    arms = [compare_arm(root, arm, controls, protocol, mode, time(pair["authorized_at"])) for arm in pair["arms"]]
    if (len(arms) != 2 or {a["group"] for a in arms} != {"A", "B"}
            or arms[0]["run_id"] == arms[1]["run_id"] or arms[0]["controls"] != arms[1]["controls"]
            or arms[0]["frozen_controls_digest"] != arms[1]["frozen_controls_digest"]
            or arms[0]["oa"] != arms[1]["oa"]):
        raise ValueError("A/B must have distinct Runs, common OA and identical experimental controls")
    return {"experiment_id": pair["experiment_id"], "evidence_mode": mode,
            "decision": "evidence_for_owner_review", "arms": arms}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pair", type=Path)
    try:
        print(json.dumps(compare(parser.parse_args().pair), indent=2, ensure_ascii=False))
    except (KeyError, ValueError, OSError, TypeError) as exc:
        parser.exit(2, f"invalid pilot evidence: {exc}\n")
