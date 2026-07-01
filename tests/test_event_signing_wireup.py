"""P2.1 — HMAC event signing wire-up.

Verifies the security.event_signing config path: zf.yaml → loader →
start.py constructs EventSigner from env var → EventLog signs every
appended event → reading verifies and decodes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from zf.cli.start import _build_event_signer
from zf.core.config.loader import load_config
from zf.core.config.schema import SecurityConfig, EventSigningConfig, ZfConfig
from zf.core.events.factory import EventSigningConfigError
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.signing import EventSigner


def _write_yaml(path: Path, security: dict) -> None:
    path.write_text(yaml.dump({
        "version": "1.0",
        "project": {"name": "test"},
        "security": security,
    }))


class TestLoaderParsesSecurity:
    def test_default_security_disabled(self, tmp_path: Path):
        cfg_path = tmp_path / "zf.yaml"
        cfg_path.write_text(yaml.dump({"version": "1.0", "project": {"name": "t"}}))
        config = load_config(cfg_path)
        assert config.security.event_signing.enabled is False
        assert config.security.event_signing.secret_env == "ZF_EVENT_SECRET"

    def test_loader_picks_up_event_signing_block(self, tmp_path: Path):
        cfg_path = tmp_path / "zf.yaml"
        _write_yaml(cfg_path, {
            "event_signing": {"enabled": True, "secret_env": "MY_HMAC_KEY"},
        })
        config = load_config(cfg_path)
        assert config.security.event_signing.enabled is True
        assert config.security.event_signing.secret_env == "MY_HMAC_KEY"


class TestBuildEventSigner:
    def test_disabled_returns_none(self):
        config = ZfConfig(security=SecurityConfig(
            event_signing=EventSigningConfig(enabled=False),
        ))
        assert _build_event_signer(config) is None

    def test_enabled_with_missing_env_fails_closed_by_default(
        self, monkeypatch
    ):
        monkeypatch.delenv("ZF_NONEXISTENT_TEST_KEY", raising=False)
        config = ZfConfig(security=SecurityConfig(
            event_signing=EventSigningConfig(
                enabled=True, secret_env="ZF_NONEXISTENT_TEST_KEY",
            ),
        ))
        with pytest.raises(EventSigningConfigError) as exc_info:
            _build_event_signer(config)
        assert "ZF_NONEXISTENT_TEST_KEY" in str(exc_info.value)

    def test_enabled_with_missing_env_falls_back_when_allowed(
        self, monkeypatch, capsys
    ):
        monkeypatch.delenv("ZF_NONEXISTENT_TEST_KEY", raising=False)
        config = ZfConfig(security=SecurityConfig(
            event_signing=EventSigningConfig(
                enabled=True,
                secret_env="ZF_NONEXISTENT_TEST_KEY",
                allow_unsigned_fallback=True,
            ),
        ))
        signer = _build_event_signer(config)
        assert signer is None
        assert "ZF_NONEXISTENT_TEST_KEY" in capsys.readouterr().err

    def test_enabled_with_env_returns_signer(self, monkeypatch):
        monkeypatch.setenv("ZF_TEST_SIGNING_SECRET", "shhhh-it-is-secret")
        config = ZfConfig(security=SecurityConfig(
            event_signing=EventSigningConfig(
                enabled=True, secret_env="ZF_TEST_SIGNING_SECRET",
            ),
        ))
        signer = _build_event_signer(config)
        assert isinstance(signer, EventSigner)


class TestSignedRoundTrip:
    def test_appended_events_are_signed_and_verify_on_read(self, tmp_path: Path):
        signer = EventSigner(b"the-shared-secret")
        log_path = tmp_path / "events.jsonl"
        log = EventLog(log_path, signer=signer)
        log.append(ZfEvent(type="test.event", actor="zf-cli", payload={"x": 1}))

        # Raw line is a signed envelope
        raw = log_path.read_text().strip().splitlines()[0]
        assert '"sig"' in raw
        assert '"event"' in raw

        # Read back: signer verifies, returns the original event
        log2 = EventLog(log_path, signer=signer)
        events = list(log2.read_all())
        assert len(events) == 1
        assert events[0].type == "test.event"
        assert events[0].payload["x"] == 1

    def test_tampered_event_fails_verification(self, tmp_path: Path):
        signer = EventSigner(b"the-shared-secret")
        log_path = tmp_path / "events.jsonl"
        log = EventLog(log_path, signer=signer)
        log.append(ZfEvent(type="test.event", actor="zf-cli", payload={"x": 1}))

        # Tamper: rewrite the line's payload but keep the old sig
        original = log_path.read_text()
        tampered = original.replace('"x": 1', '"x": 999')
        log_path.write_text(tampered)

        # Reader with the same signer rejects (returns None for that line)
        log2 = EventLog(log_path, signer=signer)
        events = list(log2.read_all())
        # The malformed/rejected line is silently dropped
        assert events == []


class TestUnsignedLineRejection:
    """A signed log must not accept plain (unsigned) event lines.

    Injecting one unsigned `judge.passed` line into events.jsonl would
    otherwise bypass the entire signing feature (2026-06-10 review P0-2).
    """

    def _forged_line(self) -> str:
        return (
            '{"id": "ev-forged", "type": "review.approved", '
            '"actor": "attacker", "payload": {}, '
            '"ts": "2026-06-10T00:00:00+00:00"}'
        )

    def test_signer_enabled_rejects_plain_line_on_read_all(self, tmp_path: Path):
        signer = EventSigner(b"the-shared-secret")
        log_path = tmp_path / "events.jsonl"
        log = EventLog(log_path, signer=signer)
        log.append(ZfEvent(type="test.event", actor="zf-cli", payload={}))
        with log_path.open("a", encoding="utf-8") as f:
            f.write(self._forged_line() + "\n")

        log2 = EventLog(log_path, signer=signer)
        events = list(log2.read_all())
        types = [e.type for e in events]
        assert "review.approved" not in types
        # Surfaced as a tamper signal, not silently dropped
        malformed = [e for e in events if e.type == "event.malformed"]
        assert len(malformed) == 1
        assert "unsigned" in malformed[0].payload["error"]

    def test_signer_enabled_rejects_plain_line_on_decode_line(self, tmp_path: Path):
        signer = EventSigner(b"the-shared-secret")
        log = EventLog(tmp_path / "events.jsonl", signer=signer)
        ev = log.decode_line(self._forged_line())
        assert ev is not None
        assert ev.type == "event.malformed"

    def test_allow_unsigned_keeps_legacy_tolerance(self, tmp_path: Path):
        signer = EventSigner(b"the-shared-secret")
        log_path = tmp_path / "events.jsonl"
        log_path.write_text(self._forged_line() + "\n")
        log = EventLog(log_path, signer=signer, allow_unsigned=True)
        events = list(log.read_all())
        assert [e.type for e in events] == ["review.approved"]

    def test_no_signer_keeps_plain_lines_working(self, tmp_path: Path):
        log_path = tmp_path / "events.jsonl"
        log_path.write_text(self._forged_line() + "\n")
        log = EventLog(log_path)
        events = list(log.read_all())
        assert [e.type for e in events] == ["review.approved"]

    def test_factory_threads_allow_unsigned_fallback(self, tmp_path: Path, monkeypatch):
        from zf.core.events.factory import event_log_from_project

        monkeypatch.setenv("ZF_EVENT_SECRET", "s3cret")
        config = ZfConfig(security=SecurityConfig(
            event_signing=EventSigningConfig(
                enabled=True, allow_unsigned_fallback=True,
            ),
        ))
        log = event_log_from_project(tmp_path, config=config)
        assert log.signer is not None
        assert log.allow_unsigned is True

        strict = ZfConfig(security=SecurityConfig(
            event_signing=EventSigningConfig(enabled=True),
        ))
        strict_log = event_log_from_project(tmp_path, config=strict)
        assert strict_log.signer is not None
        assert strict_log.allow_unsigned is False
