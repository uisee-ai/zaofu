from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    ContractDConfig,
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    VerificationConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.cli.events import _autofill_completion_revisions
from zf.runtime.orchestrator import Orchestrator


def test_task_capsule_events_are_known_types() -> None:
    from zf.core.events.known_types import KNOWN_EVENT_TYPES

    assert "task.doc.published" in KNOWN_EVENT_TYPES
    assert "task.dispatch_context.bound" in KNOWN_EVENT_TYPES
    assert "task.completion.stale_rejected" in KNOWN_EVENT_TYPES
    assert "runtime.snapshot.recorded" in KNOWN_EVENT_TYPES
    assert "runtime.snapshot.invalid" in KNOWN_EVENT_TYPES
    assert "dispatch.blocked" in KNOWN_EVENT_TYPES


class _StubTransport:
    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        return None

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""


def _orch(tmp_path: Path) -> tuple[Orchestrator, TaskStore, EventLog]:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    log = EventLog(state_dir / "events.jsonl")
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(tmux_session="zf-test"),
        roles=[
            RoleConfig(
                name="dev",
                backend="mock",
                publishes=["dev.build.done", "dev.blocked"],
            ),
        ],
        verification=VerificationConfig(
            contract=ContractDConfig(dispatch_token_required=True),
        ),
    )
    return Orchestrator(state_dir, config, _StubTransport()), store, log  # type: ignore[arg-type]


def _orch_with_dev_instance(
    tmp_path: Path,
    instance_id: str = "dev-1",
) -> tuple[Orchestrator, TaskStore, EventLog]:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    log = EventLog(state_dir / "events.jsonl")
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(tmux_session="zf-test"),
        roles=[
            RoleConfig(
                name="dev",
                instance_id=instance_id,
                backend="mock",
                publishes=["dev.build.done", "dev.blocked"],
            ),
        ],
        verification=VerificationConfig(
            contract=ContractDConfig(dispatch_token_required=True),
        ),
    )
    return Orchestrator(state_dir, config, _StubTransport()), store, log  # type: ignore[arg-type]


def test_emit_autofills_completion_revisions_for_active_dispatch(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-1",
        title="bound task",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-1",
        contract=TaskContract(
            behavior="build the feature",
            source_revision="source-r1",
            contract_revision="contract-r1",
            capsule_revision="capsule-r1",
        ),
    ))
    payload = {
        "dispatch_id": "disp-1",
        "source_revision": "source-wrong",
    }

    _autofill_completion_revisions(
        payload,
        state_dir=state_dir,
        event_type="dev.build.done",
        task_id="TASK-1",
        dispatch_id="disp-1",
    )

    assert payload["source_revision"] == "source-r1"
    assert payload["contract_revision"] == "contract-r1"
    assert payload["capsule_revision"] == "capsule-r1"
    assert payload["revision_autofill"] == {
        "source": "zf_emit_active_dispatch",
        "dispatch_id": "disp-1",
        "fields": [
            "source_revision",
            "contract_revision",
            "capsule_revision",
        ],
        "original": {
            "source_revision": "source-wrong",
            "contract_revision": "",
            "capsule_revision": "",
        },
    }


def test_completion_missing_capsule_revision_is_rejected(tmp_path: Path) -> None:
    orch, store, log = _orch(tmp_path)
    store.add(Task(
        id="TASK-1",
        title="bound task",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-1",
        contract=TaskContract(
            behavior="build the feature",
            source_revision="source-r1",
            contract_revision="contract-r1",
            capsule_revision="capsule-r1",
        ),
    ))

    decisions = orch.run_once([
        ZfEvent(
            type="dev.build.done",
            actor="dev",
            task_id="TASK-1",
            payload={"dispatch_id": "disp-1"},
        ),
    ])

    assert decisions and decisions[0].action == "block"
    assert store.get("TASK-1").status == "in_progress"
    events = log.read_all()
    assert any(event.type == "task.completion.stale_rejected" for event in events)


def test_stale_completion_routes_same_lane_rework(tmp_path: Path) -> None:
    orch, store, log = _orch_with_dev_instance(tmp_path, instance_id="dev-1")
    store.add(Task(
        id="TASK-1",
        title="bound task",
        status="in_progress",
        assigned_to="dev-1",
        active_dispatch_id="disp-1",
        contract=TaskContract(
            behavior="build the feature",
            source_revision="source-r1",
            contract_revision="contract-r1",
            capsule_revision="capsule-r1",
        ),
    ))

    decisions = orch.run_once([
        ZfEvent(
            type="task.completion.stale_rejected",
            actor="zf-cli",
            task_id="TASK-1",
            payload={
                "reason": "source_revision_mismatch",
                "origin_event": "dev.build.done",
                "origin_event_id": "evt-origin",
                "expected": {
                    "source_revision": "source-r1",
                    "contract_revision": "contract-r1",
                    "capsule_revision": "capsule-r1",
                },
                "actual": {
                    "source_revision": "source-old",
                    "contract_revision": "contract-old",
                    "capsule_revision": "capsule-old",
                },
                "missing_or_mismatched": [
                    "source_revision",
                    "contract_revision",
                    "capsule_revision",
                ],
                "required_actions": [
                    "Re-emit dev.build.done for dispatch_id disp-1 with "
                    "source_revision=source-r1",
                ],
            },
        ),
    ])

    assert decisions and decisions[0].action == "dispatch"
    assert decisions[0].role == "dev"
    reloaded = store.get("TASK-1")
    assert reloaded is not None
    assert reloaded.assigned_to == "dev-1"
    assert reloaded.active_dispatch_id != "disp-1"
    events = log.read_all()
    assert any(
        event.type == "task.rework.requested"
        and event.payload.get("assignee") == "dev-1"
        and event.payload.get("trigger_event_type")
        == "task.completion.stale_rejected"
        for event in events
    )
    assert any(
        event.type == "task.dispatched"
        and event.payload.get("assignee") == "dev-1"
        and event.payload.get("source") == "rework"
        for event in events
    )


def test_completion_with_current_capsule_revisions_passes_revision_gate(
    tmp_path: Path,
) -> None:
    orch, store, _log = _orch(tmp_path)
    store.add(Task(
        id="TASK-1",
        title="bound task",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-1",
        contract=TaskContract(
            behavior="build the feature",
            source_revision="source-r1",
            contract_revision="contract-r1",
            capsule_revision="capsule-r1",
        ),
    ))

    decisions = orch.run_once([
        ZfEvent(
            type="dev.build.done",
            actor="dev",
            task_id="TASK-1",
            payload={
                "dispatch_id": "disp-1",
                "source_revision": "source-r1",
                "contract_revision": "contract-r1",
                "capsule_revision": "capsule-r1",
            },
        ),
    ])

    assert not [decision for decision in decisions if decision.action == "block"]


def test_completion_with_stale_runtime_snapshot_ref_is_rejected(
    tmp_path: Path,
) -> None:
    orch, store, log = _orch(tmp_path)
    task = Task(
        id="TASK-1",
        title="bound task",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-current",
        contract=TaskContract(
            behavior="build the feature",
            source_revision="source-r1",
            contract_revision="contract-r1",
            capsule_revision="capsule-r1",
        ),
    )
    store.add(task)
    from zf.runtime.runtime_snapshot import (
        RuntimeSnapshotInput,
        build_runtime_snapshot,
        write_runtime_snapshot,
    )

    stale_task = Task(
        id="TASK-1",
        title="bound task",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-old",
        contract=TaskContract(
            behavior="build the feature",
            source_revision="source-r1",
            contract_revision="contract-r1",
            capsule_revision="capsule-r1",
        ),
    )
    snapshot = build_runtime_snapshot(RuntimeSnapshotInput(
        state_dir=orch.state_dir,
        project_root=orch.project_root,
        source="dispatch",
        task=stale_task,
        role=orch.config.roles[0],
        dispatch_id="disp-old",
    ))
    snapshot_result = write_runtime_snapshot(
        snapshot,
        state_dir=orch.state_dir,
        project_root=orch.project_root,
    )

    decisions = orch.run_once([
        ZfEvent(
            type="dev.build.done",
            actor="dev",
            task_id="TASK-1",
            payload={
                "dispatch_id": "disp-current",
                "source_revision": "source-r1",
                "contract_revision": "contract-r1",
                "capsule_revision": "capsule-r1",
                "snapshot_ref": snapshot_result.snapshot_ref,
            },
        ),
    ])

    assert decisions and decisions[0].action == "block"
    events = log.read_all()
    stale = [
        event for event in events
        if event.type == "task.completion.stale_rejected"
    ]
    assert stale
    assert stale[-1].payload["reason"] == "snapshot_dispatch_id_mismatch"
    assert stale[-1].payload["snapshot_ref"] == snapshot_result.snapshot_ref


def test_completion_with_nested_revisions_passes_revision_gate(
    tmp_path: Path,
) -> None:
    orch, store, _log = _orch(tmp_path)
    store.add(Task(
        id="TASK-1",
        title="bound task",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-1",
        contract=TaskContract(
            behavior="build the feature",
            source_revision="source-r1",
            contract_revision="contract-r1",
            capsule_revision="capsule-r1",
        ),
    ))

    decisions = orch.run_once([
        ZfEvent(
            type="dev.build.done",
            actor="dev",
            task_id="TASK-1",
            payload={
                "dispatch_id": "disp-1",
                "revisions": {
                    "source_revision": "source-r1",
                    "contract_revision": "contract-r1",
                    "capsule_revision": "capsule-r1",
                },
            },
        ),
    ])

    assert not [decision for decision in decisions if decision.action == "block"]


def test_completion_with_evidence_ref_revisions_passes_revision_gate(
    tmp_path: Path,
) -> None:
    orch, store, _log = _orch(tmp_path)
    store.add(Task(
        id="TASK-1",
        title="bound task",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-1",
        contract=TaskContract(
            behavior="build the feature",
            source_revision="source-r1",
            contract_revision="contract-r1",
            capsule_revision="capsule-r1",
        ),
    ))

    decisions = orch.run_once([
        ZfEvent(
            type="dev.build.done",
            actor="dev",
            task_id="TASK-1",
            payload={
                "dispatch_id": "disp-1",
                "evidence_refs": [
                    "git:abc123",
                    "source_revision:source-r1",
                    "contract_revision:contract-r1",
                    "capsule_revision:capsule-r1",
                ],
            },
        ),
    ])

    assert not [decision for decision in decisions if decision.action == "block"]
