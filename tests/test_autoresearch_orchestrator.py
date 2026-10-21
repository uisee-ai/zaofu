from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from zf.autoresearch import orchestrator as ar_orchestrator
from zf.autoresearch.campaign import (
    resolve_campaign,
    write_campaign_plan,
)
from zf.autoresearch.orchestrator import (
    AutoresearchRunConfig,
    build_inner_runner_command,
    cleanup_prepared_worktree,
    effective_run_config,
    ensure_web_dependencies,
    prepare_worktree,
    summarize_events,
    sync_tracked_checkout_changes,
    tmux_supervisor_command,
    upsert_failure_backlog,
    write_report,
)
from zf.autoresearch.worktree_preparation import (
    cleanup_interrupted_prepared_worktree,
)
from zf.autoresearch.loop import LoopConfig
from zf.autoresearch.scenarios import resolve_scenario
from zf.core.events.log import EventLog


def test_default_config_templates_exist() -> None:
    root = Path(__file__).resolve().parents[1]

    assert (root / AutoresearchRunConfig().config_template).is_file()
    assert (root / LoopConfig().config_template).is_file()
    assert "examples/prod/controller" in str(
        AutoresearchRunConfig().config_template
    )


def test_prepare_worktree_renders_v4_controller_in_blocking_mode(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "feishu.yaml").write_text(
        "feishu_identity: {enabled: false}\nfeishu_routing: {}\n",
        encoding="utf-8",
    )
    cfg = AutoresearchRunConfig(
        worktree=worktree,
        config_template=(
            root
            / "examples/prod/controller/prd-task-pipeline-v4-canary.yaml"
        ),
        reuse_worktree=True,
        sync_dirty=False,
    )

    prepare_worktree(
        cfg,
        scenario=resolve_scenario("controlled-stuck-recovery"),
        run_id="v4-controller",
        run_dir=tmp_path / "run",
    )

    rendered = yaml.safe_load(
        (worktree / "zf.yaml").read_text(encoding="utf-8")
    )
    metadata = rendered["workflow"]["_flow_metadata"]
    orchestration = rendered["workflow"]["orchestration"]
    assert metadata["prd_ref"] == "docs/prd/autoresearch.md"
    assert metadata["target_root"] == "."
    assert metadata["task_pipeline"]["mode"] == "blocking"
    assert orchestration["mode"] == "exception_advisor"
    assert (
        orchestration["flow_policies"]["prd"]["checkpoint_policies"][
            "plan_candidate"
        ]
        == "blocking"
    )
    assert rendered["project"]["state_dir"] == ".zf"
    assert "feishu_identity" not in rendered["integrations"]
    assert "feishu_routing" not in rendered["integrations"]


def test_prepare_worktree_checkpoints_explicit_flow_baseline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=worktree, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=worktree,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=worktree,
        check=True,
    )
    (worktree / "zf.yaml").write_text(
        "version: '1.0'\nproject: {name: original}\n",
        encoding="utf-8",
    )
    (worktree / "candidate.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "--", "zf.yaml", "candidate.txt"],
        cwd=worktree,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "base"],
        cwd=worktree,
        check=True,
    )
    template = tmp_path / "template.yaml"
    template.write_text(
        "version: '1.0'\nproject: {name: template}\nsession: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ar_orchestrator,
        "sync_tracked_checkout_changes",
        lambda *_args, **_kwargs: {
            "added": [],
            "modified": ["candidate.txt"],
            "deleted": [],
            "renamed": [],
            "skipped": False,
        },
    )
    monkeypatch.setattr(
        ar_orchestrator,
        "ensure_web_dependencies",
        lambda *_args, **_kwargs: "present",
    )
    (worktree / "candidate.txt").write_text("after\n", encoding="utf-8")
    run_dir = tmp_path / "run"

    prepare_worktree(
        AutoresearchRunConfig(
            worktree=worktree,
            config_template=template,
            reuse_worktree=True,
            sync_dirty=True,
        ),
        scenario=resolve_scenario("self-eval-backlog"),
        run_id="flow-baseline",
        run_dir=run_dir,
    )

    tracked = subprocess.run(
        ["git", "show", "--pretty=", "--name-only", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    manifest = json.loads(
        (
            run_dir
            / "worktree-preparation"
            / "worktree-preparation.json"
        ).read_text(encoding="utf-8")
    )
    assert tracked == ["candidate.txt", "zf.yaml"]
    assert len(manifest["baseline_checkpoint_commit"]) == 40


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


def test_prepared_worktree_restores_only_unchanged_framework_files(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    original = "version: '1.0'\nproject: {name: original}\n"
    (worktree / "zf.yaml").write_text(original, encoding="utf-8")
    template = tmp_path / "template.yaml"
    template.write_text(
        "version: '1.0'\nproject: {name: template}\nsession: {}\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    scenario = resolve_scenario(
        "self-eval-backlog",
        expected_done=1,
        timeout_seconds=30,
    )
    cfg = AutoresearchRunConfig(
        worktree=worktree,
        config_template=template,
        reuse_worktree=True,
        sync_dirty=False,
    )

    seed = prepare_worktree(
        cfg,
        scenario=scenario,
        run_id="restore-framework-files",
        run_dir=run_dir,
    )
    assert (worktree / "zf.yaml").read_text(encoding="utf-8") != original
    assert seed.exists()
    assert not (worktree / "zf.yaml.pre-autoresearch").exists()

    outcome = cleanup_prepared_worktree(worktree=worktree, run_dir=run_dir)

    assert outcome["status"] == "cleaned"
    assert (worktree / "zf.yaml").read_text(encoding="utf-8") == original
    assert not seed.exists()


def test_prepared_worktree_removes_only_framework_created_web_dependencies(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    template = tmp_path / "template.yaml"
    template.write_text(
        "version: '1.0'\nproject: {name: template}\nsession: {}\n",
        encoding="utf-8",
    )
    dependency_source = tmp_path / "shared-node-modules"
    dependency_source.mkdir()

    def _link_dependencies(root: Path, *, log_path: Path) -> str:
        target = root / "web" / "node_modules"
        target.parent.mkdir(parents=True)
        target.symlink_to(dependency_source, target_is_directory=True)
        return "linked"

    monkeypatch.setattr(
        ar_orchestrator,
        "ensure_web_dependencies",
        _link_dependencies,
    )
    run_dir = worktree / ".zf" / "autoresearch" / "runs" / "linked-deps"
    prepare_worktree(
        AutoresearchRunConfig(
            worktree=worktree,
            config_template=template,
            reuse_worktree=True,
            sync_dirty=False,
        ),
        scenario=resolve_scenario("self-eval-backlog"),
        run_id="linked-deps",
        run_dir=run_dir,
    )

    dependencies = worktree / "web" / "node_modules"
    assert dependencies.is_symlink()

    outcome = cleanup_interrupted_prepared_worktree(worktree=worktree)

    assert outcome["status"] == "cleaned"
    assert str(dependencies) in outcome["removed"]
    assert not dependencies.is_symlink()


def test_prepared_worktree_preserves_preexisting_web_dependencies(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worktree = tmp_path / "worktree"
    dependencies = worktree / "web" / "node_modules"
    dependencies.mkdir(parents=True)
    marker = dependencies / "candidate.txt"
    marker.write_text("keep\n", encoding="utf-8")
    template = tmp_path / "template.yaml"
    template.write_text(
        "version: '1.0'\nproject: {name: template}\nsession: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ar_orchestrator,
        "ensure_web_dependencies",
        lambda *_args, **_kwargs: "present",
    )
    run_dir = worktree / ".zf" / "autoresearch" / "runs" / "present-deps"
    prepare_worktree(
        AutoresearchRunConfig(
            worktree=worktree,
            config_template=template,
            reuse_worktree=True,
            sync_dirty=False,
        ),
        scenario=resolve_scenario("self-eval-backlog"),
        run_id="present-deps",
        run_dir=run_dir,
    )

    outcome = cleanup_prepared_worktree(worktree=worktree, run_dir=run_dir)

    assert outcome["status"] == "cleaned"
    assert marker.read_text(encoding="utf-8") == "keep\n"


def test_preparation_failure_restores_framework_owned_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    original_zf = "version: '1.0'\nproject: {name: original}\n"
    original_seed = "operator seed\n"
    (worktree / "zf.yaml").write_text(original_zf, encoding="utf-8")
    (worktree / "autoresearch-seed.txt").write_text(
        original_seed,
        encoding="utf-8",
    )
    template = tmp_path / "template.yaml"
    template.write_text(
        "version: '1.0'\nproject: {name: template}\nsession: {}\n",
        encoding="utf-8",
    )
    dependency_source = tmp_path / "shared-node-modules"
    dependency_source.mkdir()

    def _fail_after_link(root: Path, *, log_path: Path) -> str:
        target = root / "web" / "node_modules"
        target.parent.mkdir(parents=True)
        target.symlink_to(dependency_source, target_is_directory=True)
        raise RuntimeError("dependency preparation failed")

    monkeypatch.setattr(
        ar_orchestrator,
        "ensure_web_dependencies",
        _fail_after_link,
    )
    run_dir = tmp_path / "run"

    with pytest.raises(RuntimeError, match="dependency preparation failed"):
        prepare_worktree(
            AutoresearchRunConfig(
                worktree=worktree,
                config_template=template,
                reuse_worktree=True,
                sync_dirty=False,
            ),
            scenario=resolve_scenario("self-eval-backlog"),
            run_id="preparation-failure",
            run_dir=run_dir,
        )

    journal = json.loads(
        (
            run_dir
            / "worktree-preparation"
            / "worktree-preparation.json"
        ).read_text(encoding="utf-8")
    )
    assert journal["schema_version"] == "autoresearch-worktree-preparation.v2"
    assert journal["phase"] == "failed"
    assert (worktree / "zf.yaml").read_text(encoding="utf-8") == original_zf
    assert (
        worktree / "autoresearch-seed.txt"
    ).read_text(encoding="utf-8") == original_seed
    assert not (worktree / "web" / "node_modules").is_symlink()


def test_prepared_worktree_retains_user_modified_framework_file(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "zf.yaml").write_text(
        "version: '1.0'\nproject: {name: original}\n",
        encoding="utf-8",
    )
    template = tmp_path / "template.yaml"
    template.write_text(
        "version: '1.0'\nproject: {name: template}\nsession: {}\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    scenario = resolve_scenario("self-eval-backlog")
    prepare_worktree(
        AutoresearchRunConfig(
            worktree=worktree,
            config_template=template,
            reuse_worktree=True,
            sync_dirty=False,
        ),
        scenario=scenario,
        run_id="retain-modified-framework-file",
        run_dir=run_dir,
    )
    changed = "version: '1.0'\nproject: {name: candidate-change}\n"
    (worktree / "zf.yaml").write_text(changed, encoding="utf-8")

    outcome = cleanup_prepared_worktree(worktree=worktree, run_dir=run_dir)

    assert outcome["status"] == "retained"
    assert (worktree / "zf.yaml").read_text(encoding="utf-8") == changed


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
        config_template=Path(
            "examples/prod/controller/prd-task-pipeline-v4-canary.yaml"
        ),
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
    assert "--inject-worker-stuck" not in script
    assert "--tmux" not in script


def test_write_campaign_plan_threads_review_gate_mode(tmp_path: Path) -> None:
    campaign = resolve_campaign("harness-hardening")

    paths = write_campaign_plan(
        campaign=campaign,
        output_dir=tmp_path / "plan",
        worktree_root=tmp_path / "worktrees",
        config_template=Path(
            "examples/prod/controller/prd-task-pipeline-v4-canary.yaml"
        ),
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
    assert cmd[cmd.index("--flow-kind") + 1] == "prd"
    assert cmd[cmd.index("--flow-source") + 1] == "docs/prd/autoresearch.md"
    assert cmd[cmd.index("--flow-target-root") + 1] == "."
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


def test_controlled_stuck_scenario_enables_required_injection() -> None:
    cfg = AutoresearchRunConfig(inject_worker_stuck=False)

    effective = effective_run_config(
        cfg,
        resolve_scenario("controlled-stuck-recovery"),
    )

    assert effective.inject_worker_stuck


def test_non_stuck_scenario_preserves_explicit_injection_override() -> None:
    cfg = AutoresearchRunConfig(inject_worker_stuck=True)

    effective = effective_run_config(cfg, resolve_scenario("self-eval-backlog"))

    assert effective.inject_worker_stuck


def test_summarize_events_audits_incident_across_event_archives(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    archive_dir = state_dir / "events"
    archive_dir.mkdir(parents=True)
    archived = [
        {
            "id": "evt-dispatch",
            "type": "task.dispatched",
            "task_id": "TASK-1",
            "correlation_id": "corr-run",
            "payload": {
                "assignee": "dev-1",
                "role": "dev",
                "dispatch_id": "disp-1",
            },
        },
        {
            "id": "evt-injection",
            "type": "autoresearch.inject.worker_stuck",
            "origin": "external",
            "task_id": "TASK-1",
            "causation_id": "evt-dispatch",
            "correlation_id": "corr-run",
            "payload": {
                "instance_id": "dev-1",
                "role": "dev",
                "dispatch_id": "disp-1",
                "trigger_event_id": "evt-dispatch",
            },
        },
    ]
    active = [
        {
            "id": "evt-stuck",
            "type": "worker.stuck",
            "origin": "kernel",
            "task_id": "TASK-1",
            "causation_id": "evt-injection",
            "correlation_id": "corr-run",
            "payload": {
                "instance_id": "dev-1",
                "role": "dev",
                "dispatch_id": "disp-1",
                "trigger_event_id": "evt-injection",
            },
        },
        {
            "id": "evt-recovered",
            "type": "worker.stuck.recovered",
            "origin": "kernel",
            "task_id": "TASK-1",
            "causation_id": "evt-stuck",
            "correlation_id": "corr-run",
            "payload": {
                "instance_id": "dev-1",
                "role": "dev",
                "dispatch_id": "disp-1",
            },
        },
    ]
    (archive_dir / "2026-07-29.jsonl").write_text(
        "\n".join(json.dumps(row) for row in archived) + "\n",
        encoding="utf-8",
    )
    (state_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(row) for row in active) + "\n",
        encoding="utf-8",
    )

    summary = summarize_events(
        tmp_path,
        expected_done=1,
        stuck_incident_required=True,
    )

    assert summary["stuck_incident_audit"]["status"] == "passed"
    assert summary["derived_metrics"]["stuck_injection_satisfied"]


def test_stuck_injection_waits_after_non_target_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worktree = tmp_path / "wt"
    events = worktree / ".zf" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(
        json.dumps({
            "id": "evt-old-arch",
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
        "rows=[{'id':'evt-session','type':'session.started'},"
        "{'id':'evt-new-dev','type':'task.dispatched','task_id':'T1',"
        "'payload':{'assignee':'dev-1','role':'dev','dispatch_id':'disp-1'}}];"
        "p.open('a', encoding='utf-8').write("
        "'\\n'.join(json.dumps(row) for row in rows)+'\\n');"
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


def test_emit_stuck_injection_uses_external_event_writer(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    (worktree / ".zf").mkdir(parents=True)
    dispatch = {
        "id": "evt-dispatch",
        "type": "task.dispatched",
        "task_id": "TASK-1",
        "correlation_id": "corr-run",
        "payload": {
            "assignee": "dev-1",
            "role": "dev",
            "dispatch_id": "disp-1",
        },
    }

    emitted = ar_orchestrator._emit_stuck_injection(
        worktree=worktree,
        run_dir=tmp_path / "run",
        dispatch_event=dispatch,
        target_instance="dev-1",
    )

    assert emitted
    event = EventLog(worktree / ".zf" / "events.jsonl").read_all()[-1]
    assert event.type == "autoresearch.inject.worker_stuck"
    assert event.origin == "external"
    assert event.causation_id == "evt-dispatch"
    assert event.correlation_id == "corr-run"
    assert event.payload["trigger_event_id"] == "evt-dispatch"


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


def test_repo_root_resolves_pep610_noneditable_local_install(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "frozen-source"
    (source / ".git").mkdir(parents=True)
    (source / "pyproject.toml").write_text("[project]\nname='zaofu'\n", encoding="utf-8")

    monkeypatch.delenv("ZF_AUTORESEARCH_SOURCE_ROOT", raising=False)
    monkeypatch.setattr(
        ar_orchestrator,
        "__file__",
        str(tmp_path / "venv" / "site-packages" / "zf" / "autoresearch" / "orchestrator.py"),
    )
    monkeypatch.setattr(
        ar_orchestrator,
        "installed_local_source_root",
        lambda: source,
    )

    assert ar_orchestrator.repo_root() == source.resolve()


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


def test_diagnostic_worktree_uses_its_own_skill_sources(tmp_path):
    from zf.autoresearch.orchestrator import _apply_autoresearch_controller_overrides
    worktree = tmp_path / "worktree"
    (worktree / "skills").mkdir(parents=True)
    (worktree / "yoke").mkdir()
    data = {"skill_sources": [
        {"name": "zaofu-skills", "path": "/old/skills", "mode": "readonly"},
        {"name": "yoke", "path": "yoke", "mode": "readonly"},
        {"name": "yoke", "path": "/old/yoke", "mode": "readonly"},
        {"name": "custom", "path": "/custom/skills", "mode": "readonly"},
    ]}
    _apply_autoresearch_controller_overrides(data, cfg=AutoresearchRunConfig(worktree=worktree), run_id="test")
    roots = [str(Path(s["path"]).resolve()) for s in data["skill_sources"]]
    assert str(worktree / "skills") not in roots  # implicit project source
    assert roots.count(str(worktree / "yoke")) == 1
    assert "/old/yoke" not in roots
    assert "/custom/skills" in roots


def test_diagnostic_worktree_pins_local_yoke_over_installed_fallback(tmp_path):
    from zf.autoresearch.orchestrator import _apply_autoresearch_controller_overrides
    from zf.core.config.schema import RoleConfig, SkillSourceConfig, ZfConfig
    from zf.core.skills import validate_skill_sources
    local = tmp_path / "yoke" / "verify-review"
    local.mkdir(parents=True)
    (local / "SKILL.md").write_text("---\nname: verify-review\ndescription: local method\n---\nLocal method\n")
    data = {"skill_sources": []}
    _apply_autoresearch_controller_overrides(data, cfg=AutoresearchRunConfig(worktree=tmp_path), run_id="test")
    config = ZfConfig(
        roles=[RoleConfig(name="verify", backend="mock", skills=["verify-review"])],
        skill_sources=[SkillSourceConfig(**source) for source in data["skill_sources"]],
    )
    assert not any("multiple candidates" in issue for issue in validate_skill_sources(config=config, project_root=tmp_path))
