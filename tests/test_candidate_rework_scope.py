from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.candidate_rework_scope import prepare_candidate_rework_scope
from zf.runtime.module_gap_plan import write_gap_task_map_amend_artifact


def _task(
    task_id: str,
    allowed_paths: list[str],
    *,
    owner_role: str = "dev-web",
    blocked_by: list[str] | None = None,
    wave: int = 0,
) -> dict:
    return {
        "task_id": task_id,
        "title": task_id,
        "owner_role": owner_role,
        "allowed_paths": allowed_paths,
        "allowed_paths_reason": "exclusive test ownership",
        "blocked_by": blocked_by or [],
        "acceptance_criteria": [f"{task_id} acceptance"],
        "verification": ["npm run test"],
        "verify_commands": ["npm run test"],
        "source_refs": ["docs/prd.md"],
        "affinity_tag": "web",
        "wave": wave,
    }


def test_rework_rebinds_current_map_and_replaces_owner_for_unowned_paths(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    current_ref = "artifacts/PRD-1/current-task-map.json"
    current_path = state_dir / current_ref
    current_path.parent.mkdir(parents=True)
    current_path.write_text(json.dumps({
        "schema_version": "task-map.v1",
        "tasks": [
            _task(
                "TASK-EVIDENCE",
                [
                    "tests/mobility-airport-platform/**",
                    "artifacts/evidence/mobility-airport-platform/**",
                ],
            ),
            _task(
                "TASK-AUDIT",
                [],
                owner_role="candidate-verify",
                blocked_by=["TASK-EVIDENCE"],
                wave=1,
            ),
        ],
    }), encoding="utf-8")
    events = [
        ZfEvent(
            id="old-map",
            type="task_map.ready",
            correlation_id="workflow-1",
            payload={
                "pdd_id": "PRD-1",
                "task_map_ref": "artifacts/PRD-1/original-task-map.json",
                "task_map_generation": "generation-1",
            },
        ),
        ZfEvent(
            id="current-map",
            type="task_map.ready",
            correlation_id="replan-child",
            payload={
                "pdd_id": "PRD-1",
                "task_map_ref": current_ref,
                "task_map_generation": "generation-2",
                "supersedes_task_map_generation": "generation-1",
                "source_index_ref": "artifacts/PRD-1/source-index.json",
            },
        ),
        ZfEvent(
            id="verify-child",
            type="fanout.child.failed",
            correlation_id="workflow-1",
            payload={
                "fanout_id": "fanout-verify",
                "control_result_ref": {
                    "ref": "artifacts/results/ac25.json",
                    "sha256": "a" * 64,
                },
                "report": {
                    "schema_version": "verification-result.v1",
                    "verdict": "rejected",
                    "requirement_results": [{
                        "acceptance_id": "AC25",
                        "status": "failed",
                        "evidence_refs": ["artifacts/evidence/ac25.json"],
                        "reproduction_commands": ["npm run e2e"],
                        "findings": [{
                            "path": "src/styles/mobility.css",
                            "message": "inactive shell remains visible",
                        }],
                    }],
                    "rework_items": [{
                        "rework_item_id": "RW-AC25-MODE-ISOLATION",
                        "acceptance_id": "AC25",
                        "allowed_scope": [
                            "src/styles/mobility.css",
                            "src/mobility/MobilityApp.ts",
                            "tests/mobility-airport-platform/airport.spec.ts",
                        ],
                        "required_delta": "hide inactive mode surfaces",
                        "done_when": "computed display is none",
                    }],
                },
            },
        ),
        ZfEvent(
            id="test-failed",
            type="test.failed",
            correlation_id="workflow-1",
            payload={"fanout_id": "fanout-verify", "pdd_id": "PRD-1"},
        ),
    ]

    prepared = prepare_candidate_rework_scope(
        state_dir=state_dir,
        project_root=tmp_path,
        events=events,
        pdd_id="PRD-1",
        trace_id="workflow-1",
        source_event_id="test-failed",
        anchor={
            "task_map_ref": "artifacts/PRD-1/original-task-map.json",
            "task_map_generation": "generation-1",
            "plan_artifact_package_id": "old-package",
            "plan_revision": "generation-1",
            "source_commit": "a" * 40,
            "source_index_ref": "artifacts/PRD-1/original-source-index.json",
        },
        rework_paths=[
            "src/styles/mobility.css",
            "src/mobility/MobilityApp.ts",
            "tests/mobility-airport-platform/airport.spec.ts",
        ],
        failed_task_ids=[],
        rework_summary={},
    )

    assert prepared["anchor"]["task_map_ref"] == current_ref
    assert prepared["anchor"]["task_map_generation"] == "generation-2"
    assert "plan_artifact_package_id" not in prepared["anchor"]
    assert prepared["anchor"]["source_index_ref"] == (
        "artifacts/PRD-1/original-source-index.json"
    )
    assert prepared["failed_task_ids"] == []
    gap = prepared["rework_summary"]["gap_tasks"][0]
    assert gap["task_id"] == "TASK-REWORK-AC25-MODE-ISOLATION"
    assert gap["supersedes_task_ids"] == ["TASK-EVIDENCE"]
    assert "src/styles/mobility.css" in gap["claim_paths"]
    assert "src/mobility/MobilityApp.ts" in gap["claim_paths"]
    assert gap["verify_commands"] == ["npm run test", "npm run e2e"]

    amended = write_gap_task_map_amend_artifact(
        state_dir=state_dir,
        project_root=tmp_path,
        base_task_map_ref=current_ref,
        pdd_id="PRD-1",
        source_event_id="test-failed",
        gap_tasks=[gap],
    )
    body = json.loads(Path(amended["task_map_path"]).read_text(encoding="utf-8"))
    ids = [task["task_id"] for task in body["tasks"]]
    assert "TASK-EVIDENCE" not in ids
    assert "TASK-REWORK-AC25-MODE-ISOLATION" in ids
    audit = next(task for task in body["tasks"] if task["task_id"] == "TASK-AUDIT")
    assert audit["blocked_by"] == ["TASK-REWORK-AC25-MODE-ISOLATION"]


def test_rework_restores_latest_existing_source_index_for_same_workflow(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    valid_ref = "artifacts/PRD-1/planner/source-index.json"
    valid_path = state_dir / valid_ref
    valid_path.parent.mkdir(parents=True)
    valid_path.write_text("{}", encoding="utf-8")
    stale_workdir_ref = (
        state_dir / "workdirs/planner/project/artifacts/plan/source_index.json"
    )
    stale_workdir_ref.parent.mkdir(parents=True)
    stale_workdir_ref.write_text("{}", encoding="utf-8")
    events = [
        ZfEvent(
            id="valid-plan",
            type="task_map.ready",
            correlation_id="workflow-1",
            payload={
                "pdd_id": "PRD-1",
                "trace_id": "workflow-1",
                "source_index_ref": valid_ref,
            },
        ),
        ZfEvent(
            id="invalid-rework",
            type="task_map.ready",
            correlation_id="workflow-1",
            payload={
                "pdd_id": "PRD-1",
                "trace_id": "workflow-1",
                "source_index_ref": "artifacts/plan/source_index.json",
            },
        ),
    ]

    prepared = prepare_candidate_rework_scope(
        state_dir=state_dir,
        project_root=tmp_path,
        events=events,
        pdd_id="PRD-1",
        trace_id="workflow-1",
        source_event_id="test-failed",
        anchor={"source_index_ref": "artifacts/plan/source_index.json"},
        rework_paths=[],
        failed_task_ids=[],
        rework_summary={},
    )

    assert prepared["anchor"]["source_index_ref"] == valid_ref


def test_rework_drops_stale_plan_package_when_map_generation_already_rebound(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    events = [
        ZfEvent(
            id="current-map",
            type="task_map.ready",
            correlation_id="workflow-1",
            payload={
                "pdd_id": "PRD-1",
                "task_map_ref": "artifacts/PRD-1/current-task-map.json",
                "task_map_generation": "generation-2",
            },
        ),
    ]

    prepared = prepare_candidate_rework_scope(
        state_dir=state_dir,
        project_root=tmp_path,
        events=events,
        pdd_id="PRD-1",
        trace_id="workflow-1",
        source_event_id="test-failed",
        anchor={
            "task_map_ref": "artifacts/PRD-1/current-task-map.json",
            "task_map_generation": "generation-2",
            "plan_revision": "generation-1",
            "plan_artifact_package_id": "stale-package",
            "plan_artifact_package_ref": "artifacts/plan-packages/stale.json",
            "plan_artifact_package_digest": "a" * 64,
        },
        rework_paths=[],
        failed_task_ids=[],
        rework_summary={},
    )

    assert prepared["anchor"]["task_map_generation"] == "generation-2"
    assert "plan_revision" not in prepared["anchor"]
    assert "plan_artifact_package_id" not in prepared["anchor"]
    assert "plan_artifact_package_ref" not in prepared["anchor"]
    assert "plan_artifact_package_digest" not in prepared["anchor"]


def test_rework_requires_replan_when_only_root_id_is_outside_current_map(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    task_map_ref = "artifacts/PRD-1/task-map.json"
    task_map_path = state_dir / task_map_ref
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(json.dumps({
        "schema_version": "task-map.v1",
        "tasks": [_task("TASK-CHILD", ["src/**"])],
    }), encoding="utf-8")
    events = [ZfEvent(
        id="current-map",
        type="task_map.ready",
        correlation_id="workflow-1",
        payload={
            "pdd_id": "PRD-1",
            "task_map_ref": task_map_ref,
            "task_map_generation": "generation-2",
        },
    )]

    prepared = prepare_candidate_rework_scope(
        state_dir=state_dir,
        project_root=tmp_path,
        events=events,
        pdd_id="PRD-1",
        trace_id="workflow-1",
        source_event_id="late-failure",
        anchor={
            "task_map_ref": task_map_ref,
            "task_map_generation": "generation-2",
        },
        rework_paths=[],
        failed_task_ids=["PRD-1"],
        rework_summary={},
    )

    assert prepared["failed_task_ids"] == []
    assert prepared["requires_replan"] is True
    assert prepared["rework_summary"]["invalid_failed_task_ids"] == [
        "PRD-1"
    ]


def test_rework_replaces_terminal_path_owner_instead_of_redispatching_it(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    task_map_ref = "artifacts/PRD-1/task-map.json"
    task_map_path = state_dir / task_map_ref
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(json.dumps({
        "schema_version": "task-map.v1",
        "tasks": [_task("TASK-OWNER", ["scripts/release/**"])],
    }), encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-OWNER", title="Original owner"))
    store.update("TASK-OWNER", status="done")

    prepared = prepare_candidate_rework_scope(
        state_dir=state_dir,
        project_root=tmp_path,
        events=[],
        pdd_id="PRD-1",
        trace_id="workflow-1",
        source_event_id="verify-failed",
        anchor={
            "task_map_ref": task_map_ref,
            "task_map_generation": "generation-1",
            "source_commit": "a" * 40,
        },
        rework_paths=["scripts/release/check.mjs"],
        failed_task_ids=["TASK-OWNER"],
        rework_summary={},
    )

    assert prepared["failed_task_ids"] == []
    assert prepared["requires_replan"] is False
    assert prepared["rework_summary"]["terminal_path_owner_task_ids"] == [
        "TASK-OWNER"
    ]
    gap = prepared["rework_summary"]["gap_tasks"][0]
    assert gap["task_id"] == "TASK-REWORK-CANDIDATE-VERIFICATION-GAP"
    assert gap["supersedes_task_ids"] == ["TASK-OWNER"]
    assert gap["claim_paths"] == ["scripts/release/**"]


def test_unowned_diagnostic_does_not_invent_writer_task(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    ref = "artifacts/current-map.json"
    path = state_dir / ref
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": "task-map.v1", "tasks": [
        _task("TASK-API", ["ledger.py"], owner_role="dev-lane-0"),
        _task("TASK-CLI", ["ledger_cli.py"], owner_role="dev-lane-0", blocked_by=["TASK-API"]),
    ]}))
    prepared = prepare_candidate_rework_scope(
        state_dir=state_dir, project_root=tmp_path, events=[], pdd_id="PRD-1",
        trace_id="RUN-1", source_event_id="verification-failed",
        anchor={"task_map_ref": ref},
        rework_paths=["verification evidence capture"],
        failed_task_ids=["TASK-API"], rework_summary={},
    )
    assert prepared["requires_replan"] is True
    assert not prepared["rework_summary"].get("gap_tasks")
    assert prepared["rework_summary"]["unowned_rework_paths"] == ["verification evidence capture"]
