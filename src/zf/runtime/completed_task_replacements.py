"""Prove which historical completions have verified successors in this Candidate."""
from dataclasses import replace
from pathlib import Path

from zf.runtime.candidate_continuation import _json
from zf.runtime.candidate_replacement_authority import verified_predecessors
from zf.runtime.goal_retained_task_settlement import _task_map, _task_rows
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.plan_completion_reuse import _native_proof
from zf.runtime.task_map_successor import task_map_successor_base_commit, task_map_supersedes_task_ids
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts


def completed_task_replacements(state: Path, events: list, candidate: dict, done_ids: set[str]) -> dict:
    """Only remove an old completion through the pinned Plan ancestry and native Verify."""
    run = candidate.get("workflow_run_id")
    generation = candidate.get("task_map_generation")
    contexts = task_pipeline_generation_contexts(events)
    historic = {task_id for task_id in done_ids if task_id in contexts
                and contexts[task_id].get("workflow_run_id") == run
                and contexts[task_id].get("task_map_generation") != generation}
    if not historic or not candidate.get("plan_artifact_package_ref"):
        return {}
    admissions = {e.payload.get("plan_artifact_package_digest"): e for e in events
                  if e.type == "task.pipeline.generation.admitted" and e.origin == "kernel"
                  and e.payload.get("workflow_run_id") == run}
    current = (candidate["plan_artifact_package_ref"], candidate.get("plan_artifact_package_digest"))
    plans = {}

    def plan(ref):
        if ref not in plans:
            package = hydrate_plan_artifact_package(state, {"ref": ref[0], "sha256": ref[1]}, validate_ports=False)
            port, body = _task_map(state, package)
            if package["workflow_run_id"] != run:
                raise ValueError("Candidate replacement Plan belongs to a different Run")
            plans[ref] = package, port, body, _task_rows(body)
        return plans[ref]

    pkg, port, _, rows = plan(current)
    missing = historic - rows.keys()
    if not missing:
        return {}
    if (pkg["task_map_generation"] != generation
            or (candidate.get("task_map_ref"), candidate.get("task_map_digest")) != (port["ref"], port["sha256"])):
        raise ValueError("Candidate replacement Plan differs from its frozen Task Map")
    done = {(e.task_id, e.payload.get("task_map_generation")): e for e in events
            if e.type == "task.done" and e.origin == "kernel" and e.payload.get("workflow_run_id") == run}
    proofs = {}
    for task_id in sorted(missing):
        source = contexts[task_id]
        source_ref = (source.get("plan_artifact_package_ref"), source.get("plan_artifact_package_digest"))
        chain = []
        ref = current
        while ref != source_ref:
            if not all(ref) or ref in chain:
                raise ValueError("Candidate lacks the original admitted Plan for a completed Task: " + task_id)
            chain.append(ref)
            package, _, _, _ = plan(ref)
            ref = (package.get("supersedes_package_ref"), package.get("supersedes_package_digest"))
        package, prior_port, _, prior_rows = plan(ref)
        if (task_id not in prior_rows or package["task_map_generation"] != source["task_map_generation"]
                or (prior_port["ref"], prior_port["sha256"]) != (source.get("task_map_ref"), source.get("task_map_digest"))):
            raise ValueError("Candidate predecessor does not belong to its original Plan: " + task_id)
        frontier = {task_id}
        retired = set()
        links = []
        for ref in reversed(chain):
            package, port, body, rows = plan(ref)
            removed = frontier - rows.keys()
            if retired.intersection(rows):
                raise ValueError("Candidate replacement lineage reintroduces a retired Task")
            if removed:
                amend = body.get("amend") or {}
                admission = admissions.get(ref[1])
                if (not admission or admission.payload.get("task_map_generation") != package["task_map_generation"]
                        or admission.payload.get("plan_artifact_package_ref") != ref[0]
                        or (admission.payload.get("task_map_ref"), admission.payload.get("task_map_digest"))
                        != (port["ref"], port["sha256"])
                        or amend.get("supersedes_task_map_ref") != prior_port["ref"]
                        or not removed.issubset(amend.get("superseded_task_ids") or [])):
                    raise ValueError("Candidate lacks an admitted amendment for completed Task: " + task_id)
                edges = {}
                children = {}
                for old in sorted(removed):
                    successors = sorted(k for k, row in rows.items() if old in task_map_supersedes_task_ids(row))
                    if not successors:
                        raise ValueError("Candidate lacks an explicit successor for completed Task: " + old)
                    edges[old] = successors
                    for successor in successors:
                        if successor in children:
                            continue
                        proof = _successor_proof(state, events, done, run, package["task_map_generation"], successor)
                        children[successor] = proof
                pinned = replace(admission, payload={**admission.payload, "candidate_ref": candidate["candidate_ref"]})
                bases = {task_map_successor_base_commit(rows[child]) for child in children}
                history = _prior_freezes(state, events[:events.index(admission)], pinned.payload, bases)
                if not removed.issubset(verified_predecessors(state, pinned, history, list(children.values()))):
                    raise ValueError("Candidate lacks verified successor evidence on its predecessor freeze: " + task_id)
                links.append({"generation_event_id": admission.id, "package_digest": ref[1],
                              "edges": edges, "verification_event_ids": {
                                  k: children[k]["verification_event_id"] for k in sorted(children)}})
                retired.update(removed)
                frontier = (frontier - removed) | set(children)
            prior_port = port
        if not frontier.issubset(done_ids & set(rows)):
            raise ValueError("Candidate lacks a completed terminal successor: " + task_id)
        proofs[task_id] = links
    return proofs


def _prior_freezes(state, events, pinned, bases):
    history = []
    for event in events:
        body = event.payload
        if (event.type != "candidate.ready" or event.origin != "kernel"
                or body.get("workflow_run_id") != pinned["workflow_run_id"]
                or body.get("candidate_ref") != pinned["candidate_ref"]
                or body.get("candidate_head_commit") not in bases or body.get("repaired_failure_event_id")
                or body.get("schema_version") != "candidate-freeze-receipt.v1"):
            continue
        descriptor = body.get("freeze_receipt_ref") or {}
        receipt = _json(state, descriptor)
        if (body.get("freeze_receipt_digest") != descriptor.get("sha256")
                or receipt.get("status") != "frozen"
                or any(receipt.get(k) != body.get(k) for k in (
                    "schema_version", "freeze_id", "workflow_run_id", "task_map_generation", "candidate_ref",
                    "candidate_head_commit", "completed_task_ids", "plan_artifact_package_digest"))):
            raise ValueError("Candidate replacement needs an intact original freeze receipt")
        history.append(event)
    return history


def _successor_proof(state, events, done, run, generation, task_id):
    from zf.runtime.candidate_verification_authority import _latest_task_verification

    completion = done.get((task_id, generation))
    if not completion:
        raise ValueError("Candidate replacement requires completed integration: " + task_id)
    receipt = _json(state, completion.payload.get("integration_receipt_ref") or {})
    verify = _latest_task_verification(events[:events.index(completion)], workflow_run_id=run,
        task_id=task_id, task_map_generation=generation, target_commit=receipt.get("task_commit"))
    if verify is None:
        raise ValueError("Candidate replacement requires independent Task Verify: " + task_id)
    _native_proof(state, events[:events.index(completion)], completion, verify)
    return {"task_id": task_id, "verification_event_id": verify.id,
            "contract_snapshot_ref": verify.payload["contract_snapshot_ref"],
            "contract_snapshot_digest": verify.payload["contract_snapshot_digest"]}


def validate_completed_task_replacements(state_dir: Path, events_before_freeze: list, candidate: dict) -> None:
    """Recompute admitted replacement edges even if the supplied receipt omitted one."""
    run = candidate.get("workflow_run_id")
    contexts = task_pipeline_generation_contexts(events_before_freeze)
    done = {e.task_id for e in events_before_freeze if e.type == "task.done" and e.origin == "kernel"
            and e.payload.get("workflow_run_id") == run and e.task_id in contexts
            and contexts[e.task_id].get("task_map_generation") == e.payload.get("task_map_generation")}
    proved = completed_task_replacements(state_dir, events_before_freeze, candidate, done)
    completed = set(candidate.get("completed_task_ids", []))
    superseded = set(candidate.get("superseded_task_ids", []))
    if (proved != candidate.get("completed_task_replacements", {})
            or set(proved).intersection(completed) or not set(proved).issubset(superseded)
            or (proved and not (done - proved.keys()).issubset(completed))):
        raise ValueError("Candidate frozen roster differs from the admitted completed-Task replacement lineage")


def freeze_roster(runtime, events, context, branch, head, completed_ids, cancelled):
    from zf.runtime.task_pipeline_terminal import _digest

    try:
        replacements = completed_task_replacements(
            Path(runtime.state_dir), events, {**context, "candidate_ref": f"refs/heads/{branch}"},
            set(completed_ids),
        )
    except (ValueError, OSError, KeyError, TypeError) as exc:
        report_blocked_freeze(runtime, context, head, str(exc))
        return None
    ledger = [{"task_id": event.task_id, "event_id": event.id,
               "superseded_by_task_map_ref": event.payload.get("superseded_by_task_map_ref")}
              for event in cancelled]
    if replacements:
        ledger.append({"completed_task_replacements": replacements})
    event_ids = [event.id for event in cancelled]
    event_ids.extend(link["generation_event_id"] for links in replacements.values() for link in links)
    return {
        "completed_task_ids": [task_id for task_id in completed_ids if task_id not in replacements],
        "superseded_task_ids": sorted({event.task_id for event in cancelled} | replacements.keys()),
        "task_supersession_event_ids": list(dict.fromkeys(event_ids)),
        "supersession_ledger_digest": _digest(ledger),
        **({"completed_task_replacements": replacements} if replacements else {}),
    }


def report_blocked_freeze(runtime, context, head: str, reason: str) -> None:
    """Expose a shared pre-freeze failure once per bound target and disposition."""
    from zf.core.events import ZfEvent
    from zf.core.state.locks import locked_path

    source = context["generation_admitted_event_id"]
    run = context["workflow_run_id"]
    payload = {key:context.get(key, "") for key in (
        "workflow_run_id", "generation_id", "task_map_generation", "plan_artifact_package_digest",
        "plan_artifact_package_ref", "task_map_ref", "task_map_digest", "generation_admitted_event_id",
    )}
    payload.update(source="task_pipeline_freeze", diagnosis_owner="run_manager", failure_kind="infra",
                   failure_scope="candidate", failure_class="candidate_authority_invalid",
                   candidate_head_commit=head, reason=reason)
    with locked_path(Path(runtime.state_dir) / "locks" / "candidate-freeze-blocked.lock"):
        events = runtime.event_log.read_all()
        if any(e.type == "candidate.freeze.blocked" and e.origin == "kernel" and e.causation_id == source
               and e.payload == payload for e in events):
            return
        runtime.event_writer.append(ZfEvent(type="candidate.freeze.blocked", origin="kernel", actor="zf-cli",
                                    correlation_id=run, causation_id=source, payload=payload))
