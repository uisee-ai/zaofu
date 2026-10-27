"""One coherent, complete Agents metric read, independent of workdir probes."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from zf.runtime.agent_live import project_agent_live
from zf.runtime.operator_reliability import project_agent_cockpit
from zf.web.projections.common import _cost
from zf.web.projections.events import events_read_days
from zf.web.projections.metric_traces import current_seq
from zf.web.projections.projection_failures import collect_projection_failures
from zf.web.projections.source_cache import fingerprint, task_paths


class MonitoringSourceChanged(RuntimeError):
    """A coherent source generation could not be read; never substitute zeros."""


def source_paths(state_dir: Path, project_root: Path) -> list[Path]:
    return task_paths(state_dir) + [
        state_dir / "role_sessions.yaml", state_dir / "loop.lock",
        state_dir / "session.yaml", state_dir / "processes" / "watcher.pid.json",
        state_dir / "operator" / "kanban-agent.json", project_root / "zf.yaml",
        state_dir / "cost.jsonl", state_dir / "cost",
        *sorted((state_dir / "cost").glob("*.jsonl")),
        state_dir / "cost" / "reconciliation",
        *sorted((state_dir / "cost" / "reconciliation").rglob("*")),
    ]


def build_agent_monitoring(state_dir: Path, *, project_id: str, config=None,
                           project_root: Path) -> dict:
    from zf.web.server import _agents, _metrics_snapshot_projection, _fleet_stats_projection

    # Reuse the existing algorithms and exact source-keyed SQLite projections.
    # Clock ages and process liveness are recomputed on every request.
    for _attempt in range(2):
        before = fingerprint(state_dir, source_paths(state_dir, project_root), config)
        day = datetime.now(timezone.utc).date()
        with collect_projection_failures() as failures:
            seq = current_seq(state_dir, config)
            agents = _agents(state_dir, config=config, project_root=project_root, include_details=False)
            events = events_read_days(state_dir, 7, config=config)
            now = datetime.now(timezone.utc)
            result = {
                "schema_version": "agent-monitoring.v1", "project_id": project_id,
                "source_seq": seq, "source_version": before,
                "generated_at": now.isoformat(), "status": "ready",
                "agents": agents,
                "agent_live": project_agent_live(state_dir, events=events),
                "agent_cockpit": project_agent_cockpit(state_dir, events=events, agents=agents, now=now),
                "cost": _cost(state_dir),
                "metrics_snapshot": _metrics_snapshot_projection(state_dir, config=config),
                "fleet_stats": _fleet_stats_projection(state_dir, config=config),
            }
        if (not failures and day == datetime.now(timezone.utc).date()
                and before == fingerprint(state_dir, source_paths(state_dir, project_root), config)):
            return result
    raise MonitoringSourceChanged()
