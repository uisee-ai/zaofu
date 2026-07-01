"""1203-T3: mixed_phase_report extends w5_phase_report with per-backend
telemetry (agent.usage backend split, codex.hook.* event count, codex
observe timeouts). Thin wrapper so the existing W5-E2E report still
works unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events))


def test_mixed_mode_reports_per_backend_usage(tmp_path: Path, capsys):
    from tests.e2e.mixed_phase_report import print_mixed_report

    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        {"type": "session.started"},
        {"type": "agent.usage", "actor": "dev-1",
         "payload": {"backend": "codex",
                     "usage": {"input_tokens": 1000, "output_tokens": 500}}},
        {"type": "agent.usage", "actor": "review-1",
         "payload": {"backend": "claude-code",
                     "usage": {"input_tokens": 500, "output_tokens": 200}}},
        {"type": "codex.hook.stop", "actor": "dev-1"},
        {"type": "codex.hook.pre_tool_use", "actor": "dev-1"},
    ])

    print_mixed_report(events_path)
    out = capsys.readouterr().out
    # Mixed backend breakdown heading
    assert "Mixed Backend Breakdown" in out
    # Per-backend call counts
    assert "codex" in out
    assert "claude-code" in out


def test_mixed_mode_flags_codex_observe_timeout(tmp_path: Path, capsys):
    from tests.e2e.mixed_phase_report import print_mixed_report

    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        {"type": "worker.spawn_warning", "actor": "zf-cli",
         "payload": {"code": "codex_observe_timeout", "role": "dev"}},
        {"type": "worker.spawn_warning", "actor": "zf-cli",
         "payload": {"code": "codex_observe_timeout", "role": "test"}},
    ])
    print_mixed_report(events_path)
    out = capsys.readouterr().out
    # Should surface codex observe timeouts as a mixed-only flag
    assert "codex_observe_timeout" in out
    assert "2" in out  # both events counted


def test_mixed_mode_counts_codex_hook_events(tmp_path: Path, capsys):
    from tests.e2e.mixed_phase_report import print_mixed_report

    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [
        {"type": "codex.hook.session_start"},
        {"type": "codex.hook.user_prompt_submit"},
        {"type": "codex.hook.pre_tool_use"},
        {"type": "codex.hook.post_tool_use"},
        {"type": "codex.hook.stop"},
    ])
    print_mixed_report(events_path)
    out = capsys.readouterr().out
    assert "codex.hook" in out.lower() or "codex hook" in out.lower()
    # All 5 counted
    assert "5" in out


def test_mixed_mode_returns_zero_when_empty(tmp_path: Path):
    """Empty events path → informative report, not a crash."""
    from tests.e2e.mixed_phase_report import print_mixed_report

    events_path = tmp_path / "events.jsonl"
    events_path.touch()
    rc = print_mixed_report(events_path)
    assert rc == 1  # no session.started → phase "not-reached" → rc=1
