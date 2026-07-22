from __future__ import annotations

import json
from pathlib import Path

from zf.runtime.rework_task_scope import expand_rework_task_ids


def test_rework_scope_retains_transitive_downstream_dag(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    task_map = state_dir / "artifacts" / "SIM" / "task_map.json"
    task_map.parent.mkdir(parents=True)
    task_map.write_text(json.dumps({
        "tasks": [
            {"task_id": "SIM-CORE", "allowed_paths": ["core.js"]},
            {
                "task_id": "SIM-SCHED",
                "allowed_paths": ["sched.js"],
                "blocked_by": ["SIM-CORE"],
            },
            {
                "task_id": "SIM-BATCH",
                "allowed_paths": ["batch.js"],
                "blocked_by": ["SIM-SCHED"],
            },
            {
                "task_id": "SIM-ASSEMBLY",
                "allowed_paths": ["index.js"],
                "depends_on": ["SIM-BATCH"],
            },
            {"task_id": "SIM-UI", "allowed_paths": ["ui.js"]},
        ],
    }), encoding="utf-8")

    scope = expand_rework_task_ids(
        ["SIM-SCHED"],
        task_map_ref=".zf/artifacts/SIM/task_map.json",
        state_dir=state_dir,
        project_root=tmp_path,
    )

    assert scope == ["SIM-SCHED", "SIM-BATCH", "SIM-ASSEMBLY"]


def test_rework_scope_excludes_completed_downstream(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    task_map = state_dir / "task-map.json"
    state_dir.mkdir()
    task_map.write_text(json.dumps({
        "tasks": [
            {"task_id": "A", "allowed_paths": ["a"]},
            {"task_id": "B", "allowed_paths": ["b"], "blocked_by": ["A"]},
        ],
    }), encoding="utf-8")

    scope = expand_rework_task_ids(
        ["A"],
        task_map_ref=str(task_map),
        state_dir=state_dir,
        project_root=tmp_path,
        completed_task_ids={"B"},
    )

    assert scope == ["A"]
