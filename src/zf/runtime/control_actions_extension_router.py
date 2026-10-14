"""Late-bound routes that keep the core controlled-action switch stable."""

from __future__ import annotations

from typing import Any

from zf.core.events import ZfEvent


_EXTENSION_HANDLERS = {
    "channel-profile-register": "_channel_profile_register",
    "candidate-rework-apply": "_candidate_rework_apply",
    "evolution-asset-outcome": "_evolution_asset_outcome",
    "evolution-asset-transition": "_evolution_asset_transition",
    "execution-route-switch": "_execution_route_switch",
    "run-contract-review": "_run_contract_review_action",
    "workflow-batch-resume": "_workflow_batch_resume",
}


def dispatch_extension_action(
    service: Any,
    *,
    requested: ZfEvent,
    action: str,
    requested_action: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Dispatch an isolated action family, or return None when unregistered."""

    handler_name = _EXTENSION_HANDLERS.get(action)
    if handler_name is None:
        return None
    return getattr(service, handler_name)(
        requested=requested,
        action=action,
        requested_action=requested_action,
        payload=payload,
    )


__all__ = ["dispatch_extension_action"]
