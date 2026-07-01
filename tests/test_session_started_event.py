"""B-1203-03: session.started event must be emitted at harness boot so
phase reports can detect "harness actually started" vs "process crashed
before init". Currently only loop.started fires — session.started is
declared in known_types but never emitted.
"""

from __future__ import annotations

import ast
from pathlib import Path


def test_start_py_emits_session_started_before_loop_started():
    """Static check: start.py must emit session.started as the first
    real runtime event. Source-level assertion so we don't need a full
    harness boot in tests."""
    start_py = Path(__file__).resolve().parents[1] / "src" / "zf" / "cli" / "start.py"
    src = start_py.read_text()
    # Order matters: session.started should appear before loop.started
    sess_idx = src.find('"session.started"')
    loop_idx = src.find('"loop.started"')
    assert sess_idx != -1, "start.py must emit session.started"
    assert loop_idx != -1, "start.py must emit loop.started (regression guard)"
    assert sess_idx < loop_idx, (
        "session.started must be emitted before loop.started "
        f"(found at offset {sess_idx}, loop.started at {loop_idx})"
    )


def test_phase_report_p0_passes_with_session_started():
    """The w5_phase_report P0 assertion depends on session.started;
    after this fix a run that emits both session.started and loop.started
    will have P0=pass."""
    import json

    from tests.e2e.w5_phase_report import generate_report

    events_path = Path("/tmp/test_session_started_events.jsonl")
    events_path.write_text("\n".join(json.dumps(e) for e in [
        {"type": "session.started"},
        {"type": "loop.started"},
        {"type": "user.message"},
    ]))
    phases = generate_report(events_path)
    p0 = next(p for p in phases if "P0" in p.phase)
    assert p0.status == "pass", (
        f"P0 should pass with session.started emitted, got {p0.status}: "
        f"{p0.fail_reasons}"
    )
