"""Reader decisions must not inherit historical recovery replacement ids."""

from pathlib import Path
import subprocess

import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from tests.test_module_gap_plan_runtime import _state, _write_base_task_map


@pytest.mark.parametrize("replacement", ["current", "additive", "legacy", "unknown"])
def test_discovery_replacement_authority_through_runtime(
    tmp_path: Path, replacement: str,
) -> None:
    state, log, _, runtime = _state(tmp_path)
    map_ref = _write_base_task_map(state)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    gap = {
        "task_id": "WEB-NEW", "owner_role": "dev", "affinity_tag": "web-tui",
        "claim_paths": ["web/src/**", "tests/e2e/**"],
        "acceptance": ["missing workflow works"], "verify_commands": ["npm run test:e2e"],
        "base_commit": commit, "source_refs": [f"git:{commit}"],
    }
    if replacement != "legacy":
        gap["supersedes_task_ids"] = {
            "current": ["CANGJIE-WEB-001"], "additive": [], "unknown": ["NOT-IN-MAP"],
        }[replacement]
    envelope = ["CANGJIE-WEB-001"] if replacement == "legacy" else ["HISTORICAL-WEB"]
    runtime.run_once(events=[ZfEvent(type="flow.discovery.completed", actor="reader", payload={
        "pdd_id": "CANGJIE", "feature_id": "CANGJIE", "flow_kind": "prd",
        "task_map_ref": map_ref, "candidate_head_commit": commit,
        "supersedes_task_ids": envelope, "gap_tasks": [gap],
    })])
    events = log.read_all()
    ready = next(e for e in events if e.type == "flow.gap_plan.ready")
    expected = gap.get("supersedes_task_ids", envelope)
    assert ready.payload["supersedes_task_ids"] == expected
    failures = [e for e in events if e.type == "task_map.amend.failed"]
    if replacement == "unknown":
        assert failures and "NOT-IN-MAP" in failures[-1].payload["reason"]
        assert TaskStore(state / "kanban.json").get("WEB-NEW") is None
    else:
        assert not failures
        assert any(e.type == "task_map.ready" for e in events)
        assert TaskStore(state / "kanban.json").get("WEB-NEW") is not None
