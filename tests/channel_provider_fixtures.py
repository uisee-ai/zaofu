"""Explicit host isolation for Channel routing/projection tests."""

import pytest

from zf.web.headless_agent import ClaudeHeadlessBackend, CodexHeadlessBackend


@pytest.fixture
def unavailable_headless_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise unavailable-provider handling without discovering host CLIs."""
    monkeypatch.setattr(ClaudeHeadlessBackend, "available", lambda self: False)
    monkeypatch.setattr(CodexHeadlessBackend, "available", lambda self: False)
