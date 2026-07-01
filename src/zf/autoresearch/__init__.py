"""Autoresearch supervisor for real ZaoFu harness evaluation runs."""

from zf.autoresearch.orchestrator import (
    AutoresearchRunConfig,
    AutoresearchRunResult,
    run_autoresearch,
    start_tmux_supervisor,
)
from zf.autoresearch.eval_result import (
    EvalResult,
    GateResult,
    compare_eval_results,
)

__all__ = [
    "AutoresearchRunConfig",
    "AutoresearchRunResult",
    "EvalResult",
    "GateResult",
    "compare_eval_results",
    "run_autoresearch",
    "start_tmux_supervisor",
]
