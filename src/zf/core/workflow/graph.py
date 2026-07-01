"""In-memory deterministic workflow graph derived from ``zf.yaml``."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.workflow.graph_diagnostics import build_workflow_graph_diagnostics
from zf.core.workflow.topology import EXTERNAL_EVENTS, WorkflowEventSets


SUCCESS_SUFFIXES: frozenset[str] = frozenset({"done", "passed", "approved", "completed", "ready"})
FAILURE_SUFFIXES: frozenset[str] = frozenset({"failed", "rejected", "blocked"})


@dataclass(frozen=True)
class WorkflowNode:
    node_id: str
    stage_id: str
    type: str
    label: str = ""
    trigger: str = ""
    roles: tuple[str, ...] = ()
    success_event: str = ""
    failure_event: str = ""
    skipped_event: str = ""
    conditions: tuple[str, ...] = ()
    action: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["roles"] = list(self.roles)
        data["conditions"] = list(self.conditions)
        return data


@dataclass(frozen=True)
class WorkflowEdge:
    from_node: str
    to_node: str
    event: str = ""
    condition: str = ""
    kind: str = "workflow"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ReworkRoute:
    event: str
    target_role: str
    source: str = "workflow.rework_routing"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class TerminalPolicy:
    success_events: frozenset[str] = frozenset({"judge.passed"})
    failure_events: frozenset[str] = frozenset({"judge.failed"})

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "success_events": sorted(self.success_events),
            "failure_events": sorted(self.failure_events),
        }


@dataclass(frozen=True)
class DerivedWorkflowEventSets:
    handoff_success_events: frozenset[str]
    stage_progress_events: frozenset[str]
    rework_trigger_events: frozenset[str]
    rework_triage_trigger_events: frozenset[str]
    terminal_success_events: frozenset[str]
    readonly_gate_success_events: frozenset[str]

    def to_legacy_event_sets(self) -> WorkflowEventSets:
        return WorkflowEventSets(
            handoff_success_events=self.handoff_success_events,
            stage_progress_events=self.stage_progress_events,
            rework_trigger_events=self.rework_trigger_events,
            rework_triage_trigger_events=self.rework_triage_trigger_events,
        )

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "handoff_success_events": sorted(self.handoff_success_events),
            "stage_progress_events": sorted(self.stage_progress_events),
            "rework_trigger_events": sorted(self.rework_trigger_events),
            "rework_triage_trigger_events": sorted(self.rework_triage_trigger_events),
            "terminal_success_events": sorted(self.terminal_success_events),
            "readonly_gate_success_events": sorted(self.readonly_gate_success_events),
        }


@dataclass(frozen=True)
class WorkflowGraph:
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]
    event_sets: DerivedWorkflowEventSets
    terminal_policy: TerminalPolicy
    rework_routes: tuple[ReworkRoute, ...] = ()
    diagnostics: tuple[dict[str, str], ...] = ()

    def node(self, node_id: str) -> WorkflowNode | None:
        return next((node for node in self.nodes if node.node_id == node_id), None)

    def nodes_by_type(self, node_type: str) -> list[WorkflowNode]:
        return [node for node in self.nodes if node.type == node_type]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "workflow-graph.v1",
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "event_sets": self.event_sets.to_dict(),
            "terminal_policy": self.terminal_policy.to_dict(),
            "rework_routes": [route.to_dict() for route in self.rework_routes],
            "diagnostics": list(self.diagnostics),
        }


class WorkflowGraphCompiler:
    def compile(self, config: ZfConfig) -> WorkflowGraph:
        nodes: list[WorkflowNode] = []
        edges: list[WorkflowEdge] = []
        diagnostics: list[dict[str, str]] = []
        event_producers = _event_producers(config)
        event_consumers = _event_consumers(config)

        for role in config.roles:
            role_ref = _role_ref(role)
            if not role_ref:
                continue
            triggers = tuple(str(e) for e in list(getattr(role, "triggers", []) or []))
            publishes = tuple(str(e) for e in list(getattr(role, "publishes", []) or []))
            nodes.append(WorkflowNode(
                node_id=f"role:{role_ref}",
                stage_id=role_ref,
                type="role_stage",
                label=str(getattr(role, "name", "") or role_ref),
                trigger=",".join(triggers),
                roles=(role_ref,),
                success_event=",".join(_events_with_suffix(publishes, SUCCESS_SUFFIXES)),
                failure_event=",".join(_events_with_suffix(publishes, FAILURE_SUFFIXES)),
                conditions=_default_conditions_for_role(role, triggers),
                action=_default_action_for_role(triggers),
                metadata={
                    "name": str(getattr(role, "name", "") or ""),
                    "instance_id": str(getattr(role, "instance_id", "") or ""),
                    "backend": str(getattr(role, "backend", "") or ""),
                    "role_kind": str(getattr(role, "role_kind", "") or ""),
                    "target_role": role_ref,
                },
            ))

        _add_role_edges(config, edges)
        stages = list(getattr(config.workflow, "stages", []) or [])
        for stage in stages:
            stage_id = str(getattr(stage, "id", "") or "")
            if not stage_id:
                continue
            topology = str(getattr(stage, "topology", "") or "")
            node_type = _stage_node_type(topology)
            aggregate = getattr(stage, "aggregate", None)
            success_event = str(getattr(aggregate, "success_event", "") or "")
            failure_event = str(getattr(aggregate, "failure_event", "") or "")
            trigger = str(getattr(stage, "trigger", "") or "")
            roles = tuple(str(role) for role in list(getattr(stage, "roles", []) or []))
            node_id = f"stage:{stage_id}"
            nodes.append(WorkflowNode(
                node_id=node_id,
                stage_id=stage_id,
                type=node_type,
                label=stage_id,
                trigger=trigger,
                roles=roles,
                success_event=success_event,
                failure_event=failure_event,
                conditions=_default_conditions_for_stage(stage),
                action=_default_action_for_stage(stage),
                metadata={
                    "topology": topology,
                    "target_ref": str(getattr(stage, "target_ref", "") or ""),
                    "task_map": str(getattr(stage, "task_map", "") or ""),
                    "aggregate_mode": str(getattr(aggregate, "mode", "") or ""),
                },
            ))
            if trigger:
                edges.append(WorkflowEdge(
                    from_node=_producer_node_for_event(trigger, event_producers),
                    to_node=node_id,
                    event=trigger,
                    condition="event_seen",
                    kind="trigger",
                ))
            for role in roles:
                edges.append(WorkflowEdge(
                    from_node=node_id,
                    to_node=f"role:{role}",
                    kind="stage_role",
                ))
            if topology.startswith("fanout") or success_event or failure_event:
                aggregate_id = f"aggregate:{stage_id}"
                nodes.append(WorkflowNode(
                    node_id=aggregate_id,
                    stage_id=f"{stage_id}:aggregate",
                    type="aggregate_stage",
                    label=f"{stage_id} aggregate",
                    trigger=str(getattr(aggregate, "child_success_event", "") or ""),
                    roles=roles,
                    success_event=success_event,
                    failure_event=failure_event,
                    conditions=("fanout_barrier_satisfied",),
                    action="aggregate_fanout",
                    metadata={
                        "mode": str(getattr(aggregate, "mode", "") or "wait_for_all"),
                        "child_success_event": str(getattr(aggregate, "child_success_event", "") or ""),
                        "child_failure_event": str(getattr(aggregate, "child_failure_event", "") or ""),
                    },
                ))
                edges.append(WorkflowEdge(
                    from_node=node_id,
                    to_node=aggregate_id,
                    kind="fanout_aggregate",
                ))

        static_node = _static_gate_node(config)
        if static_node is not None:
            nodes.append(static_node)
            edges.append(WorkflowEdge(
                from_node=_producer_node_for_event("dev.build.done", event_producers),
                to_node=static_node.node_id,
                event="dev.build.done",
                condition="latest_dispatch_matches",
                kind="gate_trigger",
            ))

        terminal_policy = _terminal_policy(config)
        terminal_node = WorkflowNode(
            node_id="terminal:done",
            stage_id="terminal:done",
            type="terminal_gate",
            label="terminal done",
            trigger=",".join(sorted(terminal_policy.success_events)),
            success_event=next(iter(sorted(terminal_policy.success_events)), ""),
            failure_event=next(iter(sorted(terminal_policy.failure_events)), ""),
            conditions=("evidence_present", "terminal_evidence_accepted"),
            action="complete_task",
        )
        nodes.append(terminal_node)
        for event in terminal_policy.success_events | terminal_policy.failure_events:
            edges.append(WorkflowEdge(
                from_node=_producer_node_for_event(event, event_producers),
                to_node=terminal_node.node_id,
                event=event,
                condition="terminal_evidence_accepted",
                kind="terminal",
            ))

        rework_routes = tuple(
            ReworkRoute(event=str(event), target_role=str(target))
            for event, target in sorted((getattr(config.workflow, "rework_routing", {}) or {}).items())
        )
        for route in rework_routes:
            if not route.event:
                continue
            node_id = f"rework:{route.event}"
            nodes.append(WorkflowNode(
                node_id=node_id,
                stage_id=node_id,
                type="rework_route",
                label=f"{route.event} -> {route.target_role}",
                trigger=route.event,
                roles=(route.target_role,),
                conditions=("event_seen",),
                action="route_rework",
                metadata={
                    "target_role": route.target_role,
                    "route_event": route.event,
                    "source": route.source,
                },
            ))
            edges.append(WorkflowEdge(
                from_node=_producer_node_for_event_in_nodes(
                    route.event,
                    nodes,
                    event_producers,
                ),
                to_node=node_id,
                event=route.event,
                condition="event_seen",
                kind="rework_route",
            ))
        _add_node_event_edges(nodes, event_consumers, edges)
        diagnostics.extend(build_workflow_graph_diagnostics(
            nodes=nodes,
            stages=stages,
            pipelines=getattr(config.workflow, "pipelines", ()),
            external_triggers=getattr(
                getattr(config.workflow, "dag", None),
                "external_triggers", (),
            ),
            event_producers=event_producers,
            event_consumers=event_consumers,
            rework_routes=rework_routes,
            terminal_policy=terminal_policy,
        ))
        event_sets = derive_event_sets(nodes, rework_routes, terminal_policy)
        return WorkflowGraph(
            nodes=tuple(_dedupe_nodes(nodes)),
            edges=tuple(_dedupe_edges(edges)),
            event_sets=event_sets,
            terminal_policy=terminal_policy,
            rework_routes=rework_routes,
            diagnostics=tuple(diagnostics),
        )


def compile_workflow_graph(config: ZfConfig) -> WorkflowGraph:
    return WorkflowGraphCompiler().compile(config)


def derive_event_sets(
    nodes: list[WorkflowNode] | tuple[WorkflowNode, ...],
    rework_routes: tuple[ReworkRoute, ...],
    terminal_policy: TerminalPolicy,
) -> DerivedWorkflowEventSets:
    baseline = WorkflowEventSets.baseline()
    success_events = set(baseline.handoff_success_events)
    failure_events = set(baseline.rework_trigger_events)
    readonly_gate_success = {"review.approved", "test.passed", "judge.passed"}
    progress_events = set(baseline.stage_progress_events)

    for node in nodes:
        for event in _split_events(node.success_event):
            if event and _event_tail(event) in SUCCESS_SUFFIXES:
                success_events.add(event)
                progress_events.add(event)
        for event in _split_events(node.failure_event):
            if event and _event_tail(event) in FAILURE_SUFFIXES:
                failure_events.add(event)
                progress_events.add(event)
        for event in _split_events(node.skipped_event):
            progress_events.add(event)
        if node.type == "gate_stage" and node.success_event:
            readonly_gate_success.update(_split_events(node.success_event))
    for route in rework_routes:
        if route.event:
            failure_events.add(route.event)
            progress_events.add(route.event)
    progress_events.update(success_events)
    progress_events.update(failure_events)
    progress_events.update({"static_gate.skipped"})
    rework_triage = failure_events | {"static_gate.failed"}
    return DerivedWorkflowEventSets(
        handoff_success_events=frozenset(success_events),
        stage_progress_events=frozenset(progress_events),
        rework_trigger_events=frozenset(failure_events),
        rework_triage_trigger_events=frozenset(rework_triage),
        terminal_success_events=frozenset(terminal_policy.success_events),
        readonly_gate_success_events=frozenset(readonly_gate_success),
    )


def _role_ref(role: object) -> str:
    return str(getattr(role, "instance_id", "") or getattr(role, "name", "") or "")


def _events_with_suffix(events: tuple[str, ...], suffixes: frozenset[str]) -> list[str]:
    return [
        event for event in events
        if event and _event_tail(event) in suffixes
    ]


def _event_tail(event: str) -> str:
    return event.rsplit(".", 1)[-1]


def _stage_node_type(topology: str) -> str:
    return "fanout_stage" if topology.startswith("fanout") else "role_stage"


def _default_conditions_for_stage(stage: object) -> tuple[str, ...]:
    topology = str(getattr(stage, "topology", "") or "")
    conditions = ["event_seen"]
    if topology.startswith("fanout"):
        conditions.append("role_available")
    return tuple(conditions)


def _default_conditions_for_role(role: object, triggers: tuple[str, ...]) -> tuple[str, ...]:
    if not triggers:
        return ()
    conditions = ["event_seen"]
    if "task.dispatched" not in triggers:
        conditions.append("role_available")
    return tuple(conditions)


def _default_action_for_stage(stage: object) -> str:
    topology = str(getattr(stage, "topology", "") or "")
    return "start_fanout" if topology.startswith("fanout") else "dispatch_role"


def _default_action_for_role(triggers: tuple[str, ...]) -> str:
    if not triggers or "task.dispatched" in triggers:
        return ""
    return "dispatch_role"


def _static_gate_node(config: ZfConfig) -> WorkflowNode | None:
    static_cfg = (getattr(config, "quality_gates", {}) or {}).get("static")
    if static_cfg is None:
        return None
    return WorkflowNode(
        node_id="gate:impl_exit_gate",
        stage_id="impl_exit_gate",
        type="gate_stage",
        label="static gate",
        trigger="dev.build.done",
        success_event="static_gate.passed",
        failure_event="static_gate.failed",
        skipped_event="static_gate.skipped",
        conditions=(
            "event_seen",
            "latest_dispatch_matches",
            "task_status_in",
            "budget_available",
            "context_safe",
            "gate_policy_allows",
        ),
        action="run_gate",
        metadata={
            "gate": "static",
            "enabled": str(bool(getattr(static_cfg, "enabled", True))).lower(),
        },
    )


def _terminal_policy(config: ZfConfig) -> TerminalPolicy:
    published = {
        str(event)
        for role in getattr(config, "roles", []) or []
        for event in list(getattr(role, "publishes", []) or [])
    }
    aggregate_failures_by_success: dict[str, str] = {}
    aggregate_successes: list[str] = []
    for stage in list(getattr(config.workflow, "stages", []) or []):
        aggregate = getattr(stage, "aggregate", None)
        success_event = str(getattr(aggregate, "success_event", "") or "")
        failure_event = str(getattr(aggregate, "failure_event", "") or "")
        if success_event:
            aggregate_successes.append(success_event)
            published.add(success_event)
        if failure_event:
            published.add(failure_event)
        if success_event and failure_event:
            aggregate_failures_by_success[success_event] = failure_event
    consumers = _event_consumers(config)
    success: set[str] = set()
    for event in ("judge.passed", "verify.passed", "test.passed", "review.approved"):
        if event not in published:
            continue
        non_orchestrator_consumers = [
            consumer for consumer in consumers.get(event, [])
            if consumer != "role:orchestrator"
        ]
        if not non_orchestrator_consumers:
            success.add(event)
            break
    if not success:
        if aggregate_successes:
            success.add(aggregate_successes[-1])
        else:
            success.add("judge.passed")
    success_event = next(iter(success), "")
    failure = {
        aggregate_failures_by_success.get(success_event)
        or _terminal_failure_for_success(success_event, published)
    }
    for role in getattr(config, "roles", []) or []:
        for event in list(getattr(role, "publishes", []) or []):
            event_str = str(event)
            if event_str.endswith(".failed") and "judge" in event_str:
                failure.add(event_str)
    return TerminalPolicy(frozenset(success), frozenset(failure))


def _terminal_failure_for_success(success_event: str, published: set[str]) -> str:
    if success_event.endswith(".passed"):
        candidate = success_event.removesuffix(".passed") + ".failed"
        if candidate in published:
            return candidate
    if success_event.endswith(".approved"):
        candidate = success_event.removesuffix(".approved") + ".rejected"
        if candidate in published:
            return candidate
    return "judge.failed"


def _event_producers(config: ZfConfig) -> dict[str, list[str]]:
    producers: dict[str, list[str]] = {}
    for role in getattr(config, "roles", []) or []:
        node = f"role:{_role_ref(role)}"
        for event in list(getattr(role, "publishes", []) or []):
            producers.setdefault(str(event), []).append(node)
    return producers


def _event_consumers(config: ZfConfig) -> dict[str, list[str]]:
    consumers: dict[str, list[str]] = {}
    for role in getattr(config, "roles", []) or []:
        node = f"role:{_role_ref(role)}"
        for event in list(getattr(role, "triggers", []) or []):
            consumers.setdefault(str(event), []).append(node)
    for stage in list(getattr(config.workflow, "stages", []) or []):
        stage_id = str(getattr(stage, "id", "") or "")
        trigger = str(getattr(stage, "trigger", "") or "")
        if stage_id and trigger:
            consumers.setdefault(trigger, []).append(f"stage:{stage_id}")
    return consumers


def _producer_node_for_event(event: str, producers: dict[str, list[str]]) -> str:
    candidates = producers.get(event) or []
    if candidates:
        return candidates[0]
    if event in EXTERNAL_EVENTS:
        return f"external:{event}"
    return f"event:{event}"


def _producer_node_for_event_in_nodes(
    event: str,
    nodes: list[WorkflowNode],
    producers: dict[str, list[str]],
) -> str:
    for node in nodes:
        if event in _split_events(node.success_event):
            return node.node_id
        if event in _split_events(node.failure_event):
            return node.node_id
        if event in _split_events(node.skipped_event):
            return node.node_id
    return _producer_node_for_event(event, producers)


def _split_events(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _add_role_edges(config: ZfConfig, edges: list[WorkflowEdge]) -> None:
    producers = _event_producers(config)
    for role in getattr(config, "roles", []) or []:
        target = f"role:{_role_ref(role)}"
        for trigger in list(getattr(role, "triggers", []) or []):
            for source in producers.get(str(trigger), []):
                if source != target:
                    edges.append(WorkflowEdge(
                        from_node=source,
                        to_node=target,
                        event=str(trigger),
                        condition="event_seen",
                        kind="role_trigger",
                    ))


def _add_node_event_edges(
    nodes: list[WorkflowNode],
    event_consumers: dict[str, list[str]],
    edges: list[WorkflowEdge],
) -> None:
    for node in nodes:
        for event in (
            _split_events(node.success_event)
            | _split_events(node.failure_event)
            | _split_events(node.skipped_event)
        ):
            if not event:
                continue
            for consumer in event_consumers.get(event, []):
                if consumer != node.node_id:
                    edges.append(WorkflowEdge(
                        from_node=node.node_id,
                        to_node=consumer,
                        event=event,
                        condition="event_seen",
                        kind="node_event",
                    ))
        if node.skipped_event and node.success_event:
            for skipped in _split_events(node.skipped_event):
                for success in _split_events(node.success_event):
                    for consumer in event_consumers.get(success, []):
                        if consumer != node.node_id:
                            edges.append(WorkflowEdge(
                                from_node=node.node_id,
                                to_node=consumer,
                                event=skipped,
                                condition="skipped_as_fulfilled",
                                kind="node_event",
                            ))


def _dedupe_nodes(nodes: list[WorkflowNode]) -> list[WorkflowNode]:
    deduped: dict[str, WorkflowNode] = {}
    for node in nodes:
        deduped.setdefault(node.node_id, node)
    return list(deduped.values())


def _dedupe_edges(edges: list[WorkflowEdge]) -> list[WorkflowEdge]:
    deduped: dict[tuple[str, str, str, str, str], WorkflowEdge] = {}
    for edge in edges:
        deduped.setdefault(
            (edge.from_node, edge.to_node, edge.event, edge.condition, edge.kind),
            edge,
        )
    return list(deduped.values())
