"""Keep mutable result drafts local to one delivery attempt."""

import json
from pathlib import Path
from typing import Any, Mapping

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import write_immutable_json_sidecar


def prepare_attempt_scratch(
    *, state_dir: Path, scratch: Path, operation_id: str, attempt_id: str,
    template: Mapping[str, Any],
) -> None:
    marker = scratch.with_name(scratch.name + ".attempt.json")
    identity = {"operation_id": operation_id, "attempt_id": attempt_id}
    with locked_path(scratch):
        previous = scratch.read_text(encoding="utf-8") if scratch.exists() else None
        try:
            bound = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            bound = {}
        if previous is not None and bound != identity:
            # Older releases had no marker. An Impl self-check can still prove
            # that this is a replay of the same attempt, without relabeling it.
            try:
                body = json.loads(previous)
                check = body.get("self_check") or {}
                same_legacy_attempt = not bound and check.get("attempt_id") == attempt_id
            except (ValueError, AttributeError):
                same_legacy_attempt = False
            if not same_legacy_attempt:
                write_immutable_json_sidecar(
                    state_dir,
                    {"operation_id": operation_id, "previous_binding": bound,
                     "superseding_attempt_id": attempt_id, "draft_text": previous},
                    root="retired-result-drafts", kind="retired_result_draft",
                    schema_version="retired-result-draft.v1", created_by="result-scratch-preparation",
                )
                previous = None
        if previous is None:
            atomic_write_text(scratch, json.dumps(dict(template), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        atomic_write_text(marker, json.dumps(identity, sort_keys=True) + "\n")
