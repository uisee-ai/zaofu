"""Feishu bot credential selection.

Feishu may expose multiple product personas in one workspace. Runtime callers
should select credentials by purpose instead of assuming the global
``FEISHU_APP_ID`` bot is the right sender or inbound consumer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Any

from zf.integrations.feishu.transport import FeishuHttpTransport


@dataclass(frozen=True)
class FeishuBotCredential:
    purpose: str
    app_id: str
    app_secret: str
    app_id_env: str
    app_secret_env: str
    fallback: bool = False

    @property
    def app_label(self) -> str:
        if not self.app_id:
            return ""
        return self.app_id if len(self.app_id) <= 8 else f"...{self.app_id[-6:]}"


@dataclass(frozen=True)
class FeishuInboundBotSpec:
    purpose: str
    credential: FeishuBotCredential
    route_count: int = 0


_PURPOSE_ENVS = {
    "run_manager": ("FEISHU_RUNM", "FEISHU_RUNM_SECRET"),
    "kanban_agent": ("FEISHU_KANBAN", "FEISHU_KANBAN_SECRET"),
    "default": ("FEISHU_APP_ID", "FEISHU_APP_SECRET"),
}

_ENV_ALIASES = {
    "FEISHU_RUNM": ("FEISHU_RUN_MANAGER_APP_ID", "FEISHU_ARCHITECT_APP_ID"),
    "FEISHU_RUNM_SECRET": (
        "FEISHU_RUN_MANAGER_APP_SECRET",
        "FEISHU_ARCHITECT_APP_SECRET",
    ),
    "FEISHU_KANBAN": ("FEISHU_KANBAN_APP_ID", "FEISHU_PRODUCT_MANAGER_APP_ID"),
    "FEISHU_KANBAN_SECRET": (
        "FEISHU_KANBAN_APP_SECRET",
        "FEISHU_PRODUCT_MANAGER_APP_SECRET",
    ),
}


def credential_for_purpose(
    purpose: str,
    *,
    env: Mapping[str, str] | None = None,
    allow_fallback: bool = True,
) -> FeishuBotCredential | None:
    """Resolve credentials for a product bot purpose.

    ``run_manager`` maps to the ZF 架构师 bot (``FEISHU_RUNM``), while
    ``kanban_agent`` maps to the ZF 产品经理 bot (``FEISHU_KANBAN``). The
    default bot remains the fallback for compatibility.
    """

    env = env or os.environ
    normalized = (purpose or "default").strip() or "default"
    app_env, secret_env = _PURPOSE_ENVS.get(normalized, _PURPOSE_ENVS["default"])
    app_id = _env_get(env, app_env)
    app_secret = _env_get(env, secret_env)
    if app_id and app_secret:
        return FeishuBotCredential(
            purpose=normalized,
            app_id=app_id,
            app_secret=app_secret,
            app_id_env=app_env,
            app_secret_env=secret_env,
        )
    if not allow_fallback or normalized == "default":
        return None
    fallback = credential_for_purpose("default", env=env, allow_fallback=False)
    if fallback is None:
        return None
    return FeishuBotCredential(
        purpose=normalized,
        app_id=fallback.app_id,
        app_secret=fallback.app_secret,
        app_id_env=fallback.app_id_env,
        app_secret_env=fallback.app_secret_env,
        fallback=True,
    )


def transport_for_purpose(
    purpose: str,
    *,
    env: Mapping[str, str] | None = None,
) -> FeishuHttpTransport | None:
    credential = credential_for_purpose(purpose, env=env)
    if credential is None:
        return None
    return FeishuHttpTransport(app_id=credential.app_id, app_secret=credential.app_secret)


def inbound_bot_specs_for_config(
    config: object | None,
    *,
    env: Mapping[str, str] | None = None,
) -> list[FeishuInboundBotSpec]:
    """Return inbound bot processes needed by ``integrations.feishu_routing``.

    App-scoped route keys such as ``<app_id>:<chat_id>`` are the canonical
    signal. Target-only routes still start their purpose-specific bot when the
    matching env credential is present.
    """

    env = env or os.environ
    integrations = getattr(config, "integrations", None)
    routing = getattr(integrations, "feishu_routing", None)
    if not isinstance(routing, dict) or not routing:
        return []
    route_counts: dict[str, int] = {}
    for key, route in routing.items():
        target = _route_target(route)
        purpose = _purpose_from_route_key(str(key), target=target, env=env)
        if purpose:
            route_counts[purpose] = route_counts.get(purpose, 0) + 1
    specs: list[FeishuInboundBotSpec] = []
    seen_app_ids: set[str] = set()
    for purpose in _purpose_order(route_counts):
        credential = credential_for_purpose(purpose, env=env)
        if credential is None:
            continue
        # If purpose-specific variables are absent and both purposes fall back
        # to the same default bot, one inbound process is enough.
        if credential.app_id in seen_app_ids:
            continue
        seen_app_ids.add(credential.app_id)
        specs.append(FeishuInboundBotSpec(
            purpose=purpose,
            credential=credential,
            route_count=route_counts.get(purpose, 0),
        ))
    return specs


def _purpose_order(counts: Mapping[str, int]) -> list[str]:
    ordered = [purpose for purpose in ("run_manager", "kanban_agent", "default") if purpose in counts]
    ordered.extend(sorted(purpose for purpose in counts if purpose not in set(ordered)))
    return ordered


def _route_target(route: Any) -> str:
    if isinstance(route, dict):
        return str(route.get("target") or "")
    return str(getattr(route, "target", "") or "")


def _purpose_from_route_key(key: str, *, target: str, env: Mapping[str, str]) -> str:
    app_id = key.split(":", 1)[0] if ":" in key else ""
    if app_id and app_id != "*":
        for purpose, (app_env, _secret_env) in _PURPOSE_ENVS.items():
            if app_id == _env_get(env, app_env):
                return purpose
    if target == "run_manager":
        return "run_manager"
    if target == "kanban_agent":
        return "kanban_agent"
    return "default"


def _env_get(env: Mapping[str, str], name: str) -> str:
    value = str(env.get(name) or "")
    if value:
        return value
    for alias in _ENV_ALIASES.get(name, ()):
        value = str(env.get(alias) or "")
        if value:
            return value
    return ""
