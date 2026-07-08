"""First-run welcome onboarding gate — global flag, resume, suppress."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.workspace.onboarding import (
    apply_action,
    detect_backends,
    read_onboarding,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "home"))


def test_fresh_install_shows_welcome() -> None:
    state = read_onboarding()
    assert state.show_welcome is True
    assert state.step == 1
    assert state.completed is False


def test_complete_suppresses_permanently() -> None:
    apply_action("complete", backend="claude-code", now="2026-07-07T00:00:00+00:00")
    state = read_onboarding()
    assert state.completed is True
    assert state.show_welcome is False
    assert state.backend == "claude-code"
    assert state.completed_at == "2026-07-07T00:00:00+00:00"


def test_skip_suppresses_permanently() -> None:
    apply_action("skip")
    assert read_onboarding().show_welcome is False


def test_step_persists_for_resume() -> None:
    apply_action("step", step=3, backend="codex")
    state = read_onboarding()
    assert state.step == 3
    assert state.backend == "codex"
    assert state.show_welcome is True  # mid-wizard still shows


def test_reset_re_arms_wizard() -> None:
    apply_action("complete", now="t")
    assert read_onboarding().show_welcome is False
    apply_action("reset")
    state = read_onboarding()
    assert state.show_welcome is True
    assert state.step == 1


def test_invalid_action_rejected() -> None:
    with pytest.raises(ValueError):
        apply_action("bogus")


def test_detect_backends_mock_always_available() -> None:
    backends = {b["id"]: b for b in detect_backends()}
    assert backends["mock"]["always_available"] is True
    assert backends["mock"]["detected"] is True
    assert "claude-code" in backends and "codex" in backends
