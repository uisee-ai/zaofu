"""Read the review choice from the Plan's immutable run contract, never live config."""
from pathlib import Path
from typing import Any, Mapping

from zf.core.config.plan_review import validate_plan_review
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.run_contract import load_run_contract_snapshot


def plan_review_policy_view(state_dir: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    unknown = {"mode": "unknown", "label": "独立评审方式未记录", "reason": "", "focus": []}
    ref, digest = payload.get("plan_artifact_package_ref"), payload.get("plan_artifact_package_digest")
    if not ref or not digest:
        return unknown
    try:
        package = hydrate_plan_artifact_package(state_dir, {"ref": ref, "sha256": digest}, validate_ports=False)
        run_id = payload.get("workflow_run_id")
        if run_id and package["workflow_run_id"] != run_id:
            raise ValueError("Plan review policy belongs to another run")
        snapshot = load_run_contract_snapshot(state_dir, {
            "ref": package["run_contract_ref"], "sha256": package["run_contract_sha256"]})
        workflow = snapshot["contract"].get("workflow", {})
        if not isinstance(workflow, dict):
            raise ValueError("Plan run contract workflow is not an object")
        policy = workflow.get("plan_review")
        if not policy:
            return unknown
        policy = validate_plan_review(policy)
        labels = {"none": "未安排独立 Critic 评审", "targeted": "定向评审 · 不代表全文通过",
                  "full": "完整评审 · 配置不代表已通过"}
        return {**policy, "label": labels[policy["mode"]], "run_contract_ref": package["run_contract_ref"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {**unknown, "error": str(exc)}
