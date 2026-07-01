from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

from zf.cli.main import main
from zf.cli.autoresearch import _real_git_head
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


def test_autoresearch_run_dry_run_writes_report(tmp_path: Path, capsys) -> None:
    worktree = tmp_path / "wt"

    rc = main([
        "autoresearch",
        "run",
        "--worktree",
        str(worktree),
        "--run-id",
        "dry",
    ])

    out = capsys.readouterr().out
    assert rc == 0
    assert "Autoresearch dry-run" in out
    report = worktree / ".zf" / "autoresearch" / "runs" / "dry" / "report.md"
    assert report.exists()


def test_autoresearch_run_accepts_stuck_injection_flags(
    tmp_path: Path,
    capsys,
) -> None:
    worktree = tmp_path / "wt"

    rc = main([
        "autoresearch",
        "run",
        "--worktree",
        str(worktree),
        "--run-id",
        "dry-stuck",
        "--inject-worker-stuck",
        "--inject-worker-stuck-instance",
        "dev-2",
        "--inject-worker-stuck-timeout",
        "30",
    ])

    out = capsys.readouterr().out
    assert rc == 0
    assert "Autoresearch dry-run" in out
    assert (
        worktree / ".zf" / "autoresearch" / "runs" / "dry-stuck" / "report.md"
    ).exists()


def test_autoresearch_run_rejects_existing_worktree_without_reuse(
    tmp_path: Path,
    capsys,
) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()

    rc = main([
        "autoresearch",
        "run",
        "--worktree",
        str(worktree),
        "--confirm",
    ])

    err = capsys.readouterr().out
    assert rc == 2
    assert "worktree already exists" in err


def test_autoresearch_campaign_plan_writes_files(
    tmp_path: Path,
    capsys,
) -> None:
    out_dir = tmp_path / "campaign"

    rc = main([
        "autoresearch",
        "campaign",
        "plan",
        "--output-dir",
        str(out_dir),
        "--worktree-root",
        str(tmp_path / "worktrees"),
        "--no-tmux",
        "--review-gate",
        "auto",
    ])

    out = capsys.readouterr().out
    assert rc == 0
    assert "Autoresearch campaign plan" in out
    assert (out_dir / "campaign.json").exists()
    assert (out_dir / "campaign.md").exists()
    assert (out_dir / "run-campaign.sh").exists()
    payload = json.loads((out_dir / "campaign.json").read_text(encoding="utf-8"))
    assert payload["review_gate"] == "auto"


def test_autoresearch_git_head_uses_project_root_from_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    state_dir = tmp_path / "runtime-state"
    project_root.mkdir()
    state_dir.mkdir()
    (project_root / "zf.yaml").write_text(
        f'version: "1.0"\nproject:\n  name: test\n  state_dir: "{state_dir}"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(project_root)
    seen: dict[str, Path] = {}

    def fake_run(cmd, *, cwd, capture_output, text, timeout):  # noqa: ANN001
        seen["cwd"] = Path(cwd)
        assert cmd == ["git", "rev-parse", "HEAD"]
        assert capture_output is True
        assert text is True
        assert timeout == 10
        return SimpleNamespace(returncode=0, stdout="abc123\n")

    monkeypatch.setattr("subprocess.run", fake_run)

    assert _real_git_head(state_dir) == "abc123"
    assert seen["cwd"] == project_root.resolve()


def test_autoresearch_discover_bugs_exports_backlog(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".zf"
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="orchestrator.dispatch_failed",
        actor="zf-cli",
        payload={"reason": "missing dev"},
    ))

    rc = main([
        "autoresearch",
        "discover-bugs",
        "--state-dir",
        str(state_dir),
        "--out",
        str(tmp_path / "backlogs"),
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "runtime_fatal" in out
    created = list((tmp_path / "backlogs").glob("*.md"))
    assert len(created) == 1
    assert "> 状态: proposed" in created[0].read_text(encoding="utf-8")


def test_autoresearch_triggers_scan_outputs_decision(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".zf"
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="orchestrator.dispatch_failed",
        actor="zf-cli",
        payload={"reason": "missing dev"},
    ))

    rc = main([
        "autoresearch",
        "triggers",
        "scan",
        "--state-dir",
        str(state_dir),
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "accepted" in out
    assert "spine_review_hint" in out
    assert "zf project review-spine" in out


def test_autoresearch_review_gate_prepare_cli_writes_json(
    tmp_path: Path,
    capsys,
) -> None:
    source_root = tmp_path / "repo"
    (source_root / "src" / "zf" / "autoresearch").mkdir(parents=True)
    (source_root / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
    (source_root / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: ".zf"\n',
        encoding="utf-8",
    )
    (source_root / "src" / "zf" / "autoresearch" / "example.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / ".zf"
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        id="evt-fatal",
        type="orchestrator.dispatch_failed",
        actor="zf-cli",
        payload={"reason": "missing review worker"},
    ))

    rc = main([
        "autoresearch",
        "review-gate",
        "prepare",
        "--run-dir",
        str(tmp_path / "run"),
        "--state-dir",
        str(state_dir),
        "--source-root",
        str(source_root),
    ])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["route"] == "fanout_gate"
    assert payload["policy"]["route"] == "fanout_gate"
    assert Path(payload["codebase_context_pack"]).exists()
    assert Path(payload["failure_evidence_pack"]).exists()


def test_autoresearch_review_gate_closeout_cli_rejects_invalid_artifact(
    tmp_path: Path,
    capsys,
) -> None:
    artifact = tmp_path / "synth.json"
    artifact.write_text(json.dumps({
        "schema_version": "autoresearch.review_council.v1",
        "decision": "approve",
        "root_cause": "known",
        "minimal_patch_scope": ["src/zf/autoresearch/review_gate.py"],
        "regression_commands": [],
        "evidence_refs": ["evt-1"],
        "repair_authorization_recommendation": "manual",
    }), encoding="utf-8")

    rc = main([
        "autoresearch",
        "review-gate",
        "closeout",
        "--run-dir",
        str(tmp_path / "run"),
        "--synth-artifact",
        str(artifact),
    ])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is False
    assert "decision=approve requires non-empty regression_commands" in payload["errors"]
