"""β-3: zf bug-fix-cycle CLI.

Per docs/design/36 §4.5 + backlog beta-self-healing.md (β-3 section).

Wraps the operator playbook (β-2 markdown) into a CLI helper that:
  - reads the latest zaofu.bug.detected from a project .zf/events.jsonl
  - prints diagnosis + step-by-step instructions
  - --json flag for tooling / automation
  - --signature filter for selecting a specific bug pattern

This is a thin diagnostic surface; the actual stash / fix / restart /
resume is still operator-driven. β-3+ may automate further.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


def _make_state_with_bug(tmp_path: Path, *bugs) -> Path:
    """Build a minimal .zf/ with events.jsonl + the given bug events."""
    sd = tmp_path / ".zf"
    sd.mkdir()
    log = EventLog(sd / "events.jsonl")
    log.append(ZfEvent(type="session.started", actor="zf-cli"))
    log.append(ZfEvent(type="loop.started", actor="zf-cli"))
    for bug in bugs:
        log.append(bug)
    return sd


def _bug_event(
    signature: str = "ship_block_loop",
    confidence: str = "high",
    evidence: list[str] | None = None,
    suggested_fix_area: str = "src/zf/runtime/ship.py",
    snapshot: dict | None = None,
) -> ZfEvent:
    return ZfEvent(
        type="zaofu.bug.detected",
        actor="zf-cli",
        payload={
            "signature": signature,
            "confidence": confidence,
            "evidence_event_ids": evidence or ["evt-1", "evt-2"],
            "suggested_fix_area": suggested_fix_area,
            "run_state_snapshot": snapshot or {"pdd_id": "F-abcdef00"},
        },
    )


def _run_cli(*argv) -> tuple[int, str]:
    """Invoke bug_fix_cycle._run with parsed argparse Namespace."""
    from zf.cli.bug_fix_cycle import register, _run

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    register(sub)
    args = parser.parse_args(["bug-fix-cycle", *argv])

    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = _run(args)
    return code, buf.getvalue()


# ─── registration ────────────────────────────────────────────────────────


def test_cli_command_registered_in_main():
    """Sanity: `zf bug-fix-cycle` is wired into the main argparse."""
    from zf.cli.main import build_parser

    parser = build_parser()
    # `--help` exits 0; we just need to ensure parse_args doesn't choke
    # on the subcommand name.
    args = parser.parse_args(["bug-fix-cycle", "--state-dir", "/tmp/x"])
    assert args.command == "bug-fix-cycle"


# ─── no-bug case ─────────────────────────────────────────────────────────


def test_cli_reports_no_bug_found(tmp_path: Path):
    sd = _make_state_with_bug(tmp_path)  # no bug events

    code, out = _run_cli("--state-dir", str(sd))

    assert code == 1
    assert "No zaofu.bug.detected" in out


def test_cli_missing_events_file_returns_2(tmp_path: Path):
    # No .zf/ at all
    code, out = _run_cli("--state-dir", str(tmp_path / "absent"))

    assert code == 2
    assert "events.jsonl not found" in out


# ─── human-prose diagnosis ───────────────────────────────────────────────


def test_cli_prints_human_diagnosis_by_default(tmp_path: Path):
    sd = _make_state_with_bug(
        tmp_path,
        _bug_event(
            signature="ship_block_loop",
            confidence="high",
            evidence=["evt-foo", "evt-bar"],
            suggested_fix_area="src/zf/runtime/ship.py:_dirty_files",
            snapshot={"pdd_id": "F-12345678", "blockers": ["dirty"]},
        ),
    )

    code, out = _run_cli("--state-dir", str(sd))

    assert code == 0
    assert "ship_block_loop" in out
    assert "high" in out
    assert "ship.py:_dirty_files" in out
    assert "evt-foo" in out
    assert "evt-bar" in out
    assert "F-12345678" in out
    assert "Run state snapshot" in out
    # Playbook steps
    assert "stash" in out.lower()
    assert "restart" in out.lower()


def test_cli_reads_legacy_cangjie_snapshot_payload(tmp_path: Path):
    event = _bug_event(snapshot={"pdd_id": "F-legacy"})
    payload = dict(event.payload)
    payload["cangjie_state_snapshot"] = payload.pop("run_state_snapshot")
    event.payload = payload
    sd = _make_state_with_bug(tmp_path, event)

    code, out = _run_cli("--state-dir", str(sd))

    assert code == 0
    assert "F-legacy" in out


# ─── JSON output ─────────────────────────────────────────────────────────


def test_cli_json_output_is_valid_and_includes_payload(tmp_path: Path):
    sd = _make_state_with_bug(
        tmp_path,
        _bug_event(signature="respawn_failure_cascade"),
    )

    code, out = _run_cli("--state-dir", str(sd), "--json")

    assert code == 0
    parsed = json.loads(out)
    assert parsed["type"] == "zaofu.bug.detected"
    assert parsed["payload"]["signature"] == "respawn_failure_cascade"
    assert "event_id" in parsed


# ─── signature filter ────────────────────────────────────────────────────


def test_cli_signature_filter_picks_matching_bug(tmp_path: Path):
    sd = _make_state_with_bug(
        tmp_path,
        _bug_event(signature="ship_block_loop", evidence=["evt-1"]),
        _bug_event(signature="judge_failure_loop", evidence=["evt-2"]),
    )

    code, out = _run_cli(
        "--state-dir", str(sd),
        "--signature", "judge_failure_loop",
    )

    assert code == 0
    assert "judge_failure_loop" in out
    assert "evt-2" in out
    # The other one should NOT be the picked diagnosis
    assert out.count("evt-1") == 0 or "ship_block_loop" not in out


def test_cli_signature_filter_no_match_returns_1(tmp_path: Path):
    sd = _make_state_with_bug(
        tmp_path,
        _bug_event(signature="ship_block_loop"),
    )

    code, out = _run_cli(
        "--state-dir", str(sd),
        "--signature", "respawn_failure_cascade",
    )

    assert code == 1
    assert "No zaofu.bug.detected" in out


# ─── latest wins ─────────────────────────────────────────────────────────


def test_cli_picks_most_recent_bug_when_multiple_same_signature(tmp_path: Path):
    sd = _make_state_with_bug(
        tmp_path,
        _bug_event(signature="ship_block_loop", evidence=["evt-old"]),
        _bug_event(signature="ship_block_loop", evidence=["evt-new"]),
    )

    code, out = _run_cli("--state-dir", str(sd), "--json")

    assert code == 0
    parsed = json.loads(out)
    assert parsed["payload"]["evidence_event_ids"] == ["evt-new"]
