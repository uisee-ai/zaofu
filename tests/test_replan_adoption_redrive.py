"""doc 84 B — replan adoption re-drive after owner approval.

A (doc84-A) blocks an owner-gated replan at ``replan.adoption.awaiting_owner``
until the owner approves. This sweep is what actuates that approval: it
reloads the candidate from the refs the awaiting event carried and
re-ingests, so adoption completes without a manual manifest re-apply.
"""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.task.store import TaskStore
from zf.runtime.product_delivery import ingest_task_map_to_kanban
from zf.runtime.replan_adoption_redrive import redrive_owner_approved_adoptions
from zf.runtime.replan_contract_eval import evaluate_replan_contract

from tests.test_product_delivery import (  # shared task-map builders
    _replan_task_map_v2,
    _source_index,
    _source_index_v2,
    _state_dir,
    _task_map,
)


def _write(path: Path, payload: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _seed_and_block(tmp_path: Path):
    """Seed v1, write v2 artifacts, attempt owner-gated v2 ingest → awaiting_owner."""
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    ingest_task_map_to_kanban(
        state_dir, _task_map(), source_index=_source_index(),
        task_map_ref="tm-v1", writer=writer,
    )
    tm_v2 = _write(state_dir / "artifacts" / "F-PROD" / "task-map-v2.json", _replan_task_map_v2())
    si_v2 = _write(state_dir / "artifacts" / "F-PROD" / "source-index-v2.json", _source_index_v2())
    eval_result = evaluate_replan_contract(
        old_task_map=_task_map(),
        new_task_map=_replan_task_map_v2(),
        source_index=_source_index_v2(),
        expected_current_task_map_ref="tm-v1",
        old_task_map_ref="tm-v1",
        new_task_map_ref=tm_v2,
        idempotency_key="idem-b",
    ).to_dict()
    eval_result["owner_approval_required"] = True
    result = ingest_task_map_to_kanban(
        state_dir, _replan_task_map_v2(), source_index=_source_index_v2(),
        source_index_ref=si_v2, task_map_ref=tm_v2, replan_eval=eval_result, writer=writer,
    )
    assert result.passed is False
    assert "replan.adoption.awaiting_owner" in [e.type for e in log.read_all()]
    return state_dir, log, writer, tm_v2


def _approve(writer: EventWriter, tm_v2: str) -> None:
    writer.emit(
        "replan.owner_decision.approved",
        actor="owner",
        payload={"decision": "approved", "candidate_task_map_ref": tm_v2, "eval_ref": "idem-b"},
    )


def test_redrive_adopts_after_owner_approves(tmp_path: Path) -> None:
    state_dir, log, writer, tm_v2 = _seed_and_block(tmp_path)
    _approve(writer, tm_v2)

    redriven = redrive_owner_approved_adoptions(state_dir, project_root=tmp_path, writer=writer)

    assert redriven == [tm_v2]
    types = [e.type for e in log.read_all()]
    assert "replan.adoption.completed" in types
    store = TaskStore(state_dir / "kanban.json")
    assert store.get("TASK-PROD-C") is not None
    assert store.get("TASK-PROD-A").status == "cancelled"


def test_redrive_noop_without_owner_approval(tmp_path: Path) -> None:
    state_dir, log, writer, tm_v2 = _seed_and_block(tmp_path)

    redriven = redrive_owner_approved_adoptions(state_dir, project_root=tmp_path, writer=writer)

    assert redriven == []
    assert "replan.adoption.completed" not in [e.type for e in log.read_all()]
    assert TaskStore(state_dir / "kanban.json").get("TASK-PROD-A").status != "cancelled"


def test_redrive_is_idempotent_after_completion(tmp_path: Path) -> None:
    state_dir, log, writer, tm_v2 = _seed_and_block(tmp_path)
    _approve(writer, tm_v2)

    first = redrive_owner_approved_adoptions(state_dir, project_root=tmp_path, writer=writer)
    second = redrive_owner_approved_adoptions(state_dir, project_root=tmp_path, writer=writer)

    assert first == [tm_v2]
    assert second == []
    assert [e.type for e in log.read_all()].count("replan.adoption.completed") == 1
