"""Request-scoped fanout runtime selection for task cards and detail."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zf.web.projections.fanouts import _fanout_child_projection, _fanout_progress


def _latest_task_fanout_runtimes(
    state_dir: Path,
    task_ids: set[str],
    *,
    include_report: bool = True,
) -> dict[str, dict[str, Any]]:
    """Read each manifest once for a board, retaining original seq/tie order."""
    from zf.web.projections.events import fanout_seq_maps

    last_seq_by_fanout, started_seq_by_fanout = fanout_seq_maps(state_dir)

    winners: dict[str, tuple] = {}
    for manifest_path in sorted((state_dir / "fanouts").glob("*/manifest.json")):
        fanout_id = manifest_path.parent.name
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        seq = started_seq_by_fanout.get(
            fanout_id,
            last_seq_by_fanout.get(fanout_id, 0),
        )
        children = [
            child for child in manifest.get("children", []) or []
            if isinstance(child, dict)
        ]
        for child in children:
            task_id = str(child.get("task_id") or "").strip()
            if task_id not in task_ids:
                continue
            if task_id not in winners or seq >= winners[task_id][0]:
                winners[task_id] = (seq, fanout_id, manifest, children, child)
    latest = {}
    for task_id, (seq, fanout_id, manifest, children, child) in winners.items():
        projected_child = _fanout_child_projection(child, include_report=include_report)
        projected = {
            "fanout_id": fanout_id,
            "stage_id": str(manifest.get("stage_id") or ""),
            "workflow_run_id": str(manifest.get("workflow_run_id") or ""),
            "fanout_status": str(manifest.get("status") or "observed"),
            "child": projected_child,
            "child_id": str(projected_child.get("child_id") or ""),
            "child_status": str(projected_child.get("status") or "observed"),
            "role_instance": str(projected_child.get("role_instance") or ""),
            "lane_id": str(child.get("lane_id") or ""),
            "affinity_tag": str(child.get("affinity_tag") or ""),
            "assignment_strategy": str(child.get("assignment_strategy") or ""),
            "pipeline_id": str(projected_child.get("pipeline_id") or ""),
            "root_fanout_id": str(projected_child.get("root_fanout_id") or ""),
            "upstream_fanout_id": str(projected_child.get("upstream_fanout_id") or ""),
            "upstream_child_id": str(projected_child.get("upstream_child_id") or ""),
            "upstream_stage_slot": str(projected_child.get("upstream_stage_slot") or ""),
            "run_id": str(projected_child.get("run_id") or ""),
            "source_branch": str(projected_child.get("source_branch") or ""),
            "source_commit": str(projected_child.get("source_commit") or ""),
            "task_ref": str(projected_child.get("task_ref") or ""),
            "workdir": str(projected_child.get("workdir") or ""),
            "progress": _fanout_progress(children),
            "seq": seq,
        }
        latest[task_id] = projected
    return latest
