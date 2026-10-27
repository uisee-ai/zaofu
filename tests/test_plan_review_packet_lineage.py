from __future__ import annotations

import json

import pytest

from tests.test_operator_plan_preview import _artifacts, _preview
from zf.runtime.plan_preview_artifacts import write_plan_preview_bundle


@pytest.mark.parametrize(
    "task_run,source_run,task_pdd,source_pdd,mismatch",
    [
        ("run-1", "run-1", "TASK-1", "TASK-1", False),
        ("run-1", "run-2", "TASK-1", "TASK-1", True),
        ("run-2", "run-1", "TASK-1", "TASK-1", True),
        ("run-1", "run-1", "TASK-1", "TASK-2", True),
        ("run-1", "run-1", "TASK-1", "", False),
    ],
)
def test_preview_compares_lineage_within_identity_namespace(
    tmp_path, task_run, source_run, task_pdd, source_pdd, mismatch,
):
    state, _, payload = _artifacts(tmp_path)
    payload["workflow_run_id"] = "run-1"
    (state / "artifacts/tasks.json").write_text(json.dumps({
        "tasks": [], "pdd_id": task_pdd,
        "metadata": {"workflow_run_id": task_run},
    }))
    (state / "artifacts/source_index.json").write_text(json.dumps({
        "metadata": {"workflow_run_id": source_run, "pdd_id": source_pdd},
    }))
    payload["source_index_ref"] = "artifacts/source_index.json"
    descriptor = write_plan_preview_bundle(
        state, payload=payload, refs=payload, project_root=tmp_path,
        source_event_id="evt-plan",
    )
    payload["plan_preview_bundle_ref"] = descriptor["ref"]
    payload["plan_preview_bundle_digest"] = descriptor["sha256"]

    result = _preview(state, payload)

    assert result["status"] == "pending"
    packet = result["review_packet"]
    findings = [d for d in packet["diagnostics"] if d["code"] == "artifact_lineage_mismatch"]
    assert bool(findings) is mismatch
    provenance = next(s for s in packet["sections"] if s["id"] == "provenance")
    assert provenance["status"] == ("error" if mismatch else "ready")
    if mismatch:
        assert packet["status"] == "blocked"
        assert all(f["severity"] == "error" for f in findings)


def test_review_packet_preserves_event_pdd_and_bound_product_source(tmp_path):
    from hashlib import sha256

    from tests.test_plan_review_packet import _packet
    from zf.runtime.plan_review_packet import build_review_packet

    _, kwargs = _packet(tmp_path, [], [])
    kwargs["payload"]["pdd_id"] = "TASK-1"
    product = tmp_path / "product_acceptance_spec.json"
    product.write_text(json.dumps({"workflow_run_id": "inventory-run"}))
    artifact = next(item for item in kwargs["artifacts"] if item["name"] == "product_acceptance_spec")
    artifact["sha256"] = sha256(product.read_bytes()).hexdigest()
    packet = build_review_packet(tmp_path, **kwargs, markdown="# Plan", preview_bundle={})
    section = next(item for item in packet["sections"] if item["id"] == "provenance")
    assert section["status"] == "ready"
    assert section["counts"]["lineage_ids"] == 2
    assert "pdd_id=TASK-1" in section["notes"][0]
    assert "workflow_run_id=inventory-run" in section["notes"][0]
    assert "product_acceptance_spec" in section["artifact_names"]
    assert str(product) in section["refs"]

    product.write_text(json.dumps({"workflow_run_id": "tampered"}))
    tampered = build_review_packet(tmp_path, **kwargs, markdown="# Plan", preview_bundle={})
    assert tampered["status"] == "blocked"
    assert any(item["code"] == "review_artifact_unreadable" for item in tampered["diagnostics"])
    section = next(item for item in tampered["sections"] if item["id"] == "provenance")
    assert "product_acceptance_spec" not in section["artifact_names"]
    assert "tampered" not in section["notes"][0]
