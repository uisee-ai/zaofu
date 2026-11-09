"""Delivery estimates include authored payloads without admitting a Critic call."""
from hashlib import sha256
import json

from tests.test_result_submit import _plan_candidate, _running_plan_candidate_operation


def test_validate_counts_report_and_referenced_markdown_without_writes(tmp_path):
    runtime, prepared, service, token = _running_plan_candidate_operation(tmp_path)
    plan = runtime.project_root / "plan.md"
    plan.write_text("# Plan\n" + "正文" * 6000)
    candidate = _plan_candidate()
    candidate["plan_artifact_ref"] = str(plan)
    candidate["artifact_refs"] = [str(plan), str(plan)]
    before = {p: sha256(p.read_bytes()).hexdigest()
              for p in runtime.state_dir.rglob("*") if p.is_file()}
    result = service.validate_plan_candidate(
        operation_id=prepared.operation_id, semantic_result=candidate,
        role_instance="planner", credential=token, project_root=runtime.project_root)
    estimate = result["input_cost"]["delivery_estimate"]
    assert estimate["estimated_rendered_bytes"] > result["input_cost"]["distinct_rendered_bytes"] + 36000
    assert len([r for r in estimate["components"] if r["kind"] == "candidate_artifact"]) == 1
    assert any(r["kind"] == "author_report" for r in estimate["components"])
    assert any(r["kind"] == "execution_review" for r in estimate["components"])
    assert estimate["authoritative"] is False
    assert estimate["complete"] is False
    assert estimate["unresolved_components"]
    after = {p: sha256(p.read_bytes()).hexdigest()
             for p in runtime.state_dir.rglob("*") if p.is_file()}
    assert after == before


def test_report_overflow_and_missing_artifact_are_not_a_budget_pass(tmp_path):
    from zf.runtime.plan_candidate_delivery_cost import candidate_delivery_cost

    result = candidate_delivery_cost(state_dir=tmp_path, project_root=tmp_path,
        request={}, semantic={"summary": "中" * 1000, "artifact_refs": ["missing.md"]},
        execution_review={}, limits={"review_input_max_bytes": 1000})
    assert result["status"] == "review_required"
    assert result["estimated_rendered_bytes"] > 3000
    assert result["remaining_bytes"] < 0
    assert "artifact:missing.md" in result["unresolved_components"]


def test_context_is_bound_to_producer_digest_and_deduplicated(tmp_path):
    from zf.runtime.plan_candidate_delivery_cost import candidate_delivery_cost

    root = tmp_path / "artifacts/attempts/attempt-1/source-manifests"
    root.mkdir(parents=True)
    manifest = {"attempt_id": "attempt-1", "sources": [{
        "source_id": "requirement", "read_selections": [{"json_path": "$",
            "selected_sha256": "a" * 64, "selected_bytes": 24000, "selected_chars": 8000}]}]}
    raw = json.dumps(manifest).encode()
    digest = sha256(raw).hexdigest()
    (root / f"{digest}.json").write_bytes(raw)
    request = {"active_attempt_id": "attempt-1", "source_manifest_digest": digest}
    result = candidate_delivery_cost(state_dir=tmp_path, project_root=tmp_path,
        request=request, semantic={}, execution_review={}, limits={})
    context = [r for r in result["components"] if r["kind"] == "producer_context"]
    assert context[0]["rendered_bytes"] == 24000
    assert context[0]["minimum_pages"] == 1
    (root / f"{digest}.json").write_text("{}")
    broken = candidate_delivery_cost(state_dir=tmp_path, project_root=tmp_path,
        request=request, semantic={}, execution_review={}, limits={})
    assert "producer_context_manifest" in broken["unresolved_components"]
    assert not any(r["kind"] == "producer_context" for r in broken["components"])
