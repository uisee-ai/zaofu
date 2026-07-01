from __future__ import annotations

import json
from pathlib import Path

from zf.core.config.schema import RoleConfig
from zf.core.task.schema import Task, TaskContract
from zf.runtime.runtime_snapshot import (
    RuntimeSnapshotInput,
    build_runtime_snapshot,
    write_runtime_snapshot,
)


def test_runtime_snapshot_writes_projection_without_truth_mutation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    task = Task(
        id="TASK-1",
        title="x",
        status="in_progress",
        assigned_to="dev-1",
        active_dispatch_id="disp-1",
        contract=TaskContract(
            source_revision="source-r1",
            contract_revision="contract-r1",
            capsule_revision="capsule-r1",
            verification_tiers=["static"],
        ),
    )
    role = RoleConfig(
        name="dev",
        instance_id="dev-1",
        backend="claude-code",
        publishes=["dev.build.done"],
    )

    snapshot = build_runtime_snapshot(RuntimeSnapshotInput(
        state_dir=state_dir,
        project_root=tmp_path,
        project_id="proj",
        source="dispatch",
        task=task,
        role=role,
        dispatch_id="disp-1",
        run_id="run-1",
        refs={
            "task_doc_ref": state_dir / "task_docs" / "TASK-1" / "task.md",
            "secret_ref": "OPENAI_API_KEY=sk-thisshouldberedacted1234567890",
        },
    ))
    result = write_runtime_snapshot(
        snapshot,
        state_dir=state_dir,
        project_root=tmp_path,
    )

    assert result.snapshot_ref == ".zf/snapshots/TASK-1/disp-1/runtime-snapshot.json"
    assert result.json_path.exists()
    assert result.md_path.exists()
    data = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == "runtime-snapshot.v1"
    assert data["task"]["task_id"] == "TASK-1"
    assert data["task"]["capsule_revision"] == "capsule-r1"
    assert data["output_contract"]["expected_event"] == "dev.build.done"
    assert "sk-thisshouldberedacted" not in result.json_path.read_text(encoding="utf-8")
    assert (state_dir / "events.jsonl").read_text(encoding="utf-8") == ""
    assert (state_dir / "kanban.json").read_text(encoding="utf-8") == "[]\n"
