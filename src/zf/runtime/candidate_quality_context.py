"""Execution provenance for assembly checks; never independent Verify evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_contract_authority import task_execution_binding
from zf.runtime.task_contract_snapshot import (
    TaskContractSnapshotError, effective_contract_revision, task_map_generation,
)
from zf.runtime.verification_commands import (
    command_digest, task_contract_verification_commands,
)


def candidate_quality_context(
    *, state_dir: Path, task_store: Any, tasks: Iterable[Any], gate: str,
    command: str, pdd_id: str, branch: str, base_commit: str, commit: str,
    source_event_id: str = "",
) -> dict[str, Any]:
    """Persist the exact invocation outside the candidate checkout."""
    identity: dict[str, str] = {}
    for candidate_task in tasks:
        task = task_store.get(candidate_task.task_id)
        if task is None:
            continue
        for record in task_contract_verification_commands(task.contract):
            if gate != f"task_contract:{task.id}:{record['id']}":
                continue
            if command != record["command"]:
                raise ValueError("candidate quality command changed after selection")
            try:
                generation = task_map_generation(task)
            except TaskContractSnapshotError:
                generation = ""  # Legacy contracts do not invent a generation.
            identity = {
                "task_id": task.id,
                "command_id": record["id"],
                "workflow_run_id": task_execution_binding(task).workflow_run_id,
                "contract_revision": effective_contract_revision(task),
                "task_map_generation": generation,
            }
            break
        if identity:
            break
    if gate.startswith("task_contract:") and not identity:
        raise ValueError("candidate quality command no longer exists in TaskStore")
    attempt_id = "candidate-check-" + uuid4().hex
    return write_sidecar_json(
        state_dir, f"artifacts/candidate-quality/{attempt_id}.json",
        {
            "schema_version": "candidate-quality-context.v1",
            "evidence_kind": "candidate_integration",
            "independent_verification": False,
            "role_instance": "candidate-integrator",
            "attempt_id": attempt_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pdd_id": pdd_id, "candidate_ref": f"refs/heads/{branch}",
            "base_commit": base_commit, "target_commit": commit,
            "gate": gate, "command": command,
            "command_digest": command_digest(command),
            **identity,
        },
        kind="candidate_quality_context", schema_version="candidate-quality-context.v1",
        created_by="candidate-integrator", source_event_id=source_event_id,
        required=True,
    )
