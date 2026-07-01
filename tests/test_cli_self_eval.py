from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main


def _write_contract(
    tmp_path: Path,
    *,
    verify: str = "python3 -c 'print(\"score=7\")'",
    guard: str = "python3 -c 'print(\"guard ok\")'",
    pattern: str = r"score=(?P<score>\d+)",
) -> Path:
    contract_path = tmp_path / "contract.yaml"
    out_dir = tmp_path / "out"
    contract_path.write_text(
        f"""
version: 1
goal: Verify self-eval runner.
scope:
  allow:
    - src/zf/**
metric:
  name: score
  direction: higher_is_better
  pattern: {json.dumps(pattern)}
verify:
  command: {json.dumps(verify)}
guards:
  - name: guard
    command: {json.dumps(guard)}
output:
  dir: {out_dir}
""",
        encoding="utf-8",
    )
    return contract_path


def test_self_eval_validate_accepts_valid_contract(tmp_path: Path, capsys):
    contract = _write_contract(tmp_path)

    assert main(["self-eval", "validate", "--contract", str(contract)]) == 0

    out = capsys.readouterr().out
    assert "OK: self-eval contract valid" in out


def test_self_eval_validate_rejects_provider_wrapper(tmp_path: Path, capsys):
    contract = _write_contract(tmp_path, verify="bash -lc 'codex exec run'")

    assert main(["self-eval", "validate", "--contract", str(contract)]) == 1

    out = capsys.readouterr().out
    assert "shell -c" in out


def test_self_eval_run_writes_iterations_and_summary(tmp_path: Path, capsys):
    contract = _write_contract(tmp_path)
    out_dir = tmp_path / "override-out"

    assert main([
        "self-eval",
        "run",
        "--contract",
        str(contract),
        "--output",
        str(out_dir),
    ]) == 0

    assert (out_dir / "iterations.tsv").exists()
    assert (out_dir / "summary.md").exists()
    assert "passed" in (out_dir / "iterations.tsv").read_text(encoding="utf-8")
    assert "score=7.0" in capsys.readouterr().out


def test_self_eval_run_fails_closed_on_guard_failure(tmp_path: Path):
    sentinel = tmp_path / "verify-ran"
    contract = _write_contract(
        tmp_path,
        guard="python3 -c 'raise SystemExit(3)'",
        verify=(
            "python3 -c 'from pathlib import Path; "
            f"Path({str(sentinel)!r}).write_text(\"ran\"); print(\"score=9\")'"
        ),
    )

    assert main(["self-eval", "run", "--contract", str(contract)]) == 1

    assert not sentinel.exists()
    text = (tmp_path / "out" / "iterations.tsv").read_text(encoding="utf-8")
    assert "guard" in text
    assert "failed" in text


def test_self_eval_run_fails_closed_on_missing_metric(tmp_path: Path):
    contract = _write_contract(tmp_path, verify="python3 -c 'print(\"no metric\")'")

    assert main(["self-eval", "run", "--contract", str(contract)]) == 1

    summary = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    assert "missing numeric metric evidence" in summary


def test_self_eval_failure_can_write_backlog_task(tmp_path: Path, capsys):
    contract = _write_contract(tmp_path, verify="python3 -c 'print(\"no metric\")'")
    state_dir = tmp_path / ".zf"

    assert main([
        "self-eval",
        "run",
        "--contract",
        str(contract),
        "--state-dir",
        str(state_dir),
        "--backlog-on-failure",
    ]) == 1

    kanban = json.loads((state_dir / "kanban.json").read_text(encoding="utf-8"))
    assert len(kanban) == 1
    task = kanban[0]
    assert task["status"] == "backlog"
    assert task["key"].startswith("self-eval:")
    assert task["title"].startswith("Fix self-eval failure:")
    assert task["priority"] == 2
    assert task["contract"]["owner_role"] == "dev"
    assert task["contract"]["verification_tiers"] == ["runtime"]
    assert "self-eval run" in task["contract"]["verification"]
    assert "missing numeric metric evidence" in task["contract"]["behavior"]

    events = [
        json.loads(line)
        for line in (state_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["type"] == "task.created"
    assert events[-1]["actor"] == "zf-self-eval"
    assert events[-1]["task_id"] == task["id"]
    assert events[-1]["payload"]["source"] == "self-eval"
    assert events[-1]["payload"]["reason"] == "missing numeric metric evidence for 'score'"
    assert "Backlog task created:" in capsys.readouterr().out


def test_self_eval_backlog_write_is_idempotent_for_open_task(tmp_path: Path):
    contract = _write_contract(tmp_path, verify="python3 -c 'print(\"no metric\")'")
    state_dir = tmp_path / ".zf"
    args = [
        "self-eval",
        "run",
        "--contract",
        str(contract),
        "--state-dir",
        str(state_dir),
        "--backlog-on-failure",
    ]

    assert main(args) == 1
    assert main(args) == 1

    kanban = json.loads((state_dir / "kanban.json").read_text(encoding="utf-8"))
    assert len(kanban) == 1
    events = [
        json.loads(line)
        for line in (state_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["type"] for event in events] == ["task.created", "task.updated"]
    assert events[0]["task_id"] == events[1]["task_id"] == kanban[0]["id"]


def test_self_eval_pass_does_not_write_backlog_task(tmp_path: Path):
    contract = _write_contract(tmp_path)
    state_dir = tmp_path / ".zf"

    assert main([
        "self-eval",
        "run",
        "--contract",
        str(contract),
        "--state-dir",
        str(state_dir),
        "--backlog-on-failure",
    ]) == 0

    assert not (state_dir / "kanban.json").exists()
