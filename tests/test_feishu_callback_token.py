"""feishu-A2: signed action tokens for card buttons (sign/verify + gated flow)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.cli.feishu import _handle_event_data, _issue_write_route_allowed
from zf.cli.feishu import _verify_signed_action
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.integrations.feishu.callback_token import (
    attach_action_token,
    sign_action,
    verify_action,
)
from zf.integrations.feishu.gateway import AuthLevel

SECRET = b"hmac-secret-key"
VERS = {"1": SECRET}


def _tok(now=1000.0, **kw):
    base = dict(action="plan-approve", target="plan-7", chat_id="c1",
               ttl_seconds=100, now=now)
    base.update(kw)
    return sign_action(SECRET, **base)


# --- pure sign/verify ------------------------------------------------------

def test_roundtrip_ok():
    ok, reason = verify_action(
        _tok(), secrets_by_version=VERS, expect_action="plan-approve",
        expect_target="plan-7", expect_chat_id="c1", now=1050.0)
    assert ok and reason == "ok"


@pytest.mark.parametrize("kw,exp_action,exp_target,exp_chat,now,reason", [
    ({}, "plan-approve", "plan-7", "c1", 2000.0, "expired"),
    ({}, "plan-reject", "plan-7", "c1", 1050.0, "action_mismatch"),
    ({}, "plan-approve", "plan-9", "c1", 1050.0, "target_mismatch"),
    ({}, "plan-approve", "plan-7", "c2", 1050.0, "chat_mismatch"),
])
def test_context_binding_rejects(kw, exp_action, exp_target, exp_chat, now, reason):
    ok, got = verify_action(
        _tok(**kw), secrets_by_version=VERS, expect_action=exp_action,
        expect_target=exp_target, expect_chat_id=exp_chat, now=now)
    assert not ok and got == reason


def test_forged_signature_rejected():
    ok, reason = verify_action(
        _tok()[:-6] + "abcdef", secrets_by_version=VERS,
        expect_action="plan-approve", expect_target="plan-7",
        expect_chat_id="c1", now=1050.0)
    assert not ok and reason == "signature_mismatch"


def test_unknown_key_version_rejected():
    ok, reason = verify_action(
        _tok(), secrets_by_version={"9": SECRET}, expect_action="plan-approve",
        expect_target="plan-7", expect_chat_id="c1", now=1050.0)
    assert not ok and reason == "unknown_key_version"


def test_nonce_single_use():
    seen: set[str] = set()
    consume = lambda n: (n not in seen) and (seen.add(n) or True)
    tok = _tok()
    a = verify_action(tok, secrets_by_version=VERS, expect_action="plan-approve",
                      expect_target="plan-7", expect_chat_id="c1", now=1050.0,
                      consume_nonce=consume)
    b = verify_action(tok, secrets_by_version=VERS, expect_action="plan-approve",
                      expect_target="plan-7", expect_chat_id="c1", now=1050.0,
                      consume_nonce=consume)
    assert a[0] is True and b == (False, "nonce_replay")


def test_attach_action_token_signs_buttons():
    card = {"elements": [{"tag": "action", "actions": [
        {"tag": "button", "value": {"action": "plan-approve:plan-7"}},
    ]}]}
    attach_action_token(card, secret=SECRET, chat_id="c1",
                        ttl_seconds=100, now=1000.0)
    token = card["elements"][0]["actions"][0]["value"]["t"]
    ok, _ = verify_action(token, secrets_by_version=VERS,
                          expect_action="plan-approve", expect_target="plan-7",
                          expect_chat_id="c1", now=1050.0)
    assert ok


def test_attach_action_token_signs_form_nested_button():
    card = {"elements": [{
        "tag": "form",
        "elements": [{"tag": "action", "actions": [
            {"tag": "button", "value": {"action": "kanban-plan-answer:evt~form"}},
        ]}],
    }]}

    attach_action_token(card, secret=SECRET, chat_id="c1", ttl_seconds=100, now=1000.0)

    token = card["elements"][0]["elements"][0]["actions"][0]["value"]["t"]
    ok, _ = verify_action(
        token,
        secrets_by_version=VERS,
        expect_action="kanban-plan-answer",
        expect_target="evt~form",
        expect_chat_id="c1",
        now=1050.0,
    )
    assert ok


# --- gated inbound flow (compat flag) --------------------------------------

def _project(tmp_path, monkeypatch, *, require_signed):
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "a2-test", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_identity": {
            "enabled": True,
            "require_signed_actions": require_signed,
            "users": {"ou_app": {"operator": "carol", "level": "approver"}},
        }},
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    main(["init"])
    return tmp_path


def _button(action: str, message_id: str, token: str = "") -> dict:
    value = {"action": action}
    if token:
        value["t"] = token
    return {
        "type": "button_action",
        "payload": {"action": action, "action_token": token,
                    "action_value": value, "message_id": message_id},
        "user_id": "ou_app",
        "chat_id": "c1",
    }


def _events(state_dir: Path):
    return EventLog(state_dir / "events.jsonl").read_all()


def test_compat_off_accepts_unsigned(tmp_path, monkeypatch):
    # require_signed_actions=False, no secret → in-flight unsigned cards work.
    _project(tmp_path, monkeypatch, require_signed=False)
    ctx = resolve_project_context()
    result = _handle_event_data(
        _button("plan-approve:plan-7", "m1"), context=ctx, user_levels={})
    assert result["ok"] is True
    assert [e for e in _events(ctx.state_dir) if e.type == "plan.approved"]


def test_require_on_rejects_unsigned(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, require_signed=True)
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", "s3cr3t")
    ctx = resolve_project_context()
    result = _handle_event_data(
        _button("plan-approve:plan-7", "m2"), context=ctx, user_levels={})
    assert result["status"] == "rejected"
    rej = [e for e in _events(ctx.state_dir) if e.type == "callback.rejected"]
    assert rej and rej[-1].payload["reason"] == "token.token_required"
    assert not [e for e in _events(ctx.state_dir) if e.type == "plan.approved"]


def test_require_on_accepts_valid_token(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, require_signed=True)
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", "s3cr3t")
    ctx = resolve_project_context()
    import time
    token = sign_action(b"s3cr3t", action="plan-approve", target="plan-7",
                        chat_id="c1", ttl_seconds=100, now=time.time())
    result = _handle_event_data(
        _button("plan-approve:plan-7", "m3", token=token),
        context=ctx, user_levels={})
    assert result["ok"] is True
    assert [e for e in _events(ctx.state_dir) if e.type == "plan.approved"]


def test_signed_duplicate_callback_is_idempotent_before_nonce_replay(
    tmp_path,
    monkeypatch,
):
    _project(tmp_path, monkeypatch, require_signed=True)
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", "s3cr3t")
    ctx = resolve_project_context()
    import time

    token = sign_action(
        b"s3cr3t",
        action="plan-approve",
        target="plan-7",
        chat_id="c1",
        ttl_seconds=100,
        now=time.time(),
    )

    first = _handle_event_data(
        _button("plan-approve:plan-7", "m3", token=token),
        context=ctx,
        user_levels={},
    )
    duplicate = _handle_event_data(
        _button("plan-approve:plan-7", "m3", token=token),
        context=ctx,
        user_levels={},
    )
    replay_with_new_message = _handle_event_data(
        _button("plan-approve:plan-7", "m3-replay", token=token),
        context=ctx,
        user_levels={},
    )

    assert first["ok"] is True
    assert duplicate["status"] == "duplicate"
    assert replay_with_new_message["status"] == "rejected"
    assert len([e for e in _events(ctx.state_dir) if e.type == "plan.approved"]) == 1


def test_require_on_rejects_forged_target(tmp_path, monkeypatch):
    # token issued for plan-7 cannot approve plan-EVIL
    _project(tmp_path, monkeypatch, require_signed=True)
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", "s3cr3t")
    ctx = resolve_project_context()
    import time
    token = sign_action(b"s3cr3t", action="plan-approve", target="plan-7",
                        chat_id="c1", ttl_seconds=100, now=time.time())
    result = _handle_event_data(
        _button("plan-approve:plan-EVIL", "m4", token=token),
        context=ctx, user_levels={})
    assert result["status"] == "rejected"
    assert not [e for e in _events(ctx.state_dir) if e.type == "plan.approved"]


def test_issue_mutation_signature_binds_all_card_arguments(tmp_path, monkeypatch):
    """Multi-part Issue/Self-Issue card targets must not verify only arg 0."""
    _project(tmp_path, monkeypatch, require_signed=True)
    monkeypatch.setenv("ZF_FEISHU_ACTION_TOKEN_SECRET", "s3cr3t")
    ctx = resolve_project_context()
    captured: dict[str, str] = {}

    def fake_verify(token, **kwargs):
        captured.update({
            "action": kwargs["expect_action"],
            "target": kwargs["expect_target"],
            "chat": kwargs["expect_chat_id"],
        })
        return True, "ok"

    monkeypatch.setattr("zf.cli.feishu.verify_action", fake_verify)
    envelope = type("E", (), {
        "command": "self-issue-confirm", "args": ["batch-1", "digest-2"],
        "chat_id": "c1", "user_id": "ou_app",
    })()
    event = type("Event", (), {"payload": {"action_token": "signed"}})()
    assert _verify_signed_action(event, envelope, ctx) == (True, "ok")
    assert captured == {
        "action": "self-issue-confirm",
        "target": "batch-1:digest-2",
        "chat": "c1",
    }


def test_issue_write_requires_exact_project_chat_route(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0",
        "project": {"name": "route-test", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {
            "feishu_routing": {
                "oc_project": {"target": "channel", "channel_id": "dev"},
                "*": {"target": "channel", "channel_id": "wildcard"},
            },
        },
    }))
    main(["init"])
    ctx = resolve_project_context()
    event = type("E", (), {
        "chat_id": "oc_project", "payload": {},
    })()
    assert _issue_write_route_allowed(event, ctx) is True
    event.chat_id = "oc_other"
    assert _issue_write_route_allowed(event, ctx) is False
    result = _handle_event_data(
        _button("issue-triage-start:7:source-1", "route-m1"),
        context=ctx,
        user_levels={"ou_app": AuthLevel.OPERATOR},
    )
    assert result["status"] == "rejected"
    assert "exact Feishu project chat route" in result["message"]
