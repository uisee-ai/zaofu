"""Compatibility facade for the autoresearch closed loop.

Implementation is split by responsibility across sibling modules; public
imports stay available from ``zf.autoresearch.loop`` for CLI/tests.
"""

from __future__ import annotations

import subprocess  # noqa: F401  # Re-exported for existing test patch targets.

from zf.autoresearch.loop_bypass import bypass_inner_run
from zf.autoresearch.loop_driver import (
    LoopState,
    LoopTerminationDecision,
    run_loop,
    should_stop_loop,
)
from zf.autoresearch.loop_eval import (
    collect_autoresearch_eval_metrics,
    collect_eval_snapshot,
    compute_eval_delta,
)
from zf.autoresearch.loop_reflect import (
    DEFAULT_REFLECT_BACKEND,
    REFLECT_BACKEND_ENV,
    SUPPORTED_REFLECT_BACKENDS,
    build_reflection_prompt,
    invoke_reflection_llm,
    normalize_reflect_backend,
    parse_reflection_response,
)
from zf.autoresearch.loop_screenshot import ScreenshotResult, capture_kanban_screenshot
from zf.autoresearch.loop_types import (
    AutoresearchEvalMetrics,
    AutoresearchMetricSnapshot,
    EvalDelta,
    EvalMetricSnapshot,
    EvalSnapshot,
    IterationRecord,
    LopFreshnessSnapshot,
    LopMetricSnapshot,
    LopRecoverySnapshot,
    LoopConfig,
    LoopResult,
    ReflectionResult,
    ScoreSnapshot,
    ValidityTriageSnapshot,
    append_journal_entry,
    record_from_dict,
    record_to_dict,
)


__all__ = [
    "LoopConfig",
    "AutoresearchEvalMetrics",
    "AutoresearchMetricSnapshot",
    "EvalMetricSnapshot",
    "LopFreshnessSnapshot",
    "LopMetricSnapshot",
    "LopRecoverySnapshot",
    "ValidityTriageSnapshot",
    "ScoreSnapshot",
    "EvalSnapshot",
    "EvalDelta",
    "ReflectionResult",
    "IterationRecord",
    "LoopResult",
    "record_to_dict",
    "record_from_dict",
    "append_journal_entry",
    "collect_autoresearch_eval_metrics",
    "collect_eval_snapshot",
    "compute_eval_delta",
    "build_reflection_prompt",
    "parse_reflection_response",
    "invoke_reflection_llm",
    "normalize_reflect_backend",
    "DEFAULT_REFLECT_BACKEND",
    "REFLECT_BACKEND_ENV",
    "SUPPORTED_REFLECT_BACKENDS",
    "LoopState",
    "LoopTerminationDecision",
    "should_stop_loop",
    "run_loop",
    "ScreenshotResult",
    "capture_kanban_screenshot",
    "bypass_inner_run",
]
