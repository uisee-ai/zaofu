from __future__ import annotations

import hashlib
from pathlib import Path

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


def test_hermes_run_report_cli_is_read_only(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-dev",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_head_commit": "abc123",
            "candidate_base_commit": "base123",
        },
    ))
    writer.append(ZfEvent(
        type="fanout.child.failed",
        actor="zf-cli",
        payload={
            "reason": "missing_upstream_affinity_fanout",
            "child_id": "verify-lane-1",
        },
    ))
    writer.append(ZfEvent(
        type="candidate.quality.failed",
        actor="zf-cli",
        payload={"reason": "candidate-quality-failed"},
    ))
    writer.append(ZfEvent(type="verify.passed", actor="verify"))
    writer.append(ZfEvent(type="judge.passed", actor="judge"))

    before = _sha256(state_dir / "events.jsonl")
    out = tmp_path / "reports" / "hermes-r37.md"

    rc = main([
        "report",
        "hermes-run",
        "--state-dir",
        str(state_dir),
        "--out",
        str(out),
    ])

    assert rc == 0
    assert _sha256(state_dir / "events.jsonl") == before
    text = out.read_text(encoding="utf-8")
    assert "candidate.ready" in text
    assert "verify.passed" in text
    assert "judge.passed" in text
    assert "missing_upstream_affinity_fanout" in text
    assert "candidate-quality-failed" in text
    assert "cand/CJMIN-R37" in text


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
