"""Evidence-bound reuse of unchanged Plan inputs during a new Critic review.

The old verdict is evidence, never the new verdict. Changed inputs and current
Task Map remain mandatory; all original sources remain readable by the critic.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent
from zf.runtime.plan_local_review_policy import (
    _authority_digests, _has_full_read, _has_observed_read,
    _independent_producer, _previous_critic_manifest,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

EVIDENCE_ID = "child-artifact-unchanged-review"
EVIDENCE_ARTIFACT = "unchanged-plan-review.json"
EVIDENCE_SCHEMA = "unchanged-plan-review.v1"


def attach_unchanged_review(runtime: Any, *, payload: dict[str, Any], stage: str, trigger_id: str,
                            operation_id: str) -> None:
    """Bind candidate evidence before the immutable source manifest is published."""
    refs = payload.get("input_refs")
    if not isinstance(refs, list) or any(isinstance(s, Mapping) and s.get("source_id") == EVIDENCE_ID for s in refs):
        return
    from zf.runtime.workflow_operation import load_workflow_operation

    operation = load_workflow_operation(runtime.event_log, operation_id)
    if operation is not None:
        _pin_existing_review(runtime, payload=payload, operation=operation)
        return
    if payload.get("required_reads"):
        return
    if not any(isinstance(s, Mapping) and s.get("source_id") == "plan-rework-package" for s in refs):
        return
    contracts = [s for s in refs if isinstance(s, Mapping) and s.get("source_id") == "plan-synth-contract"]
    if len(contracts) != 1:
        return
    contract_source = contracts[0]
    try:
        contract = hydrate_sidecar_ref(runtime.state_dir, dict(contract_source)).payload
        if (contract.get("plan_revision") != payload.get("plan_revision")
                or contract_source.get("sha256") != payload.get("plan_synth_contract_digest")
                or contract_source.get("ref") != payload.get("plan_synth_contract_ref")):
            return
        evidence = build_evidence(
            runtime.state_dir, events=runtime.event_log.read_all(), sources=refs,
            run=str(payload.get("workflow_run_id") or ""), fanout=str(payload.get("fanout_id") or ""),
            stage=stage, trigger_id=trigger_id, critic_role=str(payload.get("role_instance") or ""),
        )
        if not evidence:
            return
        from zf.runtime.call_result_envelope import canonical_json_sha256, write_immutable_json_sidecar

        descriptor = write_immutable_json_sidecar(
            runtime.state_dir, evidence, root="plan-synth/unchanged-review", kind="unchanged_plan_review",
            schema_version=EVIDENCE_SCHEMA, created_by="plan-synth-handoff", source_event_id=trigger_id,
        )
        source = {**descriptor, "source_id": EVIDENCE_ID, "artifact_id": EVIDENCE_ARTIFACT, "allowed_paths": ["$"]}
        basis = {k: v for k, v in contract.items() if k != "plan_revision"}
        basis["source_bindings"] = [*basis["source_bindings"],
                                    {k: source[k] for k in ("source_id", "artifact_id", "ref", "sha256")}]
        revision = f"plan-r{canonical_json_sha256(basis)[:12]}"
        updated = {**basis, "plan_revision": revision}
        contract_ref = write_immutable_json_sidecar(
            runtime.state_dir, updated, root="plan-synth/contracts", kind="plan_synth_contract",
            schema_version="plan-synth-contract.v1", created_by="plan-synth-handoff", source_event_id=trigger_id,
        )
        refs[:] = [{**contract_ref, "source_id": "plan-synth-contract", "artifact_id": "plan-synth-contract.json",
                    "allowed_paths": ["$"]} if s is contract_source else s for s in refs]
        refs.append(source)
        payload.update(plan_revision=revision, plan_synth_contract_ref=contract_ref["ref"],
                       plan_synth_contract_digest=contract_ref["sha256"], reviewed_plan_candidate_digest=contract_ref["sha256"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError):
        return


def _pin_existing_review(runtime: Any, *, payload: dict[str, Any], operation: Mapping[str, Any]) -> None:
    """Restore an already frozen proof; never add one to an existing operation."""
    from zf.runtime.artifact_read_ledger import _safe_component
    from zf.runtime.call_result_envelope import canonical_json_sha256

    try:
        request = hydrate_sidecar_ref(runtime.state_dir, dict(operation["request_ref"])).payload["request"]
        digest = str(request.get("source_manifest_digest") or "")
        attempt = _safe_component(str(request.get("active_attempt_id") or ""))
        if not digest:
            return
        manifest = hydrate_sidecar_ref(runtime.state_dir, {
            "ref": f"artifacts/attempts/{attempt}/source-manifests/{digest}.json", "sha256": digest,
        }).payload
        sources = manifest.get("sources") or []
        old_evidence = [s for s in sources if s.get("source_id") == EVIDENCE_ID]
        old_contract = [s for s in sources if s.get("source_id") == "plan-synth-contract"]
        refs = payload["input_refs"]
        current = [s for s in refs if isinstance(s, Mapping) and s.get("source_id") == "plan-synth-contract"]
        if len(old_evidence) != 1 or len(old_contract) != 1 or len(current) != 1:
            return
        pinned = hydrate_sidecar_ref(runtime.state_dir, dict(old_contract[0])).payload
        incoming = hydrate_sidecar_ref(runtime.state_dir, dict(current[0])).payload
        basis = {k: v for k, v in pinned.items() if k != "plan_revision"}
        bindings = basis.get("source_bindings")
        if not isinstance(bindings, list) or sum(b.get("source_id") == EVIDENCE_ID for b in bindings) != 1:
            return
        basis["source_bindings"] = [b for b in bindings if b.get("source_id") != EVIDENCE_ID]
        expected = {**basis, "plan_revision": f"plan-r{canonical_json_sha256(basis)[:12]}"}
        if incoming != expected or current[0].get("sha256") != payload.get("plan_synth_contract_digest"):
            return
        refs[:] = [dict(old_contract[0]) if s is current[0] else s for s in refs]
        refs.append(dict(old_evidence[0]))
        payload.update(plan_revision=pinned["plan_revision"], plan_synth_contract_ref=old_contract[0]["ref"],
                       plan_synth_contract_digest=old_contract[0]["sha256"],
                       reviewed_plan_candidate_digest=old_contract[0]["sha256"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError):
        return


def _trusted_trigger(events: Sequence[ZfEvent], trigger: ZfEvent, run: str) -> bool:
    if trigger.origin == "kernel":
        return True
    if trigger.origin or trigger.actor != "zf-cli" or not trigger.causation_id:
        return False
    requested = [event for event in events if event.id == trigger.causation_id
                 and event.type == "control.action.requested" and event.actor == "zf-cli"
                 and event.correlation_id == run
                 and event.payload.get("action") == "stage-replan-new-generation"]
    completed = [event for event in events if event.type == "runtime.action.attempt.completed"
                 and event.actor == "zf-cli" and event.correlation_id == run
                 and event.payload.get("action") == "stage-replan-new-generation"
                 and event.payload.get("status") == "applied"
                 and event.payload.get("result_event_id") == trigger.id
                 and event.payload.get("attempt_id") == f"act-{trigger.causation_id[:12]}"]
    return len(requested) == len(completed) == 1


def build_evidence(
    state_dir: Path, *, events: Sequence[ZfEvent], sources: Sequence[Mapping[str, Any]],
    run: str, fanout: str, stage: str, trigger_id: str, critic_role: str,
) -> dict[str, Any] | None:
    """Return complete review evidence, or no optimization on any ambiguity."""
    try:
        return _build(state_dir, events=events, sources=sources, run=run, fanout=fanout,
                      stage=stage, trigger_id=trigger_id, critic_role=critic_role)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration, RecursionError):
        return None


def _build(state_dir, *, events, sources, run, fanout, stage, trigger_id, critic_role):
    from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package, reduce_plan_artifact_packages

    if not all((run, fanout, stage, trigger_id, critic_role)):
        return None
    rows = [s for s in sources if isinstance(s, Mapping) and s.get("source_id") != EVIDENCE_ID]
    if any(s.get("artifact_id") == "scoped_plan_review.json" or s.get("source_id") == "owner-requirement-amendment" for s in rows):
        return None

    def one(pred):
        found = [s for s in rows if pred(s)]
        if len(found) != 1:
            raise ValueError("ambiguous source")
        return found[0]

    baseline = one(lambda s: s.get("source_id") == "plan-rework-package")
    package = hydrate_plan_artifact_package(state_dir, {"ref": baseline["ref"], "sha256": baseline["sha256"]})
    if package.get("workflow_run_id") != run:
        return None
    current = reduce_plan_artifact_packages(events, workflow_run_id=run).get("current") or {}
    if current.get("package_digest") != baseline["sha256"]:
        return None
    trigger = [e for e in events if e.id == trigger_id]
    if (len(trigger) != 1 or not _trusted_trigger(events, trigger[0], run)
            or trigger[0].payload.get("workflow_run_id") != run
            or trigger[0].payload.get("plan_artifact_package_digest") != baseline["sha256"]
            or not trigger[0].payload.get("rework_of") or not trigger[0].payload.get("target_ref")):
        return None
    starts = [e for e in events if e.type == "fanout.started" and e.origin == "kernel"
              and e.causation_id == trigger_id and e.payload.get("fanout_id") == fanout
              and e.payload.get("stage_id") == stage and e.payload.get("workflow_run_id") == run]
    if len(starts) != 1:
        return None
    previous = _previous_critic_manifest(state_dir, events, run, baseline["sha256"])
    if previous.get("_critic_role") != critic_role:
        return None
    authority = [s for s in rows if s.get("source_id") in {"requirement", "goal-objective"}
                 or str(s.get("source_id") or "").startswith("critical-")]
    if not authority or _authority_digests(previous.get("sources", [])) != _authority_digests(authority):
        return None
    old_authority = [s for s in previous.get("sources", []) if isinstance(s, Mapping)
                     and (s.get("source_id") in {"requirement", "goal-objective"}
                          or str(s.get("source_id") or "").startswith("critical-"))]
    if any(not _has_full_read(previous["_critic_required_reads"], s)
           or not _has_observed_read(previous["_critic_observed_reads"], s) for s in old_authority):
        return None
    report_source = one(lambda s: str(s.get("source_id") or "").startswith("child-result-"))
    report = hydrate_sidecar_ref(state_dir, dict(report_source)).payload
    if not isinstance(report, Mapping) or not _independent_producer(events, {"fanout_id": fanout}, report, critic_role):
        return None
    task_map = one(lambda s: str(s.get("source_id") or "").startswith("child-artifact-")
                   and s.get("ref") == report.get("task_map_ref"))
    old_ports = {}
    for port in [*package.get("produced", []), *package.get("inherited", [])]:
        name = str(port.get("logical_name") or "")
        if not name or name in old_ports:
            return None
        old_ports[name] = port
    new_ports = {}
    for port in report.get("plan_ports", []):
        if not isinstance(port, Mapping):
            return None
        name = str(port.get("logical_name") or "")
        if not name or name in new_ports:
            return None
        new_ports[name] = port
    if not new_ports:
        return None
    prior_bindings = previous.get("_critic_source_bindings") or []
    reused = []
    deltas = []
    for name, port in new_ports.items():
        old = old_ports.get(name)
        if not old or name in {"task_map", "goal_claim_set"}:
            continue
        current_source = one(lambda s: str(s.get("source_id") or "").startswith("child-artifact-")
                             and s.get("ref") == port.get("ref") and s.get("sha256") == port.get("sha256"))
        if (current_source.get("source_id") == task_map.get("source_id")
                or "$" not in current_source.get("allowed_paths", ["$"])):
            continue
        witnesses = [s for s in previous.get("sources", []) if isinstance(s, Mapping)
                     and s.get("ref") == old.get("ref") and s.get("sha256") == old.get("sha256")
                     and any(isinstance(b, Mapping) and all(b.get(k) == s.get(k)
                             for k in ("source_id", "artifact_id", "ref", "sha256")) for b in prior_bindings)
                     and _has_full_read(previous["_critic_required_reads"], s)
                     and _has_observed_read(previous["_critic_observed_reads"], s)]
        if len(witnesses) != 1:
            continue
        record = {"logical_name": name, "source_id": current_source["source_id"],
                  "artifact_id": current_source["artifact_id"], "sha256": current_source["sha256"],
                  "ref": current_source["ref"], "prior_source_id": witnesses[0]["source_id"],
                  "prior_ref": witnesses[0]["ref"], "prior_sha256": witnesses[0]["sha256"]}
        from zf.runtime.sidecar_refs import sidecar_path, verify_sidecar_ref

        # Verify the bytes of both occurrences; digest equality never grants access.
        old_file = verify_sidecar_ref(state_dir, dict(witnesses[0]))
        new_file = verify_sidecar_ref(state_dir, dict(current_source))
        if port.get("sha256") == old.get("sha256"):
            reused.append(record)
            continue
        # Keep large/non-JSON/inefficient changes as mandatory complete sources.
        if old_file["byte_count"] > 8 * 1024 * 1024 or new_file["byte_count"] > 8 * 1024 * 1024:
            continue
        def bounded(source):
            with sidecar_path(state_dir, source["ref"]).open("rb") as stream:
                raw = stream.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != source["sha256"]:
                raise ValueError("Plan source changed or exceeds delta budget")
            return raw

        old_raw = bounded(witnesses[0])
        new_raw = bounded(current_source)
        try:
            from zf.runtime.plan_structural_delta import make_verified_delta

            delta = make_verified_delta(old_raw, new_raw, old_sha256=record["prior_sha256"], new_sha256=record["sha256"])
            encoded = json.dumps(delta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) <= 512 * 1024 and len(encoded) < len(new_raw):
                deltas.append({**record, "delta": delta})
        except (ValueError, TypeError, UnicodeDecodeError, RecursionError):
            continue
    selected = reused + deltas
    if not selected or len({r["source_id"] for r in selected}) != len(selected):
        return None
    evidence = {"schema_version": EVIDENCE_SCHEMA, "workflow_run_id": run, "fanout_id": fanout,
            "stage_id": stage, "trigger_event_id": trigger_id, "target_ref": trigger[0].payload["target_ref"],
            "baseline_package_sha256": baseline["sha256"],
            "candidate_task_map_sha256": task_map["sha256"], "candidate_report_ref": report_source["ref"],
            "candidate_report_sha256": report_source["sha256"], "candidate_child_id": report.get("child_id"),
            "candidate_report_source_id": report_source["source_id"],
            "candidate_report_artifact_id": report_source["artifact_id"], "prior_review_ref": previous["_critic_control_ref"],
            "prior_review": previous["_critic_control"], "unchanged_ports": sorted(reused, key=lambda r: r["logical_name"]),
            "changed_ports": sorted(deltas, key=lambda r: r["logical_name"])}
    # The proof is itself mandatory input; never inflate review with an
    # unbounded certificate or exceed the ordinary sidecar consumer limit.
    if len(json.dumps(evidence, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 900 * 1024:
        return None
    return evidence


def scope_unchanged_reads(state_dir: Path, *, events: Sequence[ZfEvent], manifest: Mapping[str, Any],
                          required: list[dict[str, Any]], explicit: list[dict[str, Any]],
                          fanout: str, stage: str, critic_role: str, plan_revision: str) -> list[dict[str, Any]]:
    """Skip only exact fully witnessed prior ports after rechecking immutable evidence."""
    try:
        if explicit:
            return required
        sources = manifest.get("sources") or []
        evidence_sources = [s for s in sources if isinstance(s, Mapping) and s.get("source_id") == EVIDENCE_ID
                            and s.get("artifact_id") == EVIDENCE_ARTIFACT]
        if len(evidence_sources) != 1:
            return required
        source = evidence_sources[0]
        if not _has_full_read(required, source):
            return required
        evidence = hydrate_sidecar_ref(state_dir, dict(source)).payload
        actual = build_evidence(state_dir, events=events, sources=sources, run=str(manifest.get("workflow_run_id") or ""),
                                fanout=fanout, stage=stage, trigger_id=str(manifest.get("source_event_id") or ""),
                                critic_role=critic_role)
        if not actual or evidence != actual:
            return required
        contract_sources = [s for s in sources if isinstance(s, Mapping) and s.get("source_id") == "plan-synth-contract"]
        if len(contract_sources) != 1:
            return required
        contract = hydrate_sidecar_ref(state_dir, dict(contract_sources[0])).payload
        if (contract.get("workflow_run_id") != actual["workflow_run_id"] or contract.get("fanout_id") != fanout
                or contract.get("stage_id") != stage or contract.get("trigger_event_id") != actual["trigger_event_id"]
                or contract.get("target_ref") != actual["target_ref"]
                or not plan_revision or contract.get("plan_revision") != plan_revision):
            return required
        bindings = contract.get("source_bindings") or []
        if not any(isinstance(binding, Mapping)
                   and binding.get("child_id") == actual["candidate_child_id"]
                   and binding.get("source_id") == actual["candidate_report_source_id"]
                   and binding.get("artifact_id") == actual["candidate_report_artifact_id"]
                   and binding.get("sha256") == actual["candidate_report_sha256"]
                   for binding in contract.get("child_bindings", [])):
            return required
        selected = [*actual["unchanged_ports"], *actual["changed_ports"]]
        if any(not _has_full_read(required, next(x for x in sources if x.get("source_id") == item["source_id"]))
               for item in selected):
            return required
        protected = [s for s in sources if isinstance(s, Mapping) and (
            s.get("source_id") in {"plan-rework-package", "requirement", "goal-objective"}
            or str(s.get("source_id") or "").startswith(("child-result-", "critical-"))
            or s.get("sha256") == actual["candidate_task_map_sha256"])]
        if any(not _has_full_read(required, s) for s in protected if s.get("source_id") != "plan-rework-package"):
            return required
        if not all(any(isinstance(b, Mapping) and all(b.get(k) == s.get(k) for k in ("source_id", "artifact_id", "ref", "sha256"))
                       for b in bindings) for s in [source, *[s for s in protected if not str(s.get("source_id") or "").startswith("critical-")],
                           *[next(x for x in sources if x.get("source_id") == r["source_id"]) for r in selected]]):
            return required
        ids = {r["source_id"] for r in selected}
        return [row for row in required if not (row.get("source_id") in ids and row.get("json_path") == "$")]
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, StopIteration):
        return required
