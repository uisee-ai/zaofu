"""Read real bound inputs: preserve facts, permissions and historical coverage."""
import hashlib
import json

import pytest

from zf.runtime.artifact_read_ledger import build_input_consumption_policy, read_attempt_artifact
from zf.runtime.artifact_read_requirements import build_canonical_required_reads
from zf.runtime.plan_input_read_selection import complete_partition_read, select_plan_input_reads


def fixture(state):
    sources = []

    def source(name, body, artifact=None):
        path = state / "artifacts" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, ensure_ascii=False))
        row = {"source_id": name, "artifact_id": artifact or name, "ref": path.relative_to(state).as_posix(),
               "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "allowed_paths": ["$"]}
        sources.append(row)
        return row

    plan = "交付与失败不变项\n" * 1500
    matrix = {"cases": [{"input": "bad", "expected": "unchanged"}] * 100}
    source("requirement", {"objective": "保留原文"})
    source("child-artifact-planner-1", plan)
    supplemental = source("child-artifact-planner-2", matrix, "misleading-name.json")
    report = source("child-result-planner", {"child_id": "planner", "plan_md": plan,
        "reason": "", "unknown_future_constraint": "MUST preserve this finding",
        "plan_ports": [{"logical_name": "test_matrix", "ref": supplemental["ref"],
                        "sha256": supplemental["sha256"], "body": matrix}]})
    manifest = {"workflow_run_id": "run", "attempt_id": "attempt", "dispatch_id": "dispatch", "sources": sources}
    required = build_canonical_required_reads(manifest, output_profile_id="plan-synth")
    return manifest, required, report, supplemental


def read_rows(state, manifest, rows):
    return [read_attempt_artifact(state, manifest=manifest, source_id=row["source_id"],
        artifact_id=row["artifact_id"], artifact_sha256=row["artifact_sha256"],
        json_path=row["json_path"], record=False) for row in rows]


def test_lossless_partition_preserves_unknown_fields_and_complete_baseline(tmp_path):
    manifest, required, report, _ = fixture(tmp_path)
    selected, strategy = select_plan_input_reads(tmp_path, manifest, required, policy={"mode": "full"})
    assert strategy == "lossless_report_partition"
    values = read_rows(tmp_path, manifest, selected)
    assert any("MUST preserve this finding" in value["content"] for value in values)
    receipts = [value["read"] for value in values]
    assert complete_partition_read(tmp_path, report, selected, receipts)
    missing = [value["read"] for value in values if value["read"]["json_path"] != "$.unknown_future_constraint"]
    assert not complete_partition_read(tmp_path, report, selected, missing)
    empty = next(row for row in selected if row["json_path"] == "$.reason")
    assert empty["min_returned_bytes"] == 0
    assert build_input_consumption_policy(workflow_run_id="run", attempt_id="attempt",
                                         required_reads=[empty])["required_reads"][0]["min_returned_bytes"] == 0


def test_first_targeted_uses_bound_port_and_keeps_original_available(tmp_path):
    manifest, required, report, supplement = fixture(tmp_path)
    policy = {"mode": "targeted", "focus": ["检查本片验收时点"]}
    selected, strategy = select_plan_input_reads(tmp_path, manifest, required, policy=policy)
    assert strategy == "targeted_navigation"
    assert not any(row["source_id"] == supplement["source_id"] for row in selected)
    assert not any(row["json_path"] == "$.plan_ports[0].body" for row in selected)
    values = read_rows(tmp_path, manifest, selected)
    assert any("MUST preserve" in value["content"] for value in values)
    assert not complete_partition_read(tmp_path, report, selected, [value["read"] for value in values])
    full = read_attempt_artifact(tmp_path, manifest=manifest, source_id=supplement["source_id"],
        artifact_id=supplement["artifact_id"], artifact_sha256=supplement["sha256"], record=False)
    assert "unchanged" in full["content"]  # Optional is still authorized and retrievable.


@pytest.mark.parametrize("fault", ["explicit", "full", "no_focus", "rework", "unknown_port", "wrong_digest"])
def test_unproven_or_explicit_supplement_stays_required(tmp_path, fault):
    manifest, required, report, supplement = fixture(tmp_path)
    policy = {"mode": "targeted", "focus": ["focus"]}
    if fault == "full": policy["mode"] = "full"
    if fault == "no_focus": policy["focus"] = []
    if fault == "rework": manifest["sources"].append({"source_id": "plan-rework-context"})
    if fault in {"unknown_port", "wrong_digest"}:
        path = tmp_path / report["ref"]
        body = json.loads(path.read_text())
        body["plan_ports"][0]["logical_name" if fault == "unknown_port" else "sha256"] = "unknown"
        path.write_text(json.dumps(body))
        old = report["sha256"]
        report["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        for row in required:
            if row["artifact_sha256"] == old: row["artifact_sha256"] = report["sha256"]
    selected, _ = select_plan_input_reads(tmp_path, manifest, required, policy=policy, explicit=fault == "explicit")
    assert any(row["source_id"] == supplement["source_id"] for row in selected)
    if fault == "explicit": assert selected == required


def test_actual_dispatch_pins_first_targeted_policy_and_distinguishes_it_from_incremental(tmp_path):
    from tests.test_call_result_runtime import _runtime
    from zf.runtime.call_result_runtime import prepare_call_operation

    runtime = _runtime(tmp_path)
    manifest, _, _, _ = fixture(runtime.state_dir)
    runtime.config.workflow.flow_metadata["plan_review"] = {"mode": "targeted", "focus": ["failure"], "reason": "Owner"}
    payload = {"workflow_run_id": "run", "role_instance": "critic", "output_profile_id": "plan-synth",
               "input_refs": manifest["sources"], "canonical_success_event": "fanout.synth.completed",
               "canonical_failure_event": "fanout.synth.completed"}
    prepared = prepare_call_operation(runtime, payload=payload, operation_type="fanout_synth",
        operation_key="critic", stage_id="plan", task_id="", dispatch_id="attempt")
    assert prepared.should_dispatch
    diagnostic = payload["plan_review_optimization"]
    assert diagnostic["mode"] == "targeted"
    assert diagnostic["inherited_approval"] is False
    assert diagnostic["after"]["distinct_selected_bytes"] < diagnostic["before"]["distinct_selected_bytes"]


def test_partition_bundle_deduplicates_body_but_records_each_occurrence(tmp_path):
    from zf.cli.artifact_required_read import prepare_required_read
    from zf.runtime.artifact_read_ledger_validation import required_read_satisfied

    manifest, required, report, _ = fixture(tmp_path)
    selected, _ = select_plan_input_reads(tmp_path, manifest, required, policy={"mode": "full"})
    identity = {"actor": "critic", "role": "critic", "provider": "mock", "purpose": "plan-synth"}
    cursor, receipts, contents = None, [], []
    while True:
        output, rows = prepare_required_read(tmp_path, manifest=manifest, rows=selected,
                                             identity=identity, cursor=cursor, page_bytes=8192)
        page = json.loads(output)
        receipts.extend(rows)
        contents.extend(item["content"] for item in page["items"])
        cursor = page.get("next_cursor")
        if not cursor: break
    assert all(required_read_satisfied(receipts, row) for row in selected)
    assert complete_partition_read(tmp_path, report, selected, receipts)
    assert sum(text.count("交付与失败不变项") for text in contents) <= 1500
    assert {row["source_id"] for row in receipts} == {row["source_id"] for row in selected}
