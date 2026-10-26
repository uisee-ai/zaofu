"""Assemble the resources consumed by Observability without unrelated pages."""

from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ZfConfig


def observability_snapshot(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    web_session_token: str | None = None,
) -> dict:
    # The assembly seam still owns runtime/agent adapters in server; reuse those
    # exact projections so the scoped and diagnostic APIs keep one metric source.
    from zf.web import server as s

    root = s._resolve_project_root_for_state(state_dir, project_root)
    data = s._snapshot_base(state_dir, config=config, project_root=root)
    data.update(s._snapshot_empty_defaults())
    data["snapshot_slice"] = "observability-summary"
    builders = {
        "metrics_snapshot": lambda: s._metrics_snapshot_projection(state_dir),
        "fleet_stats": lambda: s._fleet_stats_projection(state_dir, config=config),
        "provider_health": lambda: s._provider_health_projection(state_dir),
        "long_run_truth": lambda: s._read_long_run_truth(state_dir, config=config),
        "traces": lambda: s._traces(state_dir, config=config),
        "fanouts": lambda: s._fanouts(state_dir, config=config),
        "candidates": lambda: s._candidates(state_dir, config=config),
        "runs": lambda: s._runs_index(state_dir, project_root=root).get("runs", []),
        "active_runs": lambda: s._active_runs(state_dir, project_root=root).get("active_runs", []),
        "agents": lambda: s._agents(state_dir, config=config, project_root=root),
        "runtime": lambda: s._runtime(state_dir, config=config, project_root=root, web_session_token=web_session_token),
        "mutation_audit": lambda: s.project_mutation_audit(state_dir),
        "worktree_drift": lambda: s.project_worktree_drift_audit(state_dir),
        "cost": lambda: s._cost(state_dir),
    }
    for name, build in builders.items():
        data[name] = s._safe_snapshot_projection(name, data[name], build)
    return data
