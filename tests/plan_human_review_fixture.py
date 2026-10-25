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
        {"decision_id": "Q1", "status": "pending", "question": "确认库存来源", "named_owner": "待指定", "affected_task_ids": ["T-STOCK"], "enter_before": "写入前", "done_when": "确认唯一真源"}]}
    execution = {"schema_version": "task-scoped-plan-input.v1", "shared": {"human_review": {
        "overview_markdown": "## 产品目标\n库存修正可追溯；统计扩展需另行确认。",
        "journeys_markdown": "查看库存 → 修正消耗 → 重试核对；失败时保持原状态。",
    }}, "tasks": [{"task_id": "T-STOCK", "body": {"human_review": {
        "module_spec_markdown": "### 使用者与字段\n授权操作员修改目标消耗，目标必须非负；失败不改变库存。",
        "scenarios": [{"id": "SC-STOCK", "title": "差额修正和重试", "given": "库存10、原消耗3", "when": "目标改为5并重试", "then": "库存8，重试后仍为8", "acceptance_ids": ["AC-STOCK"], "source_refs": ["docs/prd.md#stock"], "command_ids": ["VC-STOCK"]}],
    }}}, {"task_id": "T-LEGACY", "body": {}}]}
    bodies = {"task_map": task_map, "execution_spec": execution, "source_inventory": {
        "pages": [{"id": "A01", "title": "库存", "task_id": "T-STOCK"}, {"id": "A02", "title": "查询", "task_id": "T-LEGACY"}],
        "forms": [{"id": "F01", "title": "修正", "task_id": "T-STOCK"}],
    }}
    ports = []
    for name, body in bodies.items():
        descriptor = write_immutable_json_sidecar(state, body, root=f"fixtures/{name}", kind=name, schema_version=f"{name}.v1", created_by="test")
        ports.append({"logical_name": name, "artifact_kind": name, "schema_version": f"{name}.v1", "producer_stage_id": "prd-plan", "ref": descriptor["ref"], "sha256": descriptor["sha256"]})
    package = build_plan_artifact_package(workflow_run_id="preview-review", flow_kind="prd", producer_stage_id="prd-plan", run_contract=_run_contract(state), plan_revision="r1", task_map_generation="g1", produced=ports, required_ports=list(bodies))
    descriptor = write_plan_artifact_package(state, package)
    writer.append(ZfEvent(type="plan.approval.requested", actor="fixture", payload={
        "plan_id": "evt-preview-review", "workflow_run_id": "preview-review", "task_count": 2,
        "task_map_ref": ports[0]["ref"], "plan_artifact_ref": "artifacts/full-plan.md",
        "plan_artifact_package_ref": descriptor["ref"], "plan_artifact_package_digest": descriptor["sha256"],
    }))
