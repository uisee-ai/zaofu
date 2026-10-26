"""Review projections use bound source identifiers, independent of project naming."""

import hashlib
import json

from zf.runtime.plan_review_packet import build_review_packet, render_human_review_dossier


def _packet(tmp_path, source_ids, covered_ids):
    bodies = {
        "source_index": {"traceability": [{"trace_id": key} for key in source_ids]},
        "coverage_ledger": {"entries": [{"trace_id": key} for key in covered_ids]},
        "product_acceptance_spec": {"user_journeys": [{"journey_id": "stock-adjustment"}]},
        "acceptance_matrix": {"acceptance": [{"id": "stock-delta"}]},
        "test_matrix": {"commands": [{"id": "stock-test"}]},
    }
    artifacts = []
    for name, body in bodies.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(body))
        artifacts.append({"name": name, "ref": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    kwargs = dict(
        project_root=tmp_path, payload={"workflow_run_id": "inventory-run"},
        artifacts=artifacts, authority_refs=["requirements.md"],
        task_map_summary={"ok": True, "task_count": 1, "tasks": []},
        decision_items=[{"decision_id": "stock-owner", "question": "Who owns stock?"}],
    )
    packet = build_review_packet(
        tmp_path, **kwargs, markdown="# Inventory plan",
        preview_bundle={"workflow_run_id": "inventory-run"},
    )
    return packet, kwargs


def test_review_accepts_project_defined_ids_without_unrelated_categories(tmp_path):
    packet, _ = _packet(tmp_path, ["库存/调整", "REQ-stock"], ["库存/调整", "REQ-stock"])
    assert packet["status"] == "ready"
    assert packet["missing_sections"] == []
    assert packet["source_ids"]["requirements"] == ["REQ-stock", "库存/调整"]


def test_review_retains_missing_source_mappings(tmp_path):
    packet, _ = _packet(tmp_path, ["REQ-stock", "REQ-refund"], ["REQ-stock"])
    section = next(row for row in packet["sections"] if row["id"] == "source_coverage")
    assert packet["status"] == "incomplete"
    assert section["counts"] == {"source": 2, "covered": 1, "missing": 1}
    assert "REQ-refund" in " ".join(section["notes"])


def test_dossier_leaves_absent_product_objective_explicit(tmp_path):
    packet, kwargs = _packet(tmp_path, [], [])
    assert "source_coverage" in packet["missing_sections"]
    dossier = render_human_review_dossier(tmp_path, **kwargs, status="pending", review_packet=packet)
    assert "绑定需求未提供结构化目标" in dossier
    assert "阶段：`未声明`" in dossier
    assert "v1.4" not in dossier
    assert "T10" not in dossier
