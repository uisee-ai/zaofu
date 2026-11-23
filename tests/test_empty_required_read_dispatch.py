"""An empty selected value must remain a required, satisfiable bound read."""
from copy import deepcopy
import json

import pytest

from tests.test_call_result_runtime import _runtime
from zf.cli.artifact_required_read import prepare_required_read
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_read_ledger import canonical_required_reads, required_read_satisfied
from zf.runtime.artifact_read_requirements import required_reads_for_attempt
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.call_result_runtime import prepare_call_operation
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _dispatch(tmp_path):
    runtime = _runtime(tmp_path)
    ports = [{"logical_name": "test_matrix", "content": {"cases": ["preserved"] * 120}}]
    refs = [write_immutable_json_sidecar(
        runtime.state_dir, body, root="test", kind="plan", schema_version="v1", created_by="test",
    ) for body in [{"plan_ports": ports, "status": "prepared"},
                   {"plan_ports": ports, "status": "completed", "backlog_ref": ""}]]
    inputs = build_plan_handoff_input_refs(
        state_dir=runtime.state_dir, project_root=runtime.project_root,
        payload={"previous_plan_candidate_refs": refs}, source_event_id="rework",
    )
    payload = {"workflow_run_id": "run", "flow_kind": "prd", "stage_id": "prd-plan",
               "fanout_id": "fanout", "child_id": "planner", "role_instance": "planner",
               "output_profile_id": "workflow-read", "attempt_domain": "plan", "input_refs": inputs,
               "canonical_success_event": "prd.plan.child.completed",
               "canonical_failure_event": "prd.plan.child.failed"}
    operation = prepare_call_operation(
        runtime, payload=payload, operation_type="fanout_reader_child", operation_key="planner",
        stage_id="prd-plan", task_id="", dispatch_id="attempt",
    )
    manifest = hydrate_sidecar_ref(runtime.state_dir, payload["attempt_source_manifest"]).payload
    required = required_reads_for_attempt(
        runtime.event_log.read_all(), "attempt", state_dir=runtime.state_dir,
        operation_id=operation.operation_id,
    )
    empty = next(row for row in required if row["json_path"] == "$.backlog_ref")
    return runtime, manifest, empty


def test_real_dispatch_empty_slice_requires_delivered_identity_bound_page(tmp_path):
    runtime, manifest, empty = _dispatch(tmp_path)
    assert empty["min_returned_bytes"] == 0
    assert not required_read_satisfied([], empty)
    output, receipts = prepare_required_read(
        runtime.state_dir, manifest=manifest, rows=[empty],
        identity={"actor": "planner", "role": "planner", "provider": "codex", "purpose": "workflow-read"},
    )
    assert json.loads(output)["items"][0]["content"] == ""
    assert required_read_satisfied(receipts, empty)
    for changes in ({"source_id": "other"}, {"artifact_sha256": "f" * 64}, {"truncated": True}):
        assert not required_read_satisfied([{**receipts[0], **changes}], empty)
    assert not required_read_satisfied(receipts, {**empty, "min_returned_bytes": 1})
    explicit = [{**empty, "min_returned_bytes": 5}]
    rows = canonical_required_reads(manifest, output_profile_id="workflow-read", explicit=explicit)
    assert next(r for r in rows if r["json_path"] == "$.backlog_ref")["min_returned_bytes"] == 5
    runtime.event_writer.append(ZfEvent(type="task.dispatched", payload={
        "attempt_id": "old-attempt", "required_reads": [{**empty, "min_returned_bytes": 1}],
    }))
    assert required_reads_for_attempt(runtime.event_log.read_all(), "old-attempt")[0]["min_returned_bytes"] == 1


@pytest.mark.parametrize("fault", ["missing", "source_digest", "selected_digest", "nonempty", "ambiguous"])
def test_unproven_empty_selection_keeps_nonempty_default(tmp_path, fault):
    _, manifest, empty = _dispatch(tmp_path)
    manifest = deepcopy(manifest)
    source = next(s for s in manifest["sources"] if s["source_id"] == empty["source_id"])
    selection = next(s for s in source["read_selections"] if s["json_path"] == "$.backlog_ref")
    if fault == "missing":
        source["read_selections"].remove(selection)
    elif fault == "source_digest":
        selection["source_sha256"] = "f" * 64
    elif fault == "selected_digest":
        selection["selected_sha256"] = "f" * 64
    elif fault == "nonempty":
        selection["selected_bytes"] = selection["selected_chars"] = 4
    else:
        source["read_selections"].append(dict(selection))
    rows = canonical_required_reads(manifest, output_profile_id="workflow-read")
    assert next(r for r in rows if r["json_path"] == "$.backlog_ref")["min_returned_bytes"] == 1
