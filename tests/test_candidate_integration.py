from __future__ import annotations

from types import SimpleNamespace

import pytest

from zf.core.config.loader import ConfigError, load_config
from zf.core.events.model import ZfEvent
from zf.runtime.candidate_integration import (
    candidate_failure_envelope,
    candidate_integration_identity,
)
from zf.runtime.rework_triage import classify_rework_trigger
from zf.runtime.report_evidence_gate import report_evidence_gap


def _missing_vite_candidate() -> dict:
    return {
        "status": "quality_failed",
        "commit": "a" * 40,
        "candidate_environment": {
            "status": "ready",
            "receipt_digest": "env-digest",
        },
        "quality": {
            "status": "failed",
            "gates_failed": ["static"],
            "gate_checks": {
                "static": [{
                    "command": "npm run build",
                    "exit_code": 127,
                    "stderr_tail": "sh: 1: vite: not found",
                }],
            },
        },
    }


def test_missing_candidate_dependency_is_structured_environment_failure() -> None:
    envelope = candidate_failure_envelope(
        _missing_vite_candidate(),
        failed_children=[],
    )

    assert envelope["failure_class"] == "candidate_dependency_missing"
    assert envelope["failure_scope"] == "candidate"
    assert envelope["failing_command"] == "npm run build"
    assert envelope["exit_code"] == 127
    assert "vite: not found" in envelope["diagnostic_summary"]

    config = SimpleNamespace(
        workflow=SimpleNamespace(rework_routing={"integration.failed": "planner"}),
    )
    triage = classify_rework_trigger(ZfEvent(
        type="integration.failed",
        payload=envelope,
    ), config)
    assert triage.classification == "environment_issue"
    assert triage.suspected_owner == "operator"


def test_candidate_integration_attempt_identity_changes_with_environment() -> None:
    candidate = _missing_vite_candidate()
    first = candidate_integration_identity(
        run_id="run-1",
        task_map_generation="generation-1",
        fanout_id="fanout-1",
        candidate_payload=candidate,
    )
    second = candidate_integration_identity(
        run_id="run-1",
        task_map_generation="generation-1",
        fanout_id="fanout-1",
        candidate_payload=candidate,
    )
    changed = {**candidate, "candidate_environment": {"receipt_digest": "env-fixed"}}
    third = candidate_integration_identity(
        run_id="run-1",
        task_map_generation="generation-1",
        fanout_id="fanout-1",
        candidate_payload=changed,
    )

    assert first == second
    assert third["integration_attempt_id"] != first["integration_attempt_id"]


def test_candidate_rebuild_head_timestamp_does_not_change_attempt_identity() -> None:
    candidate = {
        **_missing_vite_candidate(),
        "base_commit": "base",
        "included_tasks": [{
            "task_id": "T1",
            "task_ref": "refs/heads/worker/T1",
            "source_commit": "source-1",
        }],
    }
    first = candidate_integration_identity(
        run_id="run-1",
        task_map_generation="generation-1",
        fanout_id="fanout-1",
        candidate_payload=candidate,
    )
    second = candidate_integration_identity(
        run_id="run-1",
        task_map_generation="generation-1",
        fanout_id="fanout-1",
        candidate_payload={**candidate, "commit": "b" * 40},
    )

    assert first == second


def test_candidate_identity_and_failure_fingerprint_ignore_volatile_npm_logs() -> None:
    candidate = {
        "status": "quality_failed",
        "base_commit": "base-1",
        "commit": "a" * 40,
        "included_tasks": [{
            "task_id": "T1",
            "task_ref": "refs/heads/worker/T1",
            "source_commit": "source-1",
        }],
        "candidate_environment": {"status": "ready", "setup_ran": False},
        "quality": {
            "status": "failed",
            "gate_source": "task_contract",
            "gates_run": ["task_contract:T1"],
            "gates_failed": ["task_contract:T1"],
            "gate_checks": {
                "task_contract:T1": [{
                    "command": "npm --prefix app run typecheck",
                    "exit_code": 1,
                    "stderr_tail": (
                        'npm error Missing script: "typecheck"\n'
                        "npm error log: /home/user/.npm/_logs/2026-07-21T01_02_03_001Z-debug-0.log"
                    ),
                    "duration_ms": 15,
                }],
            },
        },
    }
    replay = {
        **candidate,
        "commit": "b" * 40,
        "quality": {
            **candidate["quality"],
            "gate_checks": {
                "task_contract:T1": [{
                    "command": "npm --prefix app run typecheck",
                    "exit_code": 1,
                    "stderr_tail": (
                        'npm error Missing script: "typecheck"\n'
                        "npm error log: /home/user/.npm/_logs/2026-07-21T09_08_07_999Z-debug-0.log"
                    ),
                    "duration_ms": 99,
                }],
            },
        },
    }

    first_identity = candidate_integration_identity(
        run_id="run-1",
        task_map_generation="generation-1",
        fanout_id="fanout-1",
        candidate_payload=candidate,
    )
    replay_identity = candidate_integration_identity(
        run_id="run-1",
        task_map_generation="generation-1",
        fanout_id="fanout-1",
        candidate_payload=replay,
    )
    first_failure = candidate_failure_envelope(candidate, failed_children=[])
    replay_failure = candidate_failure_envelope(replay, failed_children=[])

    assert replay_identity == first_identity
    assert first_failure["failure_fingerprint"] == replay_failure["failure_fingerprint"]
    assert first_failure["failure_class"] == "candidate_quality_gate_contract_mismatch"
    triage = classify_rework_trigger(ZfEvent(
        type="integration.failed",
        payload=first_failure,
    ))
    assert triage.classification == "design_issue"
    assert triage.suspected_owner == "planner"


def test_precommit_block_is_not_classified_as_integration_conflict() -> None:
    envelope = candidate_failure_envelope({
        "status": "conflict",
        "error": "git commit failed: pre-commit BLOCK: staged 26 files",
        "conflict_files": [],
    }, failed_children=[])

    assert envelope["failure_class"] == "candidate_integration_failure"
    config = SimpleNamespace(
        workflow=SimpleNamespace(rework_routing={"integration.failed": "planner"}),
    )
    triage = classify_rework_trigger(ZfEvent(
        type="integration.failed",
        payload=envelope,
    ), config)
    assert triage.classification == "product_issue"
    assert triage.recommended_action == "dispatch_rework"


def test_real_merge_conflict_remains_plan_level() -> None:
    envelope = candidate_failure_envelope({
        "status": "conflict",
        "error": "git cherry-pick failed: merge conflict",
        "conflict_files": ["src/shared.py"],
    }, failed_children=[])

    assert envelope["failure_class"] == "candidate_integration_conflict"


def test_runtime_evidence_refs_are_accepted() -> None:
    assert report_evidence_gap({
        "status": "passed",
        "runtime_evidence_refs": ["artifacts/runtime/probe.json"],
    }) == ""


def test_flat_project_setup_key_is_rejected(tmp_path) -> None:
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        "version: '1.0'\n"
        "project:\n"
        "  name: demo\n"
        "  setup_script: npm ci\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"project\.scripts\.setup"):
        load_config(config_path)
