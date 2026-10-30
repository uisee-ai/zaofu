from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.events import EventWriter, ZfEvent
from zf.core.events.log import EventLog
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.integrations.feishu.projection import RoutingConfig
from zf.integrations.feishu.transport import MockFeishuTransport
from zf.runtime.goal_dossier import build_goal_dossier
from zf.runtime.goal_dossier_delivery import (
    materialize_terminal_goal_deliveries,
    owner_summary_from_goal_dossier,
)
from zf.runtime.goal_claim_set import build_goal_claim_set
from zf.runtime.operator_inbox import build_operator_inbox
from zf.runtime.owner_visible_delivery import deliver_owner_visible_messages_once
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_contract_snapshot import write_task_contract_snapshot


RUN_ID = "run-owner-delivery"
GOAL_ID = "GOAL-OWNER-DELIVERY"
TARGET = "a" * 40


def _state(tmp_path: Path) -> tuple[Path, EventLog, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="Implement owner delivery",
        status="in_progress",
        assigned_to="dev-1",
    ))
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, log, EventWriter(log)


def _completion_events(
    state_dir: Path,
    *,
    with_sources: bool = True,
) -> list[ZfEvent]:
    task_map_ref: dict = {}
    claim_set_ref: dict = {}
    if with_sources:
        task_map = {
            "schema_version": "task-map.v1",
            "workflow_run_id": RUN_ID,
            "goal_id": GOAL_ID,
            "task_map_generation": "generation-2",
            "goal_claims": [{
                "goal_claim_id": "CLAIM-OWNER",
                "text": "Owner can inspect the completed delivery.",
                "mandatory": True,
            }],
            "tasks": [{
                "task_id": "TASK-1",
                "title": "Implement owner delivery",
                "goal_claim_ids": ["CLAIM-OWNER"],
            }],
        }
        task_map_ref = write_sidecar_json(
            state_dir,
            "artifacts/plan/task-map.json",
            task_map,
            kind="task_map",
            schema_version="task-map.v1",
            created_by="test",
            required=True,
        )
        claim_set = build_goal_claim_set(
            task_map,
            workflow_run_id=RUN_ID,
            goal_id=GOAL_ID,
            task_map_generation="generation-2",
        )
        claim_set_ref = write_sidecar_json(
            state_dir,
            "artifacts/claims.json",
            claim_set,
            kind="goal_claim_set",
            schema_version="goal-claim-set.v1",
            created_by="test",
            required=True,
        )
    task_map_path = str(
        task_map_ref.get("ref") or "artifacts/plan/task-map.json"
    )
    task_map_digest = str(task_map_ref.get("sha256") or "e" * 64)
    claim_set_path = str(
        claim_set_ref.get("ref") or "artifacts/claims.json"
    )
    claim_set_digest = str(claim_set_ref.get("sha256") or "c" * 64)
    events = [
        ZfEvent(
            id="evt-start",
            type="run.goal.started",
            correlation_id=RUN_ID,
            payload={
                "run_id": RUN_ID,
                "goal_id": GOAL_ID,
                "objective": "ship owner-readable delivery",
            },
        ),
        ZfEvent(
            id="evt-task",
            type="dev.build.done",
            task_id="TASK-1",
            correlation_id=RUN_ID,
            payload={
                "workflow_run_id": RUN_ID,
                "evidence_refs": ["artifacts/impl/result.json"],
                "task_map_ref": task_map_path,
                "task_map_digest": task_map_digest,
            },
        ),
        ZfEvent(
            id="evt-claim-set",
            type="goal.claim_set.pinned",
            correlation_id=RUN_ID,
            payload={
                "workflow_run_id": RUN_ID,
                "goal_id": GOAL_ID,
                "task_map_generation": "generation-2",
                "goal_claim_set_ref": claim_set_path,
                "goal_claim_set_digest": claim_set_digest,
            },
        ),
        ZfEvent(
            id="evt-closure",
            type="goal.closure.synthesized",
            correlation_id=RUN_ID,
            payload={
                "workflow_run_id": RUN_ID,
                "goal_id": GOAL_ID,
                "admitted_call_result_ref": {
                    "ref": "artifacts/closure/result.json",
                    "sha256": "d" * 64,
                },
                "task_map_ref": task_map_path,
                "task_map_digest": task_map_digest,
                "goal_closure_result": {
                    "schema_version": "goal-closure-result.v1",
                    "workflow_run_id": RUN_ID,
                    "goal_id": GOAL_ID,
                    "flow_kind": "issue",
                    "task_map_generation": "generation-2",
                    "target_commit": TARGET,
                    "objective_ref": "objective:owner-delivery",
                    "goal_claim_set_ref": claim_set_path,
                    "goal_claim_set_digest": claim_set_digest,
                    "planning_result_ref": task_map_path,
                    "candidate_ref": f"candidate/{GOAL_ID}",
                    "closure_fact_ref": "artifacts/closure/fact.json",
                    "closure_fact_digest": "f" * 64,
                    "verdict": "passed",
                    "summary": "all mandatory claims are closed",
                    "goal_coverage": [{
                        "goal_claim_id": "CLAIM-OWNER",
                        "status": "closed",
                        "supporting_result_refs": [
                            "artifacts/verify/result.json",
                        ],
                    }],
                    "input_result_refs": [
                        "artifacts/verify/result.json",
                    ],
                    "open_gap_refs": [],
                    "recommended_action": "complete",
                },
            },
        ),
        ZfEvent(
            id="evt-claim",
            type="run.goal.completion.claimed",
            correlation_id=RUN_ID,
            payload={
                "run_id": RUN_ID,
                "goal_id": GOAL_ID,
                "claim_id": "claim-1",
                "claim_type": "admitted_goal_closure_result",
                "task_map_generation": "generation-2",
                "target_commit": TARGET,
            },
        ),
        ZfEvent(
            id="evt-candidate",
            type="candidate.ready",
            correlation_id=RUN_ID,
            payload={
                "workflow_run_id": RUN_ID,
                "candidate_ref": f"candidate/{GOAL_ID}",
                "candidate_head_commit": TARGET,
            },
        ),
        ZfEvent(
            id="evt-verify",
            type="fanout.child.completed",
            correlation_id=RUN_ID,
            payload={
                "workflow_run_id": RUN_ID,
                "control_result_schema": "verification-result.v1",
                "admitted_call_result_ref": {
                    "ref": "artifacts/verify/result.json",
                    "sha256": "b" * 64,
                },
            },
        ),
        ZfEvent(
            id="evt-completed",
            type="run.goal.completed",
            actor="zf-cli",
            causation_id="evt-claim",
            correlation_id=RUN_ID,
            payload={
                "run_id": RUN_ID,
                "workflow_run_id": RUN_ID,
                "goal_id": GOAL_ID,
                "feature_id": GOAL_ID,
                "claim_id": "claim-1",
                "claim_event_id": "evt-claim",
                "source_event_id": "evt-closure",
                "task_map_generation": "generation-2",
                "target_commit": TARGET,
                "verified_target_commit": TARGET,
                "verification_event_id": "evt-verify",
                "verification_admitted_call_result_ref": {
                    "ref": "artifacts/verify/result.json",
                    "sha256": "b" * 64,
                },
                "candidate_event_id": "evt-candidate",
                "candidate_ref": f"candidate/{GOAL_ID}",
                "candidate_base_commit": "0" * 40,
                "candidate_head_commit": TARGET,
                "completed_task_ids": ["TASK-1"],
                "task_map_ref": task_map_path,
                "task_map_digest": task_map_digest,
                "source_index_ref": "artifacts/impl/source-index.json",
                "diff_ref": "artifacts/impl/diff.patch",
                "goal_claim_set_ref": claim_set_path,
                "goal_claim_set_digest": claim_set_digest,
                "admitted_call_result_ref": {
                    "ref": "artifacts/closure/result.json",
                    "sha256": "d" * 64,
                },
                "delivery_policy": "report_only",
                "delivery_status": "not_required",
                "delivery_event_id": "",
            },
        ),
    ]
    if not with_sources:
        events = [event for event in events if event.id != "evt-claim-set"]
        next(
            event for event in events if event.id == "evt-closure"
        ).payload.pop("goal_closure_result", None)
    return events


def test_completed_terminal_materializes_receipt_and_owner_request_once(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    for event in _completion_events(state_dir):
        log.append(event)

    first = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
        project_id="demo",
    )
    second = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
        project_id="demo",
    )

    projection_dir = state_dir / "projections" / "goals" / RUN_ID
    assert first.materialized == 1
    assert first.requested == 1
    assert first.failed == 0
    assert second.requested == 0
    assert second.skipped == 1
    assert (projection_dir / "goal-dossier.v1.json").is_file()
    assert (projection_dir / "dossier.md").is_file()
    assert (projection_dir / "goal-completion-receipt.v1.json").is_file()
    requests = [
        event for event in log.read_all()
        if event.type == "owner.visible_message.requested"
    ]
    assert len(requests) == 1
    payload = requests[0].payload
    assert payload["message_kind"] == "run_terminal_delivery"
    assert payload["terminal_status"] == "completed"
    assert payload["human_action_required"] is False
    assert payload["completion_receipt_ref"].endswith(
        "goal-completion-receipt.v1.json"
    )
    assert payload["narrative_status"] == "degraded"
    assert payload["owner_delivery_composite_ref"].endswith(
        "owner-delivery-composite.v1.json"
    )
    composite = json.loads((
        state_dir / payload["owner_delivery_composite_ref"]
    ).read_text(encoding="utf-8"))
    assert composite["factual"]["dossier_ref"] == payload["dossier_ref"]
    assert composite["narrative_status"] == "degraded"
    assert payload["web_deep_link"].startswith(
        "/?page=runs&run_id="
    )
    materialized = json.loads((
        projection_dir / "goal-dossier.v1.json"
    ).read_text(encoding="utf-8"))
    assert materialized["delivery_readiness"]["status"] == "ready"
    rebuilt = build_goal_dossier(state_dir, RUN_ID)
    assert rebuilt["source_fingerprint"] == materialized["source_fingerprint"]
    assert rebuilt["freshness"]["last_event_id"] == "evt-completed"


def test_completed_dossier_keeps_superseded_generation_as_history_only(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-OLD",
        title="Superseded task",
        status="blocked",
        assigned_to="dev-old",
    ))
    events = _completion_events(state_dir)
    events[1:1] = [
        ZfEvent(
            id="evt-old-failed",
            type="dev.failed",
            task_id="TASK-OLD",
            correlation_id=RUN_ID,
            payload={
                "workflow_run_id": RUN_ID,
                "task_id": "TASK-OLD",
                "task_map_generation": "generation-1",
                "reason": "superseded implementation failed",
            },
        ),
        ZfEvent(
            id="evt-old-rework",
            type="task.rework.requested",
            task_id="TASK-OLD",
            correlation_id=RUN_ID,
            payload={
                "workflow_run_id": RUN_ID,
                "task_id": "TASK-OLD",
                "task_map_generation": "generation-1",
                "contract_revision": "old-revision",
                "dispatch_id": "old-dispatch",
                "attempt": 1,
                "finding_ids": ["finding-old"],
            },
        ),
    ]
    for event in events:
        log.append(event)

    result = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
        project_id="demo",
    )
    dossier = build_goal_dossier(state_dir, RUN_ID)

    assert result.requested == 1
    assert dossier["delivery_readiness"]["status"] == "ready"
    assert dossier["state"]["task_counts"] == {
        "total": 1,
        "terminal": 1,
        "open": 0,
    }
    assert [task["id"] for task in dossier["state"]["tasks"]] == ["TASK-1"]
    assert any(
        task["id"] == "TASK-OLD"
        for task in dossier["state"]["historical_tasks"]
    )
    assert dossier["state"]["handoff"]["open_feedback_count"] == 0
    assert dossier["state"]["handoff"]["pending_handoff_count"] == 0
    assert dossier["state"]["handoff"]["historical_open_feedback_count"] == 1
    assert dossier["state"]["handoff"]["historical_pending_handoff_count"] == 1
    assert dossier["goal"]["open_feedback_count"] == 0
    assert dossier["goal"]["pending_handoff_count"] == 0
    assert dossier["goal"]["historical_open_feedback_count"] == 1
    assert dossier["goal"]["historical_pending_handoff_count"] == 1
    assert dossier["gaps"] == []
    assert dossier["claim_to_evidence"]["summary"]["closed_claims"] == 1


def test_completed_dossier_does_not_hide_current_generation_feedback(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    events = _completion_events(state_dir)
    events.insert(-1, ZfEvent(
        id="evt-current-rework",
        type="task.rework.requested",
        task_id="TASK-1",
        correlation_id=RUN_ID,
        payload={
            "workflow_run_id": RUN_ID,
            "task_id": "TASK-1",
            "task_map_generation": "generation-2",
            "contract_revision": "current-revision",
            "dispatch_id": "current-dispatch",
            "attempt": 1,
            "finding_ids": ["finding-current"],
        },
    ))
    for event in events:
        log.append(event)

    result = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
    )
    dossier = build_goal_dossier(state_dir, RUN_ID)

    assert result.failed == 1
    assert result.requested == 0
    assert dossier["state"]["handoff"]["open_feedback_count"] == 1
    assert dossier["state"]["handoff"]["pending_handoff_count"] == 1
    assert {gap["type"] for gap in dossier["gaps"]} == {"open_feedback"}
    assert dossier["delivery_readiness"]["status"] == "incomplete"


def test_inconsistent_completed_dossier_suppresses_owner_until_rebuilt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zf.runtime import goal_dossier_delivery as delivery

    state_dir, log, writer = _state(tmp_path)
    for event in _completion_events(state_dir):
        log.append(event)
    original = delivery.build_goal_dossier

    def inconsistent(*args, **kwargs):  # noqa: ANN002, ANN003
        dossier = original(*args, **kwargs)
        dossier["state"]["task_counts"] = {
            "total": 2,
            "terminal": 1,
            "open": 1,
        }
        return dossier

    monkeypatch.setattr(delivery, "build_goal_dossier", inconsistent)
    first = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
    )
    second = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
    )

    assert first.failed == 1
    assert second.failed == 1
    assert not [
        event for event in log.read_all()
        if event.type == "owner.visible_message.requested"
    ]
    inconsistencies = [
        event for event in log.read_all()
        if event.type == "goal.dossier.inconsistent"
    ]
    assert len(inconsistencies) == 1
    assert {
        row["code"] for row in inconsistencies[0].payload["diagnostics"]
    } == {"completed_task_counts_inconsistent"}

    monkeypatch.setattr(delivery, "build_goal_dossier", original)
    repaired = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
    )
    assert repaired.requested == 1
    assert len([
        event for event in log.read_all()
        if event.type == "owner.visible_message.requested"
    ]) == 1


def test_non_gate_terminal_aliases_do_not_trigger_owner_delivery(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    log.append(ZfEvent(
        id="evt-start",
        type="run.goal.started",
        correlation_id="run-alias",
        payload={"run_id": "run-alias", "goal_id": "GOAL-ALIAS"},
    ))
    log.append(ZfEvent(
        id="evt-judge",
        type="judge.passed",
        correlation_id="run-alias",
        payload={"run_id": "run-alias"},
    ))
    log.append(ZfEvent(
        id="evt-ship",
        type="ship.completed",
        correlation_id="run-alias",
        payload={"run_id": "run-alias"},
    ))

    result = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
    )

    assert result.considered == 0
    assert not [
        event for event in log.read_all()
        if event.type == "owner.visible_message.requested"
    ]


def test_blocked_then_completed_terminals_are_each_materialized_once(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    events = _completion_events(state_dir)
    events.insert(-1, ZfEvent(
        id="evt-blocked-before-completion",
        type="run.goal.blocked",
        correlation_id=RUN_ID,
        payload={
            "run_id": RUN_ID,
            "goal_id": GOAL_ID,
            "reason": "temporary owner dependency",
            "next_action": "Resolve the dependency and resume.",
        },
    ))
    for event in events:
        log.append(event)

    first = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
        project_id="demo",
    )
    second = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
        project_id="demo",
    )

    assert first.materialized == 2
    assert first.requested == 2
    assert second.materialized == 0
    assert second.requested == 0
    assert second.skipped == 2
    requests = [
        event for event in log.read_all()
        if event.type == "owner.visible_message.requested"
    ]
    assert [event.payload["terminal_status"] for event in requests] == [
        "blocked", "completed",
    ]
    state = json.loads((
        state_dir
        / "projections"
        / "goals"
        / RUN_ID
        / "delivery-materialization.v1.json"
    ).read_text(encoding="utf-8"))
    assert set(state["deliveries"]) == {
        "evt-blocked-before-completion", "evt-completed",
    }


def test_terminal_dossier_history_is_stable_when_current_task_store_drifts(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    events = _completion_events(state_dir)
    for event in events:
        log.append(event)

    first = build_goal_dossier(state_dir, RUN_ID, events=events)
    TaskStore(state_dir / "kanban.json").update(
        "TASK-1",
        status="blocked",
        assigned_to="repair-lane",
    )
    second = build_goal_dossier(state_dir, RUN_ID, events=events)

    assert first["source_fingerprint"] == second["source_fingerprint"]
    assert second["state"]["tasks"][0]["status"] == "done"
    assert second["state"]["current_overlay"]["drift"] == [{
        "task_id": "TASK-1",
        "historical_status": "done",
        "current_status": "blocked",
    }]


def test_dossier_hydrates_task_contract_claim_matrix_and_instruction_refs(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    task_map = {
        "schema_version": "task-map.v1",
        "workflow_run_id": RUN_ID,
        "goal_id": GOAL_ID,
        "task_map_generation": "generation-2",
        "goal_claims": [{
            "goal_claim_id": "CLAIM-1",
            "text": "Owner can inspect the completed run",
            "mandatory": True,
        }],
        "tasks": [{
            "task_id": "TASK-1",
            "title": "Implement owner delivery",
            "owner_role": "dev",
            "goal_claim_ids": ["CLAIM-1"],
            "acceptance_criteria": ["Run Dossier is readable"],
        }],
    }
    task_map_ref = write_sidecar_json(
        state_dir,
        "artifacts/plan/task-map-custom.json",
        task_map,
        kind="task_map",
        schema_version="task-map.v1",
        created_by="test",
        required=True,
    )
    claim_set = build_goal_claim_set(
        task_map,
        workflow_run_id=RUN_ID,
        goal_id=GOAL_ID,
        task_map_generation="generation-2",
    )
    claim_set_ref = write_sidecar_json(
        state_dir,
        "artifacts/claims-custom.json",
        claim_set,
        kind="goal_claim_set",
        schema_version="goal-claim-set.v1",
        created_by="test",
        required=True,
    )
    contract_ref = write_task_contract_snapshot(
        state_dir,
        {
            "schema_version": "task-contract-snapshot.v1",
            "workflow_run_id": RUN_ID,
            "task_id": "TASK-1",
            "contract_revision": "revision-1",
            "task_map_generation": "generation-2",
            "base_commit": "0" * 40,
            "task_ref": "tasks/active/TASK-1.md",
            "title": "Implement owner delivery",
            "acceptance_criteria": [{
                "acceptance_id": "AC-1",
                "statement": "Run Dossier is readable",
                "mandatory": True,
                "verification_owner": "task_verify",
                "verification_tier": "task_non_smoke",
                "verification_command_ids": [],
            }],
            "verification_commands": [],
            "source_refs": {},
        },
    )
    goal_closure_contract_ref = write_sidecar_json(
        state_dir,
        "artifacts/goal-closure/contract-snapshots/current.json",
        {
            "schema_version": "goal-closure-contract-snapshot.v1",
            "workflow_run_id": RUN_ID,
            "goal_id": GOAL_ID,
            "task_map_generation": "generation-2",
        },
        kind="goal_closure_contract_snapshot",
        schema_version="goal-closure-contract-snapshot.v1",
        created_by="test",
        required=True,
    )
    events = _completion_events(state_dir, with_sources=True)
    events[2].payload.update({
        "goal_claim_set_ref": claim_set_ref["ref"],
        "goal_claim_set_digest": claim_set_ref["sha256"],
    })
    events[3].payload["goal_closure_result"].update({
        "goal_claim_set_ref": claim_set_ref["ref"],
        "goal_claim_set_digest": claim_set_ref["sha256"],
        "goal_coverage": [{
            "goal_claim_id": "CLAIM-1",
            "status": "closed",
            "supporting_result_refs": ["artifacts/verify/result.json"],
        }],
    })
    events[1].payload.update({
        "task_map_ref": task_map_ref["ref"],
        "task_map_digest": task_map_ref["sha256"],
        "contract_snapshot_ref": contract_ref["ref"],
        "contract_snapshot_digest": contract_ref["sha256"],
        "briefing_ref": "artifacts/briefings/TASK-1.md",
        "briefing": "this semantic body must not be copied",
        "nested_context": {
            "briefing_ref": "artifacts/briefings/TASK-1-followup.md",
            "briefing_digest": "e" * 64,
        },
    })
    events[-1].payload.update({
        "task_map_ref": task_map_ref["ref"],
        "task_map_digest": task_map_ref["sha256"],
        "contract_snapshot_ref": goal_closure_contract_ref["ref"],
        "contract_snapshot_digest": goal_closure_contract_ref["sha256"],
        "goal_closure_contract_snapshot_ref": goal_closure_contract_ref["ref"],
        "goal_closure_contract_snapshot_digest": goal_closure_contract_ref[
            "sha256"
        ],
    })
    for event in events:
        log.append(event)

    dossier = build_goal_dossier(state_dir, RUN_ID, events=events)

    contract = dossier["task_contracts"]["TASK-1"]
    assert dossier["freshness"]["status"] == "ready"
    assert not dossier["freshness"]["diagnostics"]
    assert contract["workflow_run_id"] == RUN_ID
    assert contract["base_commit"] == "0" * 40
    assert contract["task_ref"] == "tasks/active/TASK-1.md"
    assert contract["acceptance_criteria"][0]["verification_owner"] == "task_verify"
    assert contract["acceptance_criteria"][0]["verification_tier"] == "task_non_smoke"
    assert dossier["instruction_context"] == [{
        "kind": "briefing",
        "ref": "artifacts/briefings/TASK-1.md",
        "digest": "",
        "event_id": "evt-task",
        "event_type": "dev.build.done",
        "task_id": "TASK-1",
    }, {
        "kind": "contract_snapshot",
        "ref": contract_ref["ref"],
        "digest": contract_ref["sha256"],
        "event_id": "evt-task",
        "event_type": "dev.build.done",
        "task_id": "TASK-1",
    }, {
        "kind": "briefing",
        "ref": "artifacts/briefings/TASK-1-followup.md",
        "digest": "e" * 64,
        "event_id": "evt-task",
        "event_type": "dev.build.done",
        "task_id": "TASK-1",
    }, {
        "kind": "objective",
        "ref": "objective:owner-delivery",
        "digest": "",
        "event_id": "evt-closure",
        "event_type": "goal.closure.synthesized",
        "task_id": "",
    }, {
        "kind": "goal_closure_contract_snapshot",
        "ref": goal_closure_contract_ref["ref"],
        "digest": goal_closure_contract_ref["sha256"],
        "event_id": "evt-completed",
        "event_type": "run.goal.completed",
        "task_id": "",
    }]
    assert "this semantic body" not in str(dossier["instruction_context"])
    matrix = dossier["claim_to_evidence"]
    assert matrix["rows"][0]["goal_claim_id"] == "CLAIM-1"
    assert matrix["rows"][0]["task_ids"] == ["TASK-1"]
    assert matrix["rows"][0]["implementation"][0]["task_ref"] == (
        "tasks/active/TASK-1.md"
    )
    delivered = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
    )
    materialization = json.loads((
        state_dir
        / "projections"
        / "goals"
        / RUN_ID
        / "delivery-materialization.v1.json"
    ).read_text(encoding="utf-8"))
    assert delivered.failed == 0
    assert materialization["status"] == "delivered_requested"


def test_terminal_dossier_uses_run_goal_identity_over_task_map_feature_alias(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    task_map = {
        "schema_version": "task-map.v1",
        "pdd_id": GOAL_ID,
        "feature_id": "owner-delivery-feature",
        "task_map_generation": "generation-2",
        "goal_claims": [{
            "goal_claim_id": "CLAIM-1",
            "text": "Owner can inspect the completed run",
            "mandatory": True,
        }],
        "tasks": [{
            "task_id": "TASK-1",
            "title": "Implement owner delivery",
            "goal_claim_ids": ["CLAIM-1"],
        }],
    }
    task_map_ref = write_sidecar_json(
        state_dir,
        "artifacts/plan/task-map.json",
        task_map,
        kind="task_map",
        schema_version="task-map.v1",
        created_by="test",
        required=True,
    )
    claim_set = build_goal_claim_set(
        {
            "tasks": [{
                "task_id": "TASK-1",
                "acceptance_criteria": [
                    "CLAIM-PINNED: Preserve the accepted historical claim.",
                ],
            }],
        },
        workflow_run_id=RUN_ID,
        goal_id=GOAL_ID,
        task_map_generation="generation-2",
    )
    claim_set_ref = write_sidecar_json(
        state_dir,
        "artifacts/goal-closure/claims.json",
        claim_set,
        kind="goal_claim_set",
        schema_version="goal-claim-set.v1",
        created_by="test",
        required=True,
    )
    closure_result = {
        "schema_version": "goal-closure-result.v1",
        "workflow_run_id": RUN_ID,
        "goal_id": GOAL_ID,
        "flow_kind": "issue",
        "task_map_generation": "generation-2",
        "target_commit": TARGET,
        "objective_ref": "objective:owner-delivery",
        "goal_claim_set_ref": claim_set_ref["ref"],
        "goal_claim_set_digest": claim_set_ref["sha256"],
        "planning_result_ref": task_map_ref["ref"],
        "candidate_ref": f"candidate/{GOAL_ID}",
        "closure_fact_ref": "artifacts/closure/fact.json",
        "closure_fact_digest": "f" * 64,
        "verdict": "passed",
        "summary": "all mandatory claims are closed",
        "goal_coverage": [{
            "goal_claim_id": "CLAIM-PINNED",
            "status": "closed",
            "supporting_result_refs": ["artifacts/verify/result.json"],
        }],
        "input_result_refs": ["artifacts/verify/result.json"],
        "open_gap_refs": [],
        "recommended_action": "complete",
    }
    events = _completion_events(state_dir, with_sources=False)
    events[2].payload.update({
        "goal_closure_result": closure_result,
        "task_map_ref": task_map_ref["ref"],
        "task_map_digest": task_map_ref["sha256"],
    })
    events[-1].payload.update({
        "task_map_ref": task_map_ref["ref"],
        "task_map_digest": task_map_ref["sha256"],
    })
    events.insert(2, ZfEvent(
        id="evt-claim-set",
        type="goal.claim_set.pinned",
        correlation_id=RUN_ID,
        payload={
            "workflow_run_id": RUN_ID,
            "goal_id": GOAL_ID,
            "task_map_generation": "generation-2",
            "goal_claim_set_ref": claim_set_ref["ref"],
            "goal_claim_set_digest": claim_set_ref["sha256"],
        },
    ))
    for event in events:
        log.append(event)

    dossier = build_goal_dossier(state_dir, RUN_ID, events=events)

    assert dossier["claim_to_evidence"]["summary"]["closed_claims"] == 1
    assert dossier["claim_to_evidence"]["rows"][0]["goal_claim_id"] == (
        "CLAIM-PINNED"
    )
    assert dossier["claim_to_evidence"]["rows"][0]["verdict"] == "closed"
    assert dossier["claim_to_evidence"]["rows"][0]["result_refs"] == [
        "artifacts/verify/result.json",
    ]
    assert dossier["claim_to_evidence"]["rows"][0]["evidence_refs"] == []


def test_blocked_terminal_delivers_blocker_without_completion_receipt(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    for event in (
        ZfEvent(
            id="evt-start",
            type="run.goal.started",
            correlation_id="run-blocked",
            payload={
                "run_id": "run-blocked",
                "goal_id": "GOAL-BLOCKED",
                "objective": "blocked objective",
            },
        ),
        ZfEvent(
            id="evt-failed",
            type="verify.failed",
            task_id="TASK-1",
            correlation_id="run-blocked",
            payload={
                "run_id": "run-blocked",
                "reason": "required browser evidence missing",
            },
        ),
        ZfEvent(
            id="evt-blocked",
            type="run.goal.blocked",
            correlation_id="run-blocked",
            payload={
                "run_id": "run-blocked",
                "goal_id": "GOAL-BLOCKED",
                "reason": "owner input required",
                "next_action": "Provide the missing browser credential.",
            },
        ),
    ):
        log.append(event)

    result = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
        project_id="demo",
    )

    projection_dir = state_dir / "projections" / "goals" / "run-blocked"
    assert result.requested == 1
    assert not (projection_dir / "goal-completion-receipt.v1.json").exists()
    request = [
        event for event in log.read_all()
        if event.type == "owner.visible_message.requested"
    ][0]
    assert request.payload["terminal_status"] == "blocked"
    assert request.payload["human_action_required"] is True
    assert request.payload["completion_receipt_ref"] == ""
    assert "browser credential" in request.payload["next_action"]


def test_projection_failure_preserves_terminal_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zf.runtime import goal_dossier_delivery as delivery

    state_dir, log, writer = _state(tmp_path)
    for event in _completion_events(state_dir):
        log.append(event)
    original = delivery.write_goal_dossier_projection
    monkeypatch.setattr(
        delivery,
        "write_goal_dossier_projection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    failed = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
    )

    assert failed.failed == 1
    assert [event.type for event in log.read_all()].count("run.goal.completed") == 1
    assert not [
        event for event in log.read_all()
        if event.type == "owner.visible_message.requested"
    ]
    state = json.loads((
        state_dir
        / "projections"
        / "goals"
        / RUN_ID
        / "delivery-materialization.v1.json"
    ).read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["reason"] == "disk full"

    monkeypatch.setattr(delivery, "write_goal_dossier_projection", original)
    retried = materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
    )
    assert retried.requested == 1
    assert retried.failed == 0


def test_inbox_uses_same_dossier_owner_summary(tmp_path: Path) -> None:
    state_dir, log, writer = _state(tmp_path)
    for event in _completion_events(state_dir):
        log.append(event)
    materialize_terminal_goal_deliveries(
        state_dir=state_dir,
        event_log=log,
        writer=writer,
        project_id="demo",
    )
    dossier = json.loads((
        state_dir / "projections" / "goals" / RUN_ID / "goal-dossier.v1.json"
    ).read_text(encoding="utf-8"))
    receipt = json.loads((
        state_dir
        / "projections"
        / "goals"
        / RUN_ID
        / "goal-completion-receipt.v1.json"
    ).read_text(encoding="utf-8"))
    owner_summary = owner_summary_from_goal_dossier(dossier, receipt=receipt)

    inbox = build_operator_inbox(state_dir, log.read_all())

    assert inbox["summary"]["run_deliveries"] == 0
    assert inbox["views"]["notification"]["count"] == 0
    assert inbox["summary"]["noise_pending"] == 0
    assert inbox["summary"]["notification_pending"] == 0

    transport = MockFeishuTransport()
    delivered = deliver_owner_visible_messages_once(
        event_log=log,
        writer=writer,
        transport=transport,
        routing=RoutingConfig(
            channels={"owner": "ou-owner"},
            receive_id_types={"owner": "open_id"},
        ),
    )
    assert delivered.delivered == 1
    card = json.loads(transport.sent_messages[0].content)
    assert owner_summary["summary"] in card["elements"][0]["text"]["content"]
