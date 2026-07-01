from __future__ import annotations

import json
import sys
from pathlib import Path

from zf.autoresearch import orchestrator as ar_orchestrator
from zf.autoresearch.campaign import (
    resolve_campaign,
    write_campaign_plan,
)
from zf.autoresearch.orchestrator import (
    AutoresearchRunConfig,
    build_inner_runner_command,
    ensure_web_dependencies,
    summarize_events,
    sync_tracked_checkout_changes,
    tmux_supervisor_command,
    upsert_failure_backlog,
    write_report,
)
from zf.autoresearch.scenarios import resolve_scenario


def test_resolve_builtin_scenario_allows_overrides(tmp_path: Path) -> None:
    seed = tmp_path / "seed.txt"
    seed.write_text("Custom seed\n", encoding="utf-8")

    scenario = resolve_scenario(
        "self-eval-backlog",
        seed_file=seed,
        expected_done=2,
        timeout_seconds=30,
    )

    assert scenario.seed_text == "Custom seed"
    assert scenario.expected_done == 2
    assert scenario.timeout_seconds == 30


def test_resolve_campaign_contains_metric_scenarios() -> None:
    campaign = resolve_campaign("harness-hardening")

    names = [item.scenario for item in campaign.scenarios]
    assert names == [
        "positive-pressure-4dev",
        "controlled-stuck-recovery",
        "fail-rework-converge",
        "manual-intervention-guard",
    ]
    assert any("fatal_count" in item for item in campaign.pass_criteria)


def test_write_campaign_plan_outputs_json_markdown_and_script(
    tmp_path: Path,
) -> None:
    campaign = resolve_campaign("harness-hardening")

    paths = write_campaign_plan(
        campaign=campaign,
        output_dir=tmp_path / "plan",
        worktree_root=tmp_path / "worktrees",
        config_template=Path("examples/dev-codex-backends.yaml"),
        use_tmux=False,
    )

    assert paths.json_path.exists()
    assert paths.markdown_path.exists()
    assert paths.script_path.exists()
    text = paths.markdown_path.read_text(encoding="utf-8")
    assert "positive-pressure-4dev" in text
    assert "terminal_evidence_coverage" in text
    script = paths.script_path.read_text(encoding="utf-8")
    assert "autoresearch run" in script
    assert "--inject-worker-stuck" in script
    assert "--tmux" not in script


def test_write_campaign_plan_threads_review_gate_mode(tmp_path: Path) -> None:
    campaign = resolve_campaign("harness-hardening")

    paths = write_campaign_plan(
        campaign=campaign,
        output_dir=tmp_path / "plan",
        worktree_root=tmp_path / "worktrees",
        config_template=Path("examples/dev-codex-backends.yaml"),
        use_tmux=False,
        review_gate="auto",
    )

    payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
    script = paths.script_path.read_text(encoding="utf-8")
    markdown = paths.markdown_path.read_text(encoding="utf-8")
    assert payload["review_gate"] == "auto"
    assert all(item["review_gate"] == "auto" for item in payload["scenarios"])
    assert "--review-gate auto" in script
    assert "review_gate: `auto`" in markdown


def test_build_inner_runner_command_uses_run_mixed_contract(tmp_path: Path) -> None:
    scenario = resolve_scenario(
        "self-eval-backlog",
        expected_done=4,
        timeout_seconds=99,
    )
    cfg = AutoresearchRunConfig(
        worktree=tmp_path / "wt",
        runner_module="tests.e2e.run_mixed",
        keep_running=True,
    )

    cmd = build_inner_runner_command(
        cfg,
        scenario=scenario,
        seed_path=tmp_path / "seed.txt",
    )

    assert cmd[1:3] == ["-m", "tests.e2e.run_mixed"]
    assert "--worktree" in cmd
    assert str((tmp_path / "wt").resolve()) in cmd
    assert "--expected-done" in cmd
    assert "4" in cmd
    assert "--timeout" in cmd
    assert "99" in cmd
    assert "--no-stop" in cmd


def test_write_report_includes_review_gate_section(tmp_path: Path) -> None:
    report = write_report(tmp_path / "run", {
        "run_id": "r1",
        "scenario": "s",
        "status": "fatal",
        "worktree": str(tmp_path / "wt"),
        "tasks_done": 0,
        "expected_done": 1,
        "returncode": 1,
        "elapsed_seconds": 2.0,
        "summary": {},
        "log_path": str(tmp_path / "inner.log"),
        "review_gate": {
            "mode": "auto",
            "status": "triggered",
            "triggered": True,
            "route": "fanout_gate",
            "severity": "high",
            "reason": "runtime fanout failure",
            "artifact_refs": {"summary": "review-gate/summary.json"},
        },
    })

    text = report.read_text(encoding="utf-8")
    assert "## Review Gate" in text
    assert "fanout_gate" in text
    assert "review-gate/summary.json" in text


def test_summarize_events_detects_done_and_fatal(tmp_path: Path) -> None:
    events = tmp_path / ".zf" / "events.jsonl"
    events.parent.mkdir()
    rows = [
        {
            "type": "autoresearch.inject.worker_stuck",
            "task_id": "T1",
            "payload": {"instance_id": "dev-1"},
        },
        {
            "type": "task.dispatched",
            "payload": {"assignee": "dev-1"},
        },
        {
            "type": "task.status_changed",
            "payload": {"to": "done"},
        },
        {
            "type": "worker.stuck",
            "task_id": "T1",
            "actor": "judge",
            "payload": {"dispatch_id": "disp-1"},
        },
        {
            "type": "worker.stuck.recovery_failed",
            "task_id": "T1",
            "actor": "judge",
            "payload": {"dispatch_id": "disp-1"},
        },
    ]
    events.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    summary = summarize_events(tmp_path, expected_done=4)

    assert summary["tasks_done"] == 1
    assert summary["expected_done"] == 4
    assert summary["fatal_event"]["type"] == "worker.stuck.recovery_failed"
    assert summary["dispatch_by_instance"] == {"dev-1": 1}
    assert summary["derived_metrics"]["fatal_count"] == 1
    assert summary["derived_metrics"]["stuck_injection_requested_count"] == 1
    assert not summary["derived_metrics"]["stuck_injection_satisfied"]
    assert summary["derived_metrics"]["worker_stuck_count"] == 1
    assert summary["derived_metrics"]["worker_stuck_recovery_failed_count"] == 1


def test_stuck_injection_waits_after_non_target_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worktree = tmp_path / "wt"
    events = worktree / ".zf" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(
        json.dumps({
            "type": "task.dispatched",
            "task_id": "T0",
            "payload": {"assignee": "arch", "role": "arch"},
        }) + "\n",
        encoding="utf-8",
    )

    emitted: list[dict] = []

    def fake_emit(**kwargs):
        emitted.append(kwargs["dispatch_event"])
        return True

    monkeypatch.setattr(ar_orchestrator, "_emit_stuck_injection", fake_emit)
    code = (
        "import json, pathlib, sys, time;"
        "p=pathlib.Path(sys.argv[1]);"
        "time.sleep(1.2);"
        "row={'type':'task.dispatched','task_id':'T1',"
        "'payload':{'assignee':'dev-1','role':'dev','dispatch_id':'disp-1'}};"
        "p.open('a', encoding='utf-8').write(json.dumps(row)+'\\n');"
        "time.sleep(1.5)"
    )

    result = ar_orchestrator._run_inner_runner(
        [sys.executable, "-c", code, str(events)],
        cwd=tmp_path,
        log_path=tmp_path / "inner.log",
        cfg=AutoresearchRunConfig(
            worktree=worktree,
            inject_worker_stuck=True,
            inject_worker_stuck_instance="dev-1",
            inject_worker_stuck_timeout_seconds=1,
        ),
        run_dir=tmp_path / "run",
    )

    text = (tmp_path / "inner.log").read_text(encoding="utf-8")
    assert result.returncode == 0
    assert emitted[0]["task_id"] == "T1"
    assert "worker_stuck_injection=emitted" in text
    assert "worker_stuck_injection=not_emitted" not in text


def test_summarize_events_derives_campaign_metrics(tmp_path: Path) -> None:
    events = tmp_path / ".zf" / "events.jsonl"
    events.parent.mkdir()
    duplicate_payload = {"dispatch_id": "disp-1"}
    rows = [
        {
            "type": "task.dispatched",
            "task_id": "T1",
            "payload": {"assignee": "dev-1"},
        },
        {
            "type": "task.dispatched",
            "task_id": "T2",
            "payload": {"assignee": "test-2"},
        },
        {
            "type": "dev.build.done",
            "task_id": "T1",
            "payload": duplicate_payload,
        },
        {
            "type": "dev.build.done",
            "task_id": "T1",
            "payload": duplicate_payload,
        },
        {"type": "task.done.blocked", "task_id": "T1"},
        {"type": "discriminator.failed", "task_id": "T1"},
        {"type": "task.done.evidence", "task_id": "T1"},
        {
            "type": "task.status_changed",
            "task_id": "T1",
            "payload": {"to": "done"},
        },
    ]
    events.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    summary = summarize_events(tmp_path, expected_done=1)
    metrics = summary["derived_metrics"]

    assert metrics["duplicate_success_event_count"] == 1
    assert metrics["dev_replicas_used"] == ["dev-1"]
    assert metrics["test_replicas_used"] == ["test-2"]
    assert metrics["task_done_blocked_count"] == 1
    assert metrics["discriminator_failed_count"] == 1
    assert metrics["terminal_evidence_coverage"] == 1.0


def test_write_report_records_fatal_payload(tmp_path: Path) -> None:
    path = write_report(tmp_path, {
        "run_id": "r1",
        "scenario": "self-eval-backlog",
        "status": "fatal",
        "worktree": "/tmp/wt",
        "tasks_done": 1,
        "expected_done": 4,
        "returncode": 1,
        "elapsed_seconds": 12.5,
        "log_path": "/tmp/log",
        "fatal_event": {
            "type": "worker.stuck.recovery_failed",
            "task_id": "T1",
            "actor": "judge",
            "payload": {"dispatch_id": "disp-1"},
        },
        "summary": {"dispatch_by_instance": {"dev-1": 1}},
    })

    text = path.read_text(encoding="utf-8")
    assert "worker.stuck.recovery_failed" in text
    assert "disp-1" in text


def test_failure_backlog_is_idempotent(tmp_path: Path) -> None:
    scenario = resolve_scenario("self-eval-backlog")
    cfg = AutoresearchRunConfig(
        worktree=tmp_path / "wt",
        backlog_state_dir=tmp_path / ".zf",
    )
    row = {
        "status": "fatal",
        "returncode": 1,
        "fatal_event": {"type": "worker.stuck.recovery_failed", "task_id": "T1"},
        "report_path": "/tmp/report.md",
    }

    first = upsert_failure_backlog(cfg=cfg, scenario=scenario, row=row)
    second = upsert_failure_backlog(cfg=cfg, scenario=scenario, row=row)

    assert first == second
    data = json.loads((tmp_path / ".zf" / "kanban.json").read_text())
    assert len(data) == 1
    assert data[0]["key"].startswith("autoresearch:")


def test_tmux_supervisor_command_reenters_with_no_tmux(tmp_path: Path) -> None:
    commands = tmux_supervisor_command(
        ["python3", "-m", "zf.cli.main", "autoresearch", "run", "--tmux"],
        worktree=tmp_path / "wt",
        session="outer",
    )

    joined = " ".join(" ".join(cmd) for cmd in commands)
    assert "new-session" in joined
    assert "--no-tmux" in joined
    assert "ZF_AUTORESEARCH_IN_TMUX=1" in joined
    assert f"PYTHONPATH={Path.cwd() / 'src'}" in joined


def test_ensure_web_dependencies_noops_without_web_package(tmp_path: Path) -> None:
    mode = ensure_web_dependencies(
        tmp_path / "wt",
        log_path=tmp_path / "prepare-web-deps.log",
    )

    assert mode == "skipped:no-web-package"


def test_ensure_web_dependencies_links_repo_node_modules(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "repo"
    source_bin = root / "web" / "node_modules" / ".bin"
    source_bin.mkdir(parents=True)
    (source_bin / "tsc").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(
        "zf.autoresearch.orchestrator.repo_root",
        lambda: root,
    )

    worktree = tmp_path / "wt"
    web_dir = worktree / "web"
    web_dir.mkdir(parents=True)
    (web_dir / "package.json").write_text("{}", encoding="utf-8")
    (web_dir / "package-lock.json").write_text("{}", encoding="utf-8")

    mode = ensure_web_dependencies(
        worktree,
        log_path=tmp_path / "prepare-web-deps.log",
    )

    assert mode == "linked"
    assert (web_dir / "node_modules").is_symlink()
    assert (web_dir / "node_modules" / ".bin" / "tsc").exists()


def test_sync_tracked_checkout_changes_copies_dirty_files_not_zf_yaml(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "changed.py").write_text("new\n", encoding="utf-8")
    (root / "zf.yaml").write_text("project:\n  name: root\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    worktree.mkdir()

    class Result:
        returncode = 0
        stdout = "M\0src/changed.py\0M\0zf.yaml\0"
        stderr = ""

    monkeypatch.setattr(
        "zf.autoresearch.orchestrator.repo_root",
        lambda: root,
    )
    monkeypatch.setattr(
        "zf.autoresearch.orchestrator._run",
        lambda *args, **kwargs: Result(),
    )

    summary = sync_tracked_checkout_changes(
        worktree,
        log_path=tmp_path / "synced.log",
    )

    assert summary["modified"] == ["src/changed.py"]
    assert summary["deleted"] == []
    assert summary["skipped"] is False
    assert (worktree / "src" / "changed.py").read_text(encoding="utf-8") == "new\n"
    assert not (worktree / "zf.yaml").exists()


def test_sync_tracked_checkout_changes_handles_delete_and_rename(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "kept.py").write_text("ok\n", encoding="utf-8")
    (root / "src" / "renamed_to.py").write_text("renamed\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    (worktree / "src").mkdir(parents=True)
    (worktree / "src" / "stale.py").write_text("old\n", encoding="utf-8")
    (worktree / "src" / "renamed_from.py").write_text("old\n", encoding="utf-8")

    class Result:
        returncode = 0
        # D src/stale.py;  R100 src/renamed_from.py src/renamed_to.py
        stdout = (
            "D\0src/stale.py\0"
            "R100\0src/renamed_from.py\0src/renamed_to.py\0"
        )
        stderr = ""

    monkeypatch.setattr(
        "zf.autoresearch.orchestrator.repo_root",
        lambda: root,
    )
    monkeypatch.setattr(
        "zf.autoresearch.orchestrator._run",
        lambda *args, **kwargs: Result(),
    )

    summary = sync_tracked_checkout_changes(
        worktree,
        log_path=tmp_path / "synced.log",
    )

    assert summary["deleted"] == ["src/stale.py"]
    assert summary["renamed"] == [["src/renamed_from.py", "src/renamed_to.py"]]
    assert not (worktree / "src" / "stale.py").exists()
    assert not (worktree / "src" / "renamed_from.py").exists()
    assert (worktree / "src" / "renamed_to.py").read_text(encoding="utf-8") == "renamed\n"


def test_sync_tracked_checkout_changes_strict_mode_skips(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()

    def _fail(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("git diff should not run in strict mode")

    monkeypatch.setattr("zf.autoresearch.orchestrator._run", _fail)

    summary = sync_tracked_checkout_changes(
        worktree,
        log_path=tmp_path / "synced.log",
        enabled=False,
    )

    assert summary["skipped"] is True
    assert summary["modified"] == []
    assert (tmp_path / "synced.log").read_text(encoding="utf-8").startswith("skipped:")
