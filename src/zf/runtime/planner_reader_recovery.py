"""Pin an admitted Run Manager diagnosis when the same Plan author retries."""

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


def has_bound_diagnostic_option(runtime, events, failure):
    """A disabled resident is replaced only when its on-demand consumer has an exact, authorized option."""
    from zf.runtime.on_demand_reader_diagnosis import reader_option

    run_id = failure.payload.get("workflow_run_id")
    if not run_id or failure.origin != "kernel":
        return False
    action = {
        "request_kind": "recovery_diagnosis",
        "workflow_run_id": run_id,
        "source_event_ids": [failure.id],
    }
    option = reader_option(runtime.state_dir, events, action, config=runtime.config)
    if not option:
        return False
    failure_id = option["failure_event_id"]
    child = next((e for e in events if e.id == failure_id), None)
    return bool(
        child
        and child.payload.get("control_result_schema") == "workflow-read-result.v1"
        and child.payload.get("result_semantics") == "artifact_production"
        and child.payload.get("semantic_verdict") == "abstained"
    )


def attach_diagnosis(runtime, payload, previous_operation_id):
    events = runtime.event_log.read_all()
    operations = reduce_workflow_operations(events)
    prior = operations.get(previous_operation_id, {})
    if (
        prior.get("attempt_domain") != "plan"
        or prior.get("semantic_verdict") != "abstained"
    ):
        return
    for event in reversed(events):
        p = event.payload
        if (
            event.type != "run.manager.agent.recommendation"
            or event.origin != "kernel"
            or event.actor != "run-manager"
            or p.get("action") != "child-rebuild"
        ):
            continue
        target = p.get("action_payload") or {}
        if (
            target.get("target_operation_id") != previous_operation_id
            or target.get("active_attempt_id") != prior["active_attempt_id"]
            or target.get("workflow_run_id") != prior["workflow_run_id"]
        ):
            continue
        opinion = operations.get(p.get("diagnosis_operation_id"), {})
        ref = p.get("diagnosis_result_ref")
        if (
            not ref
            or opinion.get("operation_type") != "run_manager_reflect"
            or opinion.get("status") != "settled"
            or opinion.get("admitted_call_result_ref") != ref
            or opinion.get("workflow_run_id") != prior["workflow_run_id"]
        ):
            continue
        request = hydrate_sidecar_ref(
            runtime.state_dir, opinion["request_ref"]
        ).payload["request"]
        option = (request.get("metadata") or {}).get("reader_recovery_option") or {}
        content = hydrate_sidecar_ref(runtime.state_dir, ref).payload
        if (
            not option.get("option_id")
            or option["option_id"] != content.get("selected_reader_recovery")
            or option.get("failure_event_id") != target.get("source_event_id")
            or (option.get("recommendation") or {}).get("action_payload") != target
            or content.get("apply_policy") != "proposal_only"
            or not content.get("recommendation")
        ):
            continue
        note = {
            "schema_version": "planner-recovery-diagnosis.v1",
            "workflow_run_id": prior["workflow_run_id"],
            "previous_operation_id": previous_operation_id,
            "failed_event_id": target["source_event_id"],
            "diagnosis_ref": ref,
            "recommendation": content["recommendation"],
            "authority": "advice_only",
        }
        source = write_immutable_json_sidecar(
            runtime.state_dir,
            note,
            root="planner-recovery-diagnoses",
            kind="planner_recovery_diagnosis",
            schema_version=note["schema_version"],
            created_by="kernel",
            source_event_id=event.id,
        )
        key = "plan-recovery-" + source["sha256"][:16]
        descriptors = [
            row for row in payload.get("input_refs", []) if row.get("source_id") != key
        ]
        payload["input_refs"] = [
            *descriptors,
            {**source, "source_id": key, "artifact_id": key, "allowed_paths": ["$"]},
        ]
        read = {
            "source_id": key,
            "artifact_id": key,
            "artifact_sha256": source["sha256"],
            "json_path": "$",
            "min_returned_bytes": 1,
        }
        payload["required_reads"] = [
            read,
            *(row for row in payload.get("required_reads", []) if row != read),
        ]
        return
