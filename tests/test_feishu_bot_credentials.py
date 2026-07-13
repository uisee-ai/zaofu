from __future__ import annotations

from zf.core.config.loader import _build_integrations
from zf.core.config.schema import ZfConfig
from zf.integrations.feishu.bot_credentials import (
    credential_for_purpose,
    inbound_bot_specs_for_config,
)


def _cfg(routing: dict) -> ZfConfig:
    return ZfConfig(integrations=_build_integrations({"feishu_routing": routing}))


def test_credential_for_purpose_uses_run_manager_bot_and_aliases() -> None:
    env = {
        "FEISHU_RUN_MANAGER_APP_ID": "cli_arch",
        "FEISHU_RUN_MANAGER_APP_SECRET": "secret_arch",
        "FEISHU_APP_ID": "cli_default",
        "FEISHU_APP_SECRET": "secret_default",
    }

    cred = credential_for_purpose("run_manager", env=env)

    assert cred is not None
    assert cred.app_id == "cli_arch"
    assert cred.app_secret == "secret_arch"
    assert cred.fallback is False


def test_inbound_bot_specs_follow_app_scoped_routes_and_dedupe_fallback() -> None:
    env = {
        "FEISHU_RUNM": "cli_arch",
        "FEISHU_RUNM_SECRET": "secret_arch",
        "FEISHU_KANBAN": "cli_pm",
        "FEISHU_KANBAN_SECRET": "secret_pm",
        "FEISHU_APP_ID": "cli_default",
        "FEISHU_APP_SECRET": "secret_default",
    }
    cfg = _cfg({
        "cli_arch:oc_group": {"target": "run_manager"},
        "cli_pm:oc_group": {"target": "kanban_agent"},
        "oc_group": {"target": "channel"},
    })

    specs = inbound_bot_specs_for_config(cfg, env=env)

    assert [spec.purpose for spec in specs] == [
        "run_manager",
        "kanban_agent",
        "default",
    ]
    assert [spec.credential.app_id for spec in specs] == [
        "cli_arch",
        "cli_pm",
        "cli_default",
    ]


def test_inbound_bot_specs_uses_one_default_when_specific_bots_missing() -> None:
    env = {"FEISHU_APP_ID": "cli_default", "FEISHU_APP_SECRET": "secret_default"}
    cfg = _cfg({
        "oc_group": {"target": "run_manager"},
        "oc_pm": {"target": "kanban_agent"},
    })

    specs = inbound_bot_specs_for_config(cfg, env=env)

    assert len(specs) == 1
    assert specs[0].purpose == "run_manager"
    assert specs[0].credential.fallback is True
