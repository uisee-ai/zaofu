"""feishu-B: inbound callback trust model (signature + identity + fail-closed).

Covers the backlog acceptance criteria: forged signature, replay, unauthorized
principal, authorized mapping, missing-config fail-closed, and idempotency.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from zf.cli.feishu import _handle_event_data
from zf.cli.main import main
from zf.core.config.schema import (
    FeishuIdentityConfig,
    FeishuIdentityUserConfig,
)
from zf.core.events.log import EventLog
from zf.core.config.project_context import resolve_project_context
from zf.integrations.feishu.callback_security import (
    identity_auth_levels,
    resolve_identity,
    verify_feishu_signature,
)
from zf.integrations.feishu.gateway import AuthLevel


def _sign(timestamp: str, nonce: str, token: str, body: bytes) -> str:
    return hashlib.sha256(
        timestamp.encode() + nonce.encode() + token.encode() + body
    ).hexdigest()


# --- signature primitive (acceptance #1, #2) -------------------------------

def test_valid_signature_accepted():
    body = b'{"ok": true}'
    sig = _sign("1000", "n1", "tok", body)
    ok, reason = verify_feishu_signature(
        timestamp="1000", nonce="n1", token="tok", body=body,
        signature=sig, now=1000.0, max_age_seconds=300,
    )
    assert ok and reason == "ok"


def test_forged_signature_rejected():
    body = b'{"ok": true}'
    ok, reason = verify_feishu_signature(
        timestamp="1000", nonce="n1", token="tok", body=body,
        signature="deadbeef", now=1000.0, max_age_seconds=300,
    )
    assert not ok and reason == "signature_mismatch"


def test_replayed_stale_timestamp_rejected():
    body = b'{"ok": true}'
    sig = _sign("1000", "n1", "tok", body)  # signature itself valid
    ok, reason = verify_feishu_signature(
        timestamp="1000", nonce="n1", token="tok", body=body,
        signature=sig, now=2000.0, max_age_seconds=300,  # 1000s later
    )
    assert not ok and reason == "stale_timestamp"


def test_missing_token_fails_closed():
    ok, reason = verify_feishu_signature(
        timestamp="1000", nonce="n", token="", body=b"x",
        signature="whatever", now=1000.0,
    )
    assert not ok and reason == "no_verification_token"


# --- identity mapper fail-closed (acceptance #5) ---------------------------

def test_resolve_identity_disabled_returns_none():
    cfg = FeishuIdentityConfig(
        enabled=False,
        users={"ou_x": FeishuIdentityUserConfig("alice", "approver")},
    )
    assert resolve_identity(cfg, "ou_x") is None


def test_resolve_identity_unmapped_returns_none():
    cfg = FeishuIdentityConfig(enabled=True, users={})
    assert resolve_identity(cfg, "ou_attacker") is None


def test_resolve_identity_bad_level_returns_none():
    cfg = FeishuIdentityConfig(
        enabled=True,
        users={"ou_x": FeishuIdentityUserConfig("alice", "superuser")},
    )
    assert resolve_identity(cfg, "ou_x") is None


def test_resolve_identity_maps_operator_and_level():
    cfg = FeishuIdentityConfig(
        enabled=True,
        users={"ou_x": FeishuIdentityUserConfig("alice", "approver")},
    )
    assert resolve_identity(cfg, "ou_x") == ("alice", AuthLevel.APPROVER)


def test_identity_auth_levels_skips_invalid_entries():
    cfg = FeishuIdentityConfig(
        enabled=True,
        users={
            "ou_ok": FeishuIdentityUserConfig("alice", "operator"),
            "ou_bad": FeishuIdentityUserConfig("bob", "wizard"),
        },
    )
    levels = identity_auth_levels(cfg)
    assert levels == {"ou_ok": AuthLevel.OPERATOR}


# --- inbound integration (acceptance #3, #4, #6) ---------------------------

@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "feishu-b-test", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {
            "feishu_identity": {
                "enabled": True,
                "users": {
                    "ou_boss": {"operator": "alice", "level": "operator"},
                    "ou_approver": {"operator": "carol", "level": "approver"},
                },
            }
        },
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    main(["init"])
    return tmp_path


def _button(action: str, user_id: str, message_id: str) -> dict:
    return {
        "type": "button_action",
        "payload": {"action": action, "message_id": message_id},
        "user_id": user_id,
        "chat_id": "c1",
    }


def _events(state_dir: Path) -> list:
    return EventLog(state_dir / "events.jsonl").read_all()


def test_unauthorized_principal_rejected_and_audited(project: Path):
    ctx = resolve_project_context()
    result = _handle_event_data(
        _button("approve:plan-1", "ou_attacker", "m1"),
        context=ctx, user_levels={},
    )
    assert result["status"] == "rejected"
    rejected = [e for e in _events(ctx.state_dir) if e.type == "callback.rejected"]
    assert rejected and rejected[0].payload["reason"] == "identity.unmapped"
    # no approval mutation written
    assert not [e for e in _events(ctx.state_dir) if e.type == "plan.approved"]


def test_cli_user_level_cannot_override_identity_map(project: Path):
    # An operator passing --user-level granting the attacker APPROVER must be
    # ignored: the config identity map is the sole source of permissions.
    ctx = resolve_project_context()
    result = _handle_event_data(
        _button("approve:plan-1", "ou_attacker", "m2"),
        context=ctx, user_levels={"ou_attacker": AuthLevel.APPROVER},
    )
    assert result["status"] == "rejected"


def test_authorized_principal_passes_gate(project: Path):
    ctx = resolve_project_context()
    result = _handle_event_data(
        _button("note:hello", "ou_boss", "m3"),
        context=ctx, user_levels={},
    )
    assert result["status"] == "completed"
    assert not [e for e in _events(ctx.state_dir) if e.type == "callback.rejected"]


def test_duplicate_callback_is_idempotent(project: Path):
    ctx = resolve_project_context()
    data = _button("note:hello", "ou_boss", "m4")
    first = _handle_event_data(data, context=ctx, user_levels={})
    second = _handle_event_data(data, context=ctx, user_levels={})
    assert first["status"] == "completed"
    assert second["status"] == "duplicate"


# --- P0.3 plan-approval inbound (feishu-A, gated by feishu-B) ---------------

def test_plan_approve_button_by_approver_emits_plan_approved(project: Path):
    ctx = resolve_project_context()
    result = _handle_event_data(
        _button("plan-approve:plan-7", "ou_approver", "p1"),
        context=ctx, user_levels={},
    )
    assert result["ok"] is True
    approved = [e for e in _events(ctx.state_dir) if e.type == "plan.approved"]
    assert approved and approved[0].payload["plan_id"] == "plan-7"
    assert approved[0].payload["surface"] == "feishu"
    assert approved[0].actor == "operator"  # human clicks; agent only recommends


def test_plan_approve_button_by_operator_is_rejected(project: Path):
    # operator level is below APPROVER → fail-closed, no plan.approved.
    ctx = resolve_project_context()
    result = _handle_event_data(
        _button("plan-approve:plan-7", "ou_boss", "p2"),
        context=ctx, user_levels={},
    )
    assert result["status"] == "rejected"
    assert not [e for e in _events(ctx.state_dir) if e.type == "plan.approved"]


def test_plan_reject_button_requires_reason(project: Path):
    ctx = resolve_project_context()
    result = _handle_event_data(
        _button("plan-reject:plan-7", "ou_approver", "p3"),
        context=ctx, user_levels={},
    )
    assert result["ok"] is False
    assert not [e for e in _events(ctx.state_dir) if e.type == "plan.rejected"]


def test_plan_card_has_inline_approve_button():
    from zf.integrations.feishu.plan_approval_card import build_plan_approval_card
    card = build_plan_approval_card({"plan_id": "plan-7"}, web_base_url="http://w")
    assert "plan-approve:plan-7" in str(card)
