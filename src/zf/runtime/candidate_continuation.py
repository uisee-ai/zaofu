"""Reviewed, immutable input to a successor Candidate verification Run.

The source Run and its Tasks retain their identities and terminal history.
Only execution facts are inherited; Candidate/Judge verdicts are never imported.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from zf.core.task.store import TaskStore
from zf.runtime.candidate_verification_authority import (
    _latest_task_verification, prepare_candidate_verification_authority,
)
from zf.runtime.candidate_result_binding import candidate_task_source_commits
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_config_consumer import approved_config_consumer_error
from zf.runtime.workflow_git_readiness import git_environment

SCHEMA = "candidate-continuation.v1"
ACTION = "candidate-continue"


def digest(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _json(state: Path, descriptor: Mapping[str, Any]) -> dict:
    hydrated = hydrate_sidecar_ref(state, dict(descriptor))
    if not hydrated.ok or not isinstance(hydrated.payload, dict):
        raise ValueError("continuation evidence is not a valid JSON sidecar")
    return hydrated.payload


def _latest(events, event_type: str, run_id: str):
    return next((e for e in reversed(events) if e.type == event_type
                 and str(e.payload.get("workflow_run_id") or e.payload.get("run_id")
                         or e.correlation_id or "") == run_id), None)


def _git_commit(root: Path, ref: str) -> str:
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify",
                             "--end-of-options", ref + "^{commit}"],
                            env=git_environment(), capture_output=True, text=True,
                            timeout=20, check=False)
    if result.returncode:
        raise ValueError("continuation target cannot be resolved: " + ref)
    return result.stdout.strip()


def preview(service: Any, *, source_run_id: str, candidate_event_id: str) -> dict:
    """Validate current truth; may materialize immutable verification snapshots.

    Does not append events, create Tasks, admit a Run or dispatch a provider.
    """
    state = Path(service.state_dir)
    events = service.writer.event_log.read_all()
    source = build_run_admission_projection(events).runs.get(source_run_id)
    if source is None or source.status not in {"cancelled", "failed"}:
        raise ValueError("source Run must exist and be cancelled or failed")
    candidate = _latest(events, "candidate.ready", source_run_id)
    if candidate is None or candidate.id != candidate_event_id:
        raise ValueError("continuation requires the latest source Candidate event")
    body = candidate.payload
    if body.get("schema_version") != "candidate-freeze-receipt.v1" or body.get("status") != "frozen":
        raise ValueError("continuation requires a frozen Candidate")
    if body.get("candidate_continuation_ref"):
        raise ValueError("nested Candidate continuation is not supported")
    freeze = _json(state, body.get("freeze_receipt_ref") or {})
    for key in ("workflow_run_id", "task_map_generation", "candidate_head_commit",
                "candidate_base_commit", "candidate_ref", "completed_task_ids", "freeze_id"):
        if freeze.get(key) != body.get(key) or not body.get(key):
            raise ValueError("Candidate freeze binding mismatch: " + key)
    from zf.runtime.task_pipeline_contexts import latest_task_pipeline_generation_context

    pipeline = latest_task_pipeline_generation_context(events, workflow_run_id=source_run_id)
    if (pipeline is None or pipeline.get("generation_id") != body.get("generation_id")
            or pipeline.get("task_map_generation") != body.get("task_map_generation")
            or sorted(pipeline.get("task_ids") or []) != sorted(body["completed_task_ids"])):
        raise ValueError("Candidate does not cover the current source Task generation")
    terminal_index = next(i for i, e in enumerate(events) if e.id == source.terminal_event_id)
    if events.index(candidate) >= terminal_index:
        raise ValueError("Candidate was emitted after source Run termination")
    root = Path(service.project_root or "")
    if not service.project_root or _git_commit(root, body["candidate_ref"]) != body["candidate_head_commit"]:
        raise ValueError("Candidate ref moved from the frozen target")
    _git_commit(root, body["candidate_base_commit"])
    generation = body["task_map_generation"]
    admitted = _latest(events, "plan.artifact_package.admitted", source_run_id)
    if admitted is None or any(admitted.payload.get(key) != body.get(candidate_key) for key, candidate_key in (
        ("task_map_generation", "task_map_generation"),
        ("package_ref", "plan_artifact_package_ref"), ("package_digest", "plan_artifact_package_digest"),
    )):
        raise ValueError("Candidate does not bind the current admitted Plan package")
    package_ref = {"ref": body["plan_artifact_package_ref"], "sha256": body["plan_artifact_package_digest"]}
    package = hydrate_plan_artifact_package(state, package_ref)
    from zf.runtime.product_acceptance import product_acceptance_binding_from_package

    product_acceptance_binding_from_package(state, package_ref)
    if package["workflow_run_id"] != source_run_id or package["task_map_generation"] != generation:
        raise ValueError("Plan package belongs to another Run or generation")
    approvals = [e for e in events if e.type == "plan.approval.requested"
                 and e.payload.get("workflow_run_id") == source_run_id
                 and e.payload.get("plan_artifact_package_digest") == package_ref["sha256"]]
    if not approvals:
        raise ValueError("source Plan approval request is missing")
    requested = approvals[-1]
    decision = next((e for e in reversed(events) if e.type in {"plan.approved", "plan.rejected"}
                     and e.payload.get("plan_id") == requested.payload.get("plan_id")
                     and not e.payload.get("semantic_control")), None)
    if decision is None or decision.type != "plan.approved":
        raise ValueError("source Plan lacks current owner approval")
    invoke = _latest(events, "workflow.invoke.requested", source_run_id)
    goal = _latest(events, "run.goal.started", source_run_id)
    claims = _latest(events, "goal.claim_set.pinned", source_run_id)
    if invoke is None or goal is None or claims is None:
        raise ValueError("source Run is missing invocation, Goal or claims")
    error = approved_config_consumer_error(state, service.config, invoke.payload)
    if error:
        raise ValueError(error)
    from zf.core.config.render import redact_config, renderable_config_to_primitive
    from zf.runtime.workflow_config_consumer import _execution_fields

    execution_digest = digest(_execution_fields(redact_config(renderable_config_to_primitive(service.config))))
    claim_ref = {"ref": claims.payload["goal_claim_set_ref"], "sha256": claims.payload["goal_claim_set_digest"]}
    claim_set = _json(state, claim_ref)
    if claim_set.get("workflow_run_id") != source_run_id or claim_set.get("task_map_generation") != generation:
        raise ValueError("Goal claims belong to another Run or generation")
    tasks = TaskStore(state / "kanban.json")
    completed = sorted(set(body["completed_task_ids"]))
    if completed != sorted(set(body.get("task_ids") or [])):
        raise ValueError("Candidate has unfinished Tasks")
    for task_id in completed:
        task = tasks.get(task_id)
        if task is None or task.status != "done":
            raise ValueError("source Task is not done: " + task_id)
    task_commits = candidate_task_source_commits(events, workflow_run_id=source_run_id,
                                               candidate_head_commit=body["candidate_head_commit"])
    proofs = []
    for task_id in completed:
        verify = _latest_task_verification(events, workflow_run_id=source_run_id, task_id=task_id,
            task_map_generation=generation, target_commit=task_commits.get(task_id, ""))
        if verify is None:
            raise ValueError("source Task has no exact Verify: " + task_id)
        v = verify.payload
        admission = next((e for e in events if e.id == v.get("semantic_submit_admission_event_id")), None)
        if (admission is None or admission.type != "workflow.call.result.admitted"
                or admission.origin != "kernel" or admission.payload.get("semantic_verdict") != "passed"
                or any(admission.payload.get(k) != v.get(k) for k in (
                    "workflow_run_id", "operation_id", "request_hash", "control_result_ref"))):
            raise ValueError("source Task lacks independent admitted Verify: " + task_id)
        result = _json(state, v.get("control_result_ref") or {})
        if result.get("verdict") != "passed":
            raise ValueError("source Task verification did not pass: " + task_id)
        _json(state, v.get("call_result_envelope_ref") or {})
        proofs.append({"task_id": task_id, "target_commit": task_commits[task_id],
                       "verification_event_id": verify.id, "admission_event_id": admission.id,
                       "control_result_ref": v["control_result_ref"],
                       "envelope_ref": v["call_result_envelope_ref"]})
    # Existing authority validation checks TaskStore contract revision/ownership,
    # every immutable contract and verification target. Reuse that exact boundary.
    runtime = SimpleNamespace(state_dir=state, project_root=root, config=service.config,
                              event_log=service.writer.event_log, task_store=tasks)
    authority = prepare_candidate_verification_authority(runtime, payload={"candidate_event_id": candidate.id},
        workflow_run_id=source_run_id, task_id=source.task_id or str(body.get("pdd_id") or ""))
    if not authority:
        raise ValueError("source Candidate has no verification authority")
    execution_evidence = {}
    for event in events:
        if (event.type != "runtime.action.attempt.completed" or event.origin != "kernel"
                or event.payload.get("action") != "task-host-assist"
                or event.payload.get("workflow_run_id") != source_run_id
                or event.payload.get("kind") != "verify"):
            continue
        ref = event.payload.get("receipt_ref")
        if not isinstance(ref, dict):
            continue
        fact = _json(state, ref)
        if fact.get("target_commit") != body["candidate_head_commit"]:
            continue
        if fact.get("workflow_run_id") != source_run_id:
            raise ValueError("host execution evidence source Run mismatch")
        if fact.get("output_ref"):
            hydrate_sidecar_ref(state, fact["output_ref"])
        command_digest = str(fact.get("command_digest") or "")
        if not command_digest:
            raise ValueError("host execution evidence lacks a command digest")
        # Keep the latest observation per duty, including a failed observation.
        # Older receipt formats without IDs retain their digest-only history.
        command = (fact.get("manifest") or {}).get("command") or {}
        key = (command_digest, str(fact.get("command_id") or command.get("command_id") or ""),
               str(command.get("tier") or ""))
        execution_evidence[key] = {"source_event_id": event.id, "receipt_ref": ref}
    stages = [s.id for s in service.config.workflow.stages
              if s.trigger == "candidate.ready" and s.topology == "fanout_reader"]
    if not stages:
        raise ValueError("no configured Candidate reader stage")
    manifest = {
        "schema_version": SCHEMA, "source_run_id": source_run_id,
        "source_terminal_event_id": source.terminal_event_id,
        "candidate_event_id": candidate.id, "candidate_head_commit": body["candidate_head_commit"],
        "candidate_base_commit": body["candidate_base_commit"], "candidate_ref": body["candidate_ref"],
        "task_map_generation": generation, "freeze_receipt_ref": body["freeze_receipt_ref"],
        "plan_package_ref": package_ref, "plan_approval_event_id": decision.id,
        "source_invoke_event_id": invoke.id, "source_goal_event_id": goal.id,
        "source_claim_set_ref": claim_ref, "task_proofs": proofs,
        "execution_policy_digest": execution_digest,
        "execution_evidence": [execution_evidence[k] for k in sorted(execution_evidence)],
        "source_authority": authority, "candidate_reader_stage_ids": stages,
        "reuse_policy": "completed_tasks_and_execution_evidence_only",
        "rerun_policy": "configured_candidate_readers_then_existing_goal_and_delivery_gates",
    }
    return {**manifest, "manifest_digest": digest(manifest)}


def authorized_manifest(runtime: Any, descriptor: Mapping[str, Any], run_id: str) -> dict:
    """Only a completed controlled action can admit a continuation input."""
    receipt = _json(Path(runtime.state_dir), descriptor)
    manifest = receipt.get("manifest") or {}
    reviewed = str(manifest.get("manifest_digest") or "")
    if (receipt.get("schema_version") != SCHEMA or not receipt.get("approval_ref")
            or manifest.get("schema_version") != SCHEMA
            or digest({k: v for k, v in manifest.items() if k != "manifest_digest"}) != reviewed
            or receipt.get("workflow_run_id") != run_id
            or run_id != "continuation-" + reviewed[:24]):
        raise ValueError("invalid Candidate continuation identity")
    events = runtime.event_log.read_all()
    authorized = any(e.type == "runtime.action.attempt.completed" and e.origin == "kernel"
                     and e.payload.get("action") == ACTION
                     and e.payload.get("workflow_run_id") == run_id
                     and e.payload.get("receipt_ref") == dict(descriptor)
                     and e.payload.get("manifest_digest") == reviewed for e in events)
    if not authorized:
        raise ValueError("Candidate continuation lacks a controlled authorization receipt")
    return receipt


def source_evidence_run(runtime: Any, candidate: Any) -> str:
    """Narrow inheritance to Tasks of one exact authorized frozen Candidate."""
    body = candidate.payload
    run_id = str(body.get("workflow_run_id") or "")
    descriptor = body.get("candidate_continuation_ref")
    if not descriptor:
        return run_id
    receipt = authorized_manifest(runtime, descriptor, run_id)
    manifest = receipt["manifest"]
    for key in ("candidate_head_commit", "candidate_base_commit", "candidate_ref", "task_map_generation"):
        if body.get(key) != manifest.get(key):
            raise ValueError("continuation Candidate binding changed: " + key)
    if sorted(body.get("completed_task_ids") or []) != sorted(p["task_id"] for p in manifest["task_proofs"]):
        raise ValueError("continuation Candidate Task scope changed")
    return str(manifest["source_run_id"])


def closure_provenance(events, *, state_dir: Path, workflow_run_id: str) -> dict:
    """Expose historical facts as historical facts, not successor approvals."""
    candidate = _latest(events, "candidate.ready", workflow_run_id)
    if candidate is None or not candidate.payload.get("candidate_continuation_ref"):
        return {}
    runtime = SimpleNamespace(state_dir=state_dir, event_log=SimpleNamespace(read_all=lambda: events))
    source_evidence_run(runtime, candidate)
    descriptor = candidate.payload["candidate_continuation_ref"]
    receipt = authorized_manifest(runtime, descriptor, workflow_run_id)
    manifest = receipt["manifest"]
    from zf.runtime.goal_closure_lifecycle_evidence import plan_lifecycle_evidence, workflow_lifecycle_evidence

    return {"receipt_ref": descriptor, "source_run_id": manifest["source_run_id"],
        "source_candidate_event_id": manifest["candidate_event_id"],
        "source_terminal_event_id": manifest["source_terminal_event_id"],
        "task_proofs": manifest["task_proofs"], "execution_evidence": manifest["execution_evidence"],
        "workflow_lifecycle_evidence": workflow_lifecycle_evidence(events, workflow_run_id=manifest["source_run_id"]),
        "plan_lifecycle_evidence": plan_lifecycle_evidence(events, workflow_run_id=manifest["source_run_id"],
            task_map_generation=manifest["task_map_generation"],
            package_identity={"plan_artifact_package_digest": manifest["plan_package_ref"]["sha256"]}),
        "scope": "Historical execution evidence; current Candidate and Judge require independent admission."}
