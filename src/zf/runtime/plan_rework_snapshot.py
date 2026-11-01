"""Keep scoped replan inputs bounded without dropping canonical task contracts."""

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.call_result_envelope import write_immutable_json_sidecar


_CONTRACT_IDENTITY = (
    "feature_id", "parent_task_id", "source_task_id", "source_ref",
    "source_revision", "contract_revision", "product_contract_ref",
)


def bind_scoped_snapshot(
    state_dir: Path, snapshot: dict[str, Any], *, context: Mapping[str, Any],
    source_event_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected = {
        str(task_id)
        for key in ("failed_task_ids", "task_ids", "downstream_task_ids")
        for task_id in context.get(key, [])
    }
    if not selected:
        # An unscoped replan still requires the complete current snapshot.
        return snapshot, {}
    descriptor = write_immutable_json_sidecar(
        state_dir, snapshot, root="plan-synth/task-snapshots",
        kind="canonical_task_snapshot", schema_version="canonical-task-snapshot.v1",
        created_by="plan-synth-handoff", source_event_id=source_event_id,
    )
    source = {
        **descriptor, "source_id": "canonical-task-snapshot",
        "artifact_id": "canonical-task-snapshot.json", "allowed_paths": ["$"],
    }
    tasks = snapshot["tasks"]
    index = []
    for position, task in enumerate(tasks):
        contract = task["contract"]
        index.append({
            **{key: value for key, value in task.items() if key != "contract" and value not in ("", {}, [])},
            "contract": {key: contract[key] for key in _CONTRACT_IDENTITY if contract.get(key)},
            "full_task_json_path": f"$.tasks[{position}]",
        })
    return {
        **snapshot,
        "schema_version": "canonical-task-index.v1",
        "tasks": index,
        "full_snapshot_ref": source,
        "scoped_tasks": [task for task in tasks if task["task_id"] in selected],
        "unresolved_task_ids": sorted(selected - {task["task_id"] for task in tasks}),
        "read_scope": (
            "The index covers every current task; scoped_tasks contains complete explicitly "
            "affected tasks. Read full_snapshot_ref through canonical-task-snapshot at "
            "full_task_json_path before changing another task's contract or resolving an "
            "unknown task id. Unchanged contracts remain authoritative; this index is not "
            "a replacement task map or evidence of a passed result."
        ),
    }, source
