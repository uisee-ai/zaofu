from unittest.mock import patch

import pytest

from tests.test_run_manager import _state
from tests.test_source_repair_apply import _git, _repair_repositories
from zf.runtime.run_manager import _execute_repair_closeout_validate
from zf.runtime.repair_dispatch import RepairRequest, write_repair_contract


@pytest.mark.parametrize("mutation", ["none", "head", "tracked", "untracked", "index", "declared", "during"])
def test_closeout_validation_binds_actual_candidate_before_and_after(tmp_path, mutation):
    root, worktree, base, commit = _repair_repositories(tmp_path)
    state, log, writer = _state(tmp_path)
    req = RepairRequest(fingerprint="repair", attempt=1, candidate_id="candidate", candidate_path="", repair_task_payload={
        "contract": {"scope": ["src/zf/**"], "verification_commands": ["git diff --check"]},
    })
    contract, ref = write_repair_contract(state, req, base_commit=base, created_by="test")
    action = {"action": "repair-closeout-validate", "checkpoint_id": "validation", "fingerprint": "repair",
        "attempt": 1, "candidate_id": "candidate", "base_commit": base, "source_commit": commit,
        "worktree_path": str(worktree), "changed_files": ["src/zf/fix.py"],
        "repair_contract_required": True, "repair_contract_ref": ref, "repair_contract_digest": ref["sha256"],
        "verification_plan": [{"command": command} for command in contract["verification_commands"]] + [
            {"command": f"python scripts/dev-verify.py {mode} --base {base}"} for mode in ["plan", "run"]],
    }
    if mutation in {"tracked", "index", "head"}:
        (worktree / "src/zf/fix.py").write_text("drift = True\n")
        if mutation in {"index", "head"}: _git(worktree, "add", "src/zf/fix.py")
        if mutation == "head": _git(worktree, "commit", "-qm", "fix: another candidate")
    if mutation == "untracked": (worktree / "injected.py").write_text("changed = True\n")
    if mutation == "declared": action["changed_files"] = ["src/zf/other.py"]

    def execute(**kwargs):
        if mutation == "during": (worktree / "src/zf/fix.py").write_text("changed = True\n")
        return {"ok": True, "status": "passed", "results": [{"status": "passed"}]}

    with patch("zf.runtime.self_repair_runner.harness_root", return_value=root), patch(
        "zf.runtime.run_manager_repair_validation.execute_repair_verification_plan", side_effect=execute,
    ) as commands:
        status = _execute_repair_closeout_validate(state_dir=state, writer=writer, action=action, causation_id="closeout")
    assert status == ("applied" if mutation == "none" else "failed")
    assert commands.call_count == (1 if mutation in {"none", "during"} else 0)
    result = next(e.payload["validation_result"] for e in log.read_all()
                  if e.type in {"run.manager.action.applied", "run.manager.action.failed"})
    if mutation == "none":
        assert result["tested_candidate"]["source_commit"] == commit
        assert result["tested_candidate"]["tree"] == _git(worktree, "rev-parse", "HEAD^{tree}")
