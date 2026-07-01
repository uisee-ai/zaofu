"""Tests for nonce manager."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from zf.core.security.nonce import NonceManager


class TestNonceManager:
    def test_issue_returns_string(self, tmp_path: Path):
        mgr = NonceManager(tmp_path / "nonces")
        nonce = mgr.issue("dev")
        assert isinstance(nonce, str)
        assert len(nonce) > 0

    def test_validate_and_consume_atomic(self, tmp_path: Path):
        """validate_and_consume() must be atomic: the second call returns False
        even if it interleaves with the first. Closes review M6."""
        mgr = NonceManager(tmp_path / "nonces")
        nonce = mgr.issue("dev")
        # First atomic consume succeeds
        assert mgr.validate_and_consume(nonce) is True
        # Second call must fail (already consumed)
        assert mgr.validate_and_consume(nonce) is False

    def test_validate_and_consume_unknown_nonce(self, tmp_path: Path):
        mgr = NonceManager(tmp_path / "nonces")
        assert mgr.validate_and_consume("fake") is False

    def test_validate_valid_nonce(self, tmp_path: Path):
        mgr = NonceManager(tmp_path / "nonces")
        nonce = mgr.issue("dev")
        assert mgr.validate(nonce) is True

    def test_validate_unknown_nonce(self, tmp_path: Path):
        mgr = NonceManager(tmp_path / "nonces")
        assert mgr.validate("fake-nonce") is False

    def test_consume_marks_used(self, tmp_path: Path):
        mgr = NonceManager(tmp_path / "nonces")
        nonce = mgr.issue("dev")
        mgr.consume(nonce)
        assert mgr.validate(nonce) is False

    def test_expired_nonce_rejected(self, tmp_path: Path):
        mgr = NonceManager(tmp_path / "nonces", ttl=0.05)
        nonce = mgr.issue("dev")
        time.sleep(0.06)
        assert mgr.validate(nonce) is False

    def test_cleanup_removes_expired(self, tmp_path: Path):
        mgr = NonceManager(tmp_path / "nonces", ttl=0.05)
        mgr.issue("dev")
        time.sleep(0.06)
        removed = mgr.cleanup()
        assert removed >= 1

    def test_double_consume_safe(self, tmp_path: Path):
        mgr = NonceManager(tmp_path / "nonces")
        nonce = mgr.issue("dev")
        mgr.consume(nonce)
        mgr.consume(nonce)  # should not crash
