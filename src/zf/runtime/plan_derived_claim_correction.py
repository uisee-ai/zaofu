"""Exact owner corrections to task-derived Goal claims before business Plan approval."""
from __future__ import annotations

from copy import deepcopy

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import canonical_json_sha256, write_immutable_json_sidecar
from zf.runtime.goal_claim_set import _explicit_goal_claims, _task_acceptance_claims
from zf.runtime.run_scope import event_run_id, run_aliases
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.stage_replan_generation import completed_generation_source
from zf.runtime.workflow_operation import operation_request_hash


KEY = "derived_claim_correction"
ACTION = "stage-replan-new-generation"
FIELDS = {"package_digest", "claim_set_sha256", "goal_claim_id", "before", "after",
          "operation_request_event_id", "requirement_sha256", "quote"}


def _web_request(event):
    return (event and event.type == "web.action.requested" and event.actor == "web"
            and event.origin in {"kernel", ""} and event.payload.get("action") == ACTION
            and not event.payload.get("source_session_id"))


def _receipts(events, run):
    requests = {}
    for item in events:
        receipt = ZfEvent.from_dict(item) if isinstance(item, dict) else item
        if _web_request(receipt):
            requests[receipt.id] = receipt
            continue
        p = receipt.payload
        if (receipt.type != "runtime.action.completed" or receipt.actor != "web"
                or receipt.origin not in {"kernel", ""} or p.get("status") != "applied"
                or p.get("action") != ACTION or p.get("run_id") != run or KEY not in p):
            continue
        request = requests.get(receipt.causation_id)
        if not request:
            continue
        body = request.payload.get("request") or {}
        if (body.get("workflow_run_id") == run and body.get(KEY) == p[KEY]
                and body.get("stage_replan_generation") == p.get("stage_replan_generation")
                and body.get("approval_ref") == p.get("approval_ref")):
            yield receipt


def correction_sources(state, scope, *, attempt_id, operation_id, read_events):
    """Keep already issued WRCs on their original prefix, including legacy requests with no correction."""
    generation, package = scope.get("stage_replan_generation"), scope.get("plan_artifact_package_digest")
    run = scope.get("workflow_run_id") or scope.get("trace_id")
    if not (run and generation and package and (operation_id or attempt_id) and read_events):
        return []
    events = read_events()
    for index, event in enumerate(events):
        if (event.type == "workflow.operation.requested" and event.origin == "kernel"
                and event.payload.get("workflow_run_id") == run
                and (operation_id and event.payload.get("operation_id") == operation_id
                     or attempt_id and event.payload.get("active_attempt_id") == attempt_id)):
            events = events[:index]
            break
    rows = [{"owner_request_event_id": e.causation_id, "receipt_event_id": e.id,
             "approval_ref": e.payload["approval_ref"], **e.payload[KEY]}
            for e in _receipts(events, run) if e.payload["stage_replan_generation"] == generation
            and e.payload[KEY]["package_digest"] == package]
    if not rows:
        return []
    spec = {"schema_version": "owner-derived-claim-corrections.v1", "workflow_run_id": run,
            "stage_replan_generation": generation, "corrections": rows}
    desc = write_immutable_json_sidecar(state, spec, root="plan-amendments/derived-claims",
        kind="owner_derived_claim_corrections", schema_version=spec["schema_version"],
        created_by="plan-owner-correction", source_event_id=rows[-1]["receipt_event_id"])
    return [{**desc, "source_id": "critical-owner-claim-corrections",
             "artifact_id": "owner-derived-claim-corrections.json", "allowed_paths": ["$"]}]


def corrected_claim_baseline(previous, package, events, run, generation):
    """Only mutate the comparison copy. A fresh package pins the candidate's actual text."""
    if not previous or not generation:
        return previous, []
    receipts = [e for e in _receipts(events, run) if e.payload.get("stage_replan_generation") == generation]
    if not receipts:
        return previous, []
    digest, claim_digest = canonical_json_sha256(package), canonical_json_sha256(previous)
    baseline = deepcopy(previous)
    changed = []
    for receipt in receipts:
        correction = receipt.payload[KEY]
        if correction["package_digest"] != digest or correction["claim_set_sha256"] != claim_digest:
            continue
        claim = next((c for c in baseline["claims"] if c["goal_claim_id"] == correction["goal_claim_id"]), {})
        if claim.get("mandatory") is True and claim.get("text") == correction["before"]:
            claim["text"] = correction["after"]
            changed.append(claim)
    return baseline, changed


def correction_origin_errors(changed, current, task_map):
    """A duplicate explicit claim cannot hide a dropped, moved or downgraded acceptance criterion."""
    errors = []
    for original in changed:
        claim_id = original["goal_claim_id"]
        task = next((t for t in task_map.get("tasks", []) if t.get("task_id", t.get("id")) ==
                     original["source_ref"].removeprefix("task-map:")), {})
        items = task.get("acceptance_criteria") or task.get("acceptance") or []
        valid = not _explicit_goal_claims(task_map) and original in _task_acceptance_claims({"tasks": [task]})
        mandatory = all(a.get("mandatory", True) is True for a in items if isinstance(a, dict)
                        and (a.get("goal_claim_id") or a.get("acceptance_id") or a.get("id")) == claim_id)
        if not valid or not mandatory or original not in current.get("claims", []):
            errors.append({"code": "mandatory_claim_origin_changed", "goal_claim_id": claim_id,
                           "previous_text": original["text"], "current_text": "", "waiver_ref": ""})
    return errors


def _frozen_source(state, operation, run, generation, change):
    if (not operation or operation.actor != "zf-cli" or operation.origin != "kernel"
            or operation.type != "workflow.operation.requested" or operation.payload.get("workflow_run_id") != run):
        raise ValueError("missing a kernel-issued Plan operation for this Run")
    envelope = hydrate_sidecar_ref(state, operation.payload["request_ref"]).payload
    if (operation_request_hash(envelope) != operation.payload.get("request_hash")
            or envelope.get("workflow_run_id") != run or envelope.get("attempt_domain") != "plan"):
        raise ValueError("Plan request identity mismatch")
    contract = envelope["request"]
    trigger = contract.get("plan_candidate_validation", {}).get("manifest", {}).get("trigger_payload", {})
    if (trigger.get("stage_replan_generation") != generation
            or trigger.get("plan_artifact_package_digest") != change["package_digest"]
            or trigger.get("goal_claim_set_digest") != change["claim_set_sha256"]):
        raise ValueError("source request belongs to a different owner generation or candidate")
    attempt, digest = envelope["active_attempt_id"], contract["source_manifest_digest"]
    manifest = hydrate_sidecar_ref(state, {
        "ref": f"artifacts/attempts/{attempt}/source-manifests/{digest}.json", "sha256": digest,
    }).payload
    sources = [r for r in manifest["sources"] if r.get("kind") == "requirement_authority"
               and r.get("sha256") == change["requirement_sha256"] and "$" in r.get("allowed_paths", [])]
    if len(sources) != 1 or manifest.get("attempt_id") != attempt:
        raise ValueError("source is not an unambiguous frozen Requirement authority")
    blob = hydrate_sidecar_ref(state, {"ref": sources[0]["ref"], "sha256": change["requirement_sha256"]})
    if not _contains_quote(blob.payload, change["quote"]):
        raise ValueError("owner evidence quote is absent from the frozen Requirement")


def _contains_quote(value, quote):
    if isinstance(value, str):
        return quote in value
    if isinstance(value, (list, dict)):
        return any(_contains_quote(v, quote) for v in (value.values() if isinstance(value, dict) else value))
    return False


def _authorize(service, requested, payload, events):
    from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package, reduce_plan_artifact_packages

    run, generation, change = payload["workflow_run_id"], payload["stage_replan_generation"], payload[KEY]
    by_id = {e.id: e for e in events}
    if (service.actor != "web" or service.surface != "web" or not _web_request(requested)
            or by_id.get(requested.id) != requested or requested.payload.get("request") != payload):
        raise ValueError("a token-gated owner Web request is required")
    aliases = run_aliases(events)
    related = [e for e in events if event_run_id(e, aliases=aliases) == run]
    from zf.runtime.plan_approval_replay import pending_plan_requests

    if (pending_plan_requests(related) or any(e.type in {"run.completed", "run.cancelled"} or
            (e.type == "plan.approved" and e.actor == "operator" and not e.payload.get("semantic_control")
             and not e.payload.get("auto")) for e in related)):
        raise ValueError("this Run reached business Plan review or closure")
    goal = next((e for e in reversed(related) if e.type == "run.goal.updated"
                 and e.payload.get("stage_replan_generation")), None)
    if not goal or goal.payload["stage_replan_generation"] != generation:
        raise ValueError("owner stage generation is stale")
    receipt = next((e for e in reversed(related) if e.payload.get("run_goal_update_event_id") == goal.id
                    and e.payload.get("stage_replan_generation") == generation
                    and completed_generation_source(e, by_id, aliases)), None)
    if (not receipt or receipt.payload.get("escalation_event_id") != payload["escalation_event_id"]
            or not _web_request(by_id.get(goal.causation_id))):
        raise ValueError("missing completed Owner authorization for this stage generation")
    prior = reduce_plan_artifact_packages(events, workflow_run_id=run)["current"] or {}
    admitted = by_id.get(prior.get("event_id"))
    if (prior.get("package_digest") != change["package_digest"] or not admitted
            or admitted.origin != "kernel" or admitted.actor != "zf-cli"):
        raise ValueError("prior Plan Package is no longer current")
    package = hydrate_plan_artifact_package(service.state_dir, {
        "ref": prior["package_ref"], "sha256": change["package_digest"],
    }, validate_ports=False)
    ports = {r["logical_name"]: r for r in [*package["produced"], *package["inherited"]]}
    if ports["goal_claim_set"]["sha256"] != change["claim_set_sha256"]:
        raise ValueError("Goal claim-set digest does not match the prior Plan Package")
    claims = hydrate_sidecar_ref(service.state_dir, ports["goal_claim_set"]).payload
    task_map = hydrate_sidecar_ref(service.state_dir, ports["task_map"]).payload
    original = next((c for c in claims["claims"] if c["goal_claim_id"] == change["goal_claim_id"]), None)
    if (not original or original["mandatory"] is not True or original["text"] != change["before"]
            or claims["source"] not in {"task_map.acceptance_criteria_fallback",
                                       "objective.acceptance+task_map.acceptance_criteria_fallback"}
            or _explicit_goal_claims(task_map) or original not in _task_acceptance_claims(task_map)):
        raise ValueError("only an exact, mandatory task-acceptance fallback claim can be corrected")
    _frozen_source(service.state_dir, by_id.get(change["operation_request_event_id"]), run, generation, change)
    existing = next((e for e in _receipts(events, run) if e.payload[KEY]["package_digest"] == change["package_digest"]
                     and e.payload[KEY]["goal_claim_id"] == change["goal_claim_id"]
                     and e.payload["stage_replan_generation"] == generation), None)
    if existing:
        raise ValueError(f"claim already has a binding Owner correction: {existing.id}")


def apply_owner_correction(service, requested, action, requested_action, payload):
    base_fields = {KEY, "workflow_run_id", "stage_replan_generation", "escalation_event_id", "approval_ref"}
    try:
        change = payload[KEY]
        if (set(payload) != base_fields or not isinstance(change, dict) or set(change) != FIELDS
                or any(not isinstance(v, str) or not v.strip() or len(v) > 4096
                       for v in [*(payload[k] for k in base_fields - {KEY}), *change.values()])
                or change["after"].strip() != change["after"] or change["before"] == change["after"]):
            raise ValueError("exact Run, generation, package, claim, replacement and Requirement quote required")
        with locked_path(service.state_dir / "locks" / "plan-approval-decisions"):
            _authorize(service, requested, payload, service.writer.event_log.read_all())
            return service._recovery_ok(requested, requested, action, requested_action, {
                "run_id": payload["workflow_run_id"], "stage_replan_generation": payload["stage_replan_generation"],
                "approval_ref": payload["approval_ref"], KEY: change,
            })
    except (ValueError, KeyError, OSError, TypeError) as exc:
        return service._recovery_failed(requested, action, requested_action, str(exc), 409)
