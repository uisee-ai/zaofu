from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

from tests.e2e import run_mixed, run_star
from tests.e2e import robustness_suite
from tests.e2e.scripted_runner import run_scripted_scenario


def test_scenario_roles_are_explicit():
    assert robustness_suite.SCENARIOS["codex"].description == (
        "Codex isolation smoke"
    )
    assert robustness_suite.SCENARIOS["mixed"].description == (
        "Mixed-backend E2E stress entry"
    )
    assert robustness_suite.SCENARIOS["codex"].tasks == 1
    assert robustness_suite.SCENARIOS["mixed"].tasks == 3
    assert robustness_suite.SCENARIOS["codex"].timeout_s == 1800


def test_codex_topology_assertion_tracks_critic_role():
    result = robustness_suite.assert_config_topology()

    assert result.ok, result.detail


def test_backlog_coverage_manifest_has_all_nine_groups():
    assert len(robustness_suite.BACKLOG_TEST_GROUPS) == 9
    names = [group.backlog for group in robustness_suite.BACKLOG_TEST_GROUPS]
    assert any("0617" in name for name in names)
    assert any("0618" in name for name in names)
    assert any("0619" in name for name in names)
    assert any("0620" in name for name in names)
    assert any("0621" in name for name in names)
    assert any("0622" in name for name in names)
    assert any("0623" in name for name in names)
    assert any("0624" in name for name in names)
    assert any("0625" in name for name in names)
    assert all(group.pytest_targets for group in robustness_suite.BACKLOG_TEST_GROUPS)


def test_materialize_mixed_workspace_uses_dev_mixed_config(tmp_path: Path):
    root = robustness_suite.materialize_workspace(
        robustness_suite.SCENARIOS["mixed"],
        worktree=tmp_path / "mixed",
        clean=True,
    )

    data = yaml.safe_load((root / "zf.yaml").read_text(encoding="utf-8"))
    assert data["preset"] == "dev-mixed-backends"
    assert data["session"]["tmux_session"].startswith("zf-mixed-")
    assert data["global_budget_usd"] == 100.0
    assert "cost" not in data

    kanban = json.loads((root / ".zf" / "kanban.json").read_text())
    features = json.loads((root / ".zf" / "feature_list.json").read_text())
    assert kanban == []
    assert features == []
    assert (root / "src" / "__init__.py").exists()
    assert (root / "tests" / "test_smoke_fixture.py").exists()
    web_package = json.loads((root / "web" / "package.json").read_text())
    assert web_package["scripts"] == {
        "typecheck": "node scripts/typecheck.mjs",
        "test": "node scripts/test.mjs",
    }


def test_materialized_smoke_fixture_supports_src_package_imports(
    tmp_path: Path,
):
    root = robustness_suite.materialize_workspace(
        robustness_suite.SCENARIOS["codex"],
        worktree=tmp_path / "codex",
        clean=True,
    )
    (root / "src" / "greet.py").write_text(
        "def hello(name):\n"
        "    return f'Hello, {name}!'\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_greet.py").write_text(
        "from src.greet import hello\n\n"
        "def test_hello():\n"
        "    assert hello('Ada') == 'Hello, Ada!'\n",
        encoding="utf-8",
    )

    focused = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_greet.py", "-q"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    full_gate = subprocess.run(
        ["bash", "-lc", "PYTHONPATH=src pytest -q"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    assert focused.returncode == 0, focused.stdout
    assert full_gate.returncode == 0, full_gate.stdout


def test_real_provider_requires_explicit_confirmation(capsys):
    rc = robustness_suite.main(["--include-real", "mixed"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "--confirm-real" in captured.err


def test_real_runner_cleanup_is_scoped_to_worktree_tmux_session(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "run"
    (root / ".zf").mkdir(parents=True)
    mixed_calls: list[list[str]] = []
    star_calls: list[list[str]] = []

    def mixed_run(cmd: list[str], **_kwargs) -> subprocess.CompletedProcess:
        mixed_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    def star_run(cmd: list[str], **_kwargs) -> subprocess.CompletedProcess:
        star_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(run_mixed, "_run", mixed_run)
    # run_star imports _kill_lingering from star_smoke_support, so the
    # patch must target that module's _run, not run_star's.
    from tests.e2e import star_smoke_support

    monkeypatch.setattr(star_smoke_support, "_run", star_run)

    run_mixed._kill_lingering(root, session_name="zf-smoke")
    run_star._kill_lingering(root, session_name="zf-star")

    assert ["tmux", "kill-session", "-t", "zf-smoke"] in mixed_calls
    assert ["tmux", "kill-session", "-t", "zf-star"] in star_calls
    assert not any(call and call[0] == "pkill" for call in mixed_calls)
    assert not any(call and call[0] == "pkill" for call in star_calls)


def test_scorecard_counts_events_cost_and_archives(tmp_path: Path):
    state = tmp_path / ".zf"
    state.mkdir()
    events = [
        {
            "type": "agent.usage",
            "payload": {"backend": "codex"},
        },
        {
            "type": "task.status_changed",
            "payload": {"to": "done"},
        },
        {
            "type": "feature.status_changed",
            "payload": {"to": "done"},
        },
        {
            "type": "hook.orphan_event",
            "payload": {},
        },
        {
            "type": "worker.stuck",
            "payload": {},
        },
        {
            "type": "worker.orphaned",
            "payload": {},
        },
        {
            "type": "task.orphan_warning",
            "payload": {},
        },
    ]
    (state / "events.jsonl").write_text(
        "\n".join(json.dumps(event, separators=(",", ":")) for event in events)
        + "\n",
        encoding="utf-8",
    )
    (state / "cost.jsonl").write_text(
        json.dumps({"backend": "codex", "cost_usd": 1.0}) + "\n",
        encoding="utf-8",
    )
    (state / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state / "role_sessions.yaml").write_text("{}\n", encoding="utf-8")
    (state / "session.yaml").write_text("session_id: x\n", encoding="utf-8")
    (state / "kanban").mkdir()
    (state / "feature_list").mkdir()

    scorecard = robustness_suite.build_scorecard(
        state_dir=state,
        scenario="mixed",
        preset="dev-mixed-backends",
    )
    out = robustness_suite.write_scorecard(scorecard, state / "scorecard.json")

    assert scorecard.event_count == 7
    assert scorecard.cost_entries == 1
    assert scorecard.task_done_count == 1
    assert scorecard.feature_done_count == 1
    assert scorecard.backend_usage == {"codex": 1}
    assert scorecard.hook_orphan_count == 1
    assert scorecard.worker_stuck_count == 1
    assert scorecard.worker_orphan_count == 1
    assert scorecard.task_orphan_count == 1
    assert scorecard.stuck_orphan_count == 4
    assert scorecard.total_cost_usd == 1.0
    assert scorecard.critic_coverage_rate == 1.0
    assert scorecard.design_rework_recovery_rate == 1.0
    assert scorecard.codex_hook_count == 0
    assert scorecard.status == "unknown"
    assert all(scorecard.artifact_completeness.values())
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["scenario"] == "mixed"
    assert data["hook_orphan_count"] == 1


def test_archive_run_writes_standard_artifact_shape(tmp_path: Path):
    run = run_scripted_scenario(
        tmp_path / "run",
        scenario="happy_path",
        tasks_count=2,
    )
    dest = robustness_suite.archive_run(
        state_dir=run.state_dir,
        dest=tmp_path / "archive",
        scenario="scripted-happy",
        preset="scripted",
        status="pass",
        exit_code=0,
    )

    expected = {
        "events.jsonl",
        "cost.jsonl",
        "kanban_active.json",
        "feature_active.json",
        "role_sessions.yaml",
        "session.yaml",
        "phase_report.txt",
        "cost_by_backend.txt",
        "scorecard.json",
        "postmortem.md",
    }
    assert expected <= {path.name for path in dest.iterdir() if path.is_file()}
    assert (dest / "kanban_archive").is_dir()
    assert (dest / "feature_archive").is_dir()

    scorecard = json.loads((dest / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["scenario"] == "scripted-happy"
    assert scorecard["status"] == "pass"
    assert scorecard["task_done_count"] == 2
    assert scorecard["feature_done_count"] == 1
    assert scorecard["backend_usage"] == {"scripted": 12}
    assert scorecard["critic_coverage_rate"] == 1.0
    assert all(scorecard["artifact_completeness"].values())
    assert "Ship: pass" in (dest / "phase_report.txt").read_text(encoding="utf-8")
    assert "scripted" in (dest / "cost_by_backend.txt").read_text(encoding="utf-8")
    assert robustness_suite.scorecard_acceptance_issues(
        robustness_suite.build_scorecard(
            state_dir=dest,
            scenario="scripted-happy",
            preset="scripted",
            status="pass",
        ),
        expected_done=2,
    ) == []


def test_scorecard_can_be_rebuilt_from_archived_files_only(tmp_path: Path):
    run = run_scripted_scenario(
        tmp_path / "run",
        scenario="happy_path",
    )
    dest = robustness_suite.archive_run(
        state_dir=run.state_dir,
        dest=tmp_path / "archive",
        scenario="scripted",
        preset="scripted",
        status="pass",
    )

    rebuilt = robustness_suite.build_scorecard(
        state_dir=dest,
        scenario="scripted",
        preset="scripted",
        status="pass",
    )

    assert rebuilt.task_count == 1
    assert rebuilt.task_done_count == 1
    assert rebuilt.feature_done_count == 1
    assert rebuilt.backend_usage == {"scripted": 6}
    assert rebuilt.critic_coverage_rate == 1.0
    assert all(rebuilt.artifact_completeness.values())


def test_scorecard_acceptance_rejects_missing_required_events(tmp_path: Path):
    state = tmp_path / ".zf"
    state.mkdir()
    (state / "events.jsonl").write_text("", encoding="utf-8")
    (state / "cost.jsonl").write_text("", encoding="utf-8")
    (state / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state / "role_sessions.yaml").write_text("{}\n", encoding="utf-8")
    (state / "session.yaml").write_text("{}\n", encoding="utf-8")

    scorecard = robustness_suite.build_scorecard(
        state_dir=state,
        scenario="codex",
        preset="dev-codex-backends",
    )
    issues = robustness_suite.scorecard_acceptance_issues(
        scorecard,
        expected_done=1,
        require_codex_hooks=True,
    )

    assert any("task_done_count" in issue for issue in issues)
    assert any("missing_required_events" in issue for issue in issues)
    assert any("codex_hook_count" in issue for issue in issues)
