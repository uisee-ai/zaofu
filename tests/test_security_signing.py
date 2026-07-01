"""Tests for event signing — HMAC-SHA256."""

from __future__ import annotations

from zf.core.security.signing import EventSigner


class TestEventSigner:
    def test_sign_returns_hex_string(self):
        signer = EventSigner(b"secret")
        sig = signer.sign('{"type": "test"}')
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA256 hex

    def test_verify_valid(self):
        signer = EventSigner(b"secret")
        data = '{"type": "test"}'
        sig = signer.sign(data)
        assert signer.verify(data, sig) is True

    def test_verify_tampered(self):
        signer = EventSigner(b"secret")
        sig = signer.sign('{"type": "test"}')
        assert signer.verify('{"type": "tampered"}', sig) is False

    def test_verify_wrong_key(self):
        signer1 = EventSigner(b"key1")
        signer2 = EventSigner(b"key2")
        data = '{"type": "test"}'
        sig = signer1.sign(data)
        assert signer2.verify(data, sig) is False

    def test_deterministic(self):
        signer = EventSigner(b"secret")
        data = '{"type": "test"}'
        assert signer.sign(data) == signer.sign(data)
