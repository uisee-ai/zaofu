"""Authorize one supplemental Candidate Reader on its previously frozen host evidence."""

from __future__ import annotations

from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.artifact_access import require_artifact_access
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidate_plan_recovery import candidate_recovery_superseded
from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
from zf.runtime.orchestrator_agent_execution_policy import bounded_actions
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import (
    operation_request_hash,
    reduce_workflow_operations,
)

REQUESTED = "candidate.reader.reassessment.requested"
BLOCKED = "candidate.reader.reassessment.blocked"
STAGE = "reader-evidence-reassessment"


def policy_kind(config, payload):
    from zf.core.workflow.flow_metadata import flow_metadata_for
    from zf.runtime.orchestrator_agent_policy import orchestration_flow_kind

    return orchestration_flow_kind(payload, flow_metadata_for(config, payload=payload))


def enabled(config, flow_kind):
    return "reassess_reader_evidence" in (bounded_actions(config, flow_kind) or [])


def native_reassessment_enabled(config, flow_kind):
    from zf.runtime.native_activity_reassessment import ACTION
    return ACTION in (bounded_actions(config, flow_kind) or [])


def _candidate(events, p):
    run_id = p["workflow_run_id"]
    candidate = frozen_candidate_for_payload(events, run_id, payload=p)
    current = frozen_candidate_for_payload(events, run_id, payload={})
    if (
        not current
        or not candidate
        or current.id != candidate.id
        or candidate_recovery_superseded(events, candidate, run_id)
        or candidate.payload.get("candidate_head_commit") != p.get("source_commit")
        or candidate.payload.get("task_map_generation") != p.get("task_map_generation")
    ):
        raise ValueError("Reader evidence target superseded or mismatched")
    return candidate


def _scope(service, events, marker, operation_id, finding_ids):
    from zf.runtime.candidate_failure_checkpoint import aggregate_candidate_binding

    p = marker.payload
    if (
        marker.origin != "kernel"
        or marker.type != "orchestrator.replan_requested"
        or p.get("classification") != "oa_semantic_return_to_plan"
        or not p.get("orchestrator_operation_id")
        or not p.get("orchestrator_decision_ref")
    ):
        raise ValueError("Reader reassessment requires an admitted OA marker")
    candidate = _candidate(events, p)
    history = events[: events.index(marker)]
    binding = aggregate_candidate_binding(
        service.state_dir,
        history,
        failure_id=p["candidate_failure_id"],
        run_id=p["workflow_run_id"],
        task_id=p["pdd_id"],
    )
    if any(
        binding[k] != p[k]
        for k in ("candidate_event_id", "source_commit", "task_map_generation")
    ):
        raise ValueError("Reader reassessment aggregate differs from marker")
    canonical = {row["finding_id"]: row for row in binding["findings"]}
    rows = [
        row
        for row in (p.get("rework_summary") or {}).get("candidate_obligations", [])
        if row.get("operation_id") == operation_id
        and (row.get("disposition") or {}).get("classification") == "execution_unknown"
    ]
    if (
        not rows
        or sorted(finding_ids) != sorted(row["finding_id"] for row in rows)
        or len(set(finding_ids)) != len(rows)
        or any(
            not canonical.get(row["finding_id"])
            or any(row.get(k) != v for k, v in canonical[row["finding_id"]].items())
            for row in rows
        )
    ):
        raise ValueError("Reader finding selection is incomplete or unbound")
    operation = reduce_workflow_operations(history).get(operation_id, {})
    body = hydrate_sidecar_ref(service.state_dir, operation["request_ref"]).payload
    request = body["request"]
    role = operation.get("role_instance")
    kind = request.get("candidate_reassessment_flow_kind", "")
    if request.get("candidate_reader_reassessment") is not True or not enabled(
        service.config, kind
    ):
        raise ValueError("Reader reassessment lacks frozen or current authorization")
    if (
        request.get("output_profile_id") != "candidate-verify"
        or not role
        or request.get("role_instance") != role
        or operation_request_hash(body) != operation["request_hash"]
        or any(
            request.get(k) != v
            for k, v in {
                "workflow_run_id": p["workflow_run_id"],
                "task_id": p["pdd_id"],
                "task_map_generation": p["task_map_generation"],
                "target_commit": p["source_commit"],
            }.items()
        )
    ):
        raise ValueError("Reader request identity differs from frozen candidate")
    freeze_ref = candidate.payload["freeze_receipt_ref"]
    if freeze_ref["sha256"] != candidate.payload.get("freeze_receipt_digest"):
        raise ValueError("Reader freeze receipt binding differs")
    freeze = hydrate_sidecar_ref(service.state_dir, freeze_ref).payload
    if (
        freeze.get("schema_version") != "candidate-freeze-receipt.v1"
        or freeze.get("status") != "frozen"
        or any(
            freeze.get(k) != candidate.payload.get(k)
            for k in (
                "freeze_id",
                "workflow_run_id",
                "task_map_generation",
                "candidate_head_commit",
            )
        )
    ):
        raise ValueError("Reader freeze receipt differs from its candidate")
    ref = request.get("candidate_host_evidence_ref")
    lifecycle = _lifecycle_inputs(
        service.state_dir, request, operation_id, candidate.id
    )
    if not ref and not lifecycle:
        raise ValueError("Reader has no frozen verification evidence")
    evidence = hydrate_sidecar_ref(service.state_dir, ref).payload if ref else {}
    consumer = evidence.get("consumer") or {}
    identity = {
        k: request[k]
        for k in (
            "role_instance",
            "workflow_run_id",
            "task_id",
            "task_map_generation",
            "target_commit",
            "contract_snapshot_digest",
            "target_snapshot_digest",
        )
    }
    if ref and (
        evidence.get("authorizes_execution") is not False
        or not evidence.get("receipts")
        or any(
            consumer.get(k) != v
            for k, v in {
                **identity,
                "operation_id": operation_id,
                "attempt_id": request["active_attempt_id"],
            }.items()
        )
    ):
        raise ValueError("Reader frozen host receipts have a different consumer")
    for descriptor in [ref, *evidence["input_refs"]] if ref else []:
        require_artifact_access(
            descriptor.get("access_scope"),
            role=role,
            actor=role,
            purpose="candidate-verify",
        )
    receipts = {
        row["receipt_id"]: row["receipt_ref"] for row in evidence.get("receipts", [])
    }
    if len(receipts) != len(evidence.get("receipts", [])):
        raise ValueError("duplicate frozen receipt ID")
    for descriptor in receipts.values():
        hydrate_sidecar_ref(service.state_dir, descriptor)
    supplemental = None
    native_requested = any(row["disposition"].get("next_action") == "reassess_native_activity" for row in rows)
    if (native_requested and lifecycle and request.get("reassess_native_activity") is True
            and native_reassessment_enabled(service.config, kind)):
        from zf.runtime.native_activity_reassessment import supplemental_activity
        handoff = hydrate_sidecar_ref(service.state_dir, request["milestone_handoff_ref"]).payload
        supplemental = supplemental_activity(service.state_dir, history, candidate, lifecycle, handoff["members"], role)
    return {
        "schema_version": "candidate-reader-reassessment.v1",
        "authorizes_execution": False,
        **{
            k: p[k]
            for k in (
                "workflow_run_id",
                "pdd_id",
                "candidate_event_id",
                "source_commit",
                "task_map_generation",
                "candidate_failure_id",
            )
        },
        "flow_kind": kind,
        "role_instance": role,
        "marker_event_id": marker.id,
        "source_operation_id": operation_id,
        "source_request_ref": operation["request_ref"],
        "freeze_receipt_ref": freeze_ref,
        "candidate_host_evidence_ref": ref,
        "receipt_refs": receipts,
        "lifecycle": lifecycle,
        **({"native_reassessment": supplemental} if supplemental else {}),
        "findings": rows,
    }


def _lifecycle_inputs(state, request, operation_id, candidate_id):
    ref = request.get("milestone_handoff_ref")
    if not ref:
        return {}
    milestone = hydrate_sidecar_ref(state, ref).payload
    lifecycle = milestone.get("task_pipeline_lifecycles_ref")
    if not lifecycle:
        return {}
    role = request["role_instance"]
    for desc in (ref, lifecycle):
        require_artifact_access(
            desc.get("access_scope"), role=role, actor=role, purpose="candidate-verify"
        )
    if (
        milestone.get("schema_version") != "milestone-handoff.v1"
        or milestone.get("authorizes_execution") is not False
        or milestone.get("candidate_event_id") != candidate_id
        or milestone.get("consumer")
        != {
            "operation_id": operation_id,
            "actor": role,
            "attempt_id": request["active_attempt_id"],
        }
        or any(
            milestone.get("binding", {}).get(k) != request[k]
            for k in (
                "workflow_run_id",
                "target_commit",
                "task_map_generation",
                "task_id",
                "contract_snapshot_digest",
                "target_snapshot_digest",
            )
        )
    ):
        raise ValueError("Reader milestone evidence binding differs")
    body = hydrate_sidecar_ref(state, lifecycle).payload
    summary = body.get("summary", {})
    if (
        body.get("schema_version") != "milestone-task-lifecycles.v1"
        or summary.get("workflow_run_id") != request["workflow_run_id"]
        or summary.get("candidate_generation") != request["task_map_generation"]
        or summary.get("target_commit") != request["target_commit"]
        or summary.get("candidate_event_id") != candidate_id
    ):
        raise ValueError("Reader lifecycle measurement binding differs")
    windows = {
        f"lifecycle-{lifecycle['sha256']}-{i}": window
        for i, window in enumerate(summary.get("generations", []))
    }
    if not windows:
        return {}
    # Retain the original milestone grant and exactly its allowed JSON ranges.
    source = {
        **lifecycle,
        "source_id": "milestone-task-lifecycles",
        "artifact_id": "milestone-task-lifecycles",
        "allowed_paths": [
            "$.summary",
            *(f"$.tasks[{i}]" for i in range(len(milestone["members"]))),
        ],
        "access_scope": {"actors": [role], "purposes": ["candidate-verify"]},
    }
    return {"source": source, "windows": windows}


def request_reader_reassessment(
    service, *, marker, operation_id, finding_ids
) -> ZfEvent:
    """Consume only the signed marker already present in this project's ledger."""
    with locked_path(
        Path(service.state_dir) / "locks" / "candidate-reader-reassessment"
    ):
        events = service.writer.event_log.read_all()
        recorded = next((e for e in events if e.id == marker.id), None)
        if not recorded or recorded != marker:
            raise ValueError("Reader reassessment marker not in ledger")
        prior = next(
            (
                e
                for e in events
                if e.type == REQUESTED
                and e.origin == "kernel"
                and e.causation_id == marker.id
                and e.payload.get("source_operation_id") == operation_id
            ),
            None,
        )
        if prior:
            if prior.payload["finding_ids"] != sorted(finding_ids):
                raise ValueError("Reader reassessment request scope differs")
            return prior
        scope = _scope(service, events, recorded, operation_id, finding_ids)
        ref = write_immutable_json_sidecar(
            service.state_dir,
            scope,
            root="candidate-reader-reassessments",
            kind="candidate_reader_reassessment",
            schema_version=scope["schema_version"],
            created_by="kernel",
            source_event_id=marker.id,
        )
        ref["access_scope"] = {
            "actors": [scope["role_instance"]],
            "purposes": ["candidate-verify"],
        }
        return service.writer.append(
            ZfEvent(
                type=REQUESTED,
                origin="kernel",
                actor="zf-runtime",
                task_id=scope["pdd_id"],
                correlation_id=scope["workflow_run_id"],
                causation_id=marker.id,
                payload={
                    **{
                        k: scope[k]
                        for k in (
                            "workflow_run_id",
                            "candidate_event_id",
                            "source_operation_id",
                            "role_instance",
                            "marker_event_id",
                        )
                    },
                    "finding_ids": sorted(finding_ids),
                    "evidence_scope_ref": ref,
                },
            )
        )


def finding_results(scope, semantic):
    rows = semantic.get("finding_results")
    required = {row["finding_id"] for row in scope["findings"]}
    if (
        not isinstance(rows, list)
        or len(rows) != len(required)
        or any(not isinstance(row, dict) for row in rows)
        or {row.get("finding_id") for row in rows} != required
    ):
        raise ValueError(
            "Address every assigned finding exactly once in finding_results"
        )
    for row in rows:
        receipts = row.get("receipt_ids", [])
        windows = row.get("lifecycle_window_ids", [])
        if (
            row.get("outcome") not in {"supported", "refuted", "unknown"}
            or not isinstance(row.get("explanation"), str)
            or not row["explanation"].strip()
            or not isinstance(receipts, list)
            or not isinstance(windows, list)
            or not (receipts or windows)
            or any(
                not isinstance(w, str)
                or not any(w in (scope.get(key) or {}).get("windows", {}) for key in ("lifecycle", "native_reassessment"))
                for w in windows
            )
            or any(
                not isinstance(v, str) or v not in scope["receipt_refs"]
                for v in receipts
            )
        ):
            raise ValueError(
                "Include a supported, refuted or unknown outcome, explanation and bound receipt_ids or lifecycle_window_ids"
            )
    return rows


def validate_submission(state, request, semantic, events):
    from zf.runtime.input_delivery_preflight import preflight_required_reads

    scope = hydrate_sidecar_ref(state, request["reassessment_scope_ref"]).payload
    _candidate(events, scope)
    rows = finding_results(scope, semantic)
    evidence = scope.get("candidate_host_evidence_ref")
    host = hydrate_sidecar_ref(state, evidence).payload if evidence else {}
    sources = {source["source_id"]: source for source in host.get("input_refs", [])}
    required = list(request["required_reads"])
    cited = {w for row in rows for w in row.get("lifecycle_window_ids", [])}
    for key in ("lifecycle", "native_reassessment"):
        measurement = scope.get(key) or {}
        if not cited.intersection(measurement.get("windows", {})):
            continue
        source = measurement["source"]
        required.append(
            {
                "source_id": source["source_id"],
                "artifact_id": source["artifact_id"],
                "artifact_sha256": source["sha256"],
                "json_path": "$.summary",
            }
        )
    for receipt_id in {r for row in rows for r in row.get("receipt_ids", [])}:
        original = sources.get("candidate-host-original-receipt-" + receipt_id)
        if (
            not original
            or original["sha256"] != scope["receipt_refs"][receipt_id]["sha256"]
        ):
            raise ValueError("Cited host receipt lacks a frozen readable source")
        required.append(
            {
                "source_id": original["source_id"],
                "artifact_id": original["artifact_id"],
                "artifact_sha256": original["sha256"],
                "json_path": "$",
            }
        )
    policy = hydrate_sidecar_ref(state, request["input_consumption_policy_ref"]).payload
    issues = preflight_required_reads(state, {**policy, "required_reads": required})
    if issues:
        raise ValueError(issues[0].get("message") or issues[0]["code"])


def reader_reassessment_state(state_dir, events, *, request_event_id):
    event = next(
        (
            e
            for e in events
            if e.id == request_event_id and e.origin == "kernel" and e.type == REQUESTED
        ),
        None,
    )
    if not event:
        raise ValueError("Unknown admitted Reader reassessment")
    base = {
        "request_event_id": event.id,
        "source_operation_id": event.payload["source_operation_id"],
        "operation_id": "",
        "status": "requested",
    }
    blocked = next(
        (
            e
            for e in events
            if e.type == BLOCKED and e.origin == "kernel" and e.causation_id == event.id
        ),
        None,
    )
    if blocked:
        return {**base, "status": "needs_owner", "reason": blocked.payload["reason"]}
    scope_ref = event.payload["evidence_scope_ref"]
    try:
        scope = hydrate_sidecar_ref(state_dir, scope_ref).payload
    except (OSError, ValueError, TypeError) as exc:
        return {**base, "status": "needs_owner", "reason": str(exc)}
    operations = reduce_workflow_operations(
        events, workflow_run_id=scope["workflow_run_id"]
    )
    op = operations.get(supplemental_operation_id(event))
    if op is None:
        return base
    try:
        signed = hydrate_sidecar_ref(state_dir, op["request_ref"]).payload["request"]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {**base, "status": "needs_owner", "reason": str(exc)}
    if (
        signed.get("reassessment_scope_ref") != scope_ref
        or signed.get("workflow_run_id") != scope["workflow_run_id"]
        or signed.get("role_instance") != scope["role_instance"]
    ):
        raise ValueError("Reader reassessment operation scope differs")
    base["operation_id"] = op["operation_id"]
    if op["status"] == "settled":
        ref = op.get("admitted_control_result_ref")
        try:
            if not ref:
                raise ValueError("Settled Reader lacks an admitted report")
            report = hydrate_sidecar_ref(state_dir, ref).payload
            rows = finding_results(scope, report)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            return {**base, "status": "needs_owner", "reason": str(exc)}
        status = (
            "needs_owner"
            if report.get("execution_status") != "completed"
            or any(r["outcome"] == "unknown" for r in rows)
            else "completed"
        )
        return {
            **base,
            "status": status,
            "control_result_ref": ref,
            "finding_results": rows,
        }
    if op["status"] in {"interrupted", "failed", "cancelled", "superseded"}:
        return {**base, "status": "needs_owner", "reason": op["status"]}
    return {
        **base,
        "status": "running" if op["status"] != "requested" else "requested",
    }


def supplemental_operation_id(event):
    from zf.runtime.workflow_operation import stable_operation_id

    return stable_operation_id(
        workflow_run_id=event.payload["workflow_run_id"],
        parent_stage_id=STAGE,
        operation_key="reassessment-" + event.id,
        operation_type="fanout_reader_child",
    )


def dispatch_pending_reassessments(runtime):
    from zf.runtime.run_admission import build_run_admission_projection

    events = runtime.event_log.read_all()
    claimed = {
        e.causation_id
        for e in events
        if e.origin == "kernel"
        and e.type in {BLOCKED, "candidate.reader.reassessment.dispatching"}
    }
    candidates = [
        e
        for e in events
        if e.type == REQUESTED and e.origin == "kernel" and e.id not in claimed
    ]
    for event in candidates:
        with locked_path(
            Path(runtime.state_dir) / "locks" / ("reader-reassessment-" + event.id)
        ):
            events = runtime.event_log.read_all()
            if any(
                e.origin == "kernel"
                and e.causation_id == event.id
                and e.type in {BLOCKED, "candidate.reader.reassessment.dispatching"}
                for e in events
            ):
                continue
            try:
                scope = hydrate_sidecar_ref(
                    runtime.state_dir, event.payload["evidence_scope_ref"]
                ).payload
                _candidate(events, scope)
                if not enabled(runtime.config, scope["flow_kind"]):
                    raise ValueError("Reader reassessment authority revoked")
                if scope.get("native_reassessment") and not native_reassessment_enabled(runtime.config, scope["flow_kind"]):
                    raise ValueError("Native activity reassessment authority revoked")
                run = build_run_admission_projection(events).runs.get(
                    scope["workflow_run_id"]
                )
                if run is None or run.status != "running":
                    if run and run.status == "paused":
                        continue
                    raise ValueError(
                        "Reader reassessment requires an admitted live run"
                    )
                role = next(
                    (
                        r
                        for r in runtime.all_roles()
                        if r.instance_id == scope["role_instance"]
                    ),
                    None,
                )
                if role is None or role.role_kind != "reader":
                    raise ValueError("Assigned read-only Reader is unavailable")
                _dispatch(runtime, event, scope, role, events)
            except (ValueError, KeyError, OSError) as exc:
                runtime.event_writer.append(
                    ZfEvent(
                        type=BLOCKED,
                        origin="kernel",
                        actor="zf-runtime",
                        causation_id=event.id,
                        correlation_id=event.payload["workflow_run_id"],
                        task_id=event.task_id,
                        payload={
                            "workflow_run_id": event.payload["workflow_run_id"],
                            "request_event_id": event.id,
                            "source_operation_id": event.payload["source_operation_id"],
                            "reason": str(exc),
                        },
                    )
                )


def _dispatch(runtime, event, scope, role, events):
    from zf.runtime.call_result_runtime import prepare_call_operation
    from zf.runtime.fanout import FanoutContext

    context = FanoutContext.create(
        stage_id=STAGE,
        topology="fanout_reader",
        trace_id=scope["workflow_run_id"],
        trigger_event_id=event.id,
        target_ref="",
        role_instances=[role.instance_id],
        identity_scope=event.id,
    )
    child = context.expected_children[0]
    dispatch_id = f"run-{context.fanout_id}-{child.child_id}"
    if not runtime._ensure_fanout_role_dispatchable(
        role=role,
        fanout_id=context.fanout_id,
        stage_id=STAGE,
        child_id=child.child_id,
        run_id=dispatch_id,
        trace_id=context.trace_id,
        task_id=scope["pdd_id"],
        causation_id=event.id,
        prepare_only=True,
    ):
        return
    ref = event.payload["evidence_scope_ref"]
    evidence_ref = scope["candidate_host_evidence_ref"]
    evidence = (
        hydrate_sidecar_ref(runtime.state_dir, evidence_ref).payload
        if evidence_ref
        else {}
    )
    # The scope is an index. Original grants and allowed ranges travel with each frozen input.
    inputs = [
        {
            **ref,
            "source_id": "reader-reassessment-scope",
            "artifact_id": "reassessment.json",
            "allowed_paths": ["$"],
        }
    ]
    if evidence_ref:
        inputs.extend(
            [
                {
                    **evidence_ref,
                    "source_id": "reader-reassessment-host",
                    "artifact_id": "host-receipts.json",
                    "allowed_paths": ["$"],
                },
                *evidence["input_refs"],
            ]
        )
    for key in ("lifecycle", "native_reassessment"):
        if scope.get(key):
            inputs.append(scope[key]["source"])
    required_reads = [
        {
            "source_id": inputs[0]["source_id"],
            "artifact_id": inputs[0]["artifact_id"],
            "artifact_sha256": ref["sha256"],
            "json_path": "$",
            "min_returned_bytes": 1,
            "allow_truncated": False,
        }
    ]
    child.payload.update(
        workflow_run_id=context.trace_id,
        flow_kind=scope["flow_kind"],
        task_id=scope["pdd_id"],
        pdd_id=scope["pdd_id"],
        role_instance=role.instance_id,
        stage_id=STAGE,
        child_id=child.child_id,
        fanout_id=context.fanout_id,
        run_id=dispatch_id,
        task_map_generation=scope["task_map_generation"],
        target_commit=scope["source_commit"],
        input_refs=inputs,
        required_reads=required_reads,
        output_profile_id="workflow-read",
        canonical_success_event="workflow.child.completed",
        canonical_failure_event="workflow.child.failed",
        skills=list(role.skills),
        instruction=(
            "Reassess only the frozen findings in reassessment.json using the bound host receipts. "
            "Do not execute host probes or change the product. Submit a workflow-read report with "
            "finding_results entries for each finding_id, outcome supported/refuted/unknown, "
            "explanation and receipt_ids or lifecycle_window_ids. Read each cited original receipt or lifecycle summary, including all pages. Cite the measured lifecycle window for concurrency findings and distinguish retrospective observations from original receipts. Preserve unknowns and other Readers' verdicts."
        ),
    )
    prepared = prepare_call_operation(
        runtime,
        payload=child.payload,
        operation_type="fanout_reader_child",
        operation_key="reassessment-" + event.id,
        stage_id=STAGE,
        task_id=scope["pdd_id"],
        dispatch_id=dispatch_id,
        causation_id=event.id,
        correlation_id=context.trace_id,
        reassessment_scope_ref=ref,
    )
    if not prepared.should_dispatch:
        return
    aggregate = {
        "mode": "wait_for_all",
        "child_success_event": "workflow.child.completed",
        "child_failure_event": "workflow.child.failed",
        "success_event": "candidate.reader.reassessment.reviewed",
        "failure_event": "candidate.reader.reassessment.unresolved",
    }
    if not any(
        e.type == "fanout.started"
        and e.origin == "kernel"
        and e.payload.get("fanout_id") == context.fanout_id
        for e in events
    ):
        started = context.started_event()
        started.origin = "kernel"
        started.payload.update(
            workflow_run_id=context.trace_id,
            pdd_id=scope["pdd_id"],
            aggregate_config=aggregate,
        )
        runtime.event_writer.append(started)
    runtime.event_writer.append(
        ZfEvent(
            type="candidate.reader.reassessment.dispatching",
            origin="kernel",
            actor="zf-runtime",
            task_id=scope["pdd_id"],
            causation_id=event.id,
            correlation_id=context.trace_id,
            payload={
                "workflow_run_id": context.trace_id,
                "operation_id": prepared.operation_id,
                "role_instance": role.instance_id,
                "request_hash": prepared.request_hash,
            },
        )
    )
    runtime._dispatch_reader_fanout_child(
        context=context,
        child=child,
        role=role,
        aggregate=aggregate,
        causation_id=event.id,
        prepared_dispatch={
            "prepared_call": prepared,
            "skill_entries": runtime._record_skill_provenance(
                role=role, task_id=scope["pdd_id"]
            ),
        },
    )
