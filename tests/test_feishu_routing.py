"""feishu-S3: feishu_routing binding layer (chat → channel/kanban/worker)."""

from __future__ import annotations

import pytest

from zf.core.config.loader import ConfigError, _build_integrations
from zf.core.config.schema import FeishuRouteConfig, ZfConfig, IntegrationsConfig
from zf.integrations.feishu.routing import resolve_feishu_route


def _cfg(routing: dict) -> ZfConfig:
    return ZfConfig(integrations=_build_integrations({"feishu_routing": routing}))


def test_channel_route_with_default_member():
    cfg = _cfg({"oc_A": {"target": "channel", "channel_id": "ch-dev",
                         "default_member": "dev"}})
    r = resolve_feishu_route(cfg, "oc_A")
    assert r == FeishuRouteConfig("channel", "ch-dev", "dev", "")


def test_kanban_agent_route():
    cfg = _cfg({"oc_dm": {"target": "kanban_agent"}})
    r = resolve_feishu_route(cfg, "oc_dm")
    assert r.target == "kanban_agent" and not r.channel_id


def test_run_manager_route():
    cfg = _cfg({"oc_rm": {"target": "run_manager"}})
    r = resolve_feishu_route(cfg, "oc_rm")
    assert r.target == "run_manager" and not r.channel_id


def test_multi_bot_route_prefers_bot_specific_key():
    cfg = _cfg({
        "oc_group": {"target": "kanban_agent"},
        "oc_group#ou_arch": {"target": "run_manager"},
        "cli_pm:oc_group": {"target": "kanban_agent"},
    })

    assert resolve_feishu_route(
        cfg,
        "oc_group",
        bot_open_id="ou_arch",
    ).target == "run_manager"
    assert resolve_feishu_route(
        cfg,
        "oc_group",
        app_id="cli_pm",
    ).target == "kanban_agent"


def test_worker_route():
    cfg = _cfg({"oc_w": {"target": "worker", "worker_session_id": "ws-1"}})
    assert resolve_feishu_route(cfg, "oc_w").worker_session_id == "ws-1"


def test_unmapped_chat_is_fail_closed_none():
    cfg = _cfg({"oc_A": {"target": "channel"}})
    assert resolve_feishu_route(cfg, "oc_unknown") is None
    assert resolve_feishu_route(cfg, "") is None


def test_no_routing_config_returns_none():
    assert resolve_feishu_route(ZfConfig(), "oc_A") is None
    assert resolve_feishu_route(None, "oc_A") is None


def test_invalid_target_rejected():
    with pytest.raises(ConfigError, match="target must be one of"):
        _build_integrations({"feishu_routing": {"oc_x": {"target": "robot"}}})


def test_worker_without_session_rejected():
    with pytest.raises(ConfigError, match="requires worker_session_id"):
        _build_integrations({"feishu_routing": {"oc_w": {"target": "worker"}}})


def test_routing_must_be_mapping():
    with pytest.raises(ConfigError, match="must be a mapping"):
        _build_integrations({"feishu_routing": ["nope"]})
