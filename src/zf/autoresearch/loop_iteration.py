"""Execute one isolated Autoresearch iteration and retain its metric source."""

from __future__ import annotations

from zf.autoresearch.loop_types import LoopConfig
from zf.autoresearch.orchestrator import (
    AutoresearchRunConfig, cleanup_autoresearch_run, run_autoresearch,
)
from zf.core.config.loader import load_config


def run_iteration(*, scenario: str, run_id: str, cfg: LoopConfig) -> dict:
    """Run one loop iteration in the reusable isolated worktree."""
    run_output_dir = (
        cfg.output_dir / "runs" / run_id
        if cfg.output_dir is not None
        else None
    )
    ar_cfg = AutoresearchRunConfig(
        scenario_name=scenario,
        worktree=cfg.worktree,
        config_template=cfg.config_template,
        run_id=run_id,
        confirm=True,
        reuse_worktree=True,
        backlog_on_failure=cfg.backlog_on_failure,
        backlog_state_dir=cfg.parent_state_dir,
        review_gate=cfg.review_gate,
        sync_dirty=False,
        output_dir=run_output_dir,
        diagnostic_context=cfg.seed_text,
    )
    try:
        result = run_autoresearch(ar_cfg)
        # Parent runtime environment must not redirect experiment metrics.
        iteration_config = load_config(result.worktree / "zf.yaml")
        iteration_state_dir = (
            result.worktree / iteration_config.project.state_dir
        ).resolve()
        return {
            "state_dir": str(iteration_state_dir),
            "status": result.status,
            "tasks_done": result.tasks_done,
            "expected_done": result.expected_done,
            "fatal_event": result.fatal_event,
            "report_path": str(result.report_path),
            "review_gate": result.review_gate or {},
        }
    finally:
        cleanup_autoresearch_run(ar_cfg, run_id=run_id)
