"""Derive an optional, evidence-bound local Critic input policy.

Every ambiguity returns the original full-read policy. The certificate is an
agent's scope claim, not an approval; all other sources and gates stay intact.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent
from zf.runtime.plan_local_review import scoped_port_paths
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, sidecar_path


def scope_critic_reads(
    state_dir: Path, *, events: Sequence[ZfEvent], manifest: Mapping[str, Any],
    required: list[dict[str, Any]], explicit: list[dict[str, Any]], critic_role: str,
    fanout_id: str, stage_id: str, plan_revision: str,
) -> list[dict[str, Any]]:
    """Return a narrower policy only with a complete reproducible proof."""
    try:
        return _scope(state_dir, events=events, manifest=manifest, required=required, explicit=explicit,
                      critic_role=critic_role, fanout_id=fanout_id, stage_id=stage_id, plan_revision=plan_revision)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration):
        return required


def _scope(
    state_dir: Path, *, events: Sequence[ZfEvent], manifest: Mapping[str, Any],
    required: list[dict[str, Any]], explicit: list[dict[str, Any]], critic_role: str,
    fanout_id: str, stage_id: str, plan_revision: str,
) -> list[dict[str, Any]]:
    sources = [s for s in manifest.get("sources", []) if isinstance(s, Mapping)]
    run = str(manifest.get("workflow_run_id") or "")
    if explicit or not all((run, critic_role, fanout_id, stage_id, plan_revision)):
        return required

    def one(predicate):
        found = [s for s in sources if predicate(s)]
        if len(found) != 1:
            raise ValueError("local review source is missing or ambiguous")
        return found[0]

    def read(source):
        from zf.runtime.artifact_source_versions import read_source_version

        if "$" not in source.get("allowed_paths", ["$"]):
            raise ValueError("local review source does not authorize full selection")
        raw, _ = read_source_version(state_dir, dict(source), path=sidecar_path(state_dir, source["ref"]))
        if hashlib.sha256(raw).hexdigest() != source.get("sha256"):
            raise ValueError("local review source digest differs")
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("local review source must be an object")
        return body

    certificate_source = one(lambda s: str(s.get("source_id") or "").startswith("child-artifact-")
                             and s.get("artifact_id") == "scoped_plan_review.json")
    certificate = read(certificate_source)
    if (certificate.get("schema_version") != "scoped-plan-review.v1"
            or certificate.get("workflow_run_id") != run
            or certificate.get("scope_complete") is not True
            or certificate.get("verification_responsibility_preserved") is not True):
        return required
    if not _has_full_read(required, certificate_source):
        return required
    affected = certificate.get("affected_task_ids")
    evidence = certificate.get("evidence_refs")
    ports = certificate.get("ports")
    if (not isinstance(affected, list) or not all(isinstance(x, str) for x in affected)
            or not isinstance(evidence, list) or not evidence
            or not isinstance(ports, list) or not ports):
        return required
    authority = [s for s in sources if s.get("source_id") in {"requirement", "goal-objective"}
                 or str(s.get("source_id") or "").startswith("critical-")]
    if (not authority or any(not _has_full_read(required, source) for source in authority)
            or not any(ref in {s.get("ref"), s.get("source_id")} for ref in evidence for s in authority)):
        return required

    rework_source = one(lambda s: s.get("source_id") == "plan-rework-context")
    rework = read(rework_source)
    if (not rework.get("rework_of") or any(rework.get(key) for key in (
            "requirement_amendment_ref", "human_resolution", "owner_decision_resolution", "operator_override"))):
        return required
    requested = rework.get("task_ids")
    if (not isinstance(requested, list) or not requested or len(requested) != len(affected)
            or set(requested) != set(affected)):
        return required
    package_source = one(lambda s: s.get("source_id") == "plan-rework-package")
    from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package

    package = hydrate_plan_artifact_package(state_dir, {"ref": package_source["ref"],
                                                       "sha256": package_source["sha256"]})
    if (package.get("workflow_run_id") != run
            or certificate.get("baseline_package_sha256") != package_source.get("sha256")):
        return required
    from zf.runtime.plan_artifact_package import reduce_plan_artifact_packages

    current = reduce_plan_artifact_packages(events, workflow_run_id=run).get("current") or {}
    if current.get("package_digest") != package_source.get("sha256"):
        return required
    previous = _previous_critic_manifest(state_dir, events, run, package_source["sha256"])
    if _authority_digests(previous.get("sources", [])) != _authority_digests(authority):
        return required
    prior_required = previous.get("_critic_required_reads") or []
    prior_observed = previous.get("_critic_observed_reads") or []
    prior_authority = [source for source in previous.get("sources", []) if isinstance(source, Mapping)
                       and (source.get("source_id") in {"requirement", "goal-objective"}
                            or str(source.get("source_id") or "").startswith("critical-"))]
    if any(not _has_full_read(prior_required, source) or not _has_observed_read(prior_observed, source)
           for source in prior_authority):
        return required
    if any(s.get("source_id") == "owner-requirement-amendment" for s in sources):
        return required

    contract_source = one(lambda s: s.get("source_id") == "plan-synth-contract")
    if not _has_full_read(required, contract_source):
        return required
    contract = read(contract_source)
    if (contract.get("schema_version") != "plan-synth-contract.v1" or contract.get("workflow_run_id") != run
            or contract.get("fanout_id") != fanout_id or contract.get("stage_id") != stage_id
            or contract.get("plan_revision") != plan_revision):
        return required
    trigger_id = str(contract.get("trigger_event_id") or "")
    if not trigger_id or manifest.get("source_event_id") != trigger_id:
        return required
    triggers = [event for event in events if event.id == trigger_id and event.origin == "kernel"]
    starts = [event for event in events if event.type == "fanout.started" and event.origin == "kernel"
              and event.payload.get("fanout_id") == contract.get("fanout_id")
              and event.payload.get("stage_id") == contract.get("stage_id")
              and event.payload.get("trigger_event_id") == trigger_id
              and event.payload.get("workflow_run_id") == run]
    if (len(triggers) != 1 or len(starts) != 1 or starts[0].causation_id != trigger_id
            or triggers[0].payload.get("workflow_run_id") != run
            or triggers[0].payload.get("rework_of") != rework.get("rework_of")
            or triggers[0].payload.get("plan_artifact_package_digest") != package_source.get("sha256")
            or triggers[0].payload.get("task_ids") != requested):
        return required
    bindings = contract.get("source_bindings")
    if not isinstance(bindings, list):
        return required

    def bound(source):
        return any(isinstance(item, Mapping) and all(item.get(k) == source.get(k)
                   for k in ("source_id", "artifact_id", "ref", "sha256")) for item in bindings)

    if not all(bound(source) for source in (certificate_source, package_source, rework_source)):
        return required
    report_source = one(lambda s: str(s.get("source_id") or "").startswith("child-result-"))
    if not bound(report_source) or not any(
        isinstance(item, Mapping) and all(item.get(k) == report_source.get(k)
        for k in ("source_id", "artifact_id", "sha256"))
        for item in contract.get("child_bindings", [])
    ):
        return required
    report = read(report_source)
    if not any(isinstance(item, Mapping) and item.get("child_id") == report.get("child_id")
               and item.get("source_id") == report_source.get("source_id")
               for item in contract.get("child_bindings", [])):
        return required
    if not _independent_producer(events, contract, report, critic_role):
        return required
    candidate_ports: dict[str, Mapping[str, Any]] = {}
    for item in report.get("plan_ports", []):
        if not isinstance(item, Mapping) or not item.get("logical_name") or not item.get("sha256") or not item.get("ref"):
            return required
        name = str(item["logical_name"])
        if name in candidate_ports:
            return required
        candidate_ports[name] = item
    old_ports = {str(p["logical_name"]): p for p in [*package.get("produced", []), *package.get("inherited", [])]}
    previous_bindings = previous.get("_critic_source_bindings") or []
    if any(name != "goal_claim_set" and not any(
        isinstance(item, Mapping) and item.get("sha256") == port.get("sha256")
        and str(item.get("source_id") or "").startswith(("child-artifact-", "child-result-"))
        and any(isinstance(source, Mapping) and all(source.get(k) == item.get(k)
                    for k in ("source_id", "artifact_id", "ref", "sha256"))
                for source in previous.get("sources", []))
        and _has_full_read(prior_required, item)
        and _has_observed_read(prior_observed, item)
        for item in previous_bindings) for name, port in old_ports.items()):
        return required
    selected = {str(p.get("logical_name") or ""): p for p in ports if isinstance(p, Mapping)}
    if len(selected) != len(ports) or not selected or "task_map" in selected:
        return required
    # The claim set is kernel-derived from the unchanged global claims. A
    # planning_result derived from the old task map is likewise replaced by
    # the full, still-required candidate task map. Other ports must be named.
    derived = {"task_map", "goal_claim_set"}
    if ("planning_result" in old_ports and "planning_result" not in candidate_ports
            and old_ports["planning_result"].get("sha256") == old_ports["task_map"].get("sha256")):
        derived.add("planning_result")
    if set(candidate_ports) != set(old_ports) - derived:
        return required
    for name, old in old_ports.items():
        if name in derived:
            continue
        candidate = candidate_ports[name]
        if name not in selected and candidate.get("sha256") != old.get("sha256"):
            return required
        one(lambda s: s.get("ref") == candidate.get("ref") and s.get("sha256") == candidate.get("sha256")
            and str(s.get("source_id") or "").startswith("child-artifact-") and bound(s))

    old_map_source = one(lambda s: s.get("source_id") == "baseline-plan-port-task_map")
    new_map_source = one(lambda s: str(s.get("source_id") or "").startswith("child-artifact-")
                         and s.get("ref") == report.get("task_map_ref") and bound(s))
    if (not bound(old_map_source) or old_map_source.get("sha256") != old_ports["task_map"].get("sha256")
            or certificate.get("baseline_task_map_sha256") != old_map_source.get("sha256")
            or certificate.get("candidate_task_map_sha256") != new_map_source.get("sha256")):
        return required
    old_map, new_map = read(old_map_source), read(new_map_source)
    if not _has_full_read(required, new_map_source):
        return required
    explicit_ids = {str(row.get("source_id") or "") for row in explicit}
    replacements: dict[str, tuple[Mapping[str, Any], list[str]]] = {}
    baseline_task_ids: set[str] = set()
    for name, item in selected.items():
        if name not in old_ports or name not in candidate_ports:
            return required
        old_source = one(lambda s: s.get("source_id") == f"baseline-plan-port-{name}")
        new_source = one(lambda s: s.get("ref") == candidate_ports[name].get("ref")
                         and s.get("sha256") == candidate_ports[name].get("sha256")
                         and str(s.get("source_id") or "").startswith("child-artifact-") and bound(s))
        if (not bound(old_source) or old_source.get("sha256") != old_ports[name].get("sha256")
                or item.get("baseline_sha256") != old_source.get("sha256")
                or item.get("candidate_sha256") != new_source.get("sha256")
                or new_source["source_id"] in explicit_ids or old_source["source_id"] in explicit_ids):
            return required
        old_port = read(old_source)
        paths = scoped_port_paths(old_map=old_map, new_map=new_map, old_port=old_port,
                                  new_port=read(new_source), affected_task_ids=affected)
        if paths is None:
            return required
        baseline_task_ids.update(old_port["tasks"][int(path[8:-1])]["task_id"] for path in paths[0][1:])
        replacements[str(new_source["source_id"])] = (new_source, paths[1])
        replacements[str(old_source["source_id"])] = (old_source, paths[0])
    if any(not _has_full_read(required, source) for name, (source, _) in replacements.items()
           if name.startswith("child-artifact-")):
        return required
    result = [row for row in required if not (row.get("source_id") in replacements
              and str(row.get("source_id") or "").startswith("child-artifact-")
              and row.get("json_path") == "$")]
    baseline_map_paths = [f"$.tasks[{index}]" for index, task in enumerate(old_map["tasks"])
                          if (task.get("task_id") or task.get("id")) in baseline_task_ids]
    replacements[str(old_map_source["source_id"])] = (old_map_source, baseline_map_paths)
    for source, paths in replacements.values():
        result.extend({"source_id": source["source_id"], "artifact_id": source["artifact_id"],
                       "artifact_sha256": source["sha256"], "json_path": path,
                       "min_returned_bytes": 1, "max_items": 0, "max_chars": 0,
                       "allow_truncated": False} for path in paths)
    return result


def _authority_digests(sources: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    return sorted((str(s.get("source_id") or ""), str(s.get("sha256") or "")) for s in sources
                  if s.get("source_id") in {"requirement", "goal-objective"}
                  or str(s.get("source_id") or "").startswith("critical-"))


def _has_full_read(rows: Sequence[Mapping[str, Any]], source: Mapping[str, Any]) -> bool:
    return any(isinstance(row, Mapping) and row.get("source_id") == source.get("source_id")
               and row.get("artifact_id") == source.get("artifact_id")
               and row.get("artifact_sha256") == source.get("sha256")
               and row.get("json_path") == "$" and not row.get("allow_truncated")
               and not row.get("max_items") and not row.get("max_chars") for row in rows)


def _has_observed_read(rows: Sequence[Mapping[str, Any]], source: Mapping[str, Any]) -> bool:
    from zf.runtime.artifact_read_ledger_validation import required_read_satisfied

    return required_read_satisfied(rows, {"source_id": source.get("source_id"),
        "artifact_id": source.get("artifact_id"), "artifact_sha256": source.get("sha256"),
        "json_path": "$", "allow_truncated": False})


def _independent_producer(events: Sequence[ZfEvent], contract: Mapping[str, Any],
                          report: Mapping[str, Any], critic_role: str) -> bool:
    role = str(report.get("role_instance") or "")
    child = str(report.get("child_id") or "")
    attempt = str(report.get("dispatch_id") or report.get("run_id") or "")
    if not role or not child or not attempt or role == critic_role:
        return False
    matches = [event for event in events if event.type == "fanout.child.dispatched" and event.origin == "kernel"
               and event.payload.get("fanout_id") == contract.get("fanout_id")
               and event.payload.get("child_id") == child and event.payload.get("run_id") == attempt
               and event.payload.get("role_instance") == role]
    return len(matches) == 1


def _previous_critic_manifest(state_dir: Path, events: Sequence[ZfEvent], run: str, digest: str) -> dict[str, Any]:
    admissions = [e for e in events if e.type == "plan.artifact_package.admitted" and e.origin == "kernel"
                  and e.payload.get("workflow_run_id") == run and e.payload.get("package_digest") == digest]
    if len(admissions) != 1:
        raise ValueError("local review baseline admission is ambiguous")
    if admissions[0].causation_id != admissions[0].payload.get("source_event_id"):
        raise ValueError("baseline package admission cause differs")
    synth = next(e for e in events if e.id == admissions[0].payload.get("source_event_id")
                 and e.type == "fanout.synth.completed")
    body = synth.payload
    if (body.get("status") != "completed" or body.get("recommendation") != "approve"
            or not body.get("plan_synth_contract_digest")
            or body.get("reviewed_plan_candidate_digest") != body.get("plan_synth_contract_digest")):
        raise ValueError("baseline has no approved exact Critic result")
    admission = [e for e in events if e.type == "workflow.call.result.admitted" and e.origin == "kernel"
                 and e.payload.get("source_event_id") == synth.id
                 and e.payload.get("workflow_run_id") == run
                 and e.payload.get("operation_id") == body.get("operation_id")]
    if len(admission) != 1 or admission[0].payload.get("control_result_ref") != body.get("control_result_ref"):
        raise ValueError("baseline Critic result is not admitted")
    from zf.runtime.call_result_envelope import hydrate_call_result_envelope

    envelope = hydrate_call_result_envelope(state_dir, admission[0].payload["envelope_ref"])
    identity = envelope.get("identity") or {}
    if any(identity.get(key) != value for key, value in (
        ("workflow_run_id", run), ("operation_id", body.get("operation_id")),
        ("attempt_id", body.get("attempt_id")),
        ("plan_synth_contract_digest", body.get("plan_synth_contract_digest")),
        ("reviewed_plan_candidate_digest", body.get("plan_synth_contract_digest")),
        ("fanout_id", body.get("fanout_id")), ("producer_stage_id", body.get("stage_id")),
        ("plan_revision", body.get("plan_revision")),
    )):
        raise ValueError("baseline Critic envelope identity differs")
    starts = [event for event in events if event.type == "workflow.operation.started" and event.origin == "kernel"
              and event.payload.get("operation_id") == body.get("operation_id")
              and event.payload.get("active_attempt_id") == body.get("attempt_id")
              and event.payload.get("request_hash") == identity.get("request_hash")
              and event.payload.get("role_instance") == identity.get("producer_role")]
    sessions = {str(event.payload.get("provider_session_id") or "") for event in starts}
    if len(sessions) != 1 or not next(iter(sessions)):
        raise ValueError("baseline Critic provider session is unproven")
    session = next(iter(sessions))
    envelope_session = (envelope.get("execution") or {}).get("provider_session_id")
    if envelope_session and envelope_session != session:
        raise ValueError("baseline Critic provider session differs")
    ledger_ref = admission[0].payload.get("read_ledger_ref")
    consumption = envelope.get("input_consumption") or {}
    if (not isinstance(ledger_ref, Mapping) or consumption.get("status") != "satisfied"
            or consumption.get("read_ledger_ref") != ledger_ref.get("ref")
            or consumption.get("read_ledger_digest") != ledger_ref.get("sha256")):
        raise ValueError("baseline Critic ledger binding differs")
    ledger = hydrate_sidecar_ref(state_dir, dict(ledger_ref)).payload
    if not isinstance(ledger, str):
        raise ValueError("baseline Critic ledger is not text")
    from zf.runtime.artifact_read_ledger_validation import _parse_ledger
    from zf.runtime.artifact_output_delivery import observe_artifact_delivery

    recorded = [row for row in _parse_ledger(ledger.encode(), expected_attempt_id=str(body.get("attempt_id") or ""))
                if row.get("provider_delivery_status") == "observed_in_transcript"
                and row.get("provider_session_id") == session
                and row.get("consumer_provider") == "codex"
                and row.get("consumer_actor") == identity.get("producer_role")]
    checked = observe_artifact_delivery(state_dir, recorded)
    observed = [row for original, row in zip(recorded, checked, strict=True)
                if row.get("provider_delivery_status") == "observed_in_transcript"
                and row.get("provider_session_id") == session
                and row.get("provider_observation_ref") == original.get("provider_observation_ref")]
    control_ref = admission[0].payload["control_result_ref"]
    envelope_ref = envelope.get("control_result") or envelope.get("control_result_ref")
    if not isinstance(envelope_ref, Mapping) or any(envelope_ref.get(k) != control_ref.get(k)
                                                   for k in ("ref", "sha256", "schema_version")):
        raise ValueError("baseline Critic envelope does not match admitted result")
    control = hydrate_sidecar_ref(state_dir, control_ref).payload
    if (control.get("schema_version") != "plan-synthesis-result.v1"
            or control.get("execution_status") != "completed" or control.get("verdict") != "passed"
            or control.get("plan_synth_contract_digest") != body.get("plan_synth_contract_digest")
            or control.get("reviewed_plan_candidate_digest") != body.get("plan_synth_contract_digest")):
        raise ValueError("baseline Critic semantic result was not approved")
    dispatch = [e for e in events if e.type == "fanout.synth.dispatched" and e.origin == "kernel"
                and e.payload.get("operation_id") == body.get("operation_id")
                and e.payload.get("attempt_id") == body.get("attempt_id")]
    if len(dispatch) != 1:
        raise ValueError("baseline Critic dispatch is ambiguous")
    if dispatch[0].payload.get("role_instance") != identity.get("producer_role"):
        raise ValueError("baseline Critic role differs from dispatch")
    if (dispatch[0].payload.get("plan_synth_contract_digest") != body.get("plan_synth_contract_digest")
            or dispatch[0].payload.get("plan_synth_contract_ref") != body.get("plan_synth_contract_ref")):
        raise ValueError("baseline Critic contract differs from dispatch")
    requests = [event for event in events if event.type == "workflow.operation.requested" and event.origin == "kernel"
                and event.payload.get("operation_id") == body.get("operation_id")
                and event.payload.get("workflow_run_id") == run
                and event.payload.get("request_hash") == identity.get("request_hash")]
    if len(requests) != 1:
        raise ValueError("baseline Critic request is ambiguous")
    request_body = hydrate_sidecar_ref(state_dir, requests[0].payload["request_ref"]).payload
    from zf.runtime.workflow_operation import operation_request_hash

    if (request_body.get("schema_version") != "workflow-operation-request.v1"
            or request_body.get("workflow_run_id") != run or request_body.get("operation_id") != body.get("operation_id")
            or request_body.get("operation_type") != "fanout_synth"
            or operation_request_hash(request_body) != identity.get("request_hash")):
        raise ValueError("baseline Critic request digest differs")
    request = request_body["request"]
    if (request.get("output_profile_id") != "plan-synth" or request.get("active_attempt_id") != body.get("attempt_id")
            or request.get("role_instance") != identity.get("producer_role")
            or request.get("fanout_id") != body.get("fanout_id")
            or request.get("source_manifest_digest") != dispatch[0].payload["attempt_source_manifest"].get("sha256")
            or request.get("input_consumption_policy_ref") != dispatch[0].payload.get("input_consumption_policy_ref")):
        raise ValueError("baseline Critic request inputs differ from dispatch")
    previous = hydrate_sidecar_ref(state_dir, dispatch[0].payload["attempt_source_manifest"]).payload
    if previous.get("workflow_run_id") != run or previous.get("attempt_id") != body.get("attempt_id"):
        raise ValueError("baseline Critic manifest identity differs")
    contract_sources = [item for item in previous.get("sources", [])
                        if item.get("source_id") == "plan-synth-contract"
                        and item.get("ref") == body.get("plan_synth_contract_ref")
                        and item.get("sha256") == body.get("plan_synth_contract_digest")]
    if len(contract_sources) != 1:
        raise ValueError("baseline Critic contract is absent from its manifest")
    contract = hydrate_sidecar_ref(state_dir, dict(contract_sources[0])).payload
    if (contract.get("schema_version") != "plan-synth-contract.v1" or contract.get("workflow_run_id") != run
            or any(not body.get(key) or contract.get(key) != body.get(key)
                   for key in ("fanout_id", "stage_id", "plan_revision"))
            or not isinstance(contract.get("source_bindings"), list)):
        raise ValueError("baseline Critic contract is invalid")
    policy = hydrate_sidecar_ref(state_dir, dispatch[0].payload["input_consumption_policy_ref"]).payload
    if (policy.get("workflow_run_id") != run or policy.get("attempt_id") != body.get("attempt_id")
            or not isinstance(policy.get("required_reads"), list)
            or request.get("required_reads") != policy.get("required_reads")
            or request.get("read_policy_digest") != dispatch[0].payload["input_consumption_policy_ref"].get("sha256")):
        raise ValueError("baseline Critic read policy identity differs")
    child_bindings = contract.get("child_bindings")
    if not isinstance(child_bindings, list) or not child_bindings:
        raise ValueError("baseline Critic has no producer bindings")
    for binding in child_bindings:
        matching = [source for source in previous.get("sources", []) if isinstance(source, Mapping)
                    and all(source.get(key) == binding.get(key) for key in ("source_id", "artifact_id", "sha256"))]
        if (len(matching) != 1 or not _has_full_read(policy["required_reads"], matching[0])
                or not _has_observed_read(observed, matching[0])):
            raise ValueError("baseline producer result is not a required bound source")
        report = hydrate_sidecar_ref(state_dir, dict(matching[0])).payload
        if (not isinstance(report, Mapping) or report.get("child_id") != binding.get("child_id")
                or not _independent_producer(events, contract, report, identity["producer_role"])):
            raise ValueError("baseline Critic is not independent of its producer")
    return {**previous, "_critic_source_bindings": contract["source_bindings"],
            "_critic_required_reads": policy["required_reads"], "_critic_observed_reads": observed}
