"""Real writer workspaces keep a sibling's unverified partial out of the producer."""
import json
from pathlib import Path

from tests.test_writer_fanout_runtime import (
    _child, _commit, _fanout_id, _git, _manifest, _seed_tasks, _start, _state,
)


def test_gap_producer_worktree_uses_candidate_while_successor_retains_partial(tmp_path):
    state, log, _transport, orch = _state(tmp_path)
    _seed_tasks(state)
    original = _git(tmp_path, "rev-parse", "HEAD")
    candidate = _commit(tmp_path, "accepted.txt", "accepted", "accepted")
    partial = _commit(tmp_path, "partial-case.txt", "unverified", "partial")
    _git(tmp_path, "reset", "--hard", candidate)
    path = state / "artifacts/F-11111111/task_map.json"
    data = json.loads(path.read_text())
    data["target_commit"] = original
    data["tasks"][1].update(base_commit=partial, source_refs=["git:" + partial],
                            blocked_by=["TASK-1"])
    path.write_text(json.dumps(data))
    _start(orch, payload={"resume_scope": "gap_tasks_only", "task_ids": ["TASK-1", "TASK-2"],
                          "dispatch_base_commit": candidate})
    manifest = _manifest(state, _fanout_id(log))
    producer = _child(manifest, "TASK-1")
    successor = _child(manifest, "TASK-2")
    assert manifest["target_ref"] == candidate
    assert producer["payload"]["dispatch_base_commit"] == candidate
    assert _git(Path(producer["workdir"]), "rev-parse", "HEAD") == candidate
    assert not (Path(producer["workdir"]) / "partial-case.txt").exists()
    assert successor["payload"]["base_commit"] == partial
    assert not [event for event in log.read_all()
                if event.type == "fanout.child.dispatched" and event.payload.get("task_id") == "TASK-2"]
