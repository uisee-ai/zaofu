from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.task_contract_snapshot import (
    TASK_CONTRACT_AUTHORITY_FIELDS,
    build_task_contract_snapshot,
    effective_contract_revision,
    task_map_generation,
)
from zf.runtime.task_contract_authority import TaskContractAuthorityService
from tests.test_writer_fanout_runtime import (
    _child,
    _commit,
    _fanout_id,
    _manifest,
    _seed_tasks,
    _start,
    _state,
)

def _typed_contract(*, behavior: str = "") -> TaskContract:
    return TaskContract(
        feature_id="F-11111111",
        behavior=behavior,
        scope=["a.txt"],
        acceptance_criteria=[{
            "id": "AC-TASK-1",
            "statement": "a.txt contains the delivered result",
            "verification_command_ids": ["CMD-TASK-1"],
        }],
        verification="test -f a.txt",
        verification_tiers=["task_non_smoke"],
        validation={
            "commands": [{
                "id": "CMD-TASK-1",
                "command": "test -f a.txt",
                "acceptance_ids": ["AC-TASK-1"],
                "owner": "task_verify",
                "tier": "runtime",
                "deterministic": True,
                "reusable": True,
            }],
        },
        evidence_contract={
            "source_refs": {
                "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
            },
        },
    )


def _replace_contract(
    state_dir: Path,
    store: TaskStore,
    contract: TaskContract,
) -> None:
    task = store.get("TASK-1")
    assert task is not None
    TaskContractAuthorityService(
        task_store=store,
        event_writer=None,
        state_dir=state_dir,
    ).replace(task, contract=contract, source="test_contract_revision")


def _impl_self_check_body(
    snapshot: dict,
    *,
    attempt_id: str,
    source_commit: str,
    include_snapshot_ref: bool = True,
) -> dict:
    command = snapshot["verification_commands"][0]
    criterion = snapshot["acceptance_criteria"][0]
    body = {
        "schema_version": "impl-self-check.v1",
        "workflow_run_id": snapshot["workflow_run_id"],
        "task_id": snapshot["task_id"],
        "attempt_id": attempt_id,
        "contract_revision": snapshot["contract_revision"],
        "task_map_generation": snapshot["task_map_generation"],
        "source_commit": source_commit,
        "target_commit": source_commit,
        **{
            key: str(snapshot[key])
            for key in TASK_CONTRACT_AUTHORITY_FIELDS
            if snapshot.get(key) not in (None, "", 0)
        },
        "command_receipts": [{
            "receipt_id": "receipt-CMD-TASK-1",
            "command_id": command["command_id"],
            "command_digest": command["command_digest"],
            "target_commit": source_commit,
            "status": "passed",
            "exit_code": 0,
            "evidence_refs": ["event:test-command"],
        }],
        "acceptance_results": [{
            "acceptance_id": criterion["acceptance_id"],
            "status": "passed",
            "command_receipt_ids": ["receipt-CMD-TASK-1"],
            "evidence_refs": ["event:test-acceptance"],
            "residual_risks": [],
        }],
        "residual_risks": [],
        "evidence_refs": ["event:test-impl"],
    }
    if include_snapshot_ref:
        body.update({
            "contract_snapshot_ref": snapshot["contract_snapshot_ref"],
            "contract_snapshot_digest": snapshot["contract_snapshot_digest"],
        })
    return body


def test_writer_completion_accepts_git_evidence_as_target_commit(
    tmp_path: Path,
):
    _state_dir, _log, _transport, orch = _state(
        tmp_path,
        harness_profile="baseline",
    )

    assert orch._completion_target_commit({  # type: ignore[attr-defined]
        "evidence_refs": ["check:pytest", "git:6d08306"],
    }) == "6d08306"


def test_adopted_task_ref_recovers_source_generation_dispatch_base(
    tmp_path: Path,
):
    """A replacement fanout may not have dispatched the adopted child itself."""
    state_dir, log, _transport, orch = _state(
        tmp_path,
        harness_profile="baseline",
    )
    orch._typed_task_contract_handoff_enabled = lambda _payload: True  # type: ignore[method-assign]
    _seed_tasks(state_dir)
    store = TaskStore(state_dir / "kanban.json")
    _replace_contract(state_dir, store, _typed_contract())
    _start(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    source_fanout_id = started.payload["fanout_id"]
    source_child = _child(_manifest(state_dir, source_fanout_id), "TASK-1")
    source_payload = (
        source_child.get("payload")
        if isinstance(source_child.get("payload"), dict)
        else {}
    )
    source_base = str(source_child.get("base_commit") or "")
    assert source_base
    assert source_child.get("contract_snapshot_ref")

    replacement_fanout_id = "fanout-dev-fanout-replacement"
    replacement_started = dict(started.payload)
    replacement_started["fanout_id"] = replacement_fanout_id
    replacement_started["trigger_event_id"] = "task-map-replacement"
    EventWriter(log).append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id="trace-1",
        payload=replacement_started,
    ))
    replacement_path = (
        state_dir / "fanouts" / replacement_fanout_id / "manifest.json"
    )
    replacement = json.loads(replacement_path.read_text(encoding="utf-8"))
    replacement_child = _child(replacement, "TASK-1")
    for key in (
        "base_commit",
        "dispatch_base_commit",
        "contract_snapshot_ref",
        "contract_snapshot_digest",
        "contract_revision",
    ):
        replacement_child.pop(key, None)
        if isinstance(replacement_child.get("payload"), dict):
            replacement_child["payload"].pop(key, None)
    replacement_path.write_text(
        json.dumps(replacement, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    source_commit = _commit(
        Path(source_child["workdir"]),
        "a.txt",
        "delivered\n",
        "deliver adopted TASK-1",
    )
    progress = ZfEvent(
        type="dev.build.done",
        actor=source_child["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": source_fanout_id,
            "child_id": source_child["child_id"],
            "run_id": source_child["run_id"],
            "dispatch_id": source_child["run_id"],
            "source_branch": source_child["source_branch"],
            "workdir": source_child["workdir"],
            "source_commit": source_commit,
            "base_commit": source_base,
            "workflow_run_id": source_child["workflow_run_id"],
            "contract_revision": source_child["contract_revision"],
            "task_map_generation": source_child["task_map_generation"],
            "contract_snapshot_ref": source_child["contract_snapshot_ref"],
            "contract_snapshot_digest": source_child["contract_snapshot_digest"],
            "task_ref": source_child["task_ref"],
            "operation_id": source_child.get("operation_id")
            or source_payload.get("operation_id"),
            "request_hash": source_child.get("request_hash")
            or source_payload.get("request_hash"),
            "attempt_id": source_child.get("attempt_id")
            or source_payload.get("attempt_id")
            or source_child["run_id"],
            "result_protocol_mode": source_child.get("result_protocol_mode")
            or source_payload.get("result_protocol_mode")
            or "blocking",
        },
    )
    log.append(progress)
    orch._maybe_update_writer_fanout(progress)  # type: ignore[attr-defined]
    ref_updated = ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-1",
        correlation_id="trace-1",
        causation_id=progress.id,
        payload={
            "task_id": "TASK-1",
            "task_ref": "task/TASK-1",
            "trigger_event_id": progress.id,
            "source_branch": source_child["source_branch"],
            "source_commit": source_commit,
        },
    )
    log.append(ref_updated)

    orch._maybe_update_writer_fanout(ref_updated)  # type: ignore[attr-defined]

    completed = [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.payload.get("fanout_id") == replacement_fanout_id
        and event.payload.get("task_id") == "TASK-1"
    ]
    assert len(completed) == 1
    assert completed[0].payload["base_commit"] == source_base
    assert completed[0].payload["source_commit"] == source_commit
    assert _child(
        _manifest(state_dir, replacement_fanout_id),
        "TASK-1",
    )["status"] == "completed"
    assert _child(
        _manifest(state_dir, source_fanout_id),
        "TASK-1",
    )["status"] == "dispatched"


def test_stale_contract_completion_is_not_adopted_into_untyped_replacement(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(
        tmp_path,
        harness_profile="baseline",
    )
    orch._typed_task_contract_handoff_enabled = lambda _payload: True  # type: ignore[method-assign]
    _seed_tasks(state_dir)
    store = TaskStore(state_dir / "kanban.json")
    _replace_contract(state_dir, store, _typed_contract(behavior="revision one"))
    _start(orch)
    started = next(event for event in log.read_all() if event.type == "fanout.started")
    source_fanout_id = started.payload["fanout_id"]
    source_child = _child(_manifest(state_dir, source_fanout_id), "TASK-1")

    _replace_contract(state_dir, store, _typed_contract(behavior="revision two"))
    current_task = store.get("TASK-1")
    assert current_task is not None
    assert effective_contract_revision(current_task) != source_child["contract_revision"]

    replacement_fanout_id = "fanout-dev-fanout-replacement"
    replacement_started = dict(started.payload)
    replacement_started["fanout_id"] = replacement_fanout_id
    replacement_started["trigger_event_id"] = "task-map-replacement"
    EventWriter(log).append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        correlation_id="trace-1",
        payload=replacement_started,
    ))
    replacement_path = (
        state_dir / "fanouts" / replacement_fanout_id / "manifest.json"
    )
    replacement = json.loads(replacement_path.read_text(encoding="utf-8"))
    replacement_child = _child(replacement, "TASK-1")
    for key in (
        "contract_snapshot_ref",
        "contract_snapshot_digest",
        "contract_revision",
        "task_map_generation",
    ):
        replacement_child.pop(key, None)
        if isinstance(replacement_child.get("payload"), dict):
            replacement_child["payload"].pop(key, None)
    replacement_path.write_text(
        json.dumps(replacement, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    stale_progress = ZfEvent(
        type="dev.build.done",
        actor=source_child["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": source_fanout_id,
            "child_id": source_child["child_id"],
            "run_id": source_child["run_id"],
            "source_commit": "deadbeef",
            "contract_revision": source_child["contract_revision"],
            "task_map_generation": source_child["task_map_generation"],
            "task_map_ref": source_child["task_map_ref"],
        },
    )
    log.append(stale_progress)

    orch._maybe_update_writer_fanout(stale_progress)  # type: ignore[attr-defined]

    events = log.read_all()
    assert not [
        event for event in events
        if event.type == "fanout.child.completion_adopted"
        and event.payload.get("fanout_id") == replacement_fanout_id
    ]
    stale = [
        event for event in events
        if event.type == "fanout.child.stale_completion"
        and event.payload.get("result_event_id") == stale_progress.id
    ]
    assert len(stale) == 1
    assert stale[0].payload["reason"] == "contract_authority_mismatch"
    assert _child(
        _manifest(state_dir, replacement_fanout_id),
        "TASK-1",
    )["status"] != "failed"


def test_current_contract_completion_recovers_identity_failed_child_once(
    tmp_path: Path,
):
    state_dir, log, _transport, orch = _state(
        tmp_path,
        harness_profile="baseline",
    )
    orch._typed_task_contract_handoff_enabled = lambda _payload: True  # type: ignore[method-assign]
    orch.config.workflow.impl_self_check_required = True
    _seed_tasks(state_dir)
    store = TaskStore(state_dir / "kanban.json")
    _replace_contract(state_dir, store, _typed_contract(behavior="revision one"))
    _start(orch)
    fanout_id = _fanout_id(log)
    child = _child(_manifest(state_dir, fanout_id), "TASK-1")
    descriptor = {
        "ref": child["contract_snapshot_ref"],
        "sha256": child["contract_snapshot_digest"],
    }
    stale_snapshot = json.loads(
        (state_dir / descriptor["ref"]).read_text(encoding="utf-8")
    )
    stale_snapshot.update({
        "contract_snapshot_ref": descriptor["ref"],
        "contract_snapshot_digest": descriptor["sha256"],
    })
    _replace_contract(state_dir, store, _typed_contract(behavior="revision two"))
    current_task = store.get("TASK-1")
    assert current_task is not None
    current_revision = effective_contract_revision(current_task)
    assert current_revision != stale_snapshot["contract_revision"]
    source_commit = _commit(
        Path(child["workdir"]),
        "a.txt",
        "delivered\n",
        "deliver current contract",
    )
    attempt_id = str(child.get("attempt_id") or child["run_id"])
    stale_progress = ZfEvent(
        type="dev.build.done",
        actor=child["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": child["child_id"],
            "run_id": child["run_id"],
            "dispatch_id": child["run_id"],
            "attempt_id": attempt_id,
            "source_branch": child["source_branch"],
            "workdir": child["workdir"],
            "source_commit": source_commit,
            "workflow_run_id": "trace-1",
            "contract_revision": stale_snapshot["contract_revision"],
            "task_map_generation": stale_snapshot["task_map_generation"],
            "contract_snapshot_ref": descriptor["ref"],
            "contract_snapshot_digest": descriptor["sha256"],
            "impl_self_check": _impl_self_check_body(
                stale_snapshot,
                attempt_id=attempt_id,
                source_commit=source_commit,
            ),
        },
    )
    log.append(stale_progress)
    orch._maybe_update_writer_fanout(stale_progress)  # type: ignore[attr-defined]
    assert [
        event for event in log.read_all()
        if event.type == "fanout.child.failed"
        and event.causation_id == stale_progress.id
        and "contract_revision mismatch" in event.payload.get("reason", "")
    ]

    current_snapshot = build_task_contract_snapshot(
        current_task,
        workflow_run_id="trace-1",
        task_map_generation_id=task_map_generation(
            current_task,
            task_map_ref=child["task_map_ref"],
        ),
        base_commit=child["base_commit"],
        task_ref="task/TASK-1",
    )
    current_progress = ZfEvent(
        type="dev.build.done",
        actor=child["role_instance"],
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "dispatch_id": child["run_id"],
            "attempt_id": attempt_id,
            "source_branch": child["source_branch"],
            "workdir": child["workdir"],
            "source_commit": source_commit,
            "workflow_run_id": "trace-1",
            "contract_revision": current_revision,
            "task_map_generation": current_snapshot["task_map_generation"],
            "impl_self_check": _impl_self_check_body(
                current_snapshot,
                attempt_id=attempt_id,
                source_commit=source_commit,
                include_snapshot_ref=False,
            ),
        },
    )
    log.append(current_progress)
    orch._maybe_update_writer_fanout(current_progress)  # type: ignore[attr-defined]
    ref_updated = ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-1",
        correlation_id="trace-1",
        causation_id=current_progress.id,
        payload={
            "task_id": "TASK-1",
            "task_ref": "task/TASK-1",
            "trigger_event_id": current_progress.id,
            "source_branch": child["source_branch"],
            "source_commit": source_commit,
        },
    )
    log.append(ref_updated)

    orch._maybe_update_writer_fanout(ref_updated)  # type: ignore[attr-defined]
    orch._maybe_update_writer_fanout(ref_updated)  # type: ignore[attr-defined]

    completed = [
        event for event in log.read_all()
        if event.type == "fanout.child.completed"
        and event.payload.get("fanout_id") == fanout_id
        and event.payload.get("task_id") == "TASK-1"
    ]
    assert len(completed) == 1
    assert completed[0].payload["contract_revision"] == current_revision
    assert completed[0].payload["target_commit"] == source_commit
    assert completed[0].payload["recovered_from_status"] == "failed"
    assert _child(_manifest(state_dir, fanout_id), "TASK-1")["status"] == "completed"
