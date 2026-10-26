from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parent.parent


def test_zf_run_teardown_uses_stop_fast_without_direct_event_append():
    script = ROOT / "tests" / "e2e" / "scripts" / "zf_run_teardown.sh"
    text = script.read_text(encoding="utf-8")

    assert "stop --fast" in text
    assert ">> \"$STATE_ABS/events.jsonl\"" not in text
    assert "fallback-append" not in text


def test_scoped_fast_teardown_runbook_bans_process_name_kill():
    runbook = ROOT / "docs" / "runbooks" / "scoped-fast-teardown.md"
    text = runbook.read_text(encoding="utf-8")

    assert "zf stop --fast" in text
    assert "Do not use `pkill`" in text
    assert "No script should append directly to `events.jsonl`" in text


def test_three_workflow_e2e_defaults_to_controller_v3_with_explicit_legacy_mode():
    script = ROOT / "tests" / "e2e" / "scripts" / "run_prod_new_three_workflow_e2e.sh"
    text = script.read_text(encoding="utf-8")

    assert 'ZF_E2E_TEMPLATE_FAMILY:-controller-v3' in text
    assert 'controller/prd-fanout-v3.yaml' in text
    assert 'controller/issue-fanout-v3.yaml' in text
    assert 'controller/refactor-lane-v3.yaml' in text
    assert "controller-v3-claude)" in text
    assert "controller-v4)" in text
    assert "prd-task-pipeline-v4-canary.yaml" in text
    assert "controller-v4-claude)" in text
    assert "prd-task-pipeline-v4-canary-claude.yaml" in text
    assert "legacy-v2)" in text
    assert 'snapshot="$RUN_ROOT/refactor-source-${source_commit:0:12}"' in text
    assert 'git archive "${source_commit}:app"' in text
    assert 'source_root="app"' not in text
    assert "stop --include-run-manager --clean-workdirs" in text
    assert "prod_flow_terminal_audit.py" in text
    assert "wait_for_terminal_delivery" in text
    assert "--fail-on-human-escalate" in text
    assert "--fail-on-repeated-child-failure" in text
    assert "terminal_delivery" in text
    assert "wait_for_terminal()" not in text
    assert "prod_flow_context_audit.py" in text
    assert "ZF_E2E_ALLOW_DIRTY_SOURCE" in text
    assert "ZF_E2E_PREFLIGHT_ONLY" in text
    assert "ZF_E2E_ROLE_TRANSPORT" in text
    assert "ZF_E2E_TRANSPORT_TIMEOUT_SECONDS" in text
    assert "ZF_E2E_OPERATION_TIMEOUT_SECONDS" in text
    assert "ZF_E2E_TASK_PIPELINE_MODE" in text
    assert 'export ZF_TASK_PIPELINE_MODE="$TASK_PIPELINE_MODE"' in text
    assert 'export CLAUDE_CODE_EFFORT_LEVEL="$REASONING_EFFORT"' in text
    assert 'provider_session["effort"] = reasoning_effort' in text
    assert "rendered Task Pipeline mode mismatch" in text
    assert 'validate --cold-start' in text
    assert "# Product Pulse E2E Seed" in text
    assert "mkdir -p src tests" in text
    assert "src/.gitkeep" in text
    assert "commit_initialized_instruction_baseline" in text
    assert 'git status --porcelain=v1 --untracked-files=no' in text
    assert "event_type_count" in text
    assert 'event_type_count "$state_dir" loop.started' in text
    assert "loop_started_before" in text
    assert "zf start exited before session became ready" in text
    assert "source_clean" in text
    assert '"rendered_sha256"' in text
    assert "ZF_E2E_RUN_TIMEOUT_SECONDS" in text
    assert "ZF_E2E_RUN_TOKEN_BUDGET" in text
    assert "ZF_E2E_RUN_COST_BUDGET_USD" in text
    assert '"outer_timeout_seconds"' in text
    assert '"run_limits_override"' in text
    assert '"operation_limits_override"' in text
    assert '"task_pipeline_mode_override"' in text
    assert 'limits["timeout_seconds"] = int(operation_timeout_seconds)' in text
    assert 'run_limits["timeout_seconds"] = int(run_timeout_seconds)' in text
    assert 'run_limits["token_budget"] = int(run_token_budget)' in text
    assert 'run_limits["cost_budget_usd"] = float(run_cost_budget_usd)' in text
    assert "emit_run_simulation_done" in text
    assert "emit simulation.done" in text
    assert text.index("emit_run_simulation_done", text.index("record_run()")) < (
        text.index("append_report", text.index("record_run()"))
    )
    assert text.index("record_run passed") < text.index(
        'stop_runtime "$watcher_pid"', text.index("record_run passed")
    )


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        (
            "ZF_E2E_OPERATION_TIMEOUT_SECONDS",
            "86401",
            "must be an integer between 1 and 86400",
        ),
        (
            "ZF_E2E_RUN_TIMEOUT_SECONDS",
            "86401",
            "must be an integer between 1 and 86400",
        ),
        (
            "ZF_E2E_RUN_TOKEN_BUDGET",
            "100000001",
            "must be an integer between 1 and 100000000",
        ),
        (
            "ZF_E2E_RUN_COST_BUDGET_USD",
            "10000.01",
            "must be a number greater than 0 and at most 10000",
        ),
        (
            "ZF_E2E_TASK_PIPELINE_MODE",
            "enforced",
            "must be shadow or blocking",
        ),
    ],
)
def test_three_workflow_e2e_rejects_invalid_run_limit_overrides(
    variable: str,
    value: str,
    message: str,
) -> None:
    script = ROOT / "tests/e2e/scripts/run_prod_new_three_workflow_e2e.sh"
    env = os.environ.copy()
    env["ZF_E2E_ALLOW_DIRTY_SOURCE"] = "true"
    env[variable] = value

    result = subprocess.run(
        ["bash", str(script)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert message in result.stderr


def test_oa_proof_wrappers_keep_exact_source_and_policy_evidence():
    scripts = ROOT / "tests" / "e2e" / "scripts"
    four_flow = (scripts / "run_oa_clean_four_flow_e2e.sh").read_text(
        encoding="utf-8"
    )
    full_ab = (scripts / "run_oa_full_workflow_ab_e2e.sh").read_text(
        encoding="utf-8"
    )
    canary = (
        scripts / "run_oa_multiflow_blocking_canary_e2e.sh"
    ).read_text(encoding="utf-8")
    context_audit = (scripts / "prod_flow_context_audit.py").read_text(
        encoding="utf-8"
    )

    assert "oa_clean_four_flow_report.py" in four_flow
    assert "generic_workflow_real_provider_drill.py" in four_flow
    assert "run_prod_new_three_workflow_e2e.sh" in four_flow
    assert "oa_full_workflow_ab_report.py" in full_ab
    assert 'ZF_E2E_OA_PLAN_POLICY="$policy"' in full_ab
    assert "run_arm shadow shadow" in full_ab
    assert "run_arm blocking blocking" in full_ab
    assert "run_pair prd &" in canary
    assert "run_pair issue &" in canary
    assert "run_pair refactor &" in canary
    assert "run_general &" in canary
    assert 'ZF_E2E_EXECUTION_MODE:-serial' in canary
    assert 'if [[ "$EXECUTION_MODE" == "parallel" ]]' in canary
    assert '--execution-mode "$EXECUTION_MODE"' in canary
    assert "shadow arm failed; skipping invalid blocking comparison" in canary
    assert "--flow-kind \"$flow\"" in canary
    assert "oa_multiflow_blocking_canary_report.py" in canary
    assert "typed_task_contract_snapshots" in context_audit
    assert "oa_stage_card_or_explicit_skip" in context_audit


def test_three_workflow_e2e_pins_initialized_source_and_golden_manifest() -> None:
    script = (
        ROOT / "tests" / "e2e" / "scripts" / "run_prod_new_three_workflow_e2e.sh"
    ).read_text(encoding="utf-8")
    init_commit = script.index("if ! commit_initialized_instruction_baseline; then")
    captured_source = script.index(
        'source_commit="$(git rev-parse HEAD)"',
        init_commit,
    )

    assert captured_source > init_commit
    assert "product_source_tree" in script
    assert "baseline_manifest_sha256" in script
    assert 'BASELINE_MANIFEST="$fixture/manifest.json"' in script
    assert 'payload.get("comp_hash")' in script
    assert 'payload.get("multi_agent_version")' in script
    assert 'typ == "agent.usage"' in script
    assert '"context_windows": sorted(usage_context_windows)' in script


def test_product_fanout_manual_uses_parseable_start_and_stop_commands():
    for name in (
        "18-product-fanout-real-e2e.md",
        "18-product-fanout-real-e2e.en.md",
    ):
        text = (ROOT / "docs" / "manual" / name).read_text(encoding="utf-8")
        assert "zf start --path" not in text
        assert "zf stop --path" not in text


def test_teardown_fallback_uses_valid_payload_and_exact_session(tmp_path, monkeypatch):
    import json
    from zf.cli.main import main
    from zf.core.events.log import EventLog

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_path = tmp_path / "argv.json"
    tmux_path = tmp_path / "tmux-args.json"
    zf = bin_dir / "zf"
    zf.write_text(
        "#!/usr/bin/env python3\nimport json,sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1] == 'stop': sys.exit(1)\n"
        f"Path({str(argv_path)!r}).write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    tmux = bin_dir / "tmux"
    tmux.write_text(
        "#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\n"
        f"Path({str(tmux_path)!r}).write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    zf.chmod(0o755)
    tmux.chmod(0o755)
    state = tmp_path / "custom-state"
    state.mkdir()
    reason = 'verify "quoted" reason\nnext line'
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "ZF": str(zf), "ZF_STATE_DIR": str(state),
           "ZF_TMUX_SESSION": "zf-exact-test"}
    subprocess.run(
        ["bash", str(ROOT / "tests/e2e/scripts/zf_run_teardown.sh"), reason],
        cwd=tmp_path, env=env, check=True, capture_output=True, text=True,
    )
    args = json.loads(argv_path.read_text())
    monkeypatch.chdir(tmp_path)
    assert main([*args, "--state-dir", str(state)]) == 0
    events = EventLog(state / "events.jsonl").read_all()
    assert len(events) == 1
    assert events[0].type == "run.teardown"
    assert events[0].payload == {
        "reason": reason, "session": "zf-exact-test", "by": "zf-run-teardown",
    }
    assert json.loads(tmux_path.read_text()) == ["kill-session", "-t", "zf-exact-test"]
