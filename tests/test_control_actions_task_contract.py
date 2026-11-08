from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.task_contract_authority import TaskContractAuthorityService


def _runtime(
    tmp_path: Path,
) -> tuple[Path, EventWriter, ControlledActionService]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    service = ControlledActionService(
        state_dir,
        writer,
        config=ZfConfig(project=ProjectConfig(name="task-contract-test")),
        project_root=tmp_path,
        actor="web",
        source="kanban-agent",
        surface="web",
    )
    return state_dir, writer, service


def _execute(
    service: ControlledActionService,
    writer: EventWriter,
    action: str,
    payload: dict,
) -> dict:
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": action, "request": payload},
    )
    return service.execute(
        action=action,
        requested_action=action,
        payload=payload,
        requested=requested,
    )


def test_create_and_partial_update_preserve_task_contract_lineage(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _runtime(tmp_path)
    created = _execute(service, writer, "create-task", {
        "task_id": "TASK-LINEAGE",
        "title": "Implement the canonical PRD",
        "contract": {
            "behavior": "Implement the approved requirement.",
            "verification": "Run the acceptance contract.",
            "spec_ref": "channel-artifacts/ch-prd/prd.md",
            "source_ref": "channel:ch-prd/main",
            "handoff_artifacts": ["channel-artifacts/ch-prd/prd.md"],
            "acceptance_criteria": [{
                "id": "AC-1",
                "text": "The PRD lineage remains queryable.",
                "tier": "runtime",
            }],
            "evidence_contract": {
                "channel_prd_digest": "sha256:" + ("a" * 64),
            },
            "unknown_extension": "must-not-enter-canonical-state",
        },
    })

    assert created["ok"] is True
    store = TaskStore(state_dir / "kanban.json")
    task = store.get("TASK-LINEAGE")
    assert task is not None
    assert task.contract.spec_ref == "channel-artifacts/ch-prd/prd.md"
    assert task.contract.source_ref == "channel:ch-prd/main"
    assert task.contract.handoff_artifacts == [
        "channel-artifacts/ch-prd/prd.md",
    ]
    assert task.contract.acceptance_criteria[0]["id"] == "AC-1"
    assert task.contract.evidence_contract[
        "channel_prd_digest"
    ].endswith("a" * 64)
    assert not hasattr(task.contract, "unknown_extension")

    updated = _execute(service, writer, "update-task", {
        "task_id": task.id,
        "contract": {
            "behavior": "Implement and document the approved requirement.",
        },
    })

    assert updated["ok"] is True
    refreshed = store.get(task.id)
    assert refreshed is not None
    assert refreshed.contract.behavior.startswith("Implement and document")
    assert refreshed.contract.spec_ref == task.contract.spec_ref
    assert refreshed.contract.source_ref == task.contract.source_ref
    assert refreshed.contract.handoff_artifacts == task.contract.handoff_artifacts
    assert refreshed.contract.evidence_contract == task.contract.evidence_contract


def test_task_contract_payload_normalizes_invalid_container_types(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _runtime(tmp_path)
    result = _execute(service, writer, "create-task", {
        "task_id": "TASK-NORMALIZE",
        "title": "Normalize malformed optional containers",
        "contract": {
            "scope": "src/**,tests/**",
            "handoff_artifacts": {"unexpected": "mapping"},
            "validation": ["not", "a", "mapping"],
            "fanout_force": "false",
            "wave": "2",
        },
    })

    assert result["ok"] is True
    task = TaskStore(state_dir / "kanban.json").get("TASK-NORMALIZE")
    assert task is not None
    assert task.contract.scope == ["src/**", "tests/**"]
    assert task.contract.handoff_artifacts == []
    assert task.contract.validation == {}
    assert task.contract.fanout_force is False
    assert task.contract.wave == 2


def test_update_task_uses_contract_authority_after_workflow_binding(
    tmp_path: Path,
) -> None:
    state_dir, writer, service = _runtime(tmp_path)
    created = _execute(service, writer, "create-task", {
        "task_id": "TASK-AUTHORITY",
        "title": "Update an authority-stamped contract",
        "contract": {
            "behavior": "Initial behavior.",
            "verification": "Run the contract check.",
        },
    })
    assert created["ok"] is True
    store = TaskStore(state_dir / "kanban.json")
    task = store.get("TASK-AUTHORITY")
    assert task is not None
    stamped = TaskContractAuthorityService(
        task_store=store,
        event_writer=writer,
        state_dir=state_dir,
    ).replace(
        task,
        contract=task.contract,
        source="workflow-binding-test",
        actor="web",
    ).task
    assert stamped.contract_authority_revision

    updated = _execute(service, writer, "update-task", {
        "task_id": task.id,
        "contract": {"behavior": "Updated behavior."},
    })

    assert updated["ok"] is True
    refreshed = store.get(task.id)
    assert refreshed is not None
    assert refreshed.contract.behavior == "Updated behavior."
    assert refreshed.contract.verification == "Run the contract check."
    assert refreshed.contract_authority_revision
