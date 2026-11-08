"""Adoption converges only evidenced historical replacement edges."""

import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from tests.test_module_gap_plan_runtime import _state, _write_base_task_map


@pytest.mark.parametrize("case", ["valid", "cancelled", "bad_digest", "other_run", "no_event", "active", "unlinked", "no_successor"])
def test_runtime_adopts_historical_supersession(tmp_path: Path, case: str) -> None:
    state, log, _, runtime = _state(tmp_path)
    ref = _write_base_task_map(state)
    path = state / "artifacts/CANGJIE/task_map.json"
    base = json.loads(path.read_text())
    old_id = base["tasks"][0]["task_id"]
    base["tasks"][0].update(task_id="WEB-MID", supersedes_task_ids=[old_id])
    if case == "no_successor":
        base["tasks"][0]["task_id"] = "UNRESOLVED-MID"
    history = state / "artifacts/CANGJIE/history.json"
    history.write_text(json.dumps(base))
    digest = hashlib.sha256(history.read_bytes()).hexdigest()
    base["tasks"][0]["task_id"] = "WEB-MID"
    base["amend"] = {"supersedes_task_map_ref": ".zf/artifacts/CANGJIE/history.json"}
    base["tasks"][0].pop("supersedes_task_ids")
    base["tasks"].append({
        "task_id": "ASSEMBLY", "owner_role": "dev", "wave": 1,
        "blocked_by": ["WEB-MID"], "allowed_paths": ["app/**"],
        "allowed_paths_reason": "assembly", "acceptance": ["consume web"],
    })
    path.write_text(json.dumps(base))
    if case != "no_event":
        log.append(ZfEvent(type="task_map.amended", origin="kernel", payload={
            "workflow_run_id": "another-run" if case == "other_run" else "run",
            "task_map_ref": ".zf/artifacts/CANGJIE/history.json",
            "task_map_digest": "0" * 64 if case == "bad_digest" else digest,
            "superseded_task_ids": [old_id],
        }))
    if case == "unlinked":
        base.pop("amend")
        path.write_text(json.dumps(base))
    store = TaskStore(state / "kanban.json")
    status = {"active": "in_progress", "cancelled": "cancelled"}.get(case, "blocked")
    store.add(Task(id=old_id, title="old", status=status,
                   active_dispatch_id="live" if case == "active" else "",
                   contract=TaskContract(evidence_contract={"workflow_run_id": "run"})))
    store.add(Task(id="ASSEMBLY", title="assembly", blocked_by=[old_id],
                   contract=TaskContract(evidence_contract={"workflow_run_id": "run"})))
    from zf.runtime.task_map_supersession_history import historical_supersession_proofs

    assert historical_supersession_proofs(
        loaded=SimpleNamespace(workflow_run_id="run", task_map_path=path),
        events=log.read_all(), task_store=store, state_dir=state, project_root=tmp_path,
    ) == {}  # A declared successor is insufficient until materialized.
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    runtime.run_once(events=[ZfEvent(type="flow.gap_plan.ready", payload={
        "pdd_id": "CANGJIE", "feature_id": "CANGJIE", "trace_id": "run",
        "workflow_run_id": "run", "task_map_ref": ref,
        "candidate_head_commit": commit,
        "gap_tasks": [{"task_id": "WEB-NEW", "owner_role": "dev",
                       "claim_paths": ["web/src/**"], "acceptance": ["web works"],
                       "verify_commands": ["npm run test:e2e"], "base_commit": commit,
                       "source_refs": [f"git:{commit}"], "supersedes_task_ids": ["WEB-MID"]}],
    })])
    if case in {"valid", "cancelled"}:
        assert store.get(old_id).status == "cancelled"
        assert store.get("ASSEMBLY").blocked_by == ["WEB-NEW"]
        event = next(e for e in log.read_all() if e.type == "task.superseded" and e.task_id == old_id)
        assert event.correlation_id == "run"
        assert event.payload["historical_supersession_event_ids"]
        from zf.runtime.writer_task_map_supersede import apply_explicit_task_supersedes

        adopted_ref = event.payload["superseded_by_task_map_ref"]
        adopted = SimpleNamespace(
            workflow_run_id="run", task_map_ref=adopted_ref,
            task_map_path=state / adopted_ref.removeprefix(".zf/"),
        )
        before = len(log.read_all())
        assert apply_explicit_task_supersedes(
            task_store=store, event_writer=runtime.event_writer, loaded=adopted,
            historical_proofs={old_id: event.payload["historical_supersession_event_ids"]},
        ) == []
        assert len(log.read_all()) == before
    else:
        assert store.get(old_id).status != "cancelled"
        assert store.get("ASSEMBLY").blocked_by == [old_id]
