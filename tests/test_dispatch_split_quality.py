from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    RoleConfig,
    WorkflowConfig,
    WorkflowSplitQualityConfig,
    WorkflowWorkUnitsConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator_dispatch import DispatchMixin


class _Harness(DispatchMixin):
    def __init__(self, state_dir: Path, config: ZfConfig) -> None:
        self.state_dir = state_dir
        self.config = config
        self.event_log = EventLog(state_dir / "events.jsonl")
        self.task_store = TaskStore(state_dir / "kanban.json")


def _config() -> ZfConfig:
    return ZfConfig(
        workflow=WorkflowConfig(
            work_units=WorkflowWorkUnitsConfig(
                enabled=True,
                split_quality=WorkflowSplitQualityConfig(mode="blocking"),
            ),
        ),
    )


def _task() -> Task:
    return Task(
        id="TASK-1",
        title="missing split quality fields",
        status="backlog",
        contract=TaskContract(
            behavior="Implement the feature",
            owner_role="dev",
        ),
    )


def test_split_quality_does_not_block_reader_dispatch(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    harness = _Harness(state_dir, _config())
    task = harness.task_store.add(_task())

    blocked = harness._split_quality_blocks_dispatch(
        task,
        RoleConfig(name="review-lane-0", role_kind="reader"),
    )

    assert blocked is False
    assert harness.task_store.get("TASK-1").status == "backlog"
    assert not [
        event for event in harness.event_log.read_all()
        if event.type == "task.split_quality.blocked"
    ]


def test_split_quality_still_blocks_writer_dispatch(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    harness = _Harness(state_dir, _config())
    task = harness.task_store.add(_task())

    blocked = harness._split_quality_blocks_dispatch(
        task,
        RoleConfig(name="dev-lane-0", role_kind="writer"),
    )

    assert blocked is True
    assert harness.task_store.get("TASK-1").status == "blocked"
    events = [
        event for event in harness.event_log.read_all()
        if event.type == "task.split_quality.blocked"
    ]
    assert len(events) == 1


def test_split_quality_blocks_too_many_acceptance_criteria(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        workflow=WorkflowConfig(
            work_units=WorkflowWorkUnitsConfig(
                enabled=True,
                split_quality=WorkflowSplitQualityConfig(
                    mode="blocking",
                    max_acceptance_criteria=2,
                ),
            ),
        ),
    )
    harness = _Harness(state_dir, config)
    task = harness.task_store.add(Task(
        id="TASK-AC",
        title="oversized acceptance",
        status="backlog",
        contract=TaskContract(
            behavior="Implement the feature",
            verification="true",
            verification_tiers=["runtime"],
            owner_role="dev",
            scope=["src/a.py"],
            acceptance_criteria=["one", "two", "three"],
        ),
    ))

    blocked = harness._split_quality_blocks_dispatch(
        task,
        RoleConfig(name="dev-lane-0", role_kind="writer"),
    )

    assert blocked is True
    event = harness.event_log.read_all()[-1]
    assert event.type == "task.split_quality.blocked"
    assert event.payload["findings"][0]["kind"] == "acceptance_too_large"


def test_split_quality_allows_refactor_task_map_module_scope_with_surface(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        workflow=WorkflowConfig(
            work_units=WorkflowWorkUnitsConfig(
                enabled=True,
                split_quality=WorkflowSplitQualityConfig(
                    mode="blocking",
                    max_scope_files=1,
                ),
            ),
        ),
    )
    harness = _Harness(state_dir, config)
    task = harness.task_store.add(Task(
        id="TASK-REF",
        title="refactor slice",
        status="backlog",
        contract=TaskContract(
            behavior="Rebuild the module",
            verification="npm test",
            verification_tiers=["runtime"],
            owner_role="dev",
            scope=["src/a.ts", "src/b.ts"],
            acceptance_criteria=["module parity is preserved"],
            evidence_contract={
                "source": "refactor_task_map",
                "source_refs": {"task_map_ref": ".zf/artifacts/F/task_map.json"},
            },
        ),
    ))

    blocked = harness._split_quality_blocks_dispatch(
        task,
        RoleConfig(name="dev-lane-0", role_kind="writer"),
    )

    assert blocked is False
    assert harness.task_store.get("TASK-REF").status == "backlog"
