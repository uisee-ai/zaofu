from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService


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


def test_create_task_causation_binds_original_requirement_for_critical_context(tmp_path):
    from zf.runtime.critical_context import critical_context_sources
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    state, writer, service = _runtime(tmp_path)
    origin = writer.emit('user.message', actor='user', payload={
        'project_id':'P', 'conversation_id':'C', 'thread_key':'main',
        'message':'完整原始需求，保留全部约束。',
    })
    result = _execute(service, writer, 'create-task', {
        'task_id':'T-SOURCE', 'title':'Minimal requirement', 'project_id':'P',
        'conversation_id':'C', 'thread_id':'main', 'causation_id':origin.id,
    })
    assert result['ok'], result
    created = next(e for e in writer.event_log.read_all() if e.type == 'task.created')
    assert created.payload['request']['originating_message_event_ids'] == [origin.id]
    writer.emit('config.run_contract.request_bound', actor='kernel', task_id='T-SOURCE', payload={'run_id':'R'})
    sources = critical_context_sources(state, writer.event_log.read_all(), 'R')
    body = hydrate_sidecar_ref(state, next(s for s in sources if s['source_id']=='critical-user-and-config')).payload
    assert body['original_user_inputs'][0]['message'] == origin.payload['message']


def test_create_task_rejects_foreign_explicit_requirement_before_mutation(tmp_path):
    state, writer, service = _runtime(tmp_path)
    origin = writer.emit('user.message', actor='user', payload={
        'project_id':'foreign', 'conversation_id':'C', 'message':'foreign requirement',
    })
    result = _execute(service, writer, 'create-task', {
        'task_id':'T-FOREIGN', 'title':'Rejected requirement', 'project_id':'P',
        'conversation_id':'C', 'originating_message_event_ids':[origin.id],
    })
    assert not result['ok']
    assert TaskStore(state / 'kanban.json').get('T-FOREIGN') is None
