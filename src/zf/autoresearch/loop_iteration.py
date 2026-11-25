"""Execute one isolated Autoresearch iteration and retain its metric source."""

from __future__ import annotations

from zf.autoresearch.loop_types import LoopConfig
from zf.autoresearch.orchestrator import (
    AutoresearchRunConfig, RESIDENT_PROVIDER_KEY, cleanup_autoresearch_run, run_autoresearch,
)
from zf.core.config.loader import load_config
from zf.core.config.schema import ZfConfig


def parent_role_provider_settings(parent: ZfConfig | None) -> dict[str, dict[str, str]]:
    if parent is None:
        return {}
    settings = {
        role.name: {"backend": role.backend, "model": role.model,
                    "model_reasoning_effort": role.model_reasoning_effort,
                    **({"sandbox": role.provider_session.sandbox}
                       if role.provider_session and role.provider_session.sandbox else {})}
        for role in parent.roles
    }
    manager = parent.runtime.run_manager
    resident = manager.resident_agent
    if resident.model or resident.model_reasoning_effort:
        settings[RESIDENT_PROVIDER_KEY] = {
            "backend": manager.backend, "model": resident.model,
            "model_reasoning_effort": resident.model_reasoning_effort,
        }
    return settings


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
        budget_usd=cfg.budget_usd,
        token_budget=cfg.token_budget,
        role_provider_settings=cfg.role_provider_settings,
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
