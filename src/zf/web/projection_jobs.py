"""Explicit read-only job registry, shared by Web and its projection child."""
from __future__ import annotations

from zf.web.projection_worker import ProjectionJob


def build_job(job: ProjectionJob) -> dict:
    if job.group in {"board", "observability-summary"}:
        from zf.web.server import _snapshot_slice
        return _snapshot_slice(job.state_dir, slice_name=job.group, config=job.config,
                               project_root=job.root, web_session_token=None)
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
