"""Task recovery must not inherit another task's partial branch from a map."""

import pytest

from tests.test_task_pipeline_partial_continuation import git, setup
from zf.core.events import ZfEvent
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.semantic_replan import enrich_semantic_replan_action
from zf.runtime.sidecar_refs import write_sidecar_json


def scenario(tmp_path, fault="", *, partial=False):
    state, log, writer, config, action, base, head = setup(
        tmp_path, "" if partial else "no_partial"
    )
    git(tmp_path, "checkout", "-qb", "worker/unrelated", base)
    (tmp_path / "unrelated.txt").write_text("unaccepted partial work\n")
    git(tmp_path, "add", "--", "unrelated.txt")
    git(tmp_path, "commit", "-qm", "unrelated partial")
    unrelated = git(tmp_path, "rev-parse", "HEAD")
    writer.emit(
        "task_map.amended",
        payload={
            "workflow_run_id": "run",
            "pdd_id": "FEATURE",
            "candidate_ref": "worker/unrelated",
            "candidate_head_commit": unrelated,
        },
    )
    git(tmp_path, "checkout", "-qb", "candidate/FEATURE", base)
    (tmp_path / "accepted.txt").write_text("accepted dependency\n")
    git(tmp_path, "add", "--", "accepted.txt")
    git(tmp_path, "commit", "-qm", "accepted dependency")
    candidate = git(tmp_path, "rev-parse", "HEAD")
    payload = {
        "schema_version": "task-integration-receipt.v1",
        "incremental": True,
        "status": "integrated",
        "workflow_run_id": "run",
        "task_id": "OTHER",
        "task_map_generation": "other-map",
        "candidate_branch": "candidate/FEATURE",
        "candidate_head": candidate,
        "candidate_generation": "cg",
        "operation_id": "integrate-other",
        "task_ref": "task/OTHER",
        "task_commit": candidate,
    }
    receipt = {
        **payload,
        "new_candidate_head": candidate,
        "integration_operation_id": "integrate-other",
    }
    if fault == "identity":
        receipt["workflow_run_id"] = "foreign"
    descriptor = write_sidecar_json(
        state,
        "artifacts/integration.json",
        receipt,
        kind="task_integration_receipt",
        schema_version="task-integration-receipt.v1",
        created_by="candidate-integrator",
        required=True,
    )
    payload.update(receipt_ref=descriptor, receipt_digest=descriptor["sha256"])
    if fault == "corrupt":
        (state / descriptor["ref"]).write_text("{}")
    if fault == "missing_receipt":
        payload.pop("receipt_ref")
    if fault == "run":
        payload["workflow_run_id"] = "another-run"
    if fault == "digest":
        payload["receipt_digest"] = "wrong"
    if fault == "unintegrated":
        payload["status"] = "failed"
    if fault == "branch":
        git(tmp_path, "update-ref", "refs/heads/candidate/FEATURE", unrelated)
    event = writer.append(
        ZfEvent(
            type="candidate.updated",
            task_id="OTHER",
            origin="worker" if fault == "origin" else "kernel",
            payload=payload,
        )
    )
    return state, log, writer, config, action, base, head, candidate, event


def test_run_manager_targets_current_incremental_candidate(tmp_path):
    state, log, writer, config, _, base, _, candidate, event = scenario(tmp_path)
    result = run_manager_tick(
        state_dir=state,
        event_log=log,
        writer=writer,
        config=config,
        project_root=tmp_path,
        spawn_repairs=False,
    )
    assert result.actions_applied == 1
    request = next(e for e in log.read_all() if e.type == "flow.discovery.requested")
    assert request.payload["target_ref"] == candidate
    assert request.payload["candidate_head_commit"] == candidate
    assert request.payload["candidate_event_id"] == event.id
    assert request.payload["base_commit"] == base
    assert request.payload["source_commit"] == base
    assert git(tmp_path, "show", candidate + ":accepted.txt") == "accepted dependency"


@pytest.mark.parametrize(
    "fault",
    [
        "identity",
        "corrupt",
        "missing_receipt",
        "run",
        "digest",
        "unintegrated",
        "branch",
        "origin",
    ],
)
def test_unproven_candidate_does_not_reuse_unrelated_partial_branch(tmp_path, fault):
    state, log, _, config, action, base, _, _, _ = scenario(tmp_path, fault)
    result = enrich_semantic_replan_action(
        action,
        state_dir=state,
        events=log.read_all(),
        config=config,
        project_root=tmp_path,
    )
    assert result["target_ref"] == base
    assert result["handoff_kind"] == "task_base_recovery"


def test_admitted_failed_task_partial_retains_priority(tmp_path):
    state, log, _, config, action, _, head, _, _ = scenario(tmp_path, partial=True)
    result = enrich_semantic_replan_action(
        action,
        state_dir=state,
        events=log.read_all(),
        config=config,
        project_root=tmp_path,
    )
    assert result["target_ref"] == head
    assert result["handoff_kind"] == "partial_task_continuation"


def test_candidate_behind_task_base_does_not_discard_newer_base(tmp_path):
    state, log, _, config, action, _, _, candidate, _ = scenario(tmp_path)
    git(tmp_path, "checkout", "-qb", "newer-base", candidate)
    (tmp_path / "newer.txt").write_text("new task base\n")
    git(tmp_path, "add", "--", "newer.txt")
    git(tmp_path, "commit", "-qm", "newer base")
    base = git(tmp_path, "rev-parse", "HEAD")
    events = log.read_all()
    for event in events:
        if event.task_id == "PART" and "base_commit" in event.payload:
            event.payload["base_commit"] = base
    result = enrich_semantic_replan_action(
        action, state_dir=state, events=events, config=config, project_root=tmp_path
    )
    assert result["target_ref"] == base
    assert result["handoff_kind"] == "task_base_recovery"
