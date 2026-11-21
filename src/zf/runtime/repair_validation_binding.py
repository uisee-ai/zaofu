"""Bind independent repair validation to the frozen Git candidate bytes."""
from pathlib import Path
from typing import Any

from zf.runtime.repair_dispatch import RepairContractError
from zf.runtime.source_repair_apply import _git, _git_text, _hard_denied, _matches_any


def validation_matches_candidate(action: dict[str, Any]) -> bool:
    result = action.get("validation_result") or {}
    tested = result.get("tested_candidate") or {}
    return bool(tested.get("tree")
                and tested.get("source_commit") == (action.get("repair_commit") or action.get("source_commit"))
                and tested.get("base_commit") == action.get("base_commit")
                and tested.get("source_root") == action.get("worktree_path")
                and tested.get("changed_files") == sorted(action.get("changed_files") or []))


def repair_validation_identity(action: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    from zf.runtime.self_repair_runner import harness_root

    root = Path(str(action.get("worktree_path") or "")).resolve()
    base = str(contract.get("base_commit") or "")
    commit = str(action.get("source_commit") or action.get("repair_commit") or "")

    def reject(reason: str) -> None:
        raise RepairContractError(reason, "repair validation checkout does not match its frozen candidate")

    if not root.is_dir() or not base or not commit:
        reject("repair_validation_identity_missing")
    common = ("rev-parse", "--path-format=absolute", "--git-common-dir")
    if (_git_text(root, "rev-parse", "--show-toplevel") != str(root)
            or not _git_text(root, *common)
            or _git_text(root, *common) != _git_text(harness_root(), *common)
            or root == harness_root().resolve()):
        reject("repair_validation_repository_mismatch")
    if _git_text(root, "rev-parse", "HEAD") != commit:
        reject("repair_validation_head_drift")
    status = _git(root, "status", "--porcelain", "--untracked-files=all")
    if not status["ok"] or status["stdout"].strip():
        reject("repair_validation_checkout_dirty")
    if (not _git(root, "merge-base", "--is-ancestor", base, commit)["ok"]
            or _git_text(root, "rev-list", "--count", f"{base}..{commit}") != "1"):
        reject("repair_validation_base_mismatch")
    result = _git(root, "diff", "--name-only", "-z", base, commit, "--")
    names = sorted(filter(None, result["stdout"].split("\0")))
    declared = sorted(action.get("changed_files") or [])
    if not result["ok"] or not names or names != declared:
        reject("repair_validation_changed_files_mismatch")
    scope = [*(contract.get("scope") or []), *(contract.get("governance_scope") or [])]
    if any(_hard_denied(name) or not _matches_any(name, scope) for name in names):
        reject("repair_validation_scope_drift")
    return {"source_root": str(root), "source_commit": commit, "base_commit": base,
            "tree": _git_text(root, "rev-parse", "HEAD^{tree}"), "changed_files": names}


def validate_bound_repair(action: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    from zf.runtime.run_manager_repair_validation import execute_repair_verification_plan

    result: dict[str, Any] = {"schema_version": "run-manager.repair-validation-result.v1", "results": []}
    try:
        before = repair_validation_identity(action, contract)
        result = execute_repair_verification_plan(
            worktree=before["source_root"], verification_plan=list(action.get("verification_plan") or []),
        )
        result["tested_candidate"] = before
        after = repair_validation_identity(action, contract)
        if after != before:
            raise RepairContractError("repair_validation_candidate_changed", "candidate changed during validation")
    except RepairContractError as exc:
        result.update(ok=False, status="failed", reason=exc.code)
    return result
