#!/usr/bin/env python3
"""Compare two captured §8 windows. Reads evidence only; never starts a Run."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path

from zf.core.events.log import EventLog
from zf.runtime.automation_metrics import cross_cutting_scorecard
from zf.runtime.recovery_case import build_recovery_metrics
from zf.runtime.run_scope import events_for_run
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


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
    return loaded.payload["config"]


def compare_arm(root, arm, controls, protocol, mode, authorized_at):
    state = (root / arm["state_dir"]).resolve()
    if not state.is_relative_to(root.resolve()):
        raise ValueError("state directory must be inside the experiment capture")
    if not (state / "events.jsonl").resolve().is_relative_to(state) or not (state / "events.jsonl").is_file():
        raise ValueError("missing captured event ledger")
    run = arm["run_id"]
    events = events_for_run(EventLog(state / "events.jsonl").read_all(), run_id=run,
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
    config = snapshot(state, invoke)
    oa = config["workflow"]["orchestration"]
    rm = config["runtime"]["run_manager"]["resident_agent"]
    diagnosis = config["runtime"].get("autoresearch_resident", {})
    runtime = config["runtime"]
    compared = {key: config.get(key) for key in ("roles", "safety", "security", "quality_gates",
                "providers", "constraints", "verification", "global_budget_usd", "budget_enforcement_enabled")}
    compared["workflow"] = {key: config["workflow"].get(key) for key in
                            ("orchestration", "execution_profiles", "pipelines", "run_limits")}
    compared["runtime_policy"] = {key: runtime.get(key) for key in
                                  ("execution_routing", "host_verification", "task_publication")}
    # Protected cardinality, roles, models, gates and execution permissions are
    # compared separately from the experimenter's captured input attestations.
    frozen_controls = hashlib.sha256(json.dumps(compared, sort_keys=True).encode()).hexdigest()
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
    recorded = [e.id for e in window if e.actor in {"operator", "owner", "human"} or
                e.type in {"operator.intent.approved", "replan.owner_decision.approved",
                           "replan.owner_decision.rejected", "replan.owner_decision.deferred"}]
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

    def receipts(*types):
        result = []
        for e in window:
            if e.origin != "kernel" or e.type not in types:
                continue
            binding = "observed_unbound"
            if freeze:
                keys = ("freeze_id", "task_map_generation", "candidate_head_commit")
                if any(e.payload.get(k) and freeze.payload.get(k) and e.payload[k] != freeze.payload[k] for k in keys):
                    binding = "older_or_different_target"
                elif (e.payload.get("candidate_snapshot_event_id") == freeze.id or
                      e.payload.get("freeze_id") and e.payload["freeze_id"] == freeze.payload.get("freeze_id")):
                    binding = "current_freeze"
            result.append({"id": e.id, "type": e.type, "status": e.payload.get("status"), "binding": binding,
                           "task_id": e.task_id or e.payload.get("task_id")})
        return result

    coverage = audit.get("coverage")
    if coverage:
        if not coverage.get("evaluation") or not coverage["event_ids"] or any(i not in ids for i in coverage["event_ids"]):
            raise ValueError("coverage evaluation requires this window's evidence")
    captures = {name: audit.get(name) for name in ("provider_profiles", "capacity", "permissions",
               "target_baseline", "fault_scenario", "acceptance_contract", "requirements")}
    if any(value is None or value == "" for value in captures.values()) or captures != protocol["controls"]:
        raise ValueError("captured controls differ from the approved experiment protocol")
    confirmed_manual = {str(row.get("event_id")) for row in manual or [] if row.get("event_id")}
    if confirmed_manual - ids.keys():
        raise ValueError("intervention references another window")
    consumer_receipts = {"resident_rm": receipts("run.manager.agent.recommendation.consumed"),
                         "on_demand_reflect": receipts("run.manager.reflect.completed"),
                         "autoresearch": receipts("run.manager.autoresearch.consumed")}
    unseen_owner = sorted(set(recorded) - confirmed_manual)
    return {"group": arm["group"], "run_id": run, "window": [approve.id, end.id], "controls": captures,
            "frozen_controls_digest": frozen_controls,
            "approved_config_digest": invoke.payload["effective_config_ref"]["sha256"],
            "oa": oa["oa_profile"], "resident_rm_enabled": rm["enabled"], "configured_consumers": consumers,
            "observed_consumer_receipts": consumer_receipts,
            "task_completion_events": receipts("task.done.accepted"), "coverage": coverage,
            "judge_receipts": receipts("judge.passed", "judge.failed"),
            "delivery_receipts": receipts("run.delivery.settled", "ship.completed"),
            "terminal_receipts": receipts("run.goal.completed", "run.goal.blocked", "run.failed"),
            "owner_events_for_review": unseen_owner, "recorded_interventions": len(manual) if manual is not None else None,
            "post_approval_interventions": len(manual) if manual is not None and not unseen_owner else None,
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
