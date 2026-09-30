"""S8 TestKit wrapper for the repository's deterministic mock workflow."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from tests.test_candidates import (
    _add_task,
    _approve,
    _git,
    _init_repo,
    _record_task_ref,
    _rebuilder,
    _state,
    _task_commit,
)
from tests.e2e.test_generic_workflow_complex_mock_e2e import (
    run_generic_workflow_complex_scenario,
)
from tests.testkit.evidence import evidence_ref
from tests.testkit.oracles import Verdict
from tests.testkit.report import (
    TestResult,
    summarize_results,
    write_evidence_index,
)
from tests.test_product_acceptance import (
    GENERATION,
    RUN_ID,
    _adapted,
    _package,
    _report,
)
from tests.test_plan_candidate_preflight import (
    _product_acceptance_spec,
    _source_index,
    _task_map,
)
from zf.core.events.writer import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight
from zf.runtime.product_acceptance import bind_product_acceptance_report


def test_mock_workflow_result_is_bound_to_structured_testkit_evidence(
    tmp_path: Path,
) -> None:
    result = run_generic_workflow_complex_scenario(tmp_path)

    assert result["schema_version"] == "generic-workflow-complex-scenario.v1"
    assert result["stage_graph"] == [
        "scope",
        "collect-a",
        "collect-b",
        "synthesize",
        "verify",
    ]
    assert result["dossier_status"] == "ready"
    assert result["required_artifact_refs"]

    result_path = tmp_path / "test-result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    evidence = evidence_ref(result_path, root=tmp_path)
    repo_root = Path(__file__).resolve().parents[2]
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    test_result = TestResult(
        case_id="s8-generic-workflow-mock",
        verdict=Verdict.PASS,
        commit=commit,
        command_id="generic-workflow-mock",
        evidence=[evidence],
        details={
            "workflow_run_id": result["workflow_run_id"],
            "terminal_event_id": result["terminal_event_id"],
            "simulation_event_id": result["simulation_event_id"],
            "effective_config_digest": result["effective_config_digest"],
        },
    )
    index = tmp_path / "evidence-index.json"
    write_evidence_index(
        index,
        run_id=result["workflow_run_id"],
        commit=commit,
        results=[test_result],
    )

    payload = json.loads(index.read_text(encoding="utf-8"))
    assert payload["verdict"] == "pass"
    assert payload["target_commit"] == commit
    assert summarize_results([test_result]) is Verdict.PASS
    assert payload["results"][0]["evidence"][0]["ref"] == "test-result.json"


def test_candidate_gate_rejects_missing_product_acceptance_evidence(
    tmp_path: Path,
) -> None:
    package, spec_descriptor = _package(tmp_path)
    operation = {
        "output_profile_id": "candidate-verify",
        "result_identity": {
            "workflow_run_id": "workflow-product-1",
            "task_map_generation": "generation-product-1",
            "candidate_ref": "candidate/product-1",
            "target_commit": "c" * 40,
            "plan_artifact_package_ref": package["ref"],
            "plan_artifact_package_digest": package["sha256"],
        },
    }
    payload = _adapted(_report(spec_descriptor))
    payload.payload.pop("product_acceptance_report")

    bound = bind_product_acceptance_report(
        payload,
        state_dir=tmp_path,
        operation=operation,
        source_event_id="s8-missing-evidence",
    )

    assert bound.issues
    assert bound.issues[-1]["code"] == "product_acceptance_invalid"


def test_complete_acceptance_binding_is_candidate_gate_input(tmp_path: Path) -> None:
    """A complete mock acceptance report becomes verifiable gate evidence."""
    package, spec_descriptor = _package(tmp_path)
    operation = {
        "output_profile_id": "candidate-verify",
        "result_identity": {
            "workflow_run_id": "workflow-product-1",
            "task_map_generation": "generation-product-1",
            "candidate_ref": "candidate/product-1",
            "target_commit": "c" * 40,
            "plan_artifact_package_ref": package["ref"],
            "plan_artifact_package_digest": package["sha256"],
        },
    }
    bound = bind_product_acceptance_report(
        _adapted(_report(spec_descriptor)),
        state_dir=tmp_path,
        operation=operation,
        source_event_id="s8-complete-acceptance",
    )

    assert bound.issues == ()
    assert bound.payload["product_acceptance_verdict"] == "passed"
    assert bound.payload["product_acceptance_spec_ref"] == spec_descriptor["ref"]
    assert bound.payload["product_acceptance_report_ref"]
    assert bound.payload["product_acceptance_report_digest"]
    assert bound.payload["workflow_run_id"] == operation["result_identity"][
        "workflow_run_id"
    ]
    assert bound.payload["target_commit"] == operation["result_identity"][
        "target_commit"
    ]


def test_product_acceptance_binding_replay_is_idempotent_after_restart_boundary(
    tmp_path: Path,
) -> None:
    """Re-admitting the same report preserves one content-addressed identity."""
    package, spec_descriptor = _package(tmp_path)
    operation = {
        "output_profile_id": "candidate-verify",
        "result_identity": {
            "workflow_run_id": "workflow-product-1",
            "task_map_generation": "generation-product-1",
            "candidate_ref": "candidate/product-1",
            "target_commit": "c" * 40,
            "plan_artifact_package_ref": package["ref"],
            "plan_artifact_package_digest": package["sha256"],
        },
    }
    adapted = _adapted(_report(spec_descriptor))
    first = bind_product_acceptance_report(
        adapted,
        state_dir=tmp_path,
        operation=operation,
        source_event_id="s8-replay",
    )
    second = bind_product_acceptance_report(
        _adapted(_report(spec_descriptor)),
        state_dir=tmp_path,
        operation=operation,
        source_event_id="s8-replay",
    )

    assert first.issues == ()
    assert second.issues == ()
    assert first.payload["product_acceptance_report_ref"] == second.payload[
        "product_acceptance_report_ref"
    ]
    assert first.payload["product_acceptance_report_digest"] == second.payload[
        "product_acceptance_report_digest"
    ]
    reports = list((tmp_path / "artifacts" / "product-acceptance" / "reports").glob("*.json"))
    assert len(reports) == 1


def test_candidate_creation_emits_commit_bound_manifest_and_events(
    tmp_path: Path,
) -> None:
    """The S8 gate observes an actual local candidate, not only a fake ref."""
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    source_commit = _task_commit(
        tmp_path,
        branch="worker/TASK-S8-CANDIDATE",
        file_name="feature.txt",
        content="candidate feature\n",
        message="TASK-S8-CANDIDATE",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-S8-CANDIDATE",
        commit=source_commit,
        branch="worker/TASK-S8-CANDIDATE",
        changed_files=["feature.txt"],
    )
    _add_task(state_dir, log, task_id="TASK-S8-CANDIDATE")
    _approve(log, "TASK-S8-CANDIDATE")

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=EventWriter(log),
    )

    assert result is not None
    assert result.status == "updated", result.payload
    candidate_ref = "candidate/F-11111111"
    candidate_commit = _git(tmp_path, "rev-parse", candidate_ref)
    assert candidate_commit
    assert _git(tmp_path, "show", f"{candidate_ref}:feature.txt") == (
        "candidate feature"
    )
    manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text(
            encoding="utf-8",
        )
    )
    assert manifest["status"] == "updated"
    assert manifest["base_ref"] == "main"
    assert manifest["commit_lineage"][0]["source_commit"] == source_commit
    assert manifest["commit_lineage"][0]["candidate_commit"] == candidate_commit
    assert {event.type for event in log.read_all()} >= {
        "candidate.started",
        "candidate.updated",
        "candidate.integration.completed",
    }


def test_actual_candidate_commit_binds_product_acceptance_and_rejects_stale(
    tmp_path: Path,
) -> None:
    """Acceptance evidence must bind the commit produced by CandidateRebuilder."""

    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    source_commit = _task_commit(
        tmp_path,
        branch="worker/TASK-S8-ACCEPTANCE",
        file_name="feature.txt",
        content="accepted feature\n",
        message="TASK-S8-ACCEPTANCE",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-S8-ACCEPTANCE",
        commit=source_commit,
        branch="worker/TASK-S8-ACCEPTANCE",
        changed_files=["feature.txt"],
    )
    _add_task(state_dir, log, task_id="TASK-S8-ACCEPTANCE")
    _approve(log, "TASK-S8-ACCEPTANCE")
    rebuilt = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=EventWriter(log),
    )
    assert rebuilt is not None and rebuilt.status == "updated", rebuilt
    candidate_commit = _git(tmp_path, "rev-parse", "candidate/F-11111111")

    _package_payload, spec_descriptor = _package(state_dir)
    report = _report(spec_descriptor)
    report["candidate_ref"] = "candidate/F-11111111"
    report["target_commit"] = candidate_commit
    operation = {
        "output_profile_id": "candidate-verify",
        "result_identity": {
            "workflow_run_id": RUN_ID,
            "task_map_generation": GENERATION,
            "candidate_ref": "candidate/F-11111111",
            "target_commit": candidate_commit,
            "plan_artifact_package_ref": _package_payload["ref"],
            "plan_artifact_package_digest": _package_payload["sha256"],
        },
    }
    adapted = _adapted(report)
    adapted.payload["candidate_ref"] = "candidate/F-11111111"
    adapted.payload["target_commit"] = candidate_commit
    bound = bind_product_acceptance_report(
        adapted,
        state_dir=state_dir,
        operation=operation,
        source_event_id="s8-actual-candidate-acceptance",
    )
    assert bound.issues == ()
    assert bound.payload["target_commit"] == candidate_commit
    assert bound.payload["candidate_ref"] == "candidate/F-11111111"

    stale = dict(report)
    stale["target_commit"] = source_commit
    stale_adapted = _adapted(stale)
    stale_adapted.payload["candidate_ref"] = "candidate/F-11111111"
    stale_adapted.payload["target_commit"] = candidate_commit
    stale_bound = bind_product_acceptance_report(
        stale_adapted,
        state_dir=state_dir,
        operation=operation,
        source_event_id="s8-stale-candidate-acceptance",
    )
    assert stale_bound.issues
    assert stale_bound.issues[-1]["code"] == "product_acceptance_invalid"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZF-TEST-006: CandidateRebuilder replay must preserve candidate commit "
        "identity; current scoped cherry-pick commit uses wall-clock metadata"
    ),
)
def test_candidate_rebuild_replay_after_restart_preserves_commit_lineage(
    tmp_path: Path,
) -> None:
    """A fresh rebuilder can replay the same candidate without drift."""
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    source_commit = _task_commit(
        tmp_path,
        branch="worker/TASK-S8-REPLAY",
        file_name="replay.txt",
        content="replay candidate\n",
        message="TASK-S8-REPLAY",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-S8-REPLAY",
        commit=source_commit,
        branch="worker/TASK-S8-REPLAY",
        changed_files=["replay.txt"],
    )
    _add_task(state_dir, log, task_id="TASK-S8-REPLAY")
    _approve(log, "TASK-S8-REPLAY")

    first = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111", event_writer=EventWriter(log)
    )
    assert first is not None and first.status == "updated", first
    first_commit = _git(tmp_path, "rev-parse", "candidate/F-11111111")
    first_manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )

    # New objects model a process restart while preserving the canonical
    # state directory and temporary git repository.
    # Cross a git commit timestamp second so a wall-clock-derived commit hash
    # cannot accidentally make this detector pass.
    time.sleep(1.1)
    restarted_log = EventLog(state_dir / "events.jsonl")
    second = _rebuilder(tmp_path, state_dir, config, restarted_log).rebuild(
        "F-11111111", event_writer=EventWriter(restarted_log)
    )
    assert second is not None and second.status == "updated", second
    second_commit = _git(tmp_path, "rev-parse", "candidate/F-11111111")
    second_manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert second_commit == first_commit
    assert second_manifest["status"] == "updated"
    assert second_manifest["commit_lineage"] == first_manifest["commit_lineage"]
    assert second_manifest["commit_lineage"][0]["source_commit"] == source_commit
    assert not any(
        event.type in {"candidate.conflict", "candidate.quality_failed"}
        for event in restarted_log.read_all()
    )


@pytest.mark.parametrize(
    "restart_before_candidate",
    [False, True],
    ids=["single-process", "after-restart"],
)
def test_candidate_preflight_acceptance_and_creation_share_one_workflow_identity(
    tmp_path: Path,
    restart_before_candidate: bool,
) -> None:
    """The candidate gate must connect plan admission to a real candidate.

    This is intentionally a cross-boundary case: the plan preflight, Product
    Acceptance binding, and CandidateRebuilder each run independently but
    must agree on the same workflow run, generation, candidate ref, and
    candidate commit.
    """

    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    workflow_run_id = "workflow-product-1"
    task_map_generation = "generation-product-1"

    preflight = evaluate_plan_candidate_preflight(
        state_dir=state_dir,
        project_root=tmp_path,
        reports=[{
            "report": {
                "task_map": _task_map(),
                "source_index": _source_index(),
                "source_index_ref": "inline:source-index",
                "plan_md": "# Candidate plan\n\nImplement TASK-1.\n",
                "plan_ports": [{
                    "logical_name": "product_acceptance_spec",
                    "body": _product_acceptance_spec(),
                }],
            },
        }],
        manifest={
            "fanout_id": "fanout-product-1",
            "stage_id": "prd-plan",
            "workflow_run_id": workflow_run_id,
            "task_map_generation": task_map_generation,
            "trigger_payload": {
                "flow_kind": "prd",
                "prd_ref": "docs/requirement.md",
            },
        },
        metadata={
            "flow_kind": "prd",
            "artifact_package": {"required_ports": [
                "requirement_spec",
                "goal_claim_set",
                "task_map",
                "planning_result",
                "product_acceptance_spec",
            ]},
        },
    )
    assert preflight["status"] == "passed", preflight
    assert preflight["summary"]["error_count"] == 0

    if restart_before_candidate:
        # Re-open the canonical ledger and construct all candidate services
        # after the preflight boundary, modelling a process restart without
        # changing the project root, state dir or workflow identity.
        log.close()
        log = EventLog(state_dir / "events.jsonl")

    source_commit = _task_commit(
        tmp_path,
        branch="worker/TASK-S8-INTEGRATED",
        file_name="integrated.txt",
        content="integrated candidate\n",
        message="TASK-S8-INTEGRATED",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-S8-INTEGRATED",
        commit=source_commit,
        branch="worker/TASK-S8-INTEGRATED",
        changed_files=["integrated.txt"],
    )
    _add_task(state_dir, log, task_id="TASK-S8-INTEGRATED")
    _approve(log, "TASK-S8-INTEGRATED")
    candidate_result = _rebuilder(
        tmp_path,
        state_dir,
        config,
        log,
    ).rebuild("F-11111111", event_writer=EventWriter(log))
    assert candidate_result is not None
    assert candidate_result.status == "updated", candidate_result.payload
    candidate_ref = "candidate/F-11111111"
    candidate_commit = _git(tmp_path, "rev-parse", candidate_ref)

    if restart_before_candidate:
        # Acceptance binding is a second recovery boundary: read the same
        # candidate and event ledger through fresh objects before admitting it.
        log.close()
        log = EventLog(state_dir / "events.jsonl")

    package, spec_descriptor = _package(state_dir)
    operation = {
        "output_profile_id": "candidate-verify",
        "result_identity": {
            "workflow_run_id": workflow_run_id,
            "task_map_generation": task_map_generation,
            "candidate_ref": candidate_ref,
            "target_commit": candidate_commit,
            "plan_artifact_package_ref": package["ref"],
            "plan_artifact_package_digest": package["sha256"],
        },
    }
    adapted = _adapted(_report(spec_descriptor))
    adapted.payload["product_acceptance_report"].update({
        "workflow_run_id": workflow_run_id,
        "task_map_generation": task_map_generation,
        "candidate_ref": candidate_ref,
        "target_commit": candidate_commit,
    })
    adapted.payload.update({
        "workflow_run_id": workflow_run_id,
        "task_map_generation": task_map_generation,
        "candidate_ref": candidate_ref,
        "target_commit": candidate_commit,
        "plan_artifact_package_ref": package["ref"],
        "plan_artifact_package_digest": package["sha256"],
    })
    bound = bind_product_acceptance_report(
        adapted,
        state_dir=state_dir,
        operation=operation,
        source_event_id="s8-integrated-acceptance",
    )

    assert bound.issues == ()
    assert bound.payload["workflow_run_id"] == workflow_run_id
    assert bound.payload["task_map_generation"] == task_map_generation
    assert bound.payload["candidate_ref"] == candidate_ref
    assert bound.payload["target_commit"] == candidate_commit
    assert bound.payload["product_acceptance_verdict"] == "passed"
    assert bound.payload["product_acceptance_report_ref"]
    manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text(
            encoding="utf-8",
        )
    )
    assert manifest["commit_lineage"][0]["source_commit"] == source_commit
    assert manifest["commit_lineage"][0]["candidate_commit"] == candidate_commit
    assert {
        event.type
        for event in log.read_all()
    } >= {"candidate.started", "candidate.updated", "candidate.integration.completed"}
