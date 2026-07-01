from __future__ import annotations

from pathlib import Path


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
