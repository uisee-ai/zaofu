from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.task.contract_validation import validate_task_contract
from zf.core.task.store import TaskStore
from zf.runtime.module_gap_plan import write_gap_task_map_amend_artifact
from zf.runtime.product_delivery import ingest_task_map_to_kanban
from zf.runtime.task_map import validate_task_map_payload


def _task(task_id: str, owner: str, path: str) -> dict:
    return {
        "task_id": task_id,
        "title": task_id,
        "owner_role": owner,
        "wave": 1,
        "scope": [path],
        "allowed_paths": [path],
        "allowed_paths_reason": "Own this product slice.",
        "source_refs": ["docs/requirements.md"],
        "acceptance": ["The declared product behavior works."],
        "verification": {"command": "python -m unittest", "tier": "task_non_smoke"},
    }


def _amend(tmp_path: Path, owner_fields: dict) -> tuple[dict, dict, Path]:
    state = tmp_path / ".state"
    state.mkdir()
    producer = _task("PRD-PRODUCER", "dev-lane-1", "src/producer.py")
    producer["root_owner_class"] = "bundle"
    final = _task("PRD-FINAL", "dev-lane-0", "src/app.py")
    final.update(root_owner_class="assembly", blocked_by=[producer["task_id"]], wave=2)
    base = {"schema_version": "task-map.v1", "feature_id": "PRD-1", "tasks": [producer, final]}
    base_path = state / "artifacts" / "base.json"
    base_path.parent.mkdir()
    base_path.write_text(json.dumps(base), encoding="utf-8")
    gap = deepcopy(final)
    gap.update(
        task_id="PRD-FINAL-V2", parent_task_id="PRD-FINAL",
        supersedes_task_ids=["PRD-FINAL"], goal_kind="prd",
        gap_category="product_completeness",
        owner_role="dev", base_commit="b" * 40,
        verify_commands=["python -m unittest"], **owner_fields,
    )
    # Exercise the real artifact caller, including its full-map schema gate.
    result = write_gap_task_map_amend_artifact(
        state_dir=state, project_root=tmp_path,
        base_task_map_ref="artifacts/base.json", pdd_id="PRD-1",
        source_event_id="offline-gap", gap_tasks=[gap],
        supersedes_task_ids=["PRD-FINAL"], successor_base_commit="b" * 40,
    )
    amended = json.loads(Path(result["task_map_path"]).read_text(encoding="utf-8"))
    assert amended["tasks"][0] == producer
    replacement = amended["tasks"][1]
    assert replacement["blocked_by"] == final["blocked_by"]
    assert replacement["acceptance"] == final["acceptance"]
    assert replacement["verification"] == final["verification"]
    assert replacement["root_owner_class"] == "assembly"
    assert amended["amend"]["superseded_task_ids"] == ["PRD-FINAL"]
    assert validate_task_map_payload(amended).passed
    return amended, replacement, state


def _materialize(tmp_path: Path, amended: dict, state: Path):
    result = ingest_task_map_to_kanban(
        state, amended, task_map_ref="artifacts/amended.json",
    )
    assert result.passed, result.errors
    task = TaskStore(state / "kanban.json").get("PRD-FINAL-V2")
    assert task is not None
    assert task.contract.evidence_contract["source"] == "product_delivery_task_map"
    config = ZfConfig(
        project=ProjectConfig(name="gap-owner", state_dir=str(state)),
        roles=[RoleConfig(name=f"dev-lane-{index}", instance_id=f"dev-lane-{index}",
                          backend="mock", role_kind="writer") for index in (0, 1)],
    )
    errors = validate_task_contract(task, config=config, project_root=tmp_path)
    return task, errors


@pytest.mark.parametrize("fields", [
    {"owner_instance": "dev-lane-1"},
    {"preferred_impl_role": "dev-lane-1"},
    {"owner_instance": "dev-lane-1", "preferred_impl_role": "dev-lane-0"},
])
def test_gap_owner_binding_survives_prd_materialization(tmp_path: Path, fields: dict) -> None:
    amended, row, state = _amend(tmp_path, fields)
    task, errors = _materialize(tmp_path, amended, state)
    assert errors == []
    assert task.contract.owner_role == ""
    assert task.contract.owner_instance == "dev-lane-1"
    assert task.assigned_to == "dev-lane-1"
    assert {field: row[field] for field in fields} == fields


def test_gap_does_not_invent_missing_owner_instance(tmp_path: Path) -> None:
    amended, row, state = _amend(tmp_path, {})
    assert "owner_instance" not in row and "preferred_impl_role" not in row
    task, errors = _materialize(tmp_path, amended, state)
    assert task.contract.owner_instance == ""
    assert any("owner_role 'dev' does not match" in error for error in errors)


@pytest.mark.parametrize("field", ["owner_instance", "preferred_impl_role"])
def test_gap_unknown_instance_stays_rejected(tmp_path: Path, field: str) -> None:
    amended, _, state = _amend(tmp_path, {field: "dev-lane-99"})
    task, errors = _materialize(tmp_path, amended, state)
    assert task.contract.owner_instance == "dev-lane-99"
    assert any("owner_instance 'dev-lane-99' does not match" in error for error in errors)


def test_gap_registered_instance_does_not_authorize_arbitrary_owner(tmp_path: Path) -> None:
    amended, _, state = _amend(tmp_path, {"owner_instance": "dev-lane-1"})
    amended["tasks"][1]["owner_role"] = "dev-assembly"
    _, errors = _materialize(tmp_path, amended, state)
    assert any("owner_role 'dev-assembly' does not match" in error for error in errors)
