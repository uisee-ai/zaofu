"""A failed probe waits for Reader aggregation without draining other commands."""
from dataclasses import asdict
from unittest.mock import Mock

import pytest

from tests.test_host_verification import policy
from tests.test_reader_stage_recovery import recovery
from zf.runtime import host_verification as module
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_host_manifest import digest


@pytest.mark.parametrize("case", ["failed", "collection_failed", "success", "unstable", "unknown_exit", "wrong_policy", "wrong_operation", "task_pipeline"])
def test_reconciler_waits_only_for_bound_failed_reader_probe(recovery, monkeypatch, case):
    service, _, _ = recovery
    p = policy()
    p.commands.append("python3 later.py")
    service.config.runtime.host_verification = p
    operation = {"operation_id": "waiting-reader", "workflow_run_id": "run-1",
                 "status": "settled", "semantic_verdict": "abstained"}
    if case == "task_pipeline":
        operation["task_pipeline_stage"] = "verify"
    # A sibling Reader is still active: no aggregate failure for this fanout yet.
    service.writer.emit("fanout.child.dispatched", payload={**operation, "fanout_id": "waiting-fanout"})
    ref = write_sidecar_json(service.state_dir, "artifacts/failed-probe.json", {
        "command_id": "first", "status": "completed" if case == "success" else "failed", "source_stable": case != "unstable",
        "exit_code": None if case == "unknown_exit" else (0 if case in {"success", "collection_failed"} else 125),
        "execution_origin": "preauthorized_container",
        "policy_digest": "wrong" if case == "wrong_policy" else digest(asdict(p)),
    }, kind="test", schema_version="test.v1", created_by="test")
    service.writer.emit("runtime.action.attempt.completed", payload={
        **operation, "operation_id": "other" if case == "wrong_operation" else operation["operation_id"],
        "kind": "verify", "approval_ref": p.approval_ref, "receipt_ref": ref, "manifest_digest": "first",
    })
    contract = {"verification_commands": [
        {"command_id": "first", "command": "python3 probe.py"},
        {"command_id": "later", "command": "python3 later.py"},
    ]}
    monkeypatch.setattr(module, "reduce_workflow_operations", lambda events: {operation["operation_id"]: operation})
    monkeypatch.setattr(module, "binding", lambda *_: (None, None, contract, None))
    monkeypatch.setattr(module, "preview", lambda _, **kw: {"manifest_digest": kw["command_id"],
                                                        "operation_id": kw["operation_id"],
                                                        "identity": {"workflow_run_id": "run-1"}})
    execute = Mock()
    monkeypatch.setattr(module, "apply", execute)
    assert module.reconcile_host_verification(service) == (0 if case in {"failed", "collection_failed"} else 1)
    assert execute.call_count == (0 if case in {"failed", "collection_failed"} else 1)
    assert not any(e.type == "candidate.ready" and e.payload.get("repaired_failure_event_id")
                   for e in service.writer.event_log.read_all())
    if case in {"failed", "collection_failed"}:
        # Waiting for this fanout cannot starve another eligible operation.
        other = {**operation, "operation_id": "other-reader"}
        monkeypatch.setattr(module, "reduce_workflow_operations", lambda events: {
            operation["operation_id"]: operation, other["operation_id"]: other,
        })
        assert module.reconcile_host_verification(service) == 1
        assert execute.call_args.args[1]["operation_id"] == "other-reader"
