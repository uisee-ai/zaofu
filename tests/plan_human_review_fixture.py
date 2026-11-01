"""Synthetic bound reading layers for the real API/browser fixture."""
from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
from tests.test_plan_artifact_package import _run_contract


def add_human_review_fixture(state, writer):
    task_map = {"tasks": [
        {"task_id": "T-STOCK", "title": "修正库存", "source_ref": "docs/prd.md#stock", "acceptance_criteria": [
            {"id": "AC-STOCK", "statement": "修正只结算差额", "verification_command_ids": ["VC-STOCK"], "verification_tier": "runtime"}]},
        {"task_id": "T-LEGACY", "title": "已有读取功能", "acceptance_criteria": [{"id": "AC-READ", "statement": "受权读取"}]},
    ], "goal_claims": [{"id": "G1", "statement": "库存一致，重复请求不重复结算"}], "owner_decision_items": [
        {"decision_id": "Q1", "status": "pending_decision", "blocking": False, "question": "确认库存来源", "owner": "数据负责人", "affected_task_ids": ["T-STOCK"], "latest_gate": "写入前", "closure_evidence": "确认唯一真源"},
        {"decision_id": "Q2", "status": "authorized_with_task_obligations", "blocking": False, "question": "落实版本锁", "affected_task_ids": ["T-LEGACY"]}]}
    execution = {"schema_version": "task-scoped-plan-input.v1", "shared": {"human_review": {
        "overview_markdown": "## 产品目标\n库存修正可追溯；统计扩展需另行确认。",
        "journeys_markdown": "查看库存 → 修正消耗 → 重试核对；失败时保持原状态。",
    }}, "tasks": [{"task_id": "T-STOCK", "body": {"human_review": {
        "module_spec_markdown": "### 使用者与字段\n授权操作员修改目标消耗，目标必须非负；失败不改变库存。",
        "scenarios": [{"id": "SC-STOCK", "name": "差额修正和重试", "given": "库存10、原消耗3", "when": "目标改为5并重试", "then": "库存8，重试后仍为8", "acceptance_ids": ["AC-STOCK"], "source_ids": ["REQ-STOCK"], "command_ids": ["VC-STOCK"]}],
    }}}, {"task_id": "T-LEGACY", "body": {"human_review": {"module_spec_markdown": "### 读取规格\n只能查看授权范围，拒绝越权查询。"}}}]}
    bodies = {"task_map": task_map, "execution_spec": execution, "source_inventory": {
        "pages": [{"id": "A01", "title": "库存", "task_ids": ["T-STOCK"], "reference_images": ["01", "02"], "design_status": "待设计", "implementation_status": "待实现", "visual_acceptance_status": "待视觉验收"}, {"id": "A02", "title": "查询", "task_ids": ["T-LEGACY"]}],
        "forms": [{"id": "F01", "title": "修正", "task_ids": ["T-STOCK"]}],
    }}
    bodies.update({
        "source_index": {"rows": [
            {"source_id": "REQ-STOCK", "statement": "库存不得重复扣减", "source_ref": "artifacts/source.md#stock"}, {"source_id": "REQ-NOTIFY", "statement": "通知渠道待确认"},
            {"source_id": "REQ-MISSING", "statement": "未分配来源示例"},
        ]},
        "coverage_ledger": {"rows": [
            {"source_id": "REQ-STOCK", "disposition": "covered", "task_ids": ["T-STOCK"], "acceptance_ids": ["AC-STOCK"], "verification_command_ids": ["VC-STOCK"], "evidence_status": "NOT_RUN"},
            {"source_id": "REQ-NOTIFY", "disposition": "pending_decision"},
        ]},
        "acceptance_matrix": {"acceptance": [{"id": "AC-STOCK", "statement": "修正只结算差额"}]},
        "test_matrix": {"commands": [{"id": "VC-STOCK", "command": "test stock", "owner": "task_verify", "tier": "runtime"}], "formal_acceptance_scenarios": [{"id": "E01", "task_id": "T-STOCK", "acceptance_id": "AC-STOCK", "trigger": "正式重试检查", "expected": "数据库库存保持8", "command_ids": ["VC-STOCK"]}]},
    })
    (state / "artifacts/source.md").write_text('# 原始产品需求\n\n<a id="stock"></a>\n\n## 库存规则\n\n库存不得重复扣减。\n')
    ports = []
    for name, body in bodies.items():
        descriptor = write_immutable_json_sidecar(state, body, root=f"fixtures/{name}", kind=name, schema_version=f"{name}.v1", created_by="test")
        ports.append({"logical_name": name, "artifact_kind": name, "schema_version": f"{name}.v1", "producer_stage_id": "prd-plan", "ref": descriptor["ref"], "sha256": descriptor["sha256"]})
    package = build_plan_artifact_package(workflow_run_id="preview-review", flow_kind="prd", producer_stage_id="prd-plan", run_contract=_run_contract(state), plan_revision="r1", task_map_generation="g1", produced=ports, required_ports=list(bodies))
    descriptor = write_plan_artifact_package(state, package)
    writer.append(ZfEvent(type="plan.approval.requested", actor="fixture", payload={
        "plan_id": "evt-preview-review", "workflow_run_id": "preview-review", "trace_id": "preview-review", "pdd_id": "PDD-REVIEW", "task_count": 2,
        "authority_refs": ["artifacts/source.md"],
        "task_map_ref": ports[0]["ref"], "plan_artifact_ref": "artifacts/full-plan.md",
        "plan_artifact_package_ref": descriptor["ref"], "plan_artifact_package_digest": descriptor["sha256"],
    }))
    # A genuinely bound historical package, not an unversioned UI mock.
    old_source = {"rows": [{"source_id": "REQ-STOCK", "statement": "旧版库存说明"}]}
    old_ports = [p for p in ports if p["logical_name"] not in {"source_index", "coverage_ledger"}]
    for name, body in [("source_index", old_source), ("coverage_ledger", {"rows": []})]:
        ref = write_immutable_json_sidecar(state, body, root=f"fixtures/old-{name}", kind=name, schema_version=f"{name}.v1", created_by="test")
        old_ports.append({"logical_name": name, "artifact_kind": name, "schema_version": f"{name}.v1", "producer_stage_id": "prd-plan", "ref": ref["ref"], "sha256": ref["sha256"]})
    old = build_plan_artifact_package(workflow_run_id="preview-review", flow_kind="prd", producer_stage_id="prd-plan", run_contract=_run_contract(state), plan_revision="r0", task_map_generation="g0", produced=old_ports, required_ports=list(bodies))
    old_ref = write_plan_artifact_package(state, old)
    writer.append(ZfEvent(type="plan.approval.requested", actor="fixture", payload={
        "plan_id": "evt-preview-review-old", "workflow_run_id": "preview-review", "trace_id": "preview-review", "pdd_id": "PDD-REVIEW", "task_count": 2,
        "task_map_ref": ports[0]["ref"], "plan_artifact_ref": "artifacts/full-plan.md",
        "plan_artifact_package_ref": old_ref["ref"], "plan_artifact_package_digest": old_ref["sha256"],
    }))
