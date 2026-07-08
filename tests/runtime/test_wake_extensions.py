"""P3 (2026-04-20): wake_patterns YAML extensions + rate limiter.

Verifies:
- Default (no yaml) = base WAKE_PATTERNS unchanged
- `workflow.wake_extensions.hooks.enabled=true` adds hook events
- Same for agent telemetry events
- Rate limiter drops excessive wakes but preserves events.jsonl
- Config loader round-trip works
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    WakeExtensionConfig,
    WakeExtensionsConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.runtime.wake_patterns import (
    WAKE_PATTERNS,
    WakeRateLimiter,
    compute_effective_wake_patterns,
    rate_limits_for_config,
)


# -- compute_effective_wake_patterns --

def test_default_config_returns_base_wake_patterns():
    """No wake_extensions configured → exact base set."""
    config = ZfConfig(project=ProjectConfig(name="x"))
    assert compute_effective_wake_patterns(config) == set(WAKE_PATTERNS)


def test_dag_external_triggers_wake_the_watcher():
    """Declared external_triggers must enter the wake surface.

    Light-topology entry (`prd.requested`) has a builtin reactor handler
    but no owning stage; without folding external_triggers in, the
    EventWatcher never wakes run_once and the light task_map synthesizer
    silently never fires (2026-07-06 light baseline stall)."""
    from zf.core.config.schema import WorkflowDagConfig

    config = ZfConfig(
        project=ProjectConfig(name="x"),
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                external_triggers=["prd.requested", "task_map.ready"],
            ),
        ),
    )
    result = compute_effective_wake_patterns(config)
    assert "prd.requested" in result
    assert "task_map.ready" in result


def test_disabled_hooks_do_not_extend():
    """hooks.enabled=False (default) → extension ignored.

    Uses `hook.write_failed` as the example event: it is NOT in base
    WAKE_PATTERNS (codex.hook.* was promoted to base in 1202-T3), so it
    genuinely exercises the disabled-extension path.
    """
    config = ZfConfig(
        project=ProjectConfig(name="x"),
        workflow=WorkflowConfig(
            wake_extensions=WakeExtensionsConfig(
                hooks=WakeExtensionConfig(
                    enabled=False,
                    include=["hook.write_failed"],
                ),
            ),
        ),
    )
    effective = compute_effective_wake_patterns(config)
    assert "hook.write_failed" not in effective


def test_enabled_hooks_extend_wake_patterns():
    """hooks.enabled=True + include → events added."""
    config = ZfConfig(
        project=ProjectConfig(name="x"),
        workflow=WorkflowConfig(
            wake_extensions=WakeExtensionsConfig(
                hooks=WakeExtensionConfig(
                    enabled=True,
                    include=["hook.write_failed", "hook.orphan_event"],
                ),
            ),
        ),
    )
    effective = compute_effective_wake_patterns(config)
    assert "hook.write_failed" in effective
    assert "hook.orphan_event" in effective
    # Base set still present
    assert "dev.build.done" in effective


def test_agent_extensions_work():
    config = ZfConfig(
        project=ProjectConfig(name="x"),
        workflow=WorkflowConfig(
            wake_extensions=WakeExtensionsConfig(
                agent=WakeExtensionConfig(
                    enabled=True,
                    include=["agent.tool.result"],
                ),
            ),
        ),
    )
    effective = compute_effective_wake_patterns(config)
    assert "agent.tool.result" in effective


def test_star_stage_events_extend_wake_patterns():
    config = ZfConfig(
        project=ProjectConfig(name="x"),
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="review-star",
                trigger="candidate.ready",
                topology="fanout_reader",
                roles=["review-a"],
                aggregate=FanoutAggregateConfig(
                    success_event="review.wave.approved",
                    failure_event="review.wave.rejected",
                    synth_role="review-synth",
                ),
            ),
        ]),
    )

    effective = compute_effective_wake_patterns(config)

    assert "candidate.ready" in effective
    assert "review.wave.approved" in effective
    assert "review.wave.rejected" in effective
    assert "fanout.synth.completed" in effective


# -- rate_limits_for_config --

def test_no_rate_limits_when_section_disabled():
    config = ZfConfig(project=ProjectConfig(name="x"))
    assert rate_limits_for_config(config) == {}


def test_rate_limits_populated_when_enabled():
    config = ZfConfig(
        project=ProjectConfig(name="x"),
        workflow=WorkflowConfig(
            wake_extensions=WakeExtensionsConfig(
                agent=WakeExtensionConfig(
                    enabled=True,
                    include=["agent.tool.result", "agent.usage"],
                    rate_limit_per_minute=30,
                ),
            ),
        ),
    )
    limits = rate_limits_for_config(config)
    assert limits["agent.tool.result"] == 30
    assert limits["agent.usage"] == 30


def test_rate_limit_zero_not_recorded():
    """0 = unlimited, don't clutter limits dict."""
    config = ZfConfig(
        project=ProjectConfig(name="x"),
        workflow=WorkflowConfig(
            wake_extensions=WakeExtensionsConfig(
                hooks=WakeExtensionConfig(
                    enabled=True,
                    include=["codex.hook.stop"],
                    rate_limit_per_minute=0,
                ),
            ),
        ),
    )
    limits = rate_limits_for_config(config)
    assert limits == {}


# -- WakeRateLimiter --

def test_limiter_allows_when_no_limit_configured():
    limiter = WakeRateLimiter({})
    for _ in range(1000):
        assert limiter.allow("anything") is True


def test_limiter_blocks_after_exceeding_limit():
    limiter = WakeRateLimiter({"noisy.event": 3})
    now = 100.0
    # First 3 allowed
    assert limiter.allow("noisy.event", now=now) is True
    assert limiter.allow("noisy.event", now=now + 1) is True
    assert limiter.allow("noisy.event", now=now + 2) is True
    # 4th within window → blocked
    assert limiter.allow("noisy.event", now=now + 3) is False


def test_limiter_resets_after_window_expires():
    limiter = WakeRateLimiter({"noisy.event": 2})
    now = 100.0
    assert limiter.allow("noisy.event", now=now) is True
    assert limiter.allow("noisy.event", now=now + 1) is True
    assert limiter.allow("noisy.event", now=now + 2) is False  # blocked
    # Advance > 60s — old timestamps drop out
    assert limiter.allow("noisy.event", now=now + 61) is True


def test_limiter_independent_per_event_type():
    """Different event types have independent budgets."""
    limiter = WakeRateLimiter({"a.event": 1, "b.event": 1})
    now = 100.0
    assert limiter.allow("a.event", now=now) is True
    assert limiter.allow("a.event", now=now) is False  # a exhausted
    assert limiter.allow("b.event", now=now) is True   # b still fresh


# -- YAML loader round-trip --

def test_loader_parses_wake_extensions(tmp_path):
    yaml_path = tmp_path / "zf.yaml"
    yaml_path.write_text("""
version: "1.0"
project:
  name: wake-ext
workflow:
  wake_extensions:
    hooks:
      enabled: true
      include:
        - codex.hook.pre_tool_use
        - hook.write_failed
    agent:
      enabled: true
      include:
        - agent.tool.result
      rate_limit_per_minute: 30
roles:
  - name: dev
    publishes: [dev.build.done]
""")
    config = load_config(yaml_path)
    hooks = config.workflow.wake_extensions.hooks
    agent = config.workflow.wake_extensions.agent

    assert hooks.enabled is True
    assert "codex.hook.pre_tool_use" in hooks.include
    assert hooks.rate_limit_per_minute == 0  # unset → 0 default

    assert agent.enabled is True
    assert "agent.tool.result" in agent.include
    assert agent.rate_limit_per_minute == 30


def test_loader_default_when_absent(tmp_path):
    """YAML without wake_extensions → defaults stay disabled."""
    yaml_path = tmp_path / "zf.yaml"
    yaml_path.write_text("""
version: "1.0"
project:
  name: no-ext
roles:
  - name: dev
    publishes: [dev.build.done]
""")
    config = load_config(yaml_path)
    assert config.workflow.wake_extensions.hooks.enabled is False
    assert config.workflow.wake_extensions.agent.enabled is False


# -- SUSPEND / essential events stay in base (backward compat guard) --

def test_suspend_events_still_in_effective_wake_without_extensions():
    """LH-3 SUSPEND events are in the base set, not the extensions.
    Disabling extensions must NOT remove them."""
    config = ZfConfig(project=ProjectConfig(name="x"))
    effective = compute_effective_wake_patterns(config)
    assert "review.suspended" in effective
    assert "test.suspended" in effective
