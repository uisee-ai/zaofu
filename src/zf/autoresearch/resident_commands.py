"""Bounded resident loop CLI command and diagnostic input handoff."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from zf.autoresearch.loop_requests import (
    default_scenarios_for_mode, loop_request_id_from_payload, normalize_research_mode,
)

def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value or [] if str(item).strip()]


def _command_for_request(
    payload: dict[str, Any],
    *,
    worktree: Path,
    state_dir: Path,
    output_root: Path,
) -> list[str]:
    request_id = loop_request_id_from_payload(payload, fallback="request")
    mode = normalize_research_mode(payload.get("mode") or payload.get("research_mode"))
    scenarios = _string_list(payload.get("scenarios")) or default_scenarios_for_mode(mode)
    fix_wait = str(payload.get("fix_wait_strategy") or "none")
    return [
        sys.executable,
        "-m",
        "zf.cli.main",
        "autoresearch",
        "loop",
        "--scenarios",
        *scenarios,
        "--worktree",
        str(worktree),
        "--parent-state-dir",
        str(state_dir),
        "--max-iterations",
        "1",
        "--fix-wait-strategy",
        fix_wait,
        "--no-backlog-on-failure",
        "--output-dir",
        str(output_root / request_id),
        "--seed-text",
        json.dumps({
            "diagnostic_request_id": request_id,
            "source_state_dir": str(state_dir.resolve()),
            **{key: payload[key] for key in (
                "source_event_id", "source_event_ids", "workflow_run_id", "task_id",
                "recovery_case_id", "failure_class", "fingerprint", "evidence_paths",
                "evidence_refs", "handoff_ref", "source_insight_ref",
            ) if key in payload},
        }, ensure_ascii=False),
    ]
