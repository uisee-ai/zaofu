"""Read-model selection for the bounded Channel conversation endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.runtime.channel_conversation_projection import project_channel_conversation
from zf.runtime.control_actions_helpers import _normal_channel_id
from zf.web.projections import read_model


def build_channel_conversation_page(
    state_dir: Path,
    channel_id: str,
    *,
    config: ZfConfig | None,
    limit: int,
    before: str,
) -> dict[str, Any] | None:
    """Use a fresh channel slice when indexed; fall back to the event ledger."""

    indexed_events = _channel_events(state_dir, channel_id, config=config)
    return project_channel_conversation(
        state_dir,
        channel_id,
        limit=limit,
        before=before,
        events=indexed_events,
    )


def _channel_events(
    state_dir: Path,
    channel_id: str,
    *,
    config: ZfConfig | None,
) -> list[Any] | None:
    try:
        events = read_model.hydrate_events_by_ref(
            state_dir,
            ref_kind="channel",
            ref_id=channel_id,
            config=config,
            require_fresh=True,
        )
        canonical_channel_id = _normal_channel_id(channel_id)
        if events == [] and canonical_channel_id and canonical_channel_id != channel_id:
            return read_model.hydrate_events_by_ref(
                state_dir,
                ref_kind="channel",
                ref_id=canonical_channel_id,
                config=config,
                require_fresh=True,
            )
        return events
    except Exception:
        # The ledger remains authoritative; an unavailable projection must not
        # make an existing Channel unreadable.
        return None
