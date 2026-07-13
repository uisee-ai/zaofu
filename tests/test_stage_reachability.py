"""ZF-E2E-RACING-P2 (2026-07-11): validate gains stage-event producibility.

Racing e2e root cause: four execution roles without ``triggers`` made every
stage after the kernel-run static gate unreachable — the pipeline froze at
static_gate.passed while ``zf validate --cold-start`` scored 5/5.
"""

from __future__ import annotations

from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.config.schema import (
    WorkflowDagConfig,
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    ZfConfig,
)
from zf.core.workflow.reachability import check_stage_reachability


_RACING_STAGE_ORDER = [
    "task.assigned",
    "dev.build.done",
    "static_gate.passed",
    "review.approved",
    "test.passed",
    "judge.passed",
]


def _racing_config(*, with_triggers: bool) -> ZfConfig:
    def _role(name: str, publishes: list[str], triggers: list[str]) -> RoleConfig:
        return RoleConfig(
            name=name,
            backend="mock",
            publishes=publishes,
            triggers=triggers if with_triggers else [],
        )

    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(enabled=True, stage_order=list(_RACING_STAGE_ORDER)),
        ),
        roles=[
            _role("dev", ["dev.build.done", "dev.blocked"], ["task.assigned"]),
            _role("review", ["review.approved", "review.rejected"], ["static_gate.passed"]),
            _role("test", ["test.passed", "test.failed"], ["review.approved"]),
            _role("judge", ["judge.passed", "judge.failed"], ["test.passed"]),
        ],
    )


class TestReachabilityCore:
    def test_racing_dead_end_shape_detected(self):
        report = check_stage_reachability(_racing_config(with_triggers=False))
        assert report.ok is False
        assert report.unproducible_stage_events == [
            "review.approved",
            "test.passed",
            "judge.passed",
        ]

    def test_triggered_chain_fully_producible(self):
        report = check_stage_reachability(_racing_config(with_triggers=True))
        assert report.ok is True
        assert report.unproducible_stage_events == []

    def test_entry_stage_publisher_fires_without_triggers(self):
        # The backlog scheduler briefs the first worker stage directly, so
        # dev.build.done must be producible even with an empty dev.triggers.
        report = check_stage_reachability(_racing_config(with_triggers=False))
        assert "dev.build.done" in report.producible
        assert "static_gate.passed" in report.producible

    def test_dag_disabled_is_na(self):
        cfg = _racing_config(with_triggers=False)
        cfg.workflow.dag.enabled = False
        report = check_stage_reachability(cfg)
        assert report.ok is True
        assert report.producible == frozenset()


class TestExamplesStayGreen:
    def test_all_examples_have_producible_stage_orders(self):
        examples = sorted(Path("examples").glob("*.yaml"))
        assert examples, "examples/ missing"
        offenders = {}
        for path in examples:
            try:
                cfg = load_config(path)
            except Exception:
                continue
            report = check_stage_reachability(cfg)
            if not report.ok:
                offenders[path.name] = report.unproducible_stage_events
        assert offenders == {}


class TestColdStartGating:
    def test_cold_start_wires_reachability_gate(self):
        source = Path("src/zf/cli/validate.py").read_text(encoding="utf-8")
        assert "_print_stage_reachability" in source
        assert "unproducible_stage_events=" in source
