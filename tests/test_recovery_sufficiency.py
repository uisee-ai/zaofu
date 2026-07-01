from __future__ import annotations

import hashlib
import json
from pathlib import Path

from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.recovery_sufficiency import (
    build_artifact_recovery_refs,
    evaluate_recovery_packet,
    rehydrate_recovery_context,
)


def _state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    return state_dir


def test_sufficiency_checker_accepts_complete_packet() -> None:
    result = evaluate_recovery_packet({
        "task_id": "TASK-1",
        "current_state": "in_progress",
        "next_required_action": "continue implementation",
        "missing_artifact_refs": [],
        "artifact_hash_status": [],
    })

    assert result.sufficient is True
    assert result.status == "sufficient"


def test_sufficiency_checker_reports_missing_refs_and_hash_mismatch() -> None:
    result = evaluate_recovery_packet({
        "task_id": "TASK-1",
        "current_state": "in_progress",
        "next_required_action": "continue implementation",
        "missing_artifact_refs": ["plan_ref"],
        "artifact_hash_status": [
            {"path": "docs/plans/task.md", "status": "mismatch"},
        ],
    })

    assert result.sufficient is False
    assert result.status == "unrecoverable"
    assert result.missing_refs == ["plan_ref"]
    assert result.hash_failures[0]["status"] == "mismatch"


def test_artifact_recovery_refs_verify_hash_and_missing_required_ref(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    artifact = tmp_path / "docs" / "plans" / "task.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("plan\n", encoding="utf-8")
    sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    task = Task(
        id="TASK-1",
        title="x",
        status="in_progress",
        contract=TaskContract(plan_ref="docs/plans/task.md"),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    (state_dir / "refs").mkdir()
    (state_dir / "refs" / "task-index.json").write_text(json.dumps({
        task.id: {
            "task_id": task.id,
            "contract_refs": {"plan_ref": "docs/plans/task.md"},
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task.md",
                    "sha256": sha,
                    "summary": "plan",
                },
            ],
        },
    }))

    refs = build_artifact_recovery_refs(
        state_dir,
        task,
        project_root=tmp_path,
        required_contract_refs=["plan_ref", "tdd_ref"],
    )

    assert refs["hash_status"][0]["status"] == "ok"
    assert refs["missing_required_refs"] == ["tdd_ref"]


def test_verify_artifact_ref_resolves_in_worker_worktree(tmp_path: Path) -> None:
    # Cross-worktree handoff: an accepted artifact produced by a worker lives only
    # in that worker's worktree (state_dir/workdirs/<instance>/project/<path>), and
    # the ref carries an empty workdir_path. The dispatch-time preflight verifier
    # resolves from the main project_root/state_dir and must still find it, else
    # task.contract.invalid (artifact_file_missing) stalls the next handoff.
    # Regression for the cj-mono / calc full-flow dev-dispatch stall.
    from zf.runtime.recovery_sufficiency import verify_artifact_ref

    state_dir = _state(tmp_path)
    artifact = (
        state_dir / "workdirs" / "critic" / "project" / "artifacts" / "review.md"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_text("review body\n", encoding="utf-8")
    sha = hashlib.sha256(artifact.read_bytes()).hexdigest()

    ref = {
        "artifact_id": "critic-review-v1",
        "kind": "critic-review",
        "path": "artifacts/review.md",  # relative; lives only in the worktree
        "workdir_path": "",  # empty — the failing case
        "sha256": sha,
        "status": "accepted",
    }
    result = verify_artifact_ref(ref, project_root=tmp_path, state_dir=state_dir)

    assert result["status"] == "ok", result
    assert result["actual_sha256"] == sha


def test_verify_artifact_ref_worktree_fallback_rejects_parent_escape(
    tmp_path: Path,
) -> None:
    # The worktree fallback must not let a ref with ".." escape the workdirs tree.
    from zf.runtime.recovery_sufficiency import verify_artifact_ref

    state_dir = _state(tmp_path)
    secret = tmp_path / "secret.md"
    secret.write_text("top secret\n", encoding="utf-8")
    sha = hashlib.sha256(secret.read_bytes()).hexdigest()
    (state_dir / "workdirs" / "dev-1" / "project").mkdir(parents=True)

    ref = {
        "artifact_id": "evil-v1",
        "path": "../../../../secret.md",
        "workdir_path": "",
        "sha256": sha,
        "status": "accepted",
    }
    result = verify_artifact_ref(ref, project_root=tmp_path, state_dir=state_dir)

    assert result["status"] == "missing"


def test_rehydrate_keeps_contract_insufficient_when_required_ref_missing(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    task = Task(
        id="TASK-1",
        title="x",
        status="in_progress",
        contract=TaskContract(owner_role="dev"),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    packet = {
        "task_id": task.id,
        "current_state": "in_progress",
        "next_required_action": "continue implementation",
        "sufficiency_requirements": {
            "required_contract_refs": ["plan_ref"],
        },
        "missing_artifact_refs": ["plan_ref"],
    }

    result = rehydrate_recovery_context(state_dir, packet, project_root=tmp_path)

    assert result.sufficient is False
    assert result.status == "insufficient"
    assert "plan_ref" in result.missing_refs
    assert any(layer["layer"] == "L0" for layer in result.layers)
