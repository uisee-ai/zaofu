from __future__ import annotations

import json
from pathlib import Path

from zf.autoresearch.campaign import resolve_campaign, write_campaign_plan


FULL_VALIDATION_ORDER = [
    "controlled-stuck-recovery",
    "positive-pressure-4dev",
    "fail-rework-converge",
    "manual-intervention-guard",
    "self-eval-backlog",
    "spec-validate-hardening",
]

COMMON_METRICS = {
    "tasks_done",
    "expected_done",
    "fatal_count",
    "duplicate_success_event_count",
    "terminal_evidence_coverage",
}

COMMON_ASSERTIONS = {
    "report.md exists",
    "events-summary.json exists",
    "tasks_done >= expected_done",
    "fatal_count == 0",
    "duplicate_success_event_count == 0",
    "terminal_evidence_coverage == 1.0",
    "inner-runner.log has no runner fatal",
}

SCENARIO_ASSERTIONS = {
    "controlled-stuck-recovery": {
        "stuck_injection_satisfied == true",
        "worker_stuck_recovery_failed_count == 0",
    },
    "positive-pressure-4dev": {
        "len(dev_replicas_used) >= 4",
        "len(test_replicas_used) >= 1",
    },
    "fail-rework-converge": {
        "rework_signal_count >= 1 or task_done_blocked_count >= 1",
    },
    "manual-intervention-guard": {
        "manual terminal done without evidence is blocked or audited",
    },
    "self-eval-backlog": {
        "failed self-eval backlog creation is idempotent",
        "passing self-eval does not create backlog",
    },
    "spec-validate-hardening": {
        "verification literal hardening has regression tests and runtime evidence",
        "tdd_ref scope graph hardening has regression tests and runtime evidence",
    },
}


def test_full_validation_campaign_defines_all_builtin_scenarios() -> None:
    campaign = resolve_campaign("full-validation")

    assert [item.scenario for item in campaign.scenarios] == FULL_VALIDATION_ORDER
    assert "Phase 0" in campaign.description
    assert "Phase 1" in campaign.description
    assert "Phase 2" in campaign.description
    assert any("backlog" in item.lower() for item in campaign.pass_criteria)


def test_full_validation_plan_writes_common_and_specific_assertions(
    tmp_path: Path,
) -> None:
    paths = write_campaign_plan(
        campaign=resolve_campaign("full-validation"),
        output_dir=tmp_path / "plan",
        worktree_root=tmp_path / "worktrees",
        config_template=Path("examples/dev-codex-backends.yaml"),
        use_tmux=False,
    )

    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))

    assert payload["campaign"] == "full-validation"
    assert [item["scenario"] for item in payload["scenarios"]] == FULL_VALIDATION_ORDER
    for scenario in payload["scenarios"]:
        name = scenario["scenario"]
        assert COMMON_METRICS.issubset(set(scenario["metrics"]))
        assert COMMON_ASSERTIONS.issubset(set(scenario["hard_assertions"]))
        assert SCENARIO_ASSERTIONS[name].issubset(set(scenario["hard_assertions"]))
        assert scenario["budget_usd"] > 0
        assert scenario["timeout_seconds"] >= 5400
        assert scenario["expected_done"] >= 1

    stuck = next(
        item for item in payload["scenarios"]
        if item["scenario"] == "controlled-stuck-recovery"
    )
    assert "--inject-worker-stuck" in stuck["command"]
    assert "--backlog-on-failure" in stuck["command"]
    assert "--tmux" not in stuck["command"]

    markdown = paths.markdown_path.read_text(encoding="utf-8")
    assert "Hard assertions" in markdown
    assert "spec-validate-hardening" in markdown
