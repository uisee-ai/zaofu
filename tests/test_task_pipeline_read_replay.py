import copy

import pytest

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations


def read(source, path="$", digest="a" * 64):
    return {"source_id": source, "artifact_id": source, "artifact_sha256": digest,
            "json_path": path, "allow_truncated": False, "max_chars": 0,
            "max_items": 0, "min_returned_bytes": 1}


def setup_service(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    service = WorkflowOperationService(state_dir=tmp_path, event_log=log,
                                       event_writer=EventWriter(log))
    request = {"task_pipeline_stage": "verify", "target_commit": "target-1",
               "contract_snapshot_digest": "contract-1", "operation_generation": 2,
               "required_reads": [read("contract", "$.acceptance_criteria"),
                                  read("contract", "$.verification_commands"),
                                  read("task_map", "$.tasks[1]", "b" * 64),
                                  read("planning_result", "$.tasks[1]", "b" * 64)]}
    def ensure(req):
        return service.ensure_operation(workflow_run_id="run-1", operation_id="op-1",
                                        operation_type="task-stage", task_id="T1", request=req)
    first = ensure(request)
    return service, request, first, ensure


def strengthened(request):
    current = copy.deepcopy(request)
    current["required_reads"] = [read("contract"), read("task_map", "$.tasks[1]", "b" * 64),
                                 read("critical-input", digest="c" * 64)]
    return current


def test_replay_proves_stronger_reads_and_digest_bound_alias_dedup(tmp_path):
    service, request, first, ensure = setup_service(tmp_path)
    service.block(operation_id="op-1", request_hash=first.request_hash,
                  workflow_run_id="run-1", task_id="T1", reason="request_hash_compatibility_failed")
    replay = ensure(strengthened(request))
    assert replay.status == "requested"
    assert replay.request_hash == first.request_hash
    state = reduce_workflow_operations(service.event_log.read_all())["op-1"]
    assert state["compatibility_proof_digest"]
    proofs = list((tmp_path / "artifacts/operations/replay-compatibility").glob("*.json"))
    import json
    proof = json.loads(proofs[0].read_text())
    assert proof["read_policy_compatibility"]["rule"] == "same-content-read-coverage.v1"
    assert proof["current_request"]["request"]["required_reads"] == strengthened(request)["required_reads"]


@pytest.mark.parametrize("mutation", ["target", "contract", "generation", "digest", "missing", "prefix", "truncated", "bounded", "minimum"])
def test_replay_does_not_hide_contract_changes_or_weaker_reads(tmp_path, mutation):
    _, request, _, ensure = setup_service(tmp_path)
    current = strengthened(request)
    if mutation == "target": current["target_commit"] = "target-2"
    elif mutation == "contract": current["contract_snapshot_digest"] = "contract-2"
    elif mutation == "generation": current["operation_generation"] = 3
    elif mutation == "digest": current["required_reads"][0]["artifact_sha256"] = "d" * 64
    elif mutation == "missing": current["required_reads"].pop(1)
    elif mutation == "prefix": current["required_reads"][1]["json_path"] = "$.tasks[10]"
    elif mutation == "truncated": current["required_reads"][0]["allow_truncated"] = True
    elif mutation == "bounded": current["required_reads"][0]["max_items"] = 1
    elif mutation == "minimum": current["required_reads"][0]["min_returned_bytes"] = 0
    assert ensure(current).status == "divergent"
