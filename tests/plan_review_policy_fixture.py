"""Synthetic review policies bound to immutable packages for the real Web API."""
from zf.core.events.model import ZfEvent
from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
from zf.runtime.run_contract import stable_json_sha256, write_run_contract_snapshot


def add_plan_review_policy_fixtures(state, writer):
    for mode in ("none", "targeted", "full"):
        policy = {"mode": mode, "reason": "按需求风险选择评审方式",
                  "focus": ["两个入口的失败状态与验收时点"] if mode == "targeted" else []}
        contract = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd", "plan_review": policy}}
        contract["contract_digest"] = stable_json_sha256(contract)
        snapshot = write_run_contract_snapshot(state, contract)
        package = build_plan_artifact_package(workflow_run_id=f"policy-{mode}", flow_kind="prd",
            producer_stage_id="prd-plan", run_contract=snapshot, plan_revision="r1", task_map_generation="g1", produced=[])
        descriptor = write_plan_artifact_package(state, package)
        writer.append(ZfEvent(type="plan.approval.requested", actor="fixture", payload={
            "plan_id": f"evt-policy-{mode}", "workflow_run_id": f"policy-{mode}",
            "trace_id": f"policy-{mode}", "plan_artifact_ref": "artifacts/full-plan.md",
            "plan_artifact_package_ref": descriptor["ref"], "plan_artifact_package_digest": descriptor["sha256"],
        }))
