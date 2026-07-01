"""Tests for workflow topology auto-derivation."""

from __future__ import annotations

from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.workflow.topology import WorkflowTopology


def _make_config(*roles: RoleConfig) -> ZfConfig:
    return ZfConfig(project=ProjectConfig(name="test"), roles=list(roles))


class TestBuildTopology:
    def test_empty_roles(self):
        topo = WorkflowTopology.from_config(_make_config())
        assert topo.edges() == []

    def test_single_edge(self):
        dev = RoleConfig(name="dev", publishes=["dev.build.done"])
        review = RoleConfig(name="review", triggers=["dev.build.done"])
        topo = WorkflowTopology.from_config(_make_config(dev, review))
        edges = topo.edges()
        assert len(edges) == 1
        assert edges[0] == ("dev", "review", "dev.build.done")

    def test_multiple_edges(self):
        dev = RoleConfig(name="dev", publishes=["dev.build.done"])
        review = RoleConfig(name="review", triggers=["dev.build.done"], publishes=["review.approved"])
        test = RoleConfig(name="test", triggers=["dev.build.done"])
        topo = WorkflowTopology.from_config(_make_config(dev, review, test))
        edges = topo.edges()
        assert len(edges) == 2
        from_names = {(e[0], e[1]) for e in edges}
        assert ("dev", "review") in from_names
        assert ("dev", "test") in from_names

    def test_chain(self):
        dev = RoleConfig(name="dev", publishes=["dev.build.done"])
        review = RoleConfig(name="review", triggers=["dev.build.done"], publishes=["review.approved"])
        done = RoleConfig(name="orchestrator", triggers=["review.approved"])
        topo = WorkflowTopology.from_config(_make_config(dev, review, done))
        edges = topo.edges()
        assert len(edges) == 2


class TestOrphanDetection:
    def test_no_orphans(self):
        dev = RoleConfig(name="dev", publishes=["dev.build.done"])
        review = RoleConfig(name="review", triggers=["dev.build.done"])
        topo = WorkflowTopology.from_config(_make_config(dev, review))
        assert topo.orphan_events() == []

    def test_orphan_event(self):
        dev = RoleConfig(name="dev", publishes=["dev.build.done", "dev.log"])
        review = RoleConfig(name="review", triggers=["dev.build.done"])
        topo = WorkflowTopology.from_config(_make_config(dev, review))
        orphans = topo.orphan_events()
        assert "dev.log" in orphans

    def test_dead_end_role(self):
        dev = RoleConfig(name="dev", publishes=["dev.build.done"])
        lonely = RoleConfig(name="lonely", triggers=["never.happens"])
        topo = WorkflowTopology.from_config(_make_config(dev, lonely))
        dead = topo.dead_end_roles()
        assert "lonely" in dead

    def test_stage_triggers_and_aggregate_events_participate_in_topology(self):
        planner = RoleConfig(name="planner", publishes=["task_map.ready"])
        orchestrator = RoleConfig(name="orchestrator", triggers=["plan.ready"])
        cfg = ZfConfig(
            project=ProjectConfig(name="test"),
            roles=[planner, orchestrator],
            workflow=WorkflowConfig(stages=[
                WorkflowStageConfig(
                    id="plan",
                    trigger="task_map.ready",
                    aggregate=FanoutAggregateConfig(
                        success_event="plan.ready",
                        failure_event="plan.failed",
                    ),
                ),
            ]),
        )

        topo = WorkflowTopology.from_config(cfg)

        assert "task_map.ready" not in topo.orphan_events()
        assert "orchestrator" not in topo.dead_end_roles()

    def test_runtime_fanout_events_are_external_producers(self):
        orchestrator = RoleConfig(
            name="orchestrator",
            triggers=["fanout.serialize", "fanout.cancelled"],
        )
        topo = WorkflowTopology.from_config(_make_config(orchestrator))

        assert topo.dead_end_roles() == []


class TestAsciiRender:
    def test_render_returns_string(self):
        dev = RoleConfig(name="dev", publishes=["dev.build.done"])
        review = RoleConfig(name="review", triggers=["dev.build.done"])
        topo = WorkflowTopology.from_config(_make_config(dev, review))
        output = topo.ascii_render()
        assert isinstance(output, str)
        assert "dev" in output
        assert "review" in output

    def test_render_empty(self):
        topo = WorkflowTopology.from_config(_make_config())
        output = topo.ascii_render()
        assert isinstance(output, str)
