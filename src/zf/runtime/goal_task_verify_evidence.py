"""Bind on-demand Task Verify bodies to the Judge's admitted Candidate cohort."""

from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import hydrate_call_result_envelope
from zf.runtime.candidate_plan_recovery import current_frozen_reader_inputs
from zf.runtime.candidate_result_binding import candidate_task_source_commits
from zf.runtime.goal_judge_admissions import JudgeAdmissionError
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _locator(descriptor: dict) -> dict:
    if (not isinstance(descriptor, dict) or descriptor.get("access_scope")
            or not descriptor.get("ref") or not descriptor.get("sha256")):
        raise ValueError("not a shareable result reference")
    return {key: descriptor[key] for key in ("ref", "sha256")}


def _member_result(state: Path, by_id: dict, member: dict, *, run: str, freeze: str) -> dict:
    publication = by_id[member["verification_event_id"]]
    published = publication.payload
    if publication.type != "task.pipeline.verify.completed" or publication.task_id != member["task_id"]:
        raise ValueError("wrong Task Verify publication")
    admission = by_id[published["semantic_submit_admission_event_id"]]
    admitted = admission.payload
    control = _locator(admitted["control_result_ref"])
    envelope_ref = _locator(admitted["envelope_ref"])
    if (admission.task_id != member["task_id"] or admission.type != "workflow.call.result.admitted" or admission.origin != "kernel"
            or admitted.get("admission_status") != "admitted" or admitted.get("semantic_verdict") != "passed"
            or admitted.get("control_result_schema") != "verification-result.v1" or admitted.get("source_event_id") != publication.id
            or admitted.get("workflow_run_id") != run or member["workflow_run_id"] != run
            or control != {"ref": member["verification_result_ref"], "sha256": member["verification_result_digest"]}
            or control != _locator(published["control_result_ref"]) or envelope_ref != _locator(published["call_result_envelope_ref"])):
        raise ValueError("Task Verify publication differs from frozen admission")
    envelope = hydrate_call_result_envelope(state, admitted["envelope_ref"])
    if publication.id not in envelope["source_event_ids"] or _locator(envelope["control_result"]) != control:
        raise ValueError("Task Verify envelope differs from its admitted result")
    body = hydrate_sidecar_ref(state, admitted["control_result_ref"]).payload
    expected = {key: member[key] for key in (
        "workflow_run_id", "task_id", "task_map_generation", "target_commit",
        "contract_snapshot_ref", "contract_snapshot_digest", "target_snapshot_ref", "target_snapshot_digest",
    )}
    invocation = {key: admitted[key] for key in ("operation_id", "request_hash", "attempt_id", "dispatch_id")}
    if (not isinstance(body, dict) or not all(invocation.values()) or not all(expected.values())
            or any(row.get(key) != value for key, value in (expected | invocation).items()
                   for row in (published, envelope["identity"], body))
            or body.get("verification_owner") != "task_verify" or body.get("verdict") != "passed"
            or body.get("schema_version") != "verification-result.v1" or body.get("role_instance") != publication.actor
            or envelope["identity"].get("producer_role") != publication.actor):
        raise ValueError("Task Verify result identity differs from its frozen Task")
    return {"source_id": "task-verify-" + member["task_id"], "task_id": member["task_id"],
            "candidate_event_id": freeze, "target_commit": member["target_commit"], "verification_event_id": publication.id,
            "admitted_event_id": admission.id, **invocation, "envelope": envelope_ref, "control_result": control}


def judge_task_verify_evidence(state: Path, events: list[ZfEvent], *, batch: dict | None,
                               run: str, generation: str, target: str) -> list[dict]:
    """Return exact addressable results. Unproven legacy or external grants remain absent."""
    if not batch:
        return []
    end = next((i for i, e in enumerate(events) if e.id == batch.get("publication")), None)
    if end is None:
        return []
    before = events[:end + 1]
    by_id = {e.id: e for e in before}
    publication = before[-1]
    if (publication.origin != "kernel" or publication.type not in {"test.passed", "verify.passed"}
            or publication.payload.get("workflow_run_id") != run):
        return []
    members_by_task = {}
    native = None
    for reader_id in batch["readers"]:
        try:
            reader = by_id[reader_id]
            a = reader.payload
            if (reader.origin != "kernel" or reader.type != "workflow.call.result.admitted"
                    or a.get("workflow_run_id") != run or a.get("semantic_verdict") != "passed"
                    or a.get("control_result_schema") != "verification-result.v1"):
                continue
            result = hydrate_sidecar_ref(state, a["control_result_ref"]).payload
            if not isinstance(result, dict) or result.get("verification_owner") != "candidate_verify":
                continue
            candidate, trigger, contract = current_frozen_reader_inputs(state, before, result)
            if (trigger.id != batch["candidate_event_id"] or candidate.id != publication.payload.get("candidate_snapshot_event_id")
                    or candidate.payload.get("task_map_generation") != generation or candidate.payload.get("candidate_head_commit") != target):
                continue
            source = contract["source_refs"]
            if (source["freeze_receipt_ref"] != candidate.payload["freeze_receipt_ref"]["ref"]
                    or source["freeze_receipt_digest"] != candidate.payload["freeze_receipt_digest"]):
                continue
            members = contract.get("child_authorities", [])
            if not {m["task_id"] for m in members}.issubset(candidate.payload["completed_task_ids"]):
                continue
        except (ValueError, OSError, KeyError, TypeError):
            continue
        native = candidate
        for member in members:
            task = member["task_id"]
            if task in members_by_task and members_by_task[task] != member:
                raise JudgeAdmissionError("current Readers disagree on Task Verify evidence: " + task)
            members_by_task[task] = member
    if not native or not members_by_task:
        return []
    freeze_history = before[:before.index(native) + 1]
    commits = candidate_task_source_commits(freeze_history, workflow_run_id=run, candidate_head_commit=target)
    task_events = {e.id: e for e in freeze_history}
    rows = []
    for task, member in sorted(members_by_task.items()):
        if not commits.get(task) or member.get("target_commit") != commits[task]:
            continue
        try:
            rows.append(_member_result(state, task_events, member, run=run, freeze=native.id))
        except (ValueError, OSError, KeyError, TypeError):
            # Invalid or pre-admission historical evidence grants no new access.
            continue
    return rows
