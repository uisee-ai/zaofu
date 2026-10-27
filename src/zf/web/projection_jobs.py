"""Explicit read-only job registry, shared by Web and its projection child."""
from __future__ import annotations

from zf.web.projection_worker import ProjectionJob


def build_job(job: ProjectionJob) -> dict:
    if job.group == "board-cards":
        from zf.web.server import _archive_tasks, _kanban, _safe_snapshot_projection
        return {
            "tasks": _safe_snapshot_projection("tasks", [], lambda: _kanban(
                job.state_dir, config=job.config, include_detail_bodies=False)),
            "archive_tasks": _safe_snapshot_projection("archive_tasks", [], lambda: _archive_tasks(
                job.state_dir, include_active=False, config=job.config)),
        }
    if job.group in {"board", "observability-summary"}:
        from zf.web.server import _snapshot_slice
        cards = None
        if job.group == "board":
            from zf.web.projections import dashboard_store as store
            cached = store.read_group(job.state_dir, "board-cards")
            if cached:
                value, meta = cached
                scope = store.scope_identity(job.state_dir, job.root, job.config)
                if (meta.get("scope") == scope and not meta.get("source_changed")
                        and meta.get("fingerprint") == store.source_fingerprint(job.state_dir, job.root, job.config, scope=scope)
                        and meta.get("trigger_fingerprint") == store.quick_fingerprint(job.state_dir, job.root, job.config, scope=scope)
                        and isinstance(value.get("tasks"), list) and isinstance(value.get("archive_tasks"), list)):
                    cards = value
        return _snapshot_slice(job.state_dir, slice_name=job.group, config=job.config,
                               project_root=job.root, web_session_token=None, board_cards=cards)
    if job.group == "pulse":
        from zf.web.projections.metric_views import pulse
        return pulse(job.state_dir, job.config)
    if job.group.startswith("agent-monitoring:"):
        from zf.web.agent_monitoring import build_agent_monitoring, MonitoringSourceChanged
        from zf.web.dashboard_projection import ProjectionSourceChanged
        try:
            return build_agent_monitoring(job.state_dir, project_id=job.project_id,
                                          config=job.config, project_root=job.root, attempts=1)
        except MonitoringSourceChanged as exc:
            raise ProjectionSourceChanged("agent sources changed during build") from exc
    raise ValueError(f"unsupported read projection: {job.group}")
