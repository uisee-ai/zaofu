"""Typed evidence descriptors survive the legacy result projection."""

from copy import deepcopy

import pytest

from zf.runtime.artifact_read_capability import provision_role_artifact_read_credential
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
from zf.runtime.orchestrator_agent_operations import (
    activate_orchestrator_agent_operation,
)
from zf.runtime.result_submit import (
    SemanticResultSubmitService,
    _compatibility_projection,
    provision_role_submit_credential,
)
from zf.runtime.workflow_operation import load_workflow_operation
from tests.test_orchestrator_agent_operations import _decision, _request, _runtime


@pytest.mark.parametrize(
    "field", ["report", "verification_result", "orchestration_decision"]
)
def test_mixed_refs_preserve_digest_scope_order_and_distinct_versions(field):
    ref = {
        "ref": "artifacts/evidence.json",
        "sha256": "a" * 64,
        "access_scope": {"roles": ["verify"]},
        "required": True,
    }
    other = {**ref, "sha256": "b" * 64}
    reversed_keys = dict(reversed(list(ref.items())))
    refs = ["receipt:first", ref, "receipt:first", reversed_keys, other]
    before = deepcopy(refs)
    projected = _compatibility_projection(field, {field: {"evidence_refs": refs}})
    assert projected["report"]["evidence_refs"] == ["receipt:first", ref, other]
    assert refs == before


def test_artifact_delivery_refs_merge_with_typed_evidence():
    ref = {"ref": "artifacts/proof.json", "sha256": "a" * 64}
    result = _compatibility_projection(
        "artifact_delivery_result",
        {
            "artifact_delivery_result": {
                "verification_evidence_refs": [ref, "artifacts/output.md"],
                "artifacts": [
                    {"ref": "artifacts/output.md"},
                    {"ref": "artifacts/second.md"},
                ],
            },
        },
    )
    assert result["report"]["evidence_refs"] == [
        ref,
        "artifacts/output.md",
        "artifacts/second.md",
    ]


def test_public_orchestrator_submit_with_descriptor_evidence_settles(tmp_path):
    runtime = _runtime(tmp_path)
    token = (
        provision_role_submit_credential(runtime.state_dir, "orchestrator")
        .read_text()
        .strip()
    )
    provision_role_artifact_read_credential(
        runtime.state_dir, "orchestrator", role_name="orchestrator", provider="mock"
    )
    prepared = _request(runtime)
    activate_orchestrator_agent_operation(
        runtime, prepared, dispatch_id="dispatch-1", causation_id="evt-run-start"
    )
    for source in prepared.context.source_manifest["sources"]:
        read_attempt_artifact(
            runtime.state_dir,
            manifest=prepared.context.source_manifest,
            source_id=source["source_id"],
            artifact_id=source["artifact_id"],
            actor="orchestrator",
            role="orchestrator",
            provider="mock",
        )
    result = _decision(prepared)
    result["evidence_refs"] = [deepcopy(prepared.context.input_ref)]
    submitted = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    ).submit(
        operation_id=prepared.operation_id,
        semantic_result=result,
        role_instance="orchestrator",
        credential=token,
    )
    assert submitted.canonical_event_type == "orchestrator.semantic.decision.submitted"
    assert (
        load_workflow_operation(runtime.event_log, prepared.operation_id)["status"]
        == "settled"
    )
    event = next(
        e for e in runtime.event_log.read_all() if e.id == submitted.canonical_event_id
    )
    hydrated = hydrate_profiled_control_result_event(runtime.state_dir, event)
    assert (
        result["evidence_refs"]
        == hydrated.payload["orchestration_decision"]["evidence_refs"]
    )
