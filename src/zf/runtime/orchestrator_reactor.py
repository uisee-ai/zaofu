"""EventReactorMixin — event handlers + state-machine moves + scope checks.

Split from orchestrator.py (P1.2 step 3). Methods rely on host
Orchestrator state (self.task_store, self.sm, self.event_log,
self._gan_round, self._set_worker_state, self._discriminator_runner,
self._dispatch_rework, self.config, etc).

P0-2 (2026-04-20): replaced the hardcoded `_event_handlers()` dict with
an `EventActionRegistry`. Built-in handlers register themselves on
first call; YAML `workflow.event_actions` entries are also registered
at the same time. Call sites should use `self.event_registry` directly
via `.primary(event_type)` — `_event_handlers()` is kept as a
deprecation shim so external probes (tests, wake_patterns) keep working.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from typing import Any
from pathlib import Path

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.reactor.registry import EventActionRegistry
from zf.core.statemachine.task import InvalidTransition
from zf.core.task.schema import Task, TaskEvidence
from zf.runtime.workstream_scope_guard import check_workstream_scope
from zf.core.verification.evidence import (
    build_done_evidence_payload,
    validate_terminal_done_evidence,
)
from zf.runtime.channel_adapter import dispatch_reply_request
from zf.runtime.channel_router import route_channel_message
from zf.runtime.cli_command import zf_cli_cmd
from zf.runtime.feature_completion import close_feature_if_all_tasks_done
from zf.runtime.artifact_manifest import (
    is_taskless_workflow_manifest_payload,
    load_manifest_from_payload,
    normalize_artifact_kind,
)
from zf.autoresearch.self_repair import (
    candidate_from_trigger_event,
    repair_task_payload_from_candidate,
    write_candidate_artifact,
)
from zf.autoresearch.loop_requests import (
    LOOP_REQUESTED,
    build_loop_request_payload,
    loop_request_exists,
    loop_request_id_from_payload,
)
from zf.autoresearch.artifacts import build_replan_contract_eval_request
from zf.runtime.autoresearch_invocation import (
    acceptance_payload,
    build_invocation_request_from_run_manager_event,
    invocation_id_from_payload,
    rejection_payload,
    trigger_payload_from_invocation,
    validate_invocation_request,
)
from zf.runtime.git_capture import (
    capture_commits_since,
    capture_files_touched_since,
    capture_git_diff_context,
)
from zf.runtime.orchestrator_types import OrchestratorDecision
from zf.runtime.product_delivery import ingest_task_map_to_kanban
from zf.runtime.provider_stop import classify_provider_stop
from zf.runtime.rework_triage import ReworkTriageResult
from zf.runtime.task_map import (
    build_task_map_from_ingest_plan,
    load_source_index,
    load_task_map,
    resolve_artifact_file,
    validate_task_map_payload,
)
from zf.runtime.terminal_ledger import (
    TERMINAL_SUCCESS_EVENTS,
    TerminalLedger,
    terminal_dispatch_id,
)


# Built-in event → handler-method-name map. The mixin walks this at
# registry-build time to register each built-in as a primary handler.
# Adding a new built-in event = one line here + one `_on_<name>` method.
_BUILTIN_HANDLER_METHODS: tuple[tuple[str, str], ...] = (
    ("dev.build.done", "_on_build_done"),
    ("arch.proposal.done", "_on_build_done"),     # same flow
    ("design.critique.done", "_on_build_done"),
    ("artifact.manifest.published", "_on_artifact_manifest_published"),
    ("review.approved", "_on_review_approved"),
    ("review.rejected", "_on_review_rejected"),
    ("verify.passed", "_on_verify_passed"),
    ("verify.failed", "_on_verify_failed"),
    ("test.passed", "_on_test_passed"),
    ("test.failed", "_on_test_failed"),
    ("judge.passed", "_on_judge_passed"),
    ("judge.failed", "_on_judge_failed"),
    ("discriminator.failed", "_on_discriminator_failed"),
    ("task.done.blocked", "_on_task_done_blocked"),
    ("orchestrator.evidence_rework.requested", "_on_evidence_rework_requested"),
    ("dev.blocked", "_on_dev_blocked"),
    ("gate.failed", "_on_gate_failed"),
    # K2: silent stall 的显式 handler —— 重驱 dispatch(幂等;emit 侧
    # 已有 5min cooldown 防风暴)。此前 wake 后落 generic out_of_scope。
    ("dispatch.silent_stall", "_on_dispatch_silent_stall"),
    # LH-3.T4: Tri-State SUSPEND — reviewer/tester needs more info or
    # external dep broken. Task goes to blocked (not rework) +
    # human.escalate.
    ("review.suspended", "_on_suspended"),
    ("test.suspended", "_on_suspended"),
    # Layer 2 owns the autonomous response to human.escalate (woken via
    # WAKE_PATTERNS, decision routed by skill
    # zf-yoke-orchestrator-role-context). Layer 1 records it
    # observationally so the handler-coverage invariant stays green.
    ("human.escalate", "_on_human_escalate"),
    # 1202-T3: Codex hook engine bridges into the same reactor as Claude
    # via a dedicated namespace. `stop` is the round-complete cue; the
    # other four are observational today but registered so they show up
    # in the handler-coverage invariant.
    ("codex.hook.session_start", "_on_codex_hook_session_start"),
    ("codex.hook.user_prompt_submit", "_on_codex_hook_user_prompt_submit"),
    ("codex.hook.pre_tool_use", "_on_codex_hook_pre_tool_use"),
    ("codex.hook.post_tool_use", "_on_codex_hook_post_tool_use"),
    ("codex.hook.stop", "_on_codex_hook_stop"),
    ("worker.completed", "_on_worker_completed"),
    ("agent.api_blocked", "_on_agent_api_blocked"),
    ("agent.timeout", "_on_agent_timeout"),
    ("task.completion.stale_rejected", "_on_completion_stale_rejected"),
    ("worker.context.critical", "_on_context_critical"),
    ("task.continuation_scheduled", "_on_completion_scheduled"),
    ("task.retry_scheduled", "_on_completion_scheduled"),
    ("phase.progressed", "_on_phase_progressed"),
    ("workflow.invoke.requested", "_on_workflow_invoke_requested"),
    ("task.fanout.requested", "_on_task_fanout_requested"),
    ("channel.message.posted", "_on_channel_message_posted"),
    ("channel.agent.reply.requested", "_on_channel_agent_reply_requested"),
    ("autoresearch.trigger.accepted", "_on_autoresearch_trigger_accepted"),
    ("autoresearch.invocation.requested", "_on_autoresearch_invocation_requested"),
    ("run.manager.autoresearch.requested", "_on_run_manager_autoresearch_requested"),
    ("autoresearch.inject.worker_stuck", "_on_autoresearch_worker_stuck_inject"),
    ("replan.proposal.created", "_on_replan_proposal_created"),
)

_READONLY_GATE_SUCCESS_EVENTS = frozenset({
    "review.approved",
    "test.passed",
    "judge.passed",
})

_GRAPH_REVIEW_TEST_JUDGE_EVENTS = frozenset({
    "arch.proposal.done",
    "design.critique.done",
    "dev.build.done",
    "static_gate.passed",
    "static_gate.skipped",
    "review.approved",
    "review.rejected",
    "review.child.completed",
    "review.child.failed",
    "verify.passed",
    "verify.failed",
    "verify.child.completed",
    "verify.child.failed",
    "test.passed",
    "test.failed",
    "judge.passed",
    "judge.failed",
})

_TASKLESS_RUNTIME_CONTROL_EVENTS = frozenset({
    "runtime.attention.needed",
    "supervisor.decision.recorded",
    "owner.visible_message.requested",
    "run.manager.autoresearch.requested",
    "autoresearch.invocation.requested",
    "autoresearch.invocation.accepted",
    "autoresearch.invocation.rejected",
    "autoresearch.trigger.accepted",
    "autoresearch.loop.requested",
    "autoresearch.loop.accepted",
    "autoresearch.loop.skipped",
    "autoresearch.loop.started",
    "autoresearch.loop.completed",
    "autoresearch.loop.failed",
    "autoresearch.bug_candidate.created",
    "automation.proposal.created",
    "replan.proposal.created",
    "replan.contract_eval.requested",
})


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list | tuple | set):
        items: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                items.append(text)
        return items
    text = str(value).strip()
    return [text] if text else []


def _payload_requests_writer_capability(payload: dict) -> bool:
    if bool(payload.get("write") or payload.get("writable")):
        return True
    for key in ("write_files", "write_scope", "exclusive_files"):
        if _string_list(payload.get(key)):
            return True
    return False


def _workflow_graph_action_priority(action_type: str) -> int:
    return {
        "route_rework": 0,
        "complete_task": 1,
        "dispatch_role": 2,
    }.get(action_type, 99)


def _workflow_graph_target_status(target_role: str, current_status: str) -> str:
    role = target_role.strip().lower()
    if role.startswith(("review", "critic", "code_review")):
        return "review"
    if role.startswith(("test", "tester", "qa", "verify", "verifier", "judge")):
        return "testing"
    if role.startswith(("dev", "developer", "builder", "writer", "arch", "architect")):
        return "in_progress"
    return current_status


def _autoresearch_repair_mode(config: object) -> str:
    autoresearch = getattr(config, "autoresearch", None)
    policy = getattr(autoresearch, "trigger_policy", None)
    mode = str(getattr(policy, "repair_mode", "") or "proposal_only")
    return mode if mode in {"proposal_only", "bounded_repair"} else "proposal_only"


def _autoresearch_failure_class(fingerprint: str) -> str:
    for prefix in (
        "task_ref_rejected",
        "missing_task_ref_after_dev_build_done",
        "fanout_timed_out",
        "completion_snapshot_ref_missing",
        "task_contract_invalid",
    ):
        if fingerprint.startswith(f"{prefix}:"):
            return prefix
    parts = fingerprint.split(":")
    if len(parts) >= 2 and parts[0] == "failure":
        return parts[1]
    if parts and parts[0] == "stall":
        return "stall"
    return ""


class EventReactorMixin:
    """Event handlers + state-machine transitions of Orchestrator.
    Mixin contract: relies on host Orchestrator's instance fields. Do
    not instantiate standalone."""

    @property
    def event_writer(self) -> EventWriter:
        writer = getattr(self, "_event_writer", None)
        if writer is None:
            writer = EventWriter(self.event_log)
            self._event_writer = writer
        return writer

    @event_writer.setter
    def event_writer(self, writer: EventWriter) -> None:
        self._event_writer = writer

    def _build_event_registry(self) -> EventActionRegistry:
        """Construct the EventActionRegistry for this orchestrator.

        Order: built-ins first, then YAML `workflow.event_actions`.
        The built-in handler is always the "primary" for its event
        (return value feeds the decision stream); YAML actions run as
        side effects via `EmitAction` etc.
        """
        registry = EventActionRegistry()

        # Register built-in handlers
        for event_type, method_name in _BUILTIN_HANDLER_METHODS:
            handler = getattr(self, method_name, None)
            if handler is None:
                continue  # defensive: stub classes used by wake_patterns
            registry.register(event_type, handler, source="builtin")

        # Load YAML-declared event_actions (appended as side-effect
        # handlers)
        yaml_actions = getattr(self.config.workflow, "event_actions", [])
        if yaml_actions:
            registry.load_yaml_actions(yaml_actions, self.event_log)

        self._register_workflow_graph_shadow_handlers(registry)
        return registry

    def _register_workflow_graph_shadow_handlers(
        self,
        registry: EventActionRegistry,
    ) -> None:
        """Wire compiled graph evaluation into runtime in shadow mode.

        The handler is intentionally read-only: no EventWriter, no TaskStore
        mutation. It gives the graph runtime a live caller without changing
        the legacy reactor's primary decisions.
        """
        try:
            from zf.core.workflow.graph import compile_workflow_graph

            graph = compile_workflow_graph(self.config)
        except Exception:
            return
        event_types: set[str] = set()
        for node in graph.nodes:
            for raw in (
                node.trigger,
                node.success_event,
                node.failure_event,
                node.skipped_event,
            ):
                event_types.update(_string_list(raw.split(",") if isinstance(raw, str) else raw))
        event_types.update(graph.event_sets.stage_progress_events)
        self._workflow_graph_shadow_graph = graph
        for event_type in sorted(event for event in event_types if event):
            if (
                event_type in _GRAPH_REVIEW_TEST_JUDGE_EVENTS
                and registry.primary(event_type) is None
            ):
                registry.register(
                    event_type,
                    self._on_workflow_graph_reconcile_event,
                    source="workflow_graph_event_handler",
                )
            registry.register(
                event_type,
                self._on_workflow_graph_reconcile_shadow,
                source="workflow_graph_shadow",
            )

    def _on_workflow_graph_reconcile_event(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        return self._workflow_graph_reconcile_bridge(event)

    def _on_workflow_graph_reconcile_shadow(
        self,
        event: ZfEvent,
    ) -> None:
        try:
            from zf.runtime.workflow_reconciler import WorkflowGraphReconciler

            graph = getattr(self, "_workflow_graph_shadow_graph", None)
            if graph is None:
                return None
            task = self.task_store.get(event.task_id) if event.task_id else None
            decisions = WorkflowGraphReconciler().plan(
                graph=graph,
                events=self.event_log.read_all(),
                task=task,
                trigger_event=event,
            )
            cache = getattr(self, "_workflow_graph_shadow_last", None)
            if cache is None:
                cache = {}
                self._workflow_graph_shadow_last = cache
            cache[event.id] = [decision.to_dict() for decision in decisions]
        except Exception:
            return None
        return None

    def _run_workflow_graph_static_gate(self, event: ZfEvent) -> bool:
        """Run the graph-derived impl_exit_gate action when explicitly enabled.

        Return True when the graph handled the event, including blocked
        readiness. The legacy static gate wrapper should then not run.
        """
        if event.type != "dev.build.done":
            return False
        dag = getattr(getattr(self.config, "workflow", None), "dag", None)
        if dag is None or not getattr(dag, "enabled", False):
            return False
        if not getattr(dag, "graph_static_gate_action", False):
            return False
        try:
            from zf.core.workflow.graph import compile_workflow_graph
            from zf.runtime.stage_actions import StageActionContext
            from zf.runtime.workflow_reconciler import WorkflowGraphReconciler

            graph = compile_workflow_graph(self.config)
            gate = graph.node("gate:impl_exit_gate")
            if gate is None:
                return False
            events = self.event_log.read_all()
            if not any(existing.id == event.id for existing in events):
                events = [*events, event]
            task = self.task_store.get(event.task_id) if event.task_id else None
            reconciler = WorkflowGraphReconciler()
            decisions = [
                decision for decision in reconciler.plan(
                    graph=graph,
                    events=events,
                    task=task,
                    trigger_event=event,
                )
                if decision.node_id == gate.node_id
            ]
            cache = getattr(self, "_workflow_graph_action_last", None)
            if cache is None:
                cache = {}
                self._workflow_graph_action_last = cache
            if not decisions:
                cache[event.id] = []
                return False
            gate_root = self._workflow_graph_static_gate_project_root(event)
            results = reconciler.commit(
                decisions,
                StageActionContext(
                    event_writer=self.event_writer,
                    task_store=self.task_store,
                    source_event=event,
                    project_root=str(gate_root),
                    config=self.config,
                ),
            )
            event_by_id = self._workflow_graph_static_gate_events_by_id(results)
            cache[event.id] = [
                {
                    "decision": decision.to_dict(),
                    "result": results[idx].to_dict() if idx < len(results) else None,
                    "static_gate_signature": self._workflow_graph_static_gate_signature(
                        results[idx] if idx < len(results) else None,
                        event_by_id,
                    ),
                }
                for idx, decision in enumerate(decisions)
            ]
            return True
        except Exception:
            return False

    def _workflow_graph_static_gate_project_root(self, event: ZfEvent) -> Path:
        resolver = getattr(self, "_static_gate_project_root", None)
        if callable(resolver):
            try:
                return resolver(event)
            except Exception:
                pass
        return Path(getattr(self, "project_root", "."))

    def _workflow_graph_static_gate_events_by_id(
        self,
        results: list[Any],
    ) -> dict[str, ZfEvent]:
        emitted_ids = {
            event_id
            for result in results
            for event_id in getattr(result, "emitted_event_ids", ())
        }
        if not emitted_ids:
            return {}
        return {
            event.id: event
            for event in self.event_log.read_all()
            if event.id in emitted_ids and event.type.startswith("static_gate.")
        }

    def _workflow_graph_static_gate_signature(
        self,
        result: Any,
        event_by_id: dict[str, ZfEvent],
    ) -> dict[str, Any]:
        if result is None:
            return {}
        from zf.runtime.workflow_shadow_diff import static_gate_event_signature

        for event_id in getattr(result, "emitted_event_ids", ()):
            event = event_by_id.get(event_id)
            if event is not None:
                return static_gate_event_signature(event)
        return {}

    def _workflow_graph_reconcile_enabled(self, event: ZfEvent | None = None) -> bool:
        if event is not None and event.type not in _GRAPH_REVIEW_TEST_JUDGE_EVENTS:
            return False
        dag = getattr(getattr(self.config, "workflow", None), "dag", None)
        return bool(
            dag is not None
            and getattr(dag, "enabled", False)
            and getattr(dag, "graph_review_test_judge_reconcile", False)
        )

    def _workflow_graph_reconcile_bridge(
        self,
        event: ZfEvent,
        *,
        source: str = "event_handler",
    ) -> OrchestratorDecision | None:
        if not self._workflow_graph_reconcile_enabled(event):
            return None
        if getattr(self, "_workflow_graph_reconcile_bridge_active", False):
            return None
        if not event.task_id:
            return None
        task = self.task_store.get(event.task_id)
        if task is None or task.status in {"done", "cancelled", "blocked"}:
            return None
        try:
            from zf.core.workflow.graph import compile_workflow_graph
            from zf.runtime.workflow_reconciler import WorkflowGraphReconciler

            self._workflow_graph_reconcile_bridge_active = True
            graph = compile_workflow_graph(self.config)
            events = self.event_log.read_all()
            if not any(existing.id == event.id for existing in events):
                events = [*events, event]
            decisions = WorkflowGraphReconciler().plan(
                graph=graph,
                events=events,
                task=task,
                trigger_event=event,
            )
            cache = getattr(self, "_workflow_graph_reconcile_last", None)
            if cache is None:
                cache = {}
                self._workflow_graph_reconcile_last = cache
            cache[event.id] = [decision.to_dict() for decision in decisions]
            ready = [
                decision for decision in decisions
                if decision.ready and decision.action_plan is not None
            ]
            if ready:
                ready.sort(key=lambda item: _workflow_graph_action_priority(
                    item.action_plan.action_type if item.action_plan else "",
                ))
                return self._commit_workflow_graph_reconcile_decision(
                    ready[0],
                    event,
                    task,
                    source=source,
                )
            if decisions:
                reason = "; ".join(
                    decision.reason for decision in decisions[:2]
                    if decision.reason
                ) or "workflow graph reconcile blocked"
                return OrchestratorDecision(
                    action="block",
                    task_id=event.task_id,
                    reason=f"{event.type} graph reconcile blocked: {reason}",
                )
        except Exception as exc:
            return OrchestratorDecision(
                action="block",
                task_id=event.task_id,
                reason=f"{event.type} graph reconcile error: {type(exc).__name__}",
            )
        finally:
            self._workflow_graph_reconcile_bridge_active = False
        return None

    def _workflow_graph_resync_reconcile(
        self,
        events: list[ZfEvent] | None = None,
    ) -> list[OrchestratorDecision]:
        if not self._workflow_graph_reconcile_enabled():
            return []
        try:
            event_list = list(events if events is not None else self.event_log.read_all())
        except Exception:
            return []
        latest: dict[str, ZfEvent] = {}
        for event in event_list:
            if event.task_id and event.type in _GRAPH_REVIEW_TEST_JUDGE_EVENTS:
                latest[event.task_id] = event
        decisions: list[OrchestratorDecision] = []
        for event in latest.values():
            if self._workflow_graph_trigger_already_committed(event):
                continue
            decision = self._workflow_graph_reconcile_bridge(
                event,
                source="resync",
            )
            if decision is not None:
                decisions.append(decision)
        return decisions

    def _commit_workflow_graph_reconcile_decision(
        self,
        decision,
        event: ZfEvent,
        task: Task,
        *,
        source: str,
    ) -> OrchestratorDecision | None:
        plan = decision.action_plan
        if plan is None:
            return None
        if self._workflow_graph_trigger_already_committed(event):
            if source == "resync":
                return None
            return OrchestratorDecision(
                action="noop",
                task_id=event.task_id,
                reason=f"{event.type} graph reconcile already committed",
            )
        if plan.action_type == "dispatch_role":
            target_role = str(plan.payload.get("target_role") or "")
            return self._workflow_graph_dispatch_role(
                task,
                event,
                target_role=target_role,
                source=source,
            )
        if plan.action_type == "route_rework":
            return self._route_rework_trigger(
                task,
                event,
                reason=f"{event.type} → rework (workflow graph {source})",
            )
        if plan.action_type == "complete_task":
            return self._workflow_graph_complete_task(task, event, source=source)
        if plan.action_type in {"run_gate", "start_fanout", "aggregate_fanout"}:
            if source != "resync":
                return None
            return self._workflow_graph_commit_runtime_action(
                decision,
                event,
                task,
                source=source,
            )
        return None

    def _workflow_graph_commit_runtime_action(
        self,
        decision,
        event: ZfEvent,
        task: Task,
        *,
        source: str,
    ) -> OrchestratorDecision | None:
        plan = decision.action_plan
        if plan is None:
            return None
        before_ids = self._workflow_graph_event_ids()
        if plan.action_type == "run_gate":
            try:
                from zf.runtime.stage_actions import StageActionContext
                from zf.runtime.workflow_reconciler import WorkflowGraphReconciler

                gate_root = self._workflow_graph_static_gate_project_root(event)
                results = WorkflowGraphReconciler().commit(
                    [decision],
                    StageActionContext(
                        event_writer=self.event_writer,
                        task_store=self.task_store,
                        source_event=event,
                        project_root=str(gate_root),
                        config=self.config,
                    ),
                )
                cache = getattr(self, "_workflow_graph_action_last", None)
                if cache is None:
                    cache = {}
                    self._workflow_graph_action_last = cache
                cache[event.id] = [
                    {
                        "decision": decision.to_dict(),
                        "result": result.to_dict(),
                    }
                    for result in results
                ]
            except Exception as exc:
                return OrchestratorDecision(
                    action="block",
                    task_id=task.id,
                    reason=(
                        f"{event.type} graph run_gate error: "
                        f"{type(exc).__name__}"
                    ),
                )
        elif plan.action_type == "start_fanout":
            for method_name in (
                "_maybe_start_reader_fanout",
                "_maybe_start_writer_fanout",
            ):
                method = getattr(self, method_name, None)
                if callable(method):
                    try:
                        method(event)
                    except Exception:
                        pass
        elif plan.action_type == "aggregate_fanout":
            payload = event.payload if isinstance(event.payload, dict) else {}
            fanout_id = str(payload.get("fanout_id") or "")
            if not fanout_id:
                return None
            for method_name in ("_evaluate_reader_fanout", "_evaluate_writer_fanout"):
                method = getattr(self, method_name, None)
                if callable(method):
                    try:
                        method(fanout_id)
                    except Exception:
                        pass
        else:
            return None
        after_events = self._workflow_graph_new_events(before_ids)
        if not after_events:
            return None
        action = {
            "run_gate": "gate",
            "start_fanout": "fanout",
            "aggregate_fanout": "aggregate",
        }.get(plan.action_type, "workflow")
        return OrchestratorDecision(
            action=action,
            task_id=task.id,
            reason=(
                f"{event.type} → {plan.action_type} "
                f"(workflow graph {source})"
            ),
        )

    def _workflow_graph_event_ids(self) -> set[str]:
        try:
            return {event.id for event in self.event_log.read_all()}
        except Exception:
            return set()

    def _workflow_graph_new_events(self, before_ids: set[str]) -> list[ZfEvent]:
        try:
            return [
                event for event in self.event_log.read_all()
                if event.id not in before_ids
            ]
        except Exception:
            return []

    def _workflow_graph_dispatch_role(
        self,
        task: Task,
        event: ZfEvent,
        *,
        target_role: str,
        source: str,
    ) -> OrchestratorDecision | None:
        if not target_role:
            return OrchestratorDecision(
                action="block",
                task_id=task.id,
                reason=f"{event.type} graph dispatch missing target_role",
            )
        target_status = _workflow_graph_target_status(target_role, task.status)
        if target_status and task.status != target_status:
            self._move_task(task.id, target_status, trigger_event=event.type)
        self.task_store.update(task.id, assigned_to=target_role)
        self.event_writer.append(ZfEvent(
            type="task.assigned",
            actor="zf-cli",
            task_id=task.id,
            payload={
                "role": target_role,
                "assignee": target_role,
                "source": f"workflow_graph_{source}",
                "trigger_event": event.type,
                "trigger_event_id": event.id,
                "effective_trigger_event": event.type,
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        return OrchestratorDecision(
            action="assign",
            task_id=task.id,
            role=target_role,
            reason=f"{event.type} → {target_role} (workflow graph {source})",
        )

    def _workflow_graph_complete_task(
        self,
        task: Task,
        event: ZfEvent,
        *,
        source: str,
    ) -> OrchestratorDecision:
        if not self._evaluate_terminal_done(event, task):
            return OrchestratorDecision(
                action="block",
                task_id=event.task_id,
                reason=f"{event.type} terminal evidence blocked",
            )
        self._record_terminal_accepted(event, task)
        try:
            self._clear_evidence_reissue(event.task_id)
        except AttributeError:
            pass
        self._move_task(task.id, "done", trigger_event=event.type)
        self._settle_task_chain_workers_idle(
            task.id,
            fallback_assignee=task.assigned_to or "",
            reason=f"task {task.id} done after {event.type}",
        )
        if event.type == "judge.passed":
            self._emit_spec_promote_decision(event, task)
        return OrchestratorDecision(
            action="move",
            task_id=task.id,
            reason=f"{event.type} → done (workflow graph {source})",
        )

    def _workflow_graph_trigger_already_committed(self, event: ZfEvent) -> bool:
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        for candidate in reversed(events):
            if candidate.task_id != event.task_id:
                continue
            payload = candidate.payload if isinstance(candidate.payload, dict) else {}
            if str(payload.get("trigger_event_id") or "") == event.id:
                if candidate.type in {
                    "task.assigned",
                    "task.rework.requested",
                    "task.done.evidence",
                    "static_gate.passed",
                    "static_gate.failed",
                    "static_gate.skipped",
                    "fanout.started",
                    "fanout.aggregate.completed",
                }:
                    return True
        return False

    def _event_handlers(self) -> dict:
        """Back-compat shim: return a simple event_type → handler dict
        built from the registry. External probes (tests, wake_patterns
        stub, validate topology check) still rely on this shape. New
        call sites should use `self.event_registry.primary()` instead.
        """
        registry = getattr(self, "event_registry", None)
        if registry is None:
            # Stub-mode (wake_patterns.reactor_handler_events() path):
            # no instance has been fully initialized. Derive handlers
            # by mapping _BUILTIN_HANDLER_METHODS to the bound methods.
            out: dict = {}
            for event_type, method_name in _BUILTIN_HANDLER_METHODS:
                handler = getattr(self, method_name, None)
                if handler is not None:
                    out[event_type] = handler
            return out
        return {
            event_type: entry.handler
            for event_type, entries in registry._entries.items()
            for entry in entries[:1]  # primary only
        }

    def _on_build_done(self, event: ZfEvent) -> OrchestratorDecision | None:
        graph_decision = self._workflow_graph_reconcile_bridge(event)
        if graph_decision is not None:
            return graph_decision
        task = self.task_store.get(event.task_id)
        if task and task.status == "in_progress":
            missing_delta, delta_evidence = self._rework_delta_missing(event, task)
            if missing_delta:
                self._emit_rework_no_delta_failed(event, delta_evidence)
                return OrchestratorDecision(
                    action="block",
                    task_id=event.task_id,
                    reason=f"{event.type} blocked: rework has no delta",
                )
            if self._task_ref_rejected(event):
                return OrchestratorDecision(
                    action="block",
                    task_id=event.task_id,
                    reason=f"{event.type} rejected by task ref validation",
                )
            # G-GAN-1: arch.proposal.done participates in the GAN loop.
            # When workflow.gan_rounds >= 2, the first N-1 proposals
            # stay in_progress + emit gan.round.started; only the
            # final round falls through to the normal review path.
            if event.type == "arch.proposal.done":
                gan_total = max(1, int(self.config.workflow.gan_rounds or 1))
                if gan_total >= 2:
                    current = self._gan_round.get(event.task_id, 0) + 1
                    self._gan_round[event.task_id] = current
                    if current < gan_total:
                        try:
                            self.event_writer.append(ZfEvent(
                                type="gan.round.started",
                                actor="orchestrator",
                                task_id=event.task_id,
                                payload={"round": current, "total": gan_total},
                            ))
                        except Exception:
                            pass
                        return OrchestratorDecision(
                            action="gan_iterate", task_id=event.task_id,
                            reason=f"gan round {current}/{gan_total} (no review yet)",
                        )
                    # Final round: emit completed and fall through
                    try:
                        self.event_writer.append(ZfEvent(
                            type="gan.round.completed",
                            actor="orchestrator",
                            task_id=event.task_id,
                            payload={"round": current, "total": gan_total},
                        ))
                    except Exception:
                        pass
                    self._gan_round.pop(event.task_id, None)
            # G-WIRE-1: scope check before moving to review. Legacy projects
            # keep violations observational; strict harness presets fail
            # closed and route rework before any review/test/judge handoff.
            violations = self._check_scope_violations(task)
            if violations and self._scope_fail_closed():
                failed_event = event
                try:
                    failed_event = self.event_writer.append(ZfEvent(
                        type="gate.failed",
                        actor="zf-cli",
                        task_id=task.id,
                        payload={
                            "gate": "scope",
                            "reason": "scope violation fail-closed",
                            "violations": [
                                {
                                    "path": item.path,
                                    "reason": item.reason,
                                }
                                for item in violations
                            ],
                            "trigger_event": event.type,
                            "trigger_event_id": event.id,
                        },
                        causation_id=event.id,
                        correlation_id=event.correlation_id,
                    ))
                except Exception:
                    pass
                dispatched_role = self._dispatch_rework(task, failed_event)
                return OrchestratorDecision(
                    action="dispatch",
                    task_id=event.task_id,
                    role=dispatched_role or task.assigned_to or "dev",
                    reason="scope.violation → rework (fail-closed)",
                )
            self._move_task(event.task_id, "review")
            # B3: the worker that emitted this build is now done with
            # the task. Their state transitions from busy → awaiting_review.
            if task.assigned_to:
                self._set_worker_state(
                    task.assigned_to, "awaiting_review",
                    reason=f"{event.type} for task {task.id}",
                )
            return OrchestratorDecision(
                action="move", task_id=event.task_id,
                reason=f"{event.type} → review",
            )
        return None

    def _on_artifact_manifest_published(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Close plan-only tasks when the final orchestrator manifest is valid."""
        task_id = event.task_id or self._payload_str(event, "task_id")
        if not task_id:
            return None
        task = self.task_store.get(task_id)
        if task is None or task.status in {"cancelled", "blocked"}:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        loaded = load_manifest_from_payload(
            payload,
            project_root=self.project_root,
            state_dir=self.state_dir,
            default_role=event.actor or "",
        )
        if not loaded.ok or loaded.manifest is None:
            return None
        manifest = loaded.manifest
        if not self._is_orchestrator_final_manifest(event, manifest):
            return None
        plan_only = self._is_plan_only_task(task)
        product_delivery = self._product_delivery_requested(manifest)
        if task.status == "done" and not product_delivery:
            return None
        if not plan_only and not product_delivery:
            return None
        approval = self._latest_design_approval_before(
            task_id=task.id,
            event_id=event.id,
        )
        if approval is None:
            if product_delivery:
                self._emit_artifact_manifest_blocked(
                    task=task,
                    event=event,
                    reason="missing_approved_design_critique",
                    details={
                        "required_event": "design.critique.done",
                        "required_verdict": "approve",
                        "delivery_mode": "product_delivery",
                    },
                )
                return OrchestratorDecision(
                    action="block",
                    task_id=task.id,
                    reason=(
                        "product delivery blocked: "
                        "missing approved design critique"
                    ),
                )
            return None
        if product_delivery and not self._valid_product_delivery_approval(approval):
            self._emit_plan_only_blocked(
                task=task,
                event=event,
                reason="product delivery approval validation failed",
                details={
                    "approval_event_id": approval.id,
                    "approval_event_type": approval.type,
                    "required": [
                        "approved verdict",
                        "non-empty checks, artifact_refs, evidence_refs, or critic_gate_ref",
                    ],
                },
            )
            return OrchestratorDecision(
                action="block",
                task_id=task.id,
                reason="product delivery blocked: invalid approval",
            )

        if product_delivery:
            return self._apply_product_delivery_manifest(
                task=task,
                event=event,
                manifest=manifest,
                approval=approval,
            )

        promote = self._promote_plan_only_artifacts(event, manifest)
        if promote["blocked"]:
            self._emit_plan_only_blocked(
                task=task,
                event=event,
                reason="artifact promotion blocked",
                details=promote,
            )
            return OrchestratorDecision(
                action="block",
                task_id=task.id,
                reason="plan-only finalization blocked: artifact promotion",
            )

        validation = self._validate_plan_only_final_backlog(
            manifest,
            task_id=task.id,
            trigger_event=event,
        )
        if not validation["passed"]:
            self._emit_plan_only_blocked(
                task=task,
                event=event,
                reason="final backlog validation failed",
                details=validation,
            )
            return OrchestratorDecision(
                action="block",
                task_id=task.id,
                reason="plan-only finalization blocked: final backlog validation",
            )

        manifest_refs = [ref.to_dict() for ref in manifest.artifact_refs]
        artifact_ref_paths = [
            str(getattr(ref, "path", "") or "").strip()
            for ref in manifest.artifact_refs
            if str(getattr(ref, "path", "") or "").strip()
        ]
        discriminator_event_id = ""
        try:
            passed = self.event_writer.append(ZfEvent(
                type="discriminator.passed",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "source": "plan_only_final_artifact_manifest",
                    "trigger_event": event.type,
                    "trigger_event_id": event.id,
                    "artifact_manifest_event_id": event.id,
                    "critic_event_id": approval.id,
                    "checks": validation.get("checks", []),
                    "promote": promote,
                    "validation": validation,
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
            discriminator_event_id = passed.id
        except Exception:
            pass
        try:
            self.event_writer.append(ZfEvent(
                type="task.done.evidence",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "source": "plan_only_final_artifact_manifest",
                    "trigger_event": event.type,
                    "trigger_event_id": event.id,
                    "artifact_manifest_event_id": event.id,
                    "critic_event_id": approval.id,
                    "discriminator_event_id": discriminator_event_id,
                    "artifact_refs": artifact_ref_paths,
                    "artifact_manifest_refs": manifest_refs,
                    "promote": promote,
                    "validation": validation,
                    "checks": validation.get("checks", []),
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass
        try:
            self._clear_evidence_reissue(task.id)
        except AttributeError:
            pass
        self._move_task(task.id, "done", trigger_event=event.type)
        self._settle_task_chain_workers_idle(
            task.id,
            fallback_assignee=task.assigned_to or "",
            reason=f"plan-only task {task.id} done after final manifest",
        )
        return OrchestratorDecision(
            action="move",
            task_id=task.id,
            reason="artifact.manifest.published → plan-only done",
        )

    @staticmethod
    def _payload_str(event: ZfEvent, key: str) -> str:
        payload = event.payload if isinstance(event.payload, dict) else {}
        return str(payload.get(key) or "").strip()

    def _is_orchestrator_final_manifest(self, event: ZfEvent, manifest: Any) -> bool:
        actor = str(event.actor or "").strip()
        role = str(getattr(manifest, "role", "") or "").strip()
        return actor == "orchestrator" or role == "orchestrator"

    def _is_plan_only_task(self, task: Task) -> bool:
        contract = getattr(task, "contract", None)
        phase = str(getattr(contract, "phase", "") or "").strip().lower()
        if phase in {"plan", "planning", "design"}:
            return True
        return not self._has_implementation_writer_role()

    def _product_delivery_requested(self, manifest: Any) -> bool:
        handoff = getattr(manifest, "handoff_contract", {}) or {}
        if not isinstance(handoff, dict):
            return False
        if bool(handoff.get("product_delivery")):
            return True
        mode = str(
            handoff.get("delivery_mode")
            or handoff.get("workflow")
            or handoff.get("spine")
            or ""
        ).strip().lower()
        return mode in {"product_delivery", "product", "task_map_delivery"}

    def _apply_product_delivery_manifest(
        self,
        *,
        task: Task,
        event: ZfEvent,
        manifest: Any,
        approval: ZfEvent,
    ) -> OrchestratorDecision | None:
        task_map_ref = self._final_task_map_ref(manifest)
        source_index_ref = self._final_source_index_ref(manifest)
        coverage_report_ref = self._final_coverage_report_ref(manifest)
        source_refs = self._final_task_map_source_refs(manifest, event)
        task_map_payload: dict[str, Any] | None = None
        source_index_payload: dict[str, Any] | None = None
        coverage_report_payload: dict[str, Any] | None = None
        if task_map_ref:
            try:
                task_map_payload = load_task_map(resolve_artifact_file(
                    task_map_ref,
                    project_root=self.project_root,
                    state_dir=self.state_dir,
                ))
            except Exception as exc:
                self._emit_plan_only_blocked(
                    task=task,
                    event=event,
                    reason="product delivery task-map load failed",
                    details={"task_map_ref": task_map_ref, "error": str(exc)},
                )
                return OrchestratorDecision(
                    action="block",
                    task_id=task.id,
                    reason="product delivery blocked: task-map load failed",
                )
            if source_index_ref:
                try:
                    source_index_payload = load_source_index(resolve_artifact_file(
                        source_index_ref,
                        project_root=self.project_root,
                        state_dir=self.state_dir,
                    ))
                except Exception as exc:
                    self._emit_plan_only_blocked(
                        task=task,
                        event=event,
                        reason="product delivery source-index load failed",
                        details={"source_index_ref": source_index_ref, "error": str(exc)},
                    )
                    return OrchestratorDecision(
                        action="block",
                        task_id=task.id,
                        reason="product delivery blocked: source-index load failed",
                    )
            if coverage_report_ref:
                try:
                    coverage_path = resolve_artifact_file(
                        coverage_report_ref,
                        project_root=self.project_root,
                        state_dir=self.state_dir,
                    )
                    loaded = json.loads(coverage_path.read_text(encoding="utf-8"))
                    if not isinstance(loaded, dict):
                        raise ValueError("coverage-report must be a JSON object")
                    coverage_report_payload = loaded
                except Exception as exc:
                    self._emit_plan_only_blocked(
                        task=task,
                        event=event,
                        reason="product delivery coverage-report load failed",
                        details={
                            "coverage_report_ref": coverage_report_ref,
                            "error": str(exc),
                        },
                    )
                    return OrchestratorDecision(
                        action="block",
                        task_id=task.id,
                        reason="product delivery blocked: coverage-report load failed",
                    )
        else:
            backlog_ref = self._final_backlog_ref(manifest)
            if not backlog_ref:
                self._emit_plan_only_blocked(
                    task=task,
                    event=event,
                    reason="product delivery requires task_map_ref or backlog_ref",
                    details={"manifest_event_id": event.id},
                )
                return OrchestratorDecision(
                    action="block",
                    task_id=task.id,
                    reason="product delivery blocked: missing task-map/backlog",
                )
            try:
                from zf.cli import spec as spec_cli

                frontmatter, _body = spec_cli._extract_frontmatter(
                    self.project_root / backlog_ref
                )
                if frontmatter is None:
                    raise ValueError("no YAML frontmatter")
                ingest_plan = spec_cli._build_ingest_plan(
                    frontmatter,
                    self.project_root / backlog_ref,
                )
                task_map_payload = build_task_map_from_ingest_plan(
                    ingest_plan,
                    source_refs=source_refs,
                )
                task_map_ref = self._write_synthesized_task_map(
                    task_id=task.id,
                    payload=task_map_payload,
                    trigger_event=event,
                )
                source_index_payload = self._build_synthesized_source_index(
                    task_map_payload,
                    source_ref=backlog_ref,
                )
                source_index_ref = self._write_synthesized_source_index(
                    task_id=task.id,
                    payload=source_index_payload,
                    trigger_event=event,
                )
                source_refs["source_index_ref"] = source_index_ref
                self._emit_synthesized_task_map_manifest(
                    task_id=task.id,
                    task_map_ref=task_map_ref,
                    task_map_payload=task_map_payload,
                    trigger_event=event,
                )
            except Exception as exc:
                self._emit_plan_only_blocked(
                    task=task,
                    event=event,
                    reason="product delivery task-map synthesis failed",
                    details={"backlog_ref": backlog_ref, "error": str(exc)},
                )
                return OrchestratorDecision(
                    action="block",
                    task_id=task.id,
                    reason="product delivery blocked: task-map synthesis failed",
                )

        result = ingest_task_map_to_kanban(
            self.state_dir,
            task_map_payload or {},
            source_refs=source_refs,
            source_index=source_index_payload,
            source_index_ref=source_index_ref,
            coverage_report=coverage_report_payload,
            coverage_report_ref=coverage_report_ref,
            require_source_index=True,
            task_map_ref=task_map_ref,
            writer=self.event_writer,
            actor="zf-cli",
            causation_id=event.id,
            correlation_id=event.correlation_id,
        )
        if not result.passed:
            self._emit_plan_only_blocked(
                task=task,
                event=event,
                reason="product delivery task-map validation failed",
                details=result.to_dict(),
            )
            return OrchestratorDecision(
                action="block",
                task_id=task.id,
                reason="product delivery blocked: task-map validation failed",
            )
        started = self.event_writer.append(ZfEvent(
            type="product_delivery.spine.started",
            actor="zf-cli",
            task_id=task.id,
            payload={
                "source": "artifact.manifest.published",
                "artifact_manifest_event_id": event.id,
                "critic_event_id": approval.id,
                "task_map_ref": task_map_ref,
                "source_index_ref": source_index_ref,
                "coverage_report_ref": coverage_report_ref,
                "created_task_ids": list(result.created_task_ids),
                "skipped_task_ids": list(result.skipped_task_ids),
                "summary": dict(result.summary),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        self.event_writer.append(ZfEvent(
            type="task.done.evidence",
            actor="zf-cli",
            task_id=task.id,
            payload={
                "source": "product_delivery_spine_started",
                "artifact_manifest_event_id": event.id,
                "critic_event_id": approval.id,
                "spine_event_id": started.id,
                "task_map_ref": task_map_ref,
                "source_index_ref": source_index_ref,
                "coverage_report_ref": coverage_report_ref,
                "created_task_ids": list(result.created_task_ids),
                "skipped_task_ids": list(result.skipped_task_ids),
            },
            causation_id=started.id,
            correlation_id=started.correlation_id,
        ))
        if task.status != "done":
            self._move_task(task.id, "done", trigger_event=event.type)
        return OrchestratorDecision(
            action="move",
            task_id=task.id,
            reason="artifact.manifest.published → product delivery spine",
        )

    def _has_implementation_writer_role(self) -> bool:
        writer_names = {"dev", "developer", "builder", "writer", "implementer"}
        for role in getattr(self.config, "roles", []) or []:
            if str(getattr(role, "name", "") or "") == "orchestrator":
                continue
            kind = str(getattr(role, "role_kind", "") or "auto").lower()
            name = str(getattr(role, "name", "") or "").lower()
            if kind == "writer":
                return True
            if kind == "auto" and name in writer_names:
                return True
        return False

    def _latest_design_approval_before(
        self,
        *,
        task_id: str,
        event_id: str,
    ) -> ZfEvent | None:
        try:
            events = self.event_log.read_all()
        except Exception:
            return None
        limit = len(events)
        if event_id:
            for idx, candidate in enumerate(events):
                if candidate.id == event_id:
                    limit = idx
                    break
        for candidate in reversed(events[:limit]):
            if candidate.task_id != task_id or candidate.type != "design.critique.done":
                continue
            payload = candidate.payload if isinstance(candidate.payload, dict) else {}
            verdict = str(payload.get("verdict") or "").strip().lower()
            if verdict in {"approve", "approved", "pass", "passed"}:
                return candidate
        return None

    def _valid_product_delivery_approval(self, event: ZfEvent) -> bool:
        payload = event.payload if isinstance(event.payload, dict) else {}
        verdict = str(payload.get("verdict") or "").strip().lower()
        if verdict not in {"approve", "approved", "pass", "passed"}:
            return False
        if payload.get("contract_valid") is False:
            return False
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            return False
        for key in ("checks", "artifact_refs", "evidence_refs"):
            value = payload.get(key)
            if isinstance(value, list) and value:
                return True
        for key in ("critic_gate_ref", "review_ref", "evidence_ref"):
            if str(payload.get(key) or "").strip():
                return True
        return False

    def _promote_plan_only_artifacts(
        self,
        event: ZfEvent,
        manifest: Any,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "promoted": [],
            "already_present": [],
            "skipped": [],
            "blocked": [],
        }
        for ref in getattr(manifest, "artifact_refs", []) or []:
            raw_path = str(ref.path or "")
            if self._is_runtime_artifact_ref_path(raw_path):
                result["skipped"].append({
                    "path": raw_path,
                    "reason": "runtime artifact is not promoted to project root",
                })
                continue
            rel = self._repo_relative_artifact_path(raw_path)
            if rel is None:
                result["blocked"].append({
                    "path": raw_path,
                    "reason": "artifact path is not repo-relative",
                })
                continue
            if rel.parts and rel.parts[0] == ".zf":
                result["skipped"].append({
                    "path": rel.as_posix(),
                    "reason": "runtime artifact is not promoted to project root",
                })
                continue
            source = self._plan_artifact_source_path(event, manifest, ref, rel)
            if source is None or not source.exists():
                result["blocked"].append({
                    "path": rel.as_posix(),
                    "reason": "source artifact missing",
                })
                continue
            if not source.is_file():
                result["blocked"].append({
                    "path": rel.as_posix(),
                    "reason": "source artifact is not a file",
                    "source": str(source),
                })
                continue
            source_hash = self._file_sha256(source)
            expected_hash = str(getattr(ref, "sha256", "") or "").lower()
            if expected_hash and source_hash != expected_hash:
                result["blocked"].append({
                    "path": rel.as_posix(),
                    "reason": "source sha256 mismatch",
                    "expected": expected_hash,
                    "actual": source_hash,
                })
                continue
            dest = self.project_root / rel
            if dest.exists():
                if not dest.is_file():
                    result["blocked"].append({
                        "path": rel.as_posix(),
                        "reason": "target exists but is not a file",
                    })
                    continue
                dest_hash = self._file_sha256(dest)
                if expected_hash and dest_hash != expected_hash:
                    result["blocked"].append({
                        "path": rel.as_posix(),
                        "reason": "target exists with different sha256",
                        "expected": expected_hash,
                        "actual": dest_hash,
                    })
                else:
                    result["already_present"].append(rel.as_posix())
                continue
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
            except OSError as exc:
                result["blocked"].append({
                    "path": rel.as_posix(),
                    "reason": f"copy failed: {exc}",
                })
                continue
            result["promoted"].append(rel.as_posix())

        event_type = (
            "artifact.promote.blocked"
            if result["blocked"]
            else "artifact.promote.completed"
        )
        try:
            self.event_writer.append(ZfEvent(
                type=event_type,
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    **result,
                    "trigger_event": event.type,
                    "trigger_event_id": event.id,
                    "source": "plan_only_finalization",
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass
        return result

    def _repo_relative_artifact_path(self, raw: str) -> Path | None:
        if not raw.strip():
            return None
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                rel = candidate.resolve(strict=False).relative_to(self.project_root)
            except ValueError:
                return None
        else:
            rel = candidate
        if any(part == ".." for part in rel.parts):
            return None
        if rel.parts and rel.parts[0] == ".git":
            return None
        return Path(rel.as_posix())

    def _is_runtime_artifact_ref_path(self, raw: str) -> bool:
        if not raw.strip():
            return False
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                candidate.resolve(strict=False).relative_to(
                    (self.state_dir / "artifacts").resolve(strict=False)
                )
                return True
            except ValueError:
                return False
        parts = candidate.parts
        if len(parts) >= 2 and parts[0] == ".zf" and parts[1] == "artifacts":
            return True
        return bool(parts and parts[0] == "artifacts" and (self.state_dir / candidate).exists())

    def _plan_artifact_source_path(
        self,
        event: ZfEvent,
        manifest: Any,
        ref: Any,
        rel: Path,
    ) -> Path | None:
        candidates: list[Path] = []
        workdir_path = str(getattr(ref, "workdir_path", "") or "").strip()
        if workdir_path:
            raw = Path(workdir_path)
            base = raw if raw.is_absolute() else self.state_dir / raw
            candidates.append(base / rel)
            if not raw.is_absolute():
                candidates.append(self.state_dir / "workdirs" / raw / rel)
        for owner in [
            str(event.actor or ""),
            str(getattr(manifest, "role", "") or ""),
        ]:
            if owner:
                candidates.append(self.state_dir / "workdirs" / owner / "project" / rel)
        candidates.append(self.project_root / rel)
        for candidate in candidates:
            try:
                if candidate.exists() and candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _validate_plan_only_final_backlog(
        self,
        manifest: Any,
        *,
        task_id: str,
        trigger_event: ZfEvent,
    ) -> dict[str, Any]:
        backlog_ref = self._final_backlog_ref(manifest)
        task_map_ref = self._final_task_map_ref(manifest)
        checks: list[dict[str, Any]] = []
        if not backlog_ref and not task_map_ref:
            return {
                "passed": False,
                "backlog_ref": "",
                "task_map_ref": "",
                "checks": [{"name": "backlog_ref_or_task_map_ref", "passed": False}],
            }
        task_map_payload: dict[str, Any] | None = None
        task_map_source = "candidate"
        if task_map_ref:
            try:
                task_map_path = resolve_artifact_file(
                    task_map_ref,
                    project_root=self.project_root,
                    state_dir=self.state_dir,
                )
                task_map_payload = load_task_map(task_map_path)
            except Exception as exc:
                checks.append({
                    "name": "task_map_load",
                    "passed": False,
                    "task_map_ref": task_map_ref,
                    "reason": str(exc),
                })
        else:
            path = self.project_root / backlog_ref
            try:
                from zf.cli import spec as spec_cli

                frontmatter, body = spec_cli._extract_frontmatter(path)
                if frontmatter is None:
                    raise ValueError("no YAML frontmatter")
                plan = spec_cli._build_ingest_plan(frontmatter, path)
                duplicates = spec_cli._find_duplicate_task_ids(plan)
                declared = {task["id"] for task in plan["tasks"]}
                orphan_ids = spec_cli._scan_body_for_task_ids(body) - declared
                missing_accept = [
                    task["id"] for task in plan["tasks"]
                    if not task["acceptance"] and not task["verification"]
                ]
            except Exception as exc:
                return {
                    "passed": False,
                    "backlog_ref": backlog_ref,
                    "task_map_ref": "",
                    "checks": [{
                        "name": "zf_spec_validate_strict",
                        "passed": False,
                        "reason": str(exc),
                    }],
                }
            checks.append({
                "name": "zf_spec_validate_strict",
                "passed": not duplicates and not orphan_ids and not missing_accept,
                "task_count": len(plan["tasks"]),
                "duplicate_task_ids": sorted(duplicates),
                "body_orphan_task_ids": sorted(orphan_ids),
                "missing_acceptance_and_verification": missing_accept,
            })
            checks.append({
                "name": "zf_spec_ingest_dry_run",
                "passed": bool(plan["tasks"]),
                "feature_id": plan["feature_id"],
                "task_count": len(plan["tasks"]),
            })
            task_map_payload = build_task_map_from_ingest_plan(
                plan,
                source_refs=self._final_task_map_source_refs(manifest, trigger_event),
            )
            task_map_ref = self._write_synthesized_task_map(
                task_id=task_id,
                payload=task_map_payload,
                trigger_event=trigger_event,
            )
            task_map_source = "synthesized_from_backlog"
        if task_map_payload is not None:
            task_map_validation = validate_task_map_payload(
                task_map_payload,
                require_task_verification=task_map_source != "candidate",
            )
            check = task_map_validation.to_check()
            check["task_map_ref"] = task_map_ref
            check["source"] = task_map_source
            checks.append(check)
            if task_map_source == "synthesized_from_backlog" and task_map_validation.passed:
                self._emit_synthesized_task_map_manifest(
                    task_id=task_id,
                    task_map_ref=task_map_ref,
                    task_map_payload=task_map_payload,
                    trigger_event=trigger_event,
                )
        return {
            "passed": all(bool(check.get("passed")) for check in checks),
            "backlog_ref": backlog_ref,
            "task_map_ref": task_map_ref,
            "checks": checks,
        }

    def _final_backlog_ref(self, manifest: Any) -> str:
        handoff = getattr(manifest, "handoff_contract", {}) or {}
        if isinstance(handoff, dict):
            for key in ("backlog_ref", "backlog_map_ref"):
                value = str(handoff.get(key) or "").strip()
                if value:
                    return value
        for ref in getattr(manifest, "artifact_refs", []) or []:
            kind = normalize_artifact_kind(str(getattr(ref, "kind", "") or ""))
            if kind in {"backlog", "backlog_plan", "backlog_map"}:
                return str(getattr(ref, "path", "") or "").strip()
        return ""

    def _final_task_map_ref(self, manifest: Any) -> str:
        handoff = getattr(manifest, "handoff_contract", {}) or {}
        if isinstance(handoff, dict):
            value = str(handoff.get("task_map_ref") or "").strip()
            if value:
                return value
        for ref in getattr(manifest, "artifact_refs", []) or []:
            kind = normalize_artifact_kind(str(getattr(ref, "kind", "") or ""))
            if kind in {"task_map", "work_unit_map"}:
                return str(getattr(ref, "path", "") or "").strip()
        return ""

    def _final_source_index_ref(self, manifest: Any) -> str:
        handoff = getattr(manifest, "handoff_contract", {}) or {}
        if isinstance(handoff, dict):
            value = str(handoff.get("source_index_ref") or "").strip()
            if value:
                return value
        for ref in getattr(manifest, "artifact_refs", []) or []:
            kind = normalize_artifact_kind(str(getattr(ref, "kind", "") or ""))
            if kind == "source_index":
                return str(getattr(ref, "path", "") or "").strip()
        return ""

    def _final_coverage_report_ref(self, manifest: Any) -> str:
        handoff = getattr(manifest, "handoff_contract", {}) or {}
        if isinstance(handoff, dict):
            value = str(handoff.get("coverage_report_ref") or "").strip()
            if value:
                return value
        for ref in getattr(manifest, "artifact_refs", []) or []:
            kind = normalize_artifact_kind(str(getattr(ref, "kind", "") or ""))
            if kind == "coverage_report":
                return str(getattr(ref, "path", "") or "").strip()
        return ""

    def _final_task_map_source_refs(
        self,
        manifest: Any,
        trigger_event: ZfEvent,
    ) -> dict[str, str]:
        refs: dict[str, str] = {}
        handoff = getattr(manifest, "handoff_contract", {}) or {}
        if isinstance(handoff, dict):
            for key in ("spec_ref", "plan_ref", "backlog_ref", "tdd_ref", "critic_gate_ref"):
                value = str(handoff.get(key) or "").strip()
                if value:
                    refs[key] = value
            critic_event_id = str(handoff.get("critic_event_id") or "").strip()
            if critic_event_id:
                refs["critic_event_id"] = critic_event_id
            for key in (
                "product_contract_ref",
                "source_ref",
                "source_index_ref",
                "coverage_report_ref",
                "supersedes_task_map_ref",
                "supersedes_ref",
                "supersedes",
            ):
                value = str(handoff.get(key) or "").strip()
                if value:
                    refs[key] = value
        refs.setdefault("artifact_manifest_event_id", trigger_event.id)
        for ref in getattr(manifest, "artifact_refs", []) or []:
            kind = normalize_artifact_kind(str(getattr(ref, "kind", "") or ""))
            path = str(getattr(ref, "path", "") or "").strip()
            if not path:
                continue
            if kind in {"spec", "sdd"}:
                refs.setdefault("spec_ref", path)
            elif kind in {"plan", "implementation_plan", "process_plan"}:
                refs.setdefault("plan_ref", path)
            elif kind in {"backlog", "backlog_plan", "backlog_map"}:
                refs.setdefault("backlog_ref", path)
            elif kind in {"tdd", "test_plan"}:
                refs.setdefault("tdd_ref", path)
            elif kind in {"critic_gate", "critic_review"}:
                refs.setdefault("critic_gate_ref", path)
            elif kind == "source_index":
                refs.setdefault("source_index_ref", path)
            elif kind == "coverage_report":
                refs.setdefault("coverage_report_ref", path)
            supersedes = str(getattr(ref, "supersedes", "") or "").strip()
            if kind == "task_map" and supersedes:
                refs.setdefault("supersedes_task_map_ref", supersedes)
        return refs

    def _write_synthesized_task_map(
        self,
        *,
        task_id: str,
        payload: dict[str, Any],
        trigger_event: ZfEvent,
    ) -> str:
        root = self.state_dir / "artifacts" / task_id
        root.mkdir(parents=True, exist_ok=True)
        path = root / "task-map.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path.resolve().as_posix()

    def _build_synthesized_source_index(
        self,
        task_map_payload: dict[str, Any],
        *,
        source_ref: str,
    ) -> dict[str, Any]:
        tasks: list[dict[str, Any]] = []
        for raw in task_map_payload.get("tasks") or []:
            if not isinstance(raw, dict):
                continue
            task_id = str(raw.get("task_id") or raw.get("id") or "").strip()
            if not task_id:
                continue
            title = str(raw.get("title") or task_id).strip()
            acceptance = raw.get("acceptance") or []
            if isinstance(acceptance, list):
                acceptance_text = "\n".join(str(item).strip() for item in acceptance if str(item).strip())
            else:
                acceptance_text = str(acceptance or "").strip()
            excerpt_parts = [
                f"Task: {title}",
                str(raw.get("plan_section") or raw.get("behavior") or "").strip(),
                acceptance_text,
                str(raw.get("verification") or "").strip(),
            ]
            excerpt = "\n".join(part for part in excerpt_parts if part)
            tasks.append({
                "task_id": task_id,
                "source_key": f"{source_ref}#{task_id}",
                "source_ref": f"{source_ref}#{task_id}" if source_ref else task_id,
                "source_task_id": task_id,
                "source_title": title,
                "source_excerpt": excerpt or title,
                "source_mode": "canonical",
            })
        return {
            "schema_version": "source-index.v1",
            "feature_id": str(task_map_payload.get("feature_id") or "").strip(),
            "source_ref": source_ref,
            "tasks": tasks,
        }

    def _write_synthesized_source_index(
        self,
        *,
        task_id: str,
        payload: dict[str, Any],
        trigger_event: ZfEvent,
    ) -> str:
        root = self.state_dir / "artifacts" / task_id
        root.mkdir(parents=True, exist_ok=True)
        path = root / "source-index.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path.resolve().as_posix()

    def _emit_synthesized_task_map_manifest(
        self,
        *,
        task_id: str,
        task_map_ref: str,
        task_map_payload: dict[str, Any],
        trigger_event: ZfEvent,
    ) -> None:
        path = Path(task_map_ref)
        try:
            sha256 = self._file_sha256(path)
        except OSError:
            return
        manifest_payload = {
            "task_id": task_id,
            "role": "orchestrator",
            "artifact_refs": [{
                "kind": "task_map",
                "path": task_map_ref,
                "sha256": sha256,
                "summary": "orchestrator final task-map",
                "status": "accepted",
                "source_event_id": trigger_event.id,
            }],
            "handoff_contract": {
                "source": "synthesized_from_backlog",
                "source_refs": dict(task_map_payload.get("source_refs") or {}),
            },
        }
        try:
            written = self.event_writer.append(ZfEvent(
                type="artifact.manifest.published",
                actor="orchestrator",
                task_id=task_id,
                payload=manifest_payload,
                causation_id=trigger_event.id,
                correlation_id=trigger_event.correlation_id,
            ))
            self._apply_artifact_manifest_published(written)
        except Exception:
            pass

    def _emit_plan_only_blocked(
        self,
        *,
        task: Task,
        event: ZfEvent,
        reason: str,
        details: dict[str, Any],
    ) -> None:
        try:
            self.event_writer.append(ZfEvent(
                type="task.done.blocked",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "reason": reason,
                    "details": details,
                    "source": "plan_only_finalization",
                    "trigger_event": event.type,
                    "trigger_event_id": event.id,
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass

    def _emit_artifact_manifest_blocked(
        self,
        *,
        task: Task,
        event: ZfEvent,
        reason: str,
        details: dict[str, Any],
    ) -> None:
        try:
            self.event_writer.append(ZfEvent(
                type="artifact.manifest.blocked",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "reason": reason,
                    "details": details,
                    "source": "product_delivery_manifest_gate",
                    "trigger_event": event.type,
                    "trigger_event_id": event.id,
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass

    def _task_ref_rejected(self, event: ZfEvent) -> bool:
        latest: str | None = None
        for candidate in reversed(self.event_log.read_all()):
            if candidate.type not in {"task.ref.rejected", "task.ref.updated"}:
                continue
            if candidate.task_id != event.task_id:
                continue
            if not isinstance(candidate.payload, dict):
                continue
            if candidate.payload.get("trigger_event_id") == event.id:
                latest = candidate.type
                break
        return latest == "task.ref.rejected"

    def _check_scope_violations(self, task: Task) -> list[Any]:
        """G-WIRE-1: diff workspace against the dispatch-time snapshot
        and emit one scope.violation per out-of-scope file change."""
        before = self._scope_snapshots.pop(task.id, None)
        if before is None:
            return []  # no snapshot → no scope on this task
        if not task.contract or not task.contract.scope:
            return []
        try:
            after = self._scope_ratchet.snapshot()
            changed = self._scope_ratchet.diff(before, after)
            violations = self._scope_ratchet.check(
                changed,
                allowed=list(task.contract.scope),
                blocked=list(task.contract.exclusions or []),
            )
        except Exception as exc:
            # 2026-06-10 review (I4): an errored scope check previously
            # returned [] — "could not verify" silently became "no
            # violation" even under verification.scope.fail_closed. Under
            # fail-closed config an unverifiable scope is treated as a
            # violation; legacy/observational projects keep the old skip.
            if self._scope_fail_closed():
                from zf.core.verification.scope_ratchet import ScopeViolation
                return [ScopeViolation(
                    path="<scope-check>",
                    reason=f"scope_check_errored: {exc}",
                )]
            return []
        for v in violations:
            try:
                self.event_writer.append(ZfEvent(
                    type="scope.violation",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={
                        "path": v.path,
                        "paths": [v.path],
                        "reason": v.reason,
                        "scope": list(task.contract.scope),
                        "exclusions": list(task.contract.exclusions or []),
                    },
                ))
            except Exception:
                continue
        return list(violations)

    def _scope_fail_closed(self) -> bool:
        try:
            return bool(self.config.verification.scope.fail_closed)
        except Exception:
            return False

    def _dispatch_token_required(self) -> bool:
        try:
            return bool(self.config.verification.contract.dispatch_token_required)
        except Exception:
            return False

    def _event_matches_active_dispatch(self, event: ZfEvent, task: Task) -> bool:
        expected = (
            getattr(task, "active_dispatch_id", "")
            or getattr(self, "_active_dispatch_ids", {}).get(task.id, "")
        )
        actual = ""
        if isinstance(event.payload, dict):
            actual = str(event.payload.get("dispatch_id") or "")
        if expected:
            return actual == expected
        return not self._dispatch_token_required()

    def _is_terminal_late_success(self, event: ZfEvent, task: Task) -> bool:
        if task.status != "backlog":
            return False
        if event.type not in {"review.approved", "verify.passed", "test.passed", "judge.passed"}:
            return False
        try:
            if self._non_orchestrator_subscribers(event.type):
                return False
        except Exception:
            if event.type != "judge.passed":
                return False
        return self._event_matches_active_dispatch(event, task)

    def _rework_delta_required(self) -> bool:
        try:
            return bool(self.config.verification.contract.rework_delta_required)
        except Exception:
            return False

    def _worker_lifecycle_events(self) -> set[str]:
        events = {
            "artifact.manifest.published",
            "arch.proposal.done",
            "design.critique.done",
            "dev.build.done",
            "dev.blocked",
            "review.approved",
            "review.rejected",
            "review.suspended",
            "verify.passed",
            "verify.failed",
            "test.passed",
            "test.failed",
            "test.suspended",
            "judge.passed",
            "judge.failed",
            "task.done.evidence",
        }
        try:
            for role in self.config.roles:
                events.update(role.publishes or [])
        except Exception:
            pass
        return events

    def _reject_invalid_lifecycle_event(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Reject unbound/stale lifecycle events before they affect truth."""
        if event.type not in self._worker_lifecycle_events():
            return None
        payload = self._fanout_result_payload(event)
        if payload.get("fanout_id") and (
            payload.get("child_id")
            or event.type == "fanout.synth.completed"
            or event.actor == "zf-cli"
        ):
            return None
        if not event.task_id and self._is_taskless_candidate_terminal_event(event):
            return None
        if not event.task_id and self._is_taskless_workflow_trigger(event):
            return None
        if not event.task_id and self._is_taskless_artifact_manifest(event):
            return None
        if not event.task_id and event.type in _TASKLESS_RUNTIME_CONTROL_EVENTS:
            return None
        if not event.task_id:
            self._emit_lifecycle_rejected(
                event,
                reason="missing_task_id",
                event_type="event.malformed",
            )
            return OrchestratorDecision(
                action="block",
                reason=f"{event.type} rejected: missing task_id",
            )
        task = self.task_store.get(event.task_id)
        if task is None:
            self._emit_lifecycle_rejected(
                event,
                reason="unknown_task_id",
                event_type="event.malformed",
            )
            return OrchestratorDecision(
                action="block",
                task_id=event.task_id,
                reason=f"{event.type} rejected: unknown task_id",
            )

        if event.type == "artifact.manifest.published":
            return self._reject_stale_artifact_manifest(event, task)

        fanout_reject = self._reject_stale_fanout_terminal_event(event, task)
        if fanout_reject is not None:
            return fanout_reject

        token_required = self._dispatch_token_required()
        if not token_required:
            return self._reject_evidence_contract_violation(event, task)

        actor = event.actor or ""
        trusted_actor = actor in {"zf-cli", "orchestrator"}
        if trusted_actor:
            return self._reject_evidence_contract_violation(event, task)
        if self._lifecycle_event_already_handed_off(event):
            return OrchestratorDecision(
                action="skip",
                task_id=event.task_id,
                reason=f"{event.type} already handed off",
            )
        if actor and task.assigned_to:
            try:
                actor_matches_assignee = self._assignee_equivalent(
                    actor,
                    task.assigned_to,
                )
            except Exception:
                actor_matches_assignee = actor == task.assigned_to
            if not actor_matches_assignee:
                self._emit_lifecycle_rejected(
                    event,
                    reason="actor_not_assigned",
                    expected=task.assigned_to,
                    actual=actor,
                )
                return OrchestratorDecision(
                    action="block",
                    task_id=event.task_id,
                    reason=f"{event.type} rejected: actor {actor} not assigned",
                )

        # P0-1 (live e2e 2026-05-18 zf-eval-test): actor must publish
        # event.type per its role.publishes. Without this, LLM agents
        # hallucinate cross-role events (observed: arch single-handedly
        # emitting review.approved + test.passed in one turn). Kernel
        # previously caught these only via dispatch_id mismatch — fragile
        # because if the LLM also copied the dispatch_id, hallucinated
        # reviews would have been accepted as truth.
        publishes = self._publishes_for_actor(actor)
        if publishes is not None and event.type not in publishes:
            self._emit_lifecycle_rejected(
                event,
                reason="event_not_published_by_role",
                expected=",".join(sorted(publishes)),
                actual=event.type,
            )
            return OrchestratorDecision(
                action="block",
                task_id=event.task_id,
                reason=(
                    f"{event.type} rejected: actor {actor!r} role does not "
                    f"publish this event type"
                ),
            )

        expected = (
            task.active_dispatch_id
            or getattr(self, "_active_dispatch_ids", {}).get(task.id, "")
        )
        actual = ""
        if isinstance(event.payload, dict):
            actual = str(event.payload.get("dispatch_id") or "")
        # B-STUCK-1: grace-accept a *recent* dispatch_id. A respawn (often
        # false, fired while the agent is mid coding-turn) rotates the
        # dispatch_id; the worker's valid completion still carries the
        # pre-respawn id. Rejecting it strands real work in a livelock, so
        # accept any of the last few dispatch_ids issued for this task.
        recent = getattr(self, "_recent_dispatch_ids", {}).get(task.id, [])
        graced = bool(actual) and actual in recent
        if expected and actual != expected and not graced:
            self._emit_lifecycle_rejected(
                event,
                reason="dispatch_id_mismatch" if actual else "dispatch_id_missing",
                expected=expected,
                actual=actual,
            )
            self._emit_terminal_rejected(
                event,
                task=task,
                reason="dispatch_id_mismatch" if actual else "dispatch_id_missing",
                expected=expected,
                actual=actual,
            )
            return OrchestratorDecision(
                action="block",
                task_id=event.task_id,
                reason=f"{event.type} rejected: dispatch_id mismatch",
            )
        revision_reject = self._reject_stale_task_capsule_revision(event, task)
        if revision_reject is not None:
            return revision_reject
        snapshot_reject = self._reject_stale_runtime_snapshot_completion(event, task)
        if snapshot_reject is not None:
            return snapshot_reject
        replay = self._terminal_replay_record(event, task)
        if replay is not None:
            self._emit_terminal_replayed(event, replay)
            return OrchestratorDecision(
                action="skip",
                task_id=event.task_id,
                reason=f"{event.type} replayed for dispatch_id {actual}",
            )
        evidence_reissue = self._reject_evidence_contract_violation(event, task)
        if evidence_reissue is not None:
            return evidence_reissue
        readonly_reject = self._reject_readonly_gate_mutation(event, task)
        if readonly_reject is not None:
            return readonly_reject
        return None

    def _reject_stale_task_capsule_revision(
        self,
        event: ZfEvent,
        task: Task,
    ) -> OrchestratorDecision | None:
        completion_events = TERMINAL_SUCCESS_EVENTS | {
            "arch.proposal.done",
            "design.critique.done",
            "dev.build.done",
            "task.done.evidence",
        }
        if event.type not in completion_events:
            return None
        contract = getattr(task, "contract", None)
        if contract is None:
            return None
        expected = {
            "source_revision": str(getattr(contract, "source_revision", "") or ""),
            "contract_revision": str(getattr(contract, "contract_revision", "") or ""),
            "capsule_revision": str(getattr(contract, "capsule_revision", "") or ""),
        }
        expected = {key: value for key, value in expected.items() if value}
        if not expected:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        actual = {
            key: self._completion_revision_value(payload, key)
            for key in expected
        }
        mismatched = [
            key for key, value in expected.items()
            if actual.get(key, "") != value
        ]
        if not mismatched:
            return None
        reason = (
            f"{mismatched[0]}_mismatch"
            if actual.get(mismatched[0], "")
            else f"{mismatched[0]}_missing"
        )
        required_actions = [
            (
                "Re-emit "
                f"{event.type} for dispatch_id "
                f"{payload.get('dispatch_id') or getattr(task, 'active_dispatch_id', '')} "
                f"with {key}={expected[key]}"
            )
            for key in mismatched
        ]
        self._emit_lifecycle_rejected(
            event,
            reason=reason,
            expected=json.dumps(expected, ensure_ascii=False, sort_keys=True),
            actual=json.dumps(actual, ensure_ascii=False, sort_keys=True),
        )
        self._emit_terminal_rejected(
            event,
            task=task,
            reason=reason,
            expected=expected.get(mismatched[0], ""),
            actual=actual.get(mismatched[0], ""),
        )
        try:
            self.event_writer.append(ZfEvent(
                type="task.completion.stale_rejected",
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    "reason": reason,
                    "origin_event": event.type,
                    "origin_event_id": event.id,
                    "expected": expected,
                    "actual": actual,
                    "missing_or_mismatched": mismatched,
                    "recommended_action": (
                        "reissue_completion_with_expected_revisions"
                    ),
                    "required_actions": required_actions,
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass
        return OrchestratorDecision(
            action="block",
            task_id=event.task_id,
            reason=f"{event.type} rejected: task capsule revision stale",
        )

    def _reject_stale_runtime_snapshot_completion(
        self,
        event: ZfEvent,
        task: Task,
    ) -> OrchestratorDecision | None:
        completion_events = TERMINAL_SUCCESS_EVENTS | {
            "arch.proposal.done",
            "design.critique.done",
            "dev.build.done",
            "task.done.evidence",
        }
        if event.type not in completion_events:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        snapshot_ref = str(
            payload.get("snapshot_ref")
            or payload.get("runtime_snapshot_ref")
            or ""
        )
        if not snapshot_ref:
            try:
                self.event_writer.append(ZfEvent(
                    type="runtime.snapshot.invalid",
                    actor="zf-cli",
                    task_id=event.task_id,
                    payload={
                        "source": "terminal_completion",
                        "reason": "completion_snapshot_ref_missing",
                        "origin_event": event.type,
                        "origin_event_id": event.id,
                    },
                    causation_id=event.id,
                    correlation_id=event.correlation_id,
                ))
            except Exception:
                pass
            return None
        try:
            from zf.runtime.runtime_snapshot import (
                read_runtime_snapshot,
                resolve_snapshot_ref,
            )

            snapshot_path = resolve_snapshot_ref(
                self.state_dir,
                snapshot_ref,
                project_root=self.project_root,
            )
            snapshot = read_runtime_snapshot(snapshot_path)
        except Exception:
            snapshot = {}
        if not snapshot:
            try:
                self.event_writer.append(ZfEvent(
                    type="runtime.snapshot.invalid",
                    actor="zf-cli",
                    task_id=event.task_id,
                    payload={
                        "source": "terminal_completion",
                        "reason": "completion_snapshot_unreadable",
                        "snapshot_ref": snapshot_ref,
                        "origin_event": event.type,
                        "origin_event_id": event.id,
                    },
                    causation_id=event.id,
                    correlation_id=event.correlation_id,
                ))
            except Exception:
                pass
            return None
        snap_task = snapshot.get("task") if isinstance(snapshot.get("task"), dict) else {}
        snap_run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
        contract = getattr(task, "contract", None)
        expected = {
            "task_id": task.id,
            "dispatch_id": str(getattr(task, "active_dispatch_id", "") or ""),
            "source_revision": str(getattr(contract, "source_revision", "") or ""),
            "contract_revision": str(getattr(contract, "contract_revision", "") or ""),
            "capsule_revision": str(getattr(contract, "capsule_revision", "") or ""),
        }
        actual = {
            "task_id": str(snap_task.get("task_id") or ""),
            "dispatch_id": str(snap_run.get("dispatch_id") or ""),
            "source_revision": str(snap_task.get("source_revision") or ""),
            "contract_revision": str(snap_task.get("contract_revision") or ""),
            "capsule_revision": str(snap_task.get("capsule_revision") or ""),
        }
        mismatched = [
            key for key, value in expected.items()
            if value and actual.get(key, "") != value
        ]
        if not mismatched:
            return None
        # LH-B1: staged enforcement. off → neither emit nor block; shadow →
        # emit the (observable) stale_rejected signal but do not block; enforced
        # (default) → emit and block. Lets operators watch the gate before
        # flipping it to fail-closed.
        mode = getattr(
            getattr(self.config, "verification", None), "snapshot_gate", "enforced"
        )
        if mode == "off":
            return None
        reason = f"snapshot_{mismatched[0]}_mismatch"
        try:
            self.event_writer.append(ZfEvent(
                type="task.completion.stale_rejected",
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    "reason": reason,
                    "gate_mode": mode,
                    "origin_event": event.type,
                    "origin_event_id": event.id,
                    "snapshot_ref": snapshot_ref,
                    "expected": expected,
                    "actual": actual,
                    "missing_or_mismatched": mismatched,
                    "recommended_action": "reissue_completion_with_current_snapshot_ref",
                    "required_actions": [
                        "Re-read the current runtime snapshot and re-emit "
                        f"{event.type} with the current snapshot_ref"
                    ],
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass
        if mode == "shadow":
            return None
        return OrchestratorDecision(
            action="block",
            task_id=event.task_id,
            reason=f"{event.type} rejected: runtime snapshot stale",
        )

    def _reject_stale_fanout_terminal_event(
        self,
        event: ZfEvent,
        task: Task,
    ) -> OrchestratorDecision | None:
        if event.type not in TERMINAL_SUCCESS_EVENTS:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(
            payload.get("fanout_id")
            or payload.get("fanout_instance_id")
            or ""
        )
        if not fanout_id:
            return None
        try:
            from zf.runtime.fanout_identity import fanout_current_status

            status = fanout_current_status(self.event_log.read_all(), fanout_id)
        except Exception:
            return None
        if status.current:
            return None
        stale_reason = status.stale_reason or "fanout_instance_not_current"
        self._emit_lifecycle_rejected(
            event,
            reason="stale_fanout_instance",
            expected=status.superseded_by,
            actual=fanout_id,
        )
        self._emit_terminal_rejected(
            event,
            task=task,
            reason="stale_fanout_instance",
            expected=status.superseded_by,
            actual=fanout_id,
        )
        try:
            self.event_writer.append(ZfEvent(
                type="task.completion.stale_rejected",
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    "reason": "stale_fanout_instance",
                    "stale_reason": stale_reason,
                    "origin_event": event.type,
                    "origin_event_id": event.id,
                    "fanout_id": fanout_id,
                    "superseded_by": status.superseded_by,
                    "logical_key": status.logical_key,
                    "recommended_action": (
                        "ignore_stale_fanout_terminal_and_wait_for_current_instance"
                    ),
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass
        return OrchestratorDecision(
            action="block",
            task_id=event.task_id,
            reason=f"{event.type} rejected: stale fanout instance",
        )

    def _on_completion_stale_rejected(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision:
        task = self.task_store.get(event.task_id) if event.task_id else None
        if task is None:
            return OrchestratorDecision(
                action="block",
                task_id=event.task_id,
                reason="stale completion rejected but task not found",
            )
        dispatched_role = self._dispatch_rework(task, event)
        if dispatched_role is None:
            return OrchestratorDecision(
                action="block",
                task_id=task.id,
                reason="stale completion rejected: rework unavailable",
            )
        return OrchestratorDecision(
            action="dispatch",
            task_id=task.id,
            role=dispatched_role,
            reason="stale completion rejected: revision reissue dispatched",
        )

    @staticmethod
    def _completion_revision_value(payload: dict, key: str) -> str:
        """Read task capsule revision from supported completion payload shapes.

        The canonical event contract keeps revision fields at the top level, but
        real provider outputs commonly group them under `revisions` or
        `task_doc`. Some role skills also report revisions as audit strings in
        `evidence_refs`, e.g. `source_revision:source-r1`. Keep the validator
        strict about values while accepting these equivalent wire shapes.
        """
        value = payload.get(key)
        if value:
            return str(value)
        for nested_key in ("revisions", "task_doc"):
            nested = payload.get(nested_key)
            if isinstance(nested, dict) and nested.get(key):
                return str(nested.get(key))
        evidence_refs = payload.get("evidence_refs")
        if isinstance(evidence_refs, list):
            for ref in evidence_refs:
                if not isinstance(ref, str):
                    continue
                raw = ref.strip()
                for sep in (":", "="):
                    prefix = f"{key}{sep}"
                    if raw.startswith(prefix):
                        return raw[len(prefix):].strip()
        return ""

    def _reject_stale_artifact_manifest(
        self,
        event: ZfEvent,
        task: Task,
    ) -> OrchestratorDecision | None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        nested = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else {}
        actor = (
            str(event.actor or "").strip()
            or str(payload.get("role") or "").strip()
            or str(nested.get("role") or "").strip()
        )
        if actor == "orchestrator":
            return None
        expected = str(getattr(task, "assigned_to", "") or "").strip()
        if actor and expected:
            try:
                actor_matches_assignee = self._assignee_equivalent(actor, expected)
            except Exception:
                actor_matches_assignee = actor == expected
            if not actor_matches_assignee:
                self._emit_lifecycle_rejected(
                    event,
                    reason="artifact_actor_not_assigned",
                    expected=expected,
                    actual=actor,
                )
                return OrchestratorDecision(
                    action="block",
                    task_id=event.task_id,
                    reason=(
                        "artifact.manifest.published rejected: "
                        f"actor {actor} not assigned"
                    ),
                )

        active_dispatch = str(getattr(task, "active_dispatch_id", "") or "")
        event_dispatch = str(payload.get("dispatch_id") or nested.get("dispatch_id") or "")
        if active_dispatch and event_dispatch and event_dispatch != active_dispatch:
            self._emit_lifecycle_rejected(
                event,
                reason="artifact_dispatch_id_mismatch",
                expected=active_dispatch,
                actual=event_dispatch,
            )
            return OrchestratorDecision(
                action="block",
                task_id=event.task_id,
                reason="artifact.manifest.published rejected: dispatch_id mismatch",
            )
        return None

    def _is_taskless_workflow_trigger(self, event: ZfEvent) -> bool:
        """Return true for workflow triggers that intentionally carry no task.

        Star fanout stages can be driven by control-plane events such as
        ``task_map.ready``. A supervisor role may declare that event in
        ``publishes`` for briefing validation, which also places it in the
        lifecycle event set. Treating it as a task completion event would block
        the fanout before housekeeping can start it.
        """
        try:
            stages = getattr(self.config.workflow, "stages", [])
        except Exception:
            return False
        for stage in stages:
            if getattr(stage, "trigger", "") != event.type:
                continue
            topology = str(getattr(stage, "topology", "") or "")
            if topology.startswith("fanout_"):
                return True
        return False

    @staticmethod
    def _is_taskless_candidate_terminal_event(event: ZfEvent) -> bool:
        """Allow kernel-minted candidate-level terminal events without task_id."""

        if event.type not in {
            "review.approved",
            "verify.passed",
            "test.passed",
            "judge.passed",
        }:
            return False
        if event.actor != "zf-cli":
            return False
        payload = event.payload if isinstance(event.payload, dict) else {}
        return bool(str(payload.get("pdd_id") or payload.get("feature_id") or "").strip())

    def _is_taskless_artifact_manifest(self, event: ZfEvent) -> bool:
        """Return true for workflow-level manifests that are not task claims."""
        if event.type != "artifact.manifest.published":
            return False
        payload = event.payload if isinstance(event.payload, dict) else {}
        return is_taskless_workflow_manifest_payload(payload)

    def _reject_readonly_gate_mutation(
        self,
        event: ZfEvent,
        task: Task,
    ) -> OrchestratorDecision | None:
        """Reject success claims from read-only gate roles that edited files."""
        if event.type not in _READONLY_GATE_SUCCESS_EVENTS:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        changed = payload.get("changed_files")
        if not isinstance(changed, list) or not changed:
            return None
        changed_files = [str(item) for item in changed if str(item).strip()]
        if not changed_files:
            return None

        reason = "readonly_gate_modified_files"
        self._emit_lifecycle_rejected(
            event,
            reason=reason,
            expected="changed_files=[] for read-only gate roles",
            actual=",".join(changed_files),
        )
        self._emit_terminal_rejected(
            event,
            task=task,
            reason=reason,
            expected="changed_files=[]",
            actual=",".join(changed_files),
        )
        try:
            self.event_writer.append(ZfEvent(
                type="gate.failed",
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    "gate": "readonly_gate_integrity",
                    "reason": reason,
                    "changed_files": changed_files,
                    "trigger_event": event.type,
                    "trigger_event_id": event.id,
                    "source": "gate_readonly_integrity",
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass
        return OrchestratorDecision(
            action="block",
            task_id=event.task_id,
            reason=(
                f"{event.type} rejected: read-only gate modified files "
                f"{', '.join(changed_files)}"
            ),
        )

    def _reject_evidence_contract_violation(
        self,
        event: ZfEvent,
        task: Task,
    ) -> OrchestratorDecision | None:
        if event.type not in {
            "arch.proposal.done",
            "design.critique.done",
            "dev.build.done",
            "review.approved",
            "test.passed",
            "judge.passed",
        }:
            return None
        violations = self._evidence_contract_ref_violations(event, task)
        if not violations:
            return None
        triage = ReworkTriageResult(
            classification="evidence_payload_gap",
            gate_rule="evidence_contract.ref_integrity",
            suspected_owner=event.actor or task.assigned_to or "",
            recommended_action="request_evidence_reissue",
            should_increment_retry=False,
            notes="; ".join(violations),
        )
        try:
            self.event_writer.append(ZfEvent(
                type="task.rework.triage.completed",
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    **triage.to_payload(event),
                    "missing": violations,
                    "source": "evidence_contract_hardening",
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass
        dispatched_role = self._dispatch_evidence_reissue(task, event, triage)
        if dispatched_role is None:
            return OrchestratorDecision(
                action="block",
                task_id=event.task_id,
                reason=f"{event.type} rejected: invalid evidence refs",
            )
        return OrchestratorDecision(
            action="dispatch",
            task_id=event.task_id,
            role=dispatched_role,
            reason=f"{event.type} rejected: evidence reissue",
        )

    def _evidence_contract_ref_violations(
        self,
        event: ZfEvent,
        task: Task,
    ) -> list[str]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        contract = getattr(task, "contract", None)
        evidence_contract = getattr(contract, "evidence_contract", {})
        if not isinstance(evidence_contract, dict) or not evidence_contract:
            return []

        violations: list[str] = []
        for key in ("artifact_refs", "evidence_refs"):
            refs = payload.get(key)
            if refs is None:
                continue
            must_be_relative = bool(
                evidence_contract.get(f"{key}_must_be_relative")
            )
            if not isinstance(refs, list):
                violations.append(f"{key} must be a list")
                continue
            for idx, raw in enumerate(refs):
                ref = self._coerce_evidence_ref(raw)
                if ref is None:
                    violations.append(
                        f"{key}[{idx}] must be a string ref or mapping with path"
                    )
                    continue
                if not ref:
                    continue
                if must_be_relative and self._evidence_ref_is_absolute(ref):
                    violations.append(
                        f"{key}[{idx}] must be relative, got {self._ref_excerpt(ref)}"
                    )
                if self._evidence_ref_uses_forbidden_prefix(
                    ref,
                    evidence_contract,
                ):
                    violations.append(
                        f"{key}[{idx}] uses forbidden ref prefix: "
                        f"{self._ref_excerpt(ref)}"
                    )
        return violations

    @staticmethod
    def _coerce_evidence_ref(raw: Any) -> str | None:
        if isinstance(raw, str):
            return raw.strip()
        if isinstance(raw, dict):
            for key in ("path", "ref", "artifact_ref", "evidence_ref"):
                ref = str(raw.get(key) or "").strip()
                if ref:
                    return ref
        return None

    @staticmethod
    def _evidence_ref_is_absolute(ref: str) -> bool:
        value = ref.replace("\\", "/")
        if value.startswith(("/", "~")):
            return True
        return len(value) >= 3 and value[1] == ":" and value[2] == "/"

    @classmethod
    def _evidence_ref_uses_forbidden_prefix(
        cls,
        ref: str,
        evidence_contract: dict,
    ) -> bool:
        prefixes = evidence_contract.get("forbidden_ref_prefixes")
        if not isinstance(prefixes, list):
            return False
        value = ref.replace("\\", "/").strip()
        for raw_prefix in prefixes:
            prefix = str(raw_prefix or "").replace("\\", "/").strip()
            if not prefix:
                continue
            if prefix == "/" and cls._evidence_ref_is_absolute(value):
                return True
            if value.startswith(prefix):
                return True
            if prefix == ".zf/" and "/.zf/" in value:
                return True
        return False

    @staticmethod
    def _ref_excerpt(ref: str) -> str:
        if len(ref) <= 160:
            return ref
        return f"{ref[:120]}...{ref[-32:]}"

    def _on_evidence_rework_requested(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        task = self.task_store.get(event.task_id)
        if task is None:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        reason = str(payload.get("reason") or "evidence rework requested")
        triage = ReworkTriageResult(
            classification="evidence_payload_gap",
            gate_rule=str(payload.get("gate_rule") or "evidence_contract"),
            suspected_owner=task.assigned_to or str(payload.get("role") or ""),
            recommended_action="request_evidence_reissue",
            should_increment_retry=False,
            notes=reason,
        )
        try:
            self.event_writer.append(ZfEvent(
                type="task.rework.triage.completed",
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    **triage.to_payload(event),
                    "source": "orchestrator.evidence_rework.requested",
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass
        dispatched_role = self._dispatch_evidence_reissue(task, event, triage)
        if dispatched_role is None:
            return OrchestratorDecision(
                action="block",
                task_id=event.task_id,
                reason="evidence rework requested but no reissue target found",
            )
        return OrchestratorDecision(
            action="dispatch",
            task_id=event.task_id,
            role=dispatched_role,
            reason="evidence rework requested → evidence reissue",
        )

    def _lifecycle_event_already_handed_off(self, event: ZfEvent) -> bool:
        """Return True when a replayed progress event already routed onward."""
        if not event.task_id or not event.id:
            return False
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        seen_origin = False
        for candidate in events:
            if candidate.id == event.id:
                seen_origin = True
                continue
            if not seen_origin or candidate.task_id != event.task_id:
                continue
            if candidate.type not in {"task.assigned", "task.dispatched"}:
                continue
            if candidate.causation_id == event.id:
                return True
            payload = candidate.payload if isinstance(candidate.payload, dict) else {}
            if payload.get("trigger_event") == event.type:
                return True
        return False

    def _terminal_ledger(self) -> TerminalLedger:
        ledger = getattr(self, "_terminal_ledger_projection", None)
        if ledger is None:
            ledger = TerminalLedger(self.state_dir)
            self._terminal_ledger_projection = ledger
        return ledger

    def _terminal_replay_record(
        self,
        event: ZfEvent,
        task: Task,
    ) -> dict[str, Any] | None:
        if event.type not in TERMINAL_SUCCESS_EVENTS or not event.task_id:
            return None
        dispatch_id = terminal_dispatch_id(event.payload)
        if not dispatch_id:
            dispatch_id = getattr(task, "active_dispatch_id", "")
        return self._terminal_ledger().accepted_record(
            task_id=event.task_id,
            dispatch_id=dispatch_id,
            event_type=event.type,
        )

    def _record_terminal_accepted(self, event: ZfEvent, task: Task) -> None:
        if event.type not in TERMINAL_SUCCESS_EVENTS or not event.task_id:
            return
        dispatch_id = terminal_dispatch_id(event.payload)
        if not dispatch_id:
            dispatch_id = getattr(task, "active_dispatch_id", "")
        record = self._terminal_ledger().record_accepted(
            task_id=event.task_id,
            dispatch_id=dispatch_id,
            event_type=event.type,
            event_id=event.id,
            actor=event.actor or "",
        )
        if record is None:
            return
        try:
            self.event_writer.append(ZfEvent(
                type="dispatch.terminal.recorded",
                actor="zf-cli",
                task_id=event.task_id,
                payload=record.to_payload(),
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass

    def _emit_terminal_replayed(
        self,
        event: ZfEvent,
        record: dict[str, Any],
    ) -> None:
        try:
            self.event_writer.append(ZfEvent(
                type="dispatch.terminal.replayed",
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    "origin_event": event.type,
                    "origin_event_id": event.id,
                    "dispatch_id": terminal_dispatch_id(event.payload),
                    "record": record,
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass

    def _emit_terminal_rejected(
        self,
        event: ZfEvent,
        *,
        task: Task,
        reason: str,
        expected: str = "",
        actual: str = "",
    ) -> None:
        if event.type not in TERMINAL_SUCCESS_EVENTS:
            return
        try:
            self.event_writer.append(ZfEvent(
                type="dispatch.terminal.rejected",
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    "reason": reason,
                    "origin_event": event.type,
                    "origin_event_id": event.id,
                    "expected_dispatch_id": expected,
                    "actual_dispatch_id": actual,
                    "task_active_dispatch_id": getattr(task, "active_dispatch_id", ""),
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass

    def _publishes_for_actor(self, actor: str) -> set[str] | None:
        """P0-1: return the set of event types the actor's role is
        configured to publish, or ``None`` if the actor is trusted
        (kernel-internal: ``zf-cli`` / ``orchestrator``) or is not
        resolvable to a declared role.

        ``None`` semantics: skip the publishes-authz check entirely.
        That covers (a) kernel-driven projection events (zf-cli emits
        gate.passed / static_gate.passed / runtime.action.rejected, etc.)
        and (b) actors that don't map to any RoleConfig (CLI invocations,
        external integrations) — those have separate trust paths.
        """
        if not actor:
            return None
        if actor in {"zf-cli", "orchestrator"}:
            return None
        for role in self.config.roles:
            if actor == role.name or actor == role.instance_id:
                pubs = set(role.publishes or [])
                # If a role declares no publishes at all, treat as
                # unconfigured (skip check) — defensive default for
                # legacy yaml.
                return pubs if pubs else None
        return None

    def _emit_lifecycle_rejected(
        self,
        event: ZfEvent,
        *,
        reason: str,
        event_type: str = "runtime.action.rejected",
        expected: str = "",
        actual: str = "",
    ) -> None:
        try:
            self.event_writer.append(ZfEvent(
                type=event_type,
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    "reason": reason,
                    "origin_event": event.type,
                    "origin_event_id": event.id,
                    "actor": event.actor or "",
                    "expected": expected,
                    "actual": actual,
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass

    def _latest_rework_request(self, task_id: str) -> ZfEvent | None:
        try:
            events = self.event_log.read_all()
        except Exception:
            return None
        for candidate in reversed(events):
            if candidate.type == "task.rework.requested" and candidate.task_id == task_id:
                return candidate
        return None

    def _payload_has_rework_delta(self, event: ZfEvent, task: Task) -> bool:
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key in (
            "files_touched",
            "files",
            "commits",
            "artifact_refs",
            "evidence_refs",
            "changed_files",
            "patches",
        ):
            value = payload.get(key)
            if isinstance(value, list) and value:
                return True
            if isinstance(value, str) and value.strip():
                return True
        evidence = task.evidence
        if evidence and (evidence.files_touched or evidence.commits):
            return True
        return False

    def _git_has_delta_since(
        self,
        base_git_head: str,
        *,
        base_diff_hash: str = "",
        base_files_touched: list[str] | None = None,
    ) -> bool:
        if not base_git_head:
            return False
        try:
            context = capture_git_diff_context(
                self.project_root,
                base_sha=base_git_head,
            )
        except Exception:
            return False
        if base_diff_hash:
            return bool(context.diff_hash and context.diff_hash != base_diff_hash)
        if base_files_touched is not None:
            before = {str(item).strip() for item in base_files_touched if str(item).strip()}
            after = {item for item in context.files_touched if item}
            return bool(after - before)
        return bool(context.files_touched)

    def _rework_delta_missing(
        self,
        event: ZfEvent,
        task: Task,
    ) -> tuple[bool, dict[str, Any]]:
        if not self._rework_delta_required() or task.retry_count <= 0:
            return False, {}
        rework = self._latest_rework_request(task.id)
        if rework is None:
            return False, {}
        payload = rework.payload if isinstance(rework.payload, dict) else {}
        required_actions = payload.get("required_actions")
        if not isinstance(required_actions, list):
            required_actions = []
        uncovered = self._uncovered_required_actions(event, required_actions)
        if uncovered:
            return True, {
                "rework_request_event_id": rework.id,
                "required_actions": required_actions,
                "uncovered_required_actions": uncovered,
                "trigger_event": event.type,
                "trigger_event_id": event.id,
                "reason": "required_actions_not_covered",
            }
        base_git_head = str(payload.get("base_git_head") or "")
        base_diff_hash = str(payload.get("base_diff_hash") or "")
        base_files_raw = payload.get("base_files_touched")
        base_files_touched = (
            [str(item) for item in base_files_raw]
            if isinstance(base_files_raw, list)
            else None
        )
        event_payload = event.payload if isinstance(event.payload, dict) else {}
        no_code_rationale = str(
            event_payload.get("no_code_rationale")
            or event_payload.get("no_code_change_reason")
            or ""
        ).strip()
        if no_code_rationale and not required_actions:
            return False, {
                "no_code_rationale": no_code_rationale,
                "rework_request_event_id": rework.id,
            }
        if self._payload_has_rework_delta(event, task):
            return False, {"source": "event_payload_or_task_evidence"}
        if self._git_has_delta_since(
            base_git_head,
            base_diff_hash=base_diff_hash,
            base_files_touched=base_files_touched,
        ):
            return False, {
                "source": "git_diff",
                "base_git_head": base_git_head,
                "base_diff_hash": base_diff_hash,
                "rework_request_event_id": rework.id,
            }
        return True, {
            "rework_request_event_id": rework.id,
            "required_actions": required_actions,
            "base_git_head": base_git_head,
            "base_diff_hash": base_diff_hash,
            "trigger_event": event.type,
            "trigger_event_id": event.id,
        }

    def _uncovered_required_actions(
        self,
        event: ZfEvent,
        required_actions: list[object],
    ) -> list[str]:
        actions = [str(item).strip() for item in required_actions if str(item).strip()]
        if not actions:
            return []
        payload = event.payload if isinstance(event.payload, dict) else {}
        evidence_parts: list[str] = []
        for key in (
            "required_actions_completed",
            "completed_required_actions",
            "fixed_required_actions",
            "summary",
            "verification",
            "evidence_refs",
            "artifact_refs",
            "files_touched",
            "commits",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                evidence_parts.extend(str(item) for item in value)
            elif isinstance(value, dict):
                evidence_parts.extend(str(item) for item in value.values())
            elif value:
                evidence_parts.append(str(value))
        evidence_text = "\n".join(evidence_parts).lower()
        if not evidence_text.strip():
            return actions
        uncovered: list[str] = []
        for action in actions:
            if not _action_covered(action, evidence_text):
                uncovered.append(action)
        return uncovered

    def _emit_rework_no_delta_failed(
        self,
        event: ZfEvent,
        evidence: dict[str, Any],
    ) -> None:
        try:
            self.event_writer.append(ZfEvent(
                type="discriminator.failed",
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    "failed_d": ["ReworkDeltaD"],
                    "details": [{
                        "d": "ReworkDeltaD",
                        "passed": False,
                        "reason": (
                            "rework success event has no code/test/doc/evidence delta"
                        ),
                        "evidence": evidence,
                    }],
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
            self.event_writer.append(ZfEvent(
                type="task.rework.blocked",
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    "reason": evidence.get("reason") or "rework_delta_missing",
                    "evidence": evidence,
                    "trigger_event": event.type,
                    "trigger_event_id": event.id,
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass

    def _on_review_approved(self, event: ZfEvent) -> OrchestratorDecision | None:
        graph_decision = self._workflow_graph_reconcile_bridge(event)
        if graph_decision is not None:
            return graph_decision
        task = self.task_store.get(event.task_id)
        if task and task.status == "review":
            self._move_task(event.task_id, "testing")
            return OrchestratorDecision(
                action="move", task_id=event.task_id,
                reason="review.approved → testing",
            )
        return None

    def _on_review_rejected(self, event: ZfEvent) -> OrchestratorDecision | None:
        graph_decision = self._workflow_graph_reconcile_bridge(event)
        if graph_decision is not None:
            return graph_decision
        task = self.task_store.get(event.task_id)
        if task and task.status in {"review", "in_progress"}:
            # G-WIRE-3: failure increments per-instance failure counter
            assignee = task.assigned_to or ""
            if assignee:
                self._failure_counter[assignee] = (
                    self._failure_counter.get(assignee, 0) + 1
                )
            # Move back to in_progress for rework
            self.task_store.update(event.task_id, status="in_progress")
            # Re-dispatch per P1-1 rework routing (contract.rework_to
            # → workflow.rework_routing → "dev" fallback).
            return self._route_rework_trigger(
                task,
                event,
                reason="review.rejected → rework",
            )
        return None

    def _done_evidence_required(self) -> bool:
        """Return whether strict terminal evidence should block done."""
        try:
            return bool(self.config.verification.contract.required)
        except Exception:
            return False

    def _terminal_done_required_prior_events(self, event: ZfEvent) -> list[str]:
        """Prior lifecycle events required before accepting terminal done.

        The requirement is topology-aware: if a configured role can publish
        review/test events, strict done evidence must link to the latest event
        for this task. `judge.passed` additionally requires test evidence when
        a test role exists.
        """
        published: set[str] = set()
        for role in self.config.roles:
            published.update(role.publishes or [])
        required: list[str] = []
        if "review.approved" in published:
            required.append("review.approved")
        if event.type == "judge.passed":
            if "verify.passed" in published:
                required.append("verify.passed")
            if "test.passed" in published:
                required.append("test.passed")
        return required

    def _terminal_done_requires_payload_evidence(self, event: ZfEvent) -> bool:
        """Require agent-supplied terminal evidence for judge pass events.

        `test.passed` is already backed by deterministic discriminator/gate
        evidence. A `judge.passed` assertion is a higher-level terminal claim,
        so strict mode requires it to carry at least summary/check evidence.
        """
        return self._done_evidence_required() and event.type == "judge.passed"

    def _configured_terminal_success_event(self) -> str:
        published = {
            event_type
            for role in self.config.roles
            for event_type in getattr(role, "publishes", [])
        }
        for event_type in ("judge.passed", "verify.passed", "test.passed", "review.approved"):
            if event_type in published:
                return event_type
        return "judge.passed"

    def _is_configured_terminal_success(self, event_type: str) -> bool:
        if event_type != self._configured_terminal_success_event():
            return False
        try:
            return not self._non_orchestrator_subscribers(event_type)
        except Exception:
            return event_type == "judge.passed"

    def _discriminator_details(self, report: Any) -> list[dict[str, Any]]:
        return [
            {
                "d": r.d_name,
                "passed": r.passed,
                "reason": r.reason,
                "evidence": r.evidence,
            }
            for r in report.d_results
        ]

    def _terminal_target_ref(self, event: ZfEvent, task: Task) -> str:
        payload = event.payload if isinstance(event.payload, dict) else {}
        candidate = str(
            payload.get("target_ref")
            or payload.get("task_ref")
            or payload.get("candidate_ref")
            or ""
        ).strip()
        if candidate:
            return candidate
        task_prefix = getattr(self.config.runtime.git, "task_ref_prefix", "task")
        return f"{task_prefix}/{task.id}"

    def _git_commit_for_ref(self, ref: str) -> str:
        if not ref:
            return ""
        candidates = [ref]
        if ref.startswith("refs/heads/"):
            candidates.append(ref[len("refs/heads/"):])
        else:
            candidates.append(f"refs/heads/{ref}")
        for candidate in dict.fromkeys(candidates):
            result = subprocess.run(
                ["git", "rev-parse", "--verify", f"{candidate}^{{commit}}"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        return ""

    def _workspace_head(self, workspace: Path) -> str:
        if not (workspace / ".git").exists():
            return ""
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def _existing_discriminator_workspace(
        self,
        *,
        event: ZfEvent,
        task: Task,
        target_commit: str,
    ) -> Path | None:
        candidates: list[str] = []
        for value in [
            event.actor or "",
            task.assigned_to or "",
            "judge",
            "test",
            "review",
        ]:
            if value and value not in candidates:
                candidates.append(value)
        for role in self.config.roles:
            if role.instance_id not in candidates:
                candidates.append(role.instance_id)

        for instance_id in candidates:
            workspace = self.state_dir / "workdirs" / instance_id / "project"
            if not workspace.exists():
                continue
            try:
                if self._workspace_head(workspace) == target_commit:
                    return workspace
            except Exception:
                continue
        return None

    def _prepare_temp_discriminator_workspace(
        self,
        *,
        task: Task,
        event: ZfEvent,
        target_commit: str,
    ) -> tuple[Path, Any]:
        root = self.state_dir / "tmp" / "discriminator-worktrees"
        root.mkdir(parents=True, exist_ok=True)
        suffix = (event.id or "event")[:12]
        workspace = root / f"{task.id}-{suffix}"
        if workspace.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(workspace)],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            shutil.rmtree(workspace, ignore_errors=True)
        result = subprocess.run(
            ["git", "worktree", "add", "--detach", str(workspace), target_commit],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"failed to prepare discriminator worktree: {detail}")

        def cleanup() -> None:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(workspace)],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            shutil.rmtree(workspace, ignore_errors=True)

        return workspace, cleanup

    def _terminal_discriminator_workspace(
        self,
        event: ZfEvent,
        task: Task,
    ) -> tuple[Path, Any, dict[str, str]]:
        target_ref = self._terminal_target_ref(event, task)
        target_commit = self._git_commit_for_ref(target_ref)
        if not target_commit:
            return self.project_root, (lambda: None), {
                "workspace": str(self.project_root),
                "target_ref": target_ref,
                "target_commit": "",
                "source": "project_root_no_target_ref",
            }

        existing = self._existing_discriminator_workspace(
            event=event,
            task=task,
            target_commit=target_commit,
        )
        if existing is not None:
            return existing, (lambda: None), {
                "workspace": str(existing),
                "target_ref": target_ref,
                "target_commit": target_commit,
                "source": "existing_role_workdir",
            }

        workspace, cleanup = self._prepare_temp_discriminator_workspace(
            task=task,
            event=event,
            target_commit=target_commit,
        )
        return workspace, cleanup, {
            "workspace": str(workspace),
            "target_ref": target_ref,
            "target_commit": target_commit,
            "source": "temporary_task_ref_worktree",
        }

    def _evaluate_terminal_done(self, event: ZfEvent, task: Task) -> bool:
        """Run done-time verification and emit a structured evidence bundle."""
        missing_delta, delta_evidence = self._rework_delta_missing(event, task)
        if missing_delta:
            self._emit_rework_no_delta_failed(event, delta_evidence)
            return False
        if self._terminal_done_evidence_already_recorded(event, task):
            return True
        def cleanup() -> None:
            return None

        workspace_info: dict[str, str] = {}
        try:
            workspace, cleanup, workspace_info = self._terminal_discriminator_workspace(
                event,
                task,
            )
            report = self._discriminator_runner.run(
                task,
                workspace,
                self.event_log,
            )
        except Exception as exc:
            try:
                self.event_writer.append(ZfEvent(
                    type="discriminator.failed",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={
                        "failed_d": ["DiscriminatorRunner"],
                        "details": [{
                            "d": "DiscriminatorRunner",
                            "passed": False,
                            "reason": str(exc),
                            "evidence": {
                                "type": type(exc).__name__,
                                **workspace_info,
                            },
                        }],
                        "workspace": workspace_info,
                    },
                ))
            except Exception:
                pass
            try:
                cleanup()
            except Exception:
                pass
            return False

        details = self._discriminator_details(report)
        if not report.passed:
            failed_d = [r.d_name for r in report.d_results if not r.passed]
            try:
                self.event_writer.append(ZfEvent(
                    type="discriminator.failed",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={
                        "failed_d": failed_d,
                        "details": details,
                        "workspace": workspace_info,
                    },
                ))
            except Exception:
                pass
            try:
                cleanup()
            except Exception:
                pass
            return False

        done_evidence = build_done_evidence_payload(
            task=task,
            trigger_event=event,
            event_log=self.event_log,
            discriminator_details=details,
        )
        missing: list[str] = []
        if self._done_evidence_required():
            missing = validate_terminal_done_evidence(
                done_evidence=done_evidence,
                require_payload_evidence=(
                    self._terminal_done_requires_payload_evidence(event)
                ),
                required_prior_events=(
                    self._terminal_done_required_prior_events(event)
                ),
            )
        if missing:
            try:
                self.event_writer.append(ZfEvent(
                    type="task.done.blocked",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={
                        "missing": missing,
                        "evidence": done_evidence,
                        "source": "terminal_done_hardening",
                        "trigger_event": event.type,
                        "trigger_event_id": event.id,
                    },
                ))
            except Exception:
                pass
            try:
                cleanup()
            except Exception:
                pass
            return False

        try:
            passed_event = self.event_writer.append(ZfEvent(
                type="discriminator.passed",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "all_d": [r.d_name for r in report.d_results],
                    "details": details,
                    "workspace": workspace_info,
                },
            ))
            done_evidence["discriminator_event_id"] = passed_event.id
        except Exception:
            pass

        try:
            self.event_writer.append(ZfEvent(
                type="task.done.evidence",
                actor="zf-cli",
                task_id=task.id,
                payload=done_evidence,
            ))
        except Exception:
            pass
        try:
            cleanup()
        except Exception:
            pass
        # B19(读取回执闭环):done 时刻比对 X15 manifest required 项与
        # 完成 payload.read_receipts;缺读发 gap(kind=read_receipt,
        # observe-first 不回滚)。best-effort。
        try:
            from zf.runtime.task_context_manifest import (
                read_receipt_gaps,
                read_task_context_manifest,
            )

            dispatch_id = str(
                getattr(task, "active_dispatch_id", "") or ""
            )
            briefing_dir = None
            base = self.state_dir / "briefings" / task.id
            if dispatch_id and (base / dispatch_id).exists():
                briefing_dir = base / dispatch_id
            elif base.exists():
                subdirs = sorted(
                    (p for p in base.iterdir() if p.is_dir()),
                    key=lambda p: p.stat().st_mtime,
                )
                briefing_dir = subdirs[-1] if subdirs else None
            if briefing_dir is not None:
                tcm = read_task_context_manifest(briefing_dir)
                if tcm:
                    receipt_gaps = read_receipt_gaps(tcm, event.payload)
                    if receipt_gaps:
                        profile = str(getattr(
                            self.config.workflow, "harness_profile",
                            "baseline",
                        ))
                        self.event_writer.append(ZfEvent(
                            type="task.context_manifest.gap",
                            actor="zf-cli",
                            task_id=task.id,
                            payload={
                                "kind": "read_receipt",
                                "missing": receipt_gaps[:10],
                                "profile": profile,
                                "mode": "observe_first",
                                "blocking": False,
                                "severity": (
                                    "STOP"
                                    if profile in ("strict", "release")
                                    else "WARN"
                                ),
                            },
                            causation_id=event.id,
                        ))
        except Exception:
            pass
        # X16(G-MEM-1 终局形):done 时刻铸 closeout learning 决策事件;
        # strict 缺失发 gap(observe-first 不回滚)。best-effort。
        try:
            from zf.runtime.closeout_learning import closeout_events_for_done

            for spec in closeout_events_for_done(
                task_id=task.id,
                terminal_event_id=event.id,
                payload=event.payload,
                harness_profile=str(getattr(
                    self.config.workflow, "harness_profile", "baseline",
                )),
            ):
                self.event_writer.append(ZfEvent(
                    type=spec["type"],
                    actor="zf-cli",
                    task_id=task.id,
                    payload=spec["payload"],
                    causation_id=event.id,
                ))
        except Exception:
            pass
        return True

    def _terminal_done_evidence_already_recorded(
        self,
        event: ZfEvent,
        task: Task,
    ) -> bool:
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        for existing in reversed(events):
            if existing.task_id != task.id or existing.type != "task.done.evidence":
                continue
            payload = existing.payload if isinstance(existing.payload, dict) else {}
            if str(payload.get("trigger_event_id") or "") == event.id:
                return True
        return False

    def _on_test_passed(self, event: ZfEvent) -> OrchestratorDecision | None:
        graph_decision = self._workflow_graph_reconcile_bridge(event)
        if graph_decision is not None:
            return graph_decision
        task = self.task_store.get(event.task_id)
        if task and (
            task.status == "testing"
            or (
                task.status == "in_progress"
                and self._is_configured_terminal_success(event.type)
            )
            or self._is_terminal_late_success(event, task)
        ):
            # GAP-1: capture git evidence before closing the task.
            since_sha = self._dispatch_heads.pop(event.task_id, "")
            commits: list[str] = []
            files_touched: list[str] = []
            if since_sha:
                project_root = self.project_root
                try:
                    commits = capture_commits_since(project_root, since_sha)
                    files_touched = capture_files_touched_since(project_root, since_sha)
                except Exception:
                    pass
            evidence = TaskEvidence(
                commits=commits,
                files_touched=files_touched,
            )
            self.task_store.update(event.task_id, evidence=evidence)
            task = self.task_store.get(event.task_id) or task
            if files_touched:
                try:
                    self.event_writer.append(ZfEvent(
                        type="task.files_touched",
                        actor="zf-cli",
                        task_id=event.task_id,
                        payload={
                            "files": files_touched,
                            "commits": commits,
                            "since_sha": since_sha,
                        },
                    ))
                except Exception:
                    pass
            if not self._evaluate_terminal_done(event, task):
                return OrchestratorDecision(
                    action="block",
                    task_id=event.task_id,
                    reason=f"{event.type} terminal evidence blocked",
                )
            # G-WIRE-3: success increments turn counter, resets failure counter
            assignee = task.assigned_to or ""
            if assignee:
                self._turn_counter[assignee] = self._turn_counter.get(assignee, 0) + 1
                self._failure_counter[assignee] = 0
            self._record_terminal_accepted(event, task)
            # Backlog 2026-05-14-1440: clear evidence reissue counter
            # once the terminal payload is finally accepted as done.
            try:
                self._clear_evidence_reissue(event.task_id)
            except AttributeError:
                pass
            self._move_task(event.task_id, "done", trigger_event=event.type)
            self._settle_task_chain_workers_idle(
                event.task_id,
                fallback_assignee=assignee,
                reason=f"task {event.task_id} done after {event.type}",
            )
            return OrchestratorDecision(
                action="move", task_id=event.task_id,
                reason=f"{event.type} → done",
            )
        return None

    def _on_test_failed(self, event: ZfEvent) -> OrchestratorDecision | None:
        graph_decision = self._workflow_graph_reconcile_bridge(event)
        if graph_decision is not None:
            return graph_decision
        task = self.task_store.get(event.task_id)
        if task and task.status in {"testing", "in_progress"}:
            # Move back to in_progress for fix
            self.task_store.update(event.task_id, status="in_progress")
            return self._route_rework_trigger(
                task,
                event,
                reason=f"{event.type} → rework",
            )
        return None

    def _on_verify_passed(self, event: ZfEvent) -> OrchestratorDecision | None:
        return self._on_test_passed(event)

    def _on_verify_failed(self, event: ZfEvent) -> OrchestratorDecision | None:
        return self._on_test_failed(event)

    def _on_judge_passed(self, event: ZfEvent) -> OrchestratorDecision | None:
        graph_decision = self._workflow_graph_reconcile_bridge(event)
        if graph_decision is not None:
            return graph_decision
        task = self.task_store.get(event.task_id)
        if task and (
            task.status in {"testing", "in_progress"}
            or self._is_terminal_late_success(event, task)
        ):
            if not self._evaluate_terminal_done(event, task):
                return OrchestratorDecision(
                    action="block",
                    task_id=event.task_id,
                    reason=f"{event.type} terminal evidence blocked",
                )
            self._record_terminal_accepted(event, task)
            # Backlog 2026-05-14-1440: clear evidence reissue counter
            # once the terminal payload is finally accepted as done.
            try:
                self._clear_evidence_reissue(event.task_id)
            except AttributeError:
                pass
            self._move_task(event.task_id, "done", trigger_event=event.type)
            self._settle_task_chain_workers_idle(
                event.task_id,
                fallback_assignee=task.assigned_to or "",
                reason=f"task {event.task_id} done after {event.type}",
            )
            # ZF-LH-SPEC-PROMOTE-001 integration (2026-05-18, I56):
            # decide whether to promote the verified behavior into
            # the canonical spec or skip (with reason). Emits one of
            # spec.promote.{completed,skipped} for audit. Best-effort.
            self._emit_spec_promote_decision(event, task)
            return OrchestratorDecision(
                action="move", task_id=event.task_id,
                reason="judge.passed → done",
            )
        return self._settle_candidate_tasks_done(event)

    def _settle_candidate_tasks_done(
        self, event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Move a passed candidate's canonical tasks to done.

        PRD/issue/refactor fanout emit a candidate/PDD-level ``judge.passed``
        with NO single ``task_id`` — it covers every canonical task that
        delivered into the passed candidate. The single-task path above can't
        resolve them, so the kanban cards stayed ``in_progress`` forever despite
        judge.passed (ledger PRD e2e 2026-06-20: delivery layer reached
        judge.passed but the projection layer never moved cards to done). Gate
        strictly on candidate-level shape (empty task_id + a pdd_id) so a
        per-task judge.passed never sweeps sibling tasks, then move every
        in-progress/testing task sharing the candidate's feature_id or the
        candidate/container task id itself.
        """
        if str(event.task_id or "").strip():
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        pdd_id = str(payload.get("pdd_id") or "").strip()
        feature_id = str(payload.get("feature_id") or "").strip()
        if not pdd_id or not feature_id:
            return None
        moved: list[str] = []
        for task in self.task_store.list_all():
            if task.status not in {"in_progress", "testing"}:
                continue
            task_feature = str(
                getattr(getattr(task, "contract", None), "feature_id", "") or ""
            ).strip()
            if not (
                task_feature == feature_id
                or task.id in {pdd_id, feature_id}
            ):
                continue
            if self._move_task(task.id, "done", trigger_event=event.type):
                moved.append(task.id)
                self._settle_task_chain_workers_idle(
                    task.id,
                    fallback_assignee=task.assigned_to or "",
                    reason=f"task {task.id} done after candidate {event.type}",
                )
        if not moved:
            return None
        return OrchestratorDecision(
            action="move",
            task_id=moved[0],
            reason=(
                f"{event.type} (candidate {pdd_id}/{feature_id}) → "
                f"{len(moved)} task(s) done"
            ),
        )

    def _emit_spec_promote_decision(self, event, task) -> None:
        """ZF-LH-SPEC-PROMOTE-001: emit spec.promote.{completed,skipped}
        after judge.passed. Defensive — never breaks the done path."""
        try:
            from zf.runtime.spec_promote import decide_promotion

            contract = getattr(task, "contract", None)
            spec_ref = ""
            if contract is not None:
                spec_ref = str(getattr(contract, "spec_ref", "") or "")
            decision = decide_promotion(
                task_id=task.id,
                spec_ref=spec_ref,
                has_acceptance_evidence=bool(
                    getattr(contract, "verification_tiers", []) or []
                ),
            )
            self.event_writer.append(ZfEvent(
                type=decision.event_type,
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "reason": decision.reason,
                    "spec_ref": decision.spec_ref,
                    "source_event_id": event.id,
                },
            ))
        except Exception:
            pass

    def _settle_task_chain_workers_idle(
        self,
        task_id: str,
        *,
        fallback_assignee: str = "",
        reason: str,
    ) -> None:
        """Clear stale worker states after terminal closure.

        A long-horizon task may pass through arch/critic/dev/review/test/judge.
        Once the task is mechanically closed, any worker whose only active
        assignment was that task should no longer remain busy in projections.
        """
        instances = {
            role.instance_id
            for role in getattr(self.config, "roles", [])
            if role.name != "orchestrator"
        }
        if not instances:
            return
        active_assignees: set[str] = set()
        try:
            for task in self.task_store.list_all():
                if task.id == task_id:
                    continue
                if task.status in {"backlog", "in_progress", "review", "testing"}:
                    if task.assigned_to:
                        active_assignees.add(task.assigned_to)
        except Exception:
            active_assignees = set()

        task_assignees: list[str] = []
        seen: set[str] = set()
        if fallback_assignee in instances:
            task_assignees.append(fallback_assignee)
            seen.add(fallback_assignee)
        try:
            for event in self.event_log.read_all():
                if event.task_id != task_id or event.type != "task.dispatched":
                    continue
                payload = event.payload if isinstance(event.payload, dict) else {}
                assignee = str(payload.get("assignee") or "")
                if assignee in instances and assignee not in seen:
                    task_assignees.append(assignee)
                    seen.add(assignee)
        except Exception:
            return

        for assignee in task_assignees:
            if assignee not in active_assignees:
                self._set_worker_state(assignee, "idle", reason=reason)

    def _on_judge_failed(self, event: ZfEvent) -> OrchestratorDecision | None:
        graph_decision = self._workflow_graph_reconcile_bridge(event)
        if graph_decision is not None:
            return graph_decision
        task = self.task_store.get(event.task_id)
        if task and task.status in {"testing", "in_progress"}:
            # Bypass state machine (testing→in_progress not in happy-path
            # table; matches _on_test_failed / _on_review_rejected pattern).
            self.task_store.update(event.task_id, status="in_progress")
            return self._route_rework_trigger(
                task,
                event,
                reason="judge.failed → rework",
            )
        return None

    def _on_discriminator_failed(
        self, event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Semantic rewrite: terminal claim failed → bounded rework.

        A discriminator failure means the task was not really done. Treat it
        as a kernel-owned liveness signal so a long-horizon feature cannot
        stop at "judge passed, but verification blocked".
        """
        task = self.task_store.get(event.task_id)
        if task is None or task.status in {"done", "cancelled", "blocked"}:
            return None
        return self._route_rework_trigger(
            task,
            event,
            reason="discriminator.failed → semantic rework",
        )

    def _on_task_done_blocked(
        self, event: ZfEvent,
    ) -> OrchestratorDecision | None:
        task = self.task_store.get(event.task_id)
        if task is None or task.status in {"done", "cancelled", "blocked"}:
            return None
        return self._route_rework_trigger(
            task,
            event,
            reason="task.done.blocked → evidence triage",
        )

    def _on_dev_blocked(self, event: ZfEvent) -> OrchestratorDecision | None:
        task = self.task_store.get(event.task_id)
        if task and task.status == "in_progress":
            self._move_task(event.task_id, "blocked")
            reason = event.payload.get("reason") if event.payload else None
            escalate_reason = (
                f"dev blocked on {event.task_id}: {reason}"
                if reason
                else f"dev blocked on {event.task_id} (no reason given)"
            )
            try:
                self.escalation.escalate(escalate_reason, task_id=event.task_id)
            except Exception:
                pass  # housekeeping never blocks the loop
            # B3: worker is now waiting for human steer
            if task.assigned_to:
                self._set_worker_state(
                    task.assigned_to, "blocked_human",
                    reason=f"dev.blocked on {event.task_id}: {reason or 'no reason'}",
                )
            return OrchestratorDecision(
                action="move", task_id=event.task_id,
                reason="dev.blocked → blocked + escalate",
            )
        return None

    def _on_suspended(self, event: ZfEvent) -> OrchestratorDecision | None:
        """LH-3.T4: handle review.suspended / test.suspended.

        Difference from rejected/failed: reviewer/tester can't make
        progress without more info (missing spec, broken env, external
        dep). Rework wouldn't help; we block the task + escalate to a
        human. Task stays assigned so context isn't lost when the
        blocker clears.
        """
        if not event.task_id:
            return None
        task = self.task_store.get(event.task_id)
        if task is None or task.status in ("done", "cancelled", "blocked"):
            return None
        reason = ""
        if isinstance(event.payload, dict):
            reason = (
                event.payload.get("reason")
                or event.payload.get("detail")
                or ""
            )
        self.task_store.update(
            event.task_id, status="blocked", blocked_reason=reason or event.type,
        )
        try:
            self.event_writer.append(ZfEvent(
                type="human.escalate",
                actor="zf-cli",
                task_id=event.task_id,
                payload={
                    "reason": f"{event.type}: {reason or 'no detail'}",
                    "origin_event": event.type,
                },
            ))
        except Exception:
            pass
        try:
            self.escalation.escalate(
                f"task {event.task_id} suspended ({event.type}): "
                f"{reason or 'no detail'}"
            )
        except Exception:
            pass
        return OrchestratorDecision(
            action="move", task_id=event.task_id,
            reason=f"{event.type} → blocked + escalate",
        )

    # -- codex.hook.* (1202-T3) --

    def _on_human_escalate(
        self, event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Observational: a ``human.escalate`` event was raised.

        Layer 1 takes no mechanical transition here — the task is already
        blocked (see ``_on_suspended``) or the emitter recorded the stall.
        ``human.escalate`` is in ``WAKE_PATTERNS``, so Layer 2 (the
        orchestrator agent) is woken with the escalation payload and owns
        the autonomous follow-up action via the routing table in skill
        ``zf-yoke-orchestrator-role-context`` (critic.gate.requested,
        task.contract.update, task.cancel, worker.respawn.requested, ...).
        Registering the handler keeps the handler-coverage invariant green.
        """
        return None

    def _on_codex_hook_session_start(
        self, event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Observational: codex session started. No state transition.

        Registered so the handler-coverage invariant stays green and so
        WAKE_PATTERNS can include it for future use.
        """
        return None

    def _on_codex_hook_user_prompt_submit(
        self, event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Observational: user prompt submitted to codex. No-op today."""
        return None

    def _on_codex_hook_pre_tool_use(
        self, event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """When the codex hook reports a denied tool call, surface it as
        `agent.api_blocked` so the circuit breaker + rate-limit paths
        treat it the same as Claude's SDK-level block.
        """
        payload = event.payload or {}
        if isinstance(payload, dict) and payload.get("permissionDecision") == "deny":
            try:
                self.event_writer.append(ZfEvent(
                    type="agent.api_blocked",
                    actor=event.actor,
                    payload={
                        "origin": "codex.hook.pre_tool_use",
                        "tool_name": payload.get("tool_name", ""),
                        "permission_mode": payload.get("permission_mode", ""),
                    },
                ))
            except Exception:
                pass
        return None

    def _on_codex_hook_post_tool_use(
        self, event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Observational: tool call completed. No-op today."""
        return None

    def _on_codex_hook_stop(
        self, event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Codex round complete plus stop-reason recovery when task-bound."""
        return self._recover_provider_stop(event)

    def _on_agent_api_blocked(
        self, event: ZfEvent,
    ) -> OrchestratorDecision | None:
        return self._recover_provider_stop(event)

    def _on_agent_timeout(
        self, event: ZfEvent,
    ) -> OrchestratorDecision | None:
        return self._recover_provider_stop(event)

    def _on_autoresearch_worker_stuck_inject(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Deterministically inject stuck recovery for autoresearch runs.

        The outer supervisor uses this audited event to exercise the same
        recovery path as the watchdog without depending on pane-output timing.
        """
        payload = event.payload if isinstance(event.payload, dict) else {}
        source = str(payload.get("source") or event.actor or "")
        if source not in {"autoresearch", "zf-autoresearch"}:
            return OrchestratorDecision(
                action="block",
                task_id=event.task_id,
                reason="autoresearch stuck injection rejected: invalid source",
            )

        instance_id = str(
            payload.get("instance_id")
            or payload.get("target_instance")
            or event.actor
            or ""
        )
        role = (
            self._find_role_by_instance(instance_id)
            or self._find_role_by_name(instance_id)
        )
        if role is None or role.name == "orchestrator":
            return OrchestratorDecision(
                action="block",
                task_id=event.task_id,
                role=instance_id,
                reason="autoresearch stuck injection rejected: unknown worker",
            )

        active_task = self._active_task_for_instance(role.instance_id)
        if active_task is None:
            return OrchestratorDecision(
                action="skip",
                task_id=event.task_id,
                role=role.instance_id,
                reason="autoresearch stuck injection skipped: worker has no active task",
            )
        if event.task_id and event.task_id != active_task.id:
            return OrchestratorDecision(
                action="block",
                task_id=event.task_id,
                role=role.instance_id,
                reason=(
                    "autoresearch stuck injection rejected: task does not "
                    f"match active task {active_task.id}"
                ),
            )

        return self._report_stuck_worker(role)

    def _recover_provider_stop(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        reason = str(payload.get("provider_stop_reason") or "").strip()
        if not reason:
            reason = classify_provider_stop(payload, status=event.type)
        if reason == "completed_with_terminal_event":
            return None

        task_id = event.task_id or str(payload.get("task_id") or "")
        if not task_id:
            return None
        task = self.task_store.get(task_id)
        if task is None or task.status in {"done", "cancelled"}:
            return None

        action = self._provider_stop_action(reason)
        role = None
        assignee = task.assigned_to or event.actor or ""
        if assignee:
            role = (
                self._find_role_by_instance(assignee)
                or self._find_role_by_name(assignee)
            )
        if reason == "completed_without_terminal_event" and role is not None:
            manifest_recovery = (
                self._request_manifest_terminal_completion_if_pending(
                    role=role,
                    task=task,
                    reason="provider_stop_completed_without_terminal_event",
                    inject_prompt=True,
                    causation_id=event.id,
                )
            )
            if manifest_recovery is not None:
                return manifest_recovery
            green_recovery = (
                self._request_green_terminal_completion_if_pending(
                    role=role,
                    task=task,
                    reason="provider_stop_after_green_verification",
                    inject_prompt=True,
                    causation_id=event.id,
                )
            )
            if green_recovery is not None:
                return green_recovery

        if action == "suspend":
            self.task_store.update(
                task_id,
                status="blocked",
                blocked_reason=f"provider_stop:{reason}",
            )
            self._emit_provider_stop_recovery(
                event,
                task=task,
                reason=reason,
                action=action,
                role=role,
            )
            try:
                self.event_writer.append(ZfEvent(
                    type="human.escalate",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "reason": f"provider stop requires operator action: {reason}",
                        "origin_event": event.type,
                    },
                    causation_id=event.id,
                    correlation_id=event.correlation_id,
                ))
            except Exception:
                pass
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                role=assignee,
                reason=f"provider stop {reason} → blocked",
            )

        if action == "cooldown":
            self._layer2_blocked_until = (
                self._now() + getattr(self, "_layer2_cooldown_s", 60.0)
            )
            from datetime import datetime, timezone
            cooldown_until = datetime.fromtimestamp(
                self._layer2_blocked_until,
                timezone.utc,
            ).isoformat()
            self._emit_provider_stop_recovery(
                event,
                task=task,
                reason=reason,
                action=action,
                role=role,
                cooldown_until=cooldown_until,
            )
            return OrchestratorDecision(
                action="skip",
                task_id=task_id,
                role=assignee,
                reason=f"provider stop {reason} → cooldown",
            )

        if action == "recycle":
            if role is not None:
                try:
                    self._set_worker_state(
                        role.instance_id,
                        "pending_recycle",
                        reason=f"provider stop {reason}",
                    )
                except Exception:
                    pass
            self.task_store.update(task_id, status="backlog", assigned_to=assignee)
            self._emit_provider_stop_recovery(
                event,
                task=task,
                reason=reason,
                action=action,
                role=role,
            )
            return OrchestratorDecision(
                action="dispatch",
                task_id=task_id,
                role=assignee,
                reason=f"provider stop {reason} → recycle/requeue",
            )

        if action == "requeue":
            self.task_store.update(task_id, status="backlog", assigned_to=assignee)
            self._emit_provider_stop_recovery(
                event,
                task=task,
                reason=reason,
                action=action,
                role=role,
            )
            return OrchestratorDecision(
                action="dispatch",
                task_id=task_id,
                role=assignee,
                reason=f"provider stop {reason} → requeue",
            )
        return None

    @staticmethod
    def _provider_stop_action(reason: str) -> str:
        if reason in {"auth_error", "hook_review_required", "tool_permission_blocked"}:
            return "suspend"
        if reason == "rate_limited":
            return "cooldown"
        if reason == "context_limit":
            return "recycle"
        if reason in {
            "completed_without_terminal_event",
            "pending_todos",
            "timeout",
            "transport_error",
        }:
            return "requeue"
        return "observe"

    def _request_green_terminal_completion_if_pending(
        self,
        *,
        role: RoleConfig,
        task: Task,
        reason: str,
        inject_prompt: bool,
        causation_id: str | None = None,
    ) -> OrchestratorDecision | None:
        """Ask a worker to finish the terminal protocol after green checks.

        Real Codex can stop after a passing pytest/verification command without
        committing and emitting the terminal event. When we can prove a recent
        verification command passed in the active dispatch, recovering by
        targeted prompt is safer than requeueing the whole task.
        """
        dispatch_id = getattr(task, "active_dispatch_id", "") or ""
        expected_event = self._expected_terminal_event_for_role(role)
        if not dispatch_id or not expected_event:
            return None
        green_event = self._latest_green_provider_tool_event(
            task_id=task.id,
            dispatch_id=dispatch_id,
            role=role,
            expected_event=expected_event,
        )
        if green_event is None:
            return None

        registry = getattr(self, "_green_completion_requests", None)
        if registry is None:
            registry = set()
            self._green_completion_requests = registry
        key = (role.instance_id, task.id, dispatch_id, green_event.id)
        first_request = key not in registry
        registry.add(key)

        self._set_worker_state(
            role.instance_id,
            "completion_pending",
            reason=(
                f"green verification already recorded for task {task.id}; "
                f"waiting for {expected_event}"
            ),
        )
        prompt_path = ""
        prompt_error = ""
        prompt_injected = False
        if first_request and inject_prompt:
            try:
                prompt_path = str(self._inject_green_terminal_completion_prompt(
                    role=role,
                    task=task,
                    dispatch_id=dispatch_id,
                    green_event=green_event,
                    expected_event=expected_event,
                ))
                prompt_injected = True
            except Exception as exc:
                prompt_error = str(exc)
        if first_request:
            try:
                self.event_writer.append(ZfEvent(
                    type="worker.stuck.recovered",
                    actor=role.instance_id,
                    task_id=task.id,
                    causation_id=causation_id or green_event.id,
                    correlation_id=green_event.correlation_id,
                    payload={
                        "role": role.name,
                        "instance_id": role.instance_id,
                        "task_id": task.id,
                        "dispatch_id": dispatch_id,
                        "recovery_action": (
                            "terminal_completion_requested_after_green_verification"
                        ),
                        "reason": reason,
                        "progress_event": green_event.type,
                        "progress_event_id": green_event.id,
                        "expected_event": expected_event,
                        "prompt_path": prompt_path,
                        "prompt_injected": prompt_injected,
                        "prompt_error": prompt_error,
                    },
                ))
            except Exception:
                pass
        return OrchestratorDecision(
            action="recover",
            task_id=task.id,
            role=role.instance_id,
            reason=(
                "green verification already recorded; "
                f"requested {expected_event}"
            ),
        )

    def _latest_green_provider_tool_event(
        self,
        *,
        task_id: str,
        dispatch_id: str,
        role: RoleConfig,
        expected_event: str,
    ) -> ZfEvent | None:
        try:
            events = self.event_log.read_all()
        except Exception:
            return None
        dispatch_idx = -1
        latest_green: ZfEvent | None = None
        actor_ids = {role.instance_id, role.name}
        for idx, event in enumerate(events):
            if event.task_id != task_id:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type == "task.dispatched":
                candidate_dispatch_id = str(payload.get("dispatch_id") or "")
                if not dispatch_id or candidate_dispatch_id == dispatch_id:
                    dispatch_idx = idx
                    latest_green = None
                continue
            if dispatch_idx < 0 or idx <= dispatch_idx:
                continue
            if event.type == expected_event and self._event_dispatch_matches(
                payload,
                dispatch_id,
            ):
                return None
            if event.actor not in actor_ids:
                continue
            if event.type != "codex.hook.post_tool_use":
                continue
            if self._provider_tool_response_looks_green(payload):
                latest_green = event
        return latest_green

    @staticmethod
    def _provider_tool_response_looks_green(payload: dict[str, Any]) -> bool:
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        command = str(tool_input.get("command") or "").lower()
        if not any(token in command for token in (
            "pytest",
            "unittest",
            "npm test",
            "pnpm test",
            "yarn test",
            "zf guard ownership",
        )):
            return False
        response = str(payload.get("tool_response") or "").lower()
        return (
            "process exited with code 0" in response
            or "exit_code\": 0" in response
            or "exit code 0" in response
            or " passed" in response
            or "passed in" in response
            or "task " in response and " is owned by " in response
        )

    def _inject_green_terminal_completion_prompt(
        self,
        *,
        role: RoleConfig,
        task: Task,
        dispatch_id: str,
        green_event: ZfEvent,
        expected_event: str,
    ) -> Path:
        from zf.runtime.injection import build_task_prompt

        briefing_dir = self.state_dir / "briefings"
        briefing_dir.mkdir(parents=True, exist_ok=True)
        path = (
            briefing_dir
            / f"{role.instance_id}-{task.id}-green-terminal-completion.md"
        )
        payload = green_event.payload if isinstance(green_event.payload, dict) else {}
        tool_input = payload.get("tool_input") if isinstance(payload, dict) else {}
        command = ""
        if isinstance(tool_input, dict):
            command = str(tool_input.get("command") or "")
        lines = [
            f"Active task: {task.id}",
            "",
            "# Terminal Completion Recovery",
            "",
            (
                "A recent verification command passed, but the provider stopped "
                f"before emitting `{expected_event}`. Do not restart broad "
                "implementation work unless the ownership guard or verification "
                "now fails."
            ),
            "",
            "## Required action",
            "1. Run `git status --short --untracked-files=all`.",
            (
                "2. If task-scoped files are uncommitted, commit them on the "
                "current worker branch and capture the commit sha."
            ),
            (
                f"3. Run `{zf_cli_cmd()} guard ownership --task {task.id} "
                f"--actor {role.instance_id}`."
            ),
            (
                f"4. Emit `{expected_event}` with dispatch id `{dispatch_id}` "
                "and the required completion payload."
            ),
            (
                "5. If the green check no longer passes, emit the configured "
                "failure or suspend event with evidence instead of final prose."
            ),
            "",
            "## Evidence already observed",
            f"- green_event_id: `{green_event.id}`",
            f"- command: `{command or '-'}`",
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        prompt = build_task_prompt(role.instance_id, path)
        context = self._dispatch_context(
            role=role,
            briefing_path=path,
            task_id=task.id,
        )
        self._send_transport_task(role.instance_id, path, prompt, context)
        return path

    def _emit_provider_stop_recovery(
        self,
        event: ZfEvent,
        *,
        task: Task,
        reason: str,
        action: str,
        role: RoleConfig | None,
        cooldown_until: str = "",
    ) -> None:
        try:
            dispatch_id = getattr(task, "active_dispatch_id", "") or ""
            recovery_payload = {
                "reason": reason,
                "action": action,
                "origin_event": event.type,
                "origin_event_id": event.id,
                "assigned_to": task.assigned_to or "",
                "role": role.name if role is not None else "",
                "instance_id": role.instance_id if role is not None else "",
                "backend": role.backend if role is not None else "",
                "dispatch_id": dispatch_id,
                "cooldown_until": cooldown_until,
            }
            self.event_writer.append(ZfEvent(
                type="provider.stop.recovery",
                actor="zf-cli",
                task_id=task.id,
                payload=recovery_payload,
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
            status = (
                "blocked" if action == "suspend"
                else "cooldown" if action == "cooldown"
                else "degraded"
            )
            self.event_writer.append(ZfEvent(
                type="provider.health.changed",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    **recovery_payload,
                    "status": status,
                    "requires_operator": action == "suspend",
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
            if action == "cooldown":
                self.event_writer.append(ZfEvent(
                    type="provider.cooldown.started",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={
                        **recovery_payload,
                        "status": "cooldown",
                        "cooldown_seconds": getattr(self, "_layer2_cooldown_s", 60.0),
                    },
                    causation_id=event.id,
                    correlation_id=event.correlation_id,
                ))
        except Exception:
            pass

    def _on_dispatch_silent_stall(
        self, event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """K2:task.assigned 无后续 dispatched 的停摆信号 → 重驱派发。

        幂等:_dispatch_ready 只派 ready/assigned 未派发的任务;
        emit 侧 5min cooldown 已防重复唤醒风暴。
        """
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = str(event.task_id or payload.get("task_id") or "")
        task = self.task_store.get(task_id) if task_id else None
        if task is not None and task.status in ("done", "cancelled", "blocked"):
            return None
        self._dispatch_ready()
        return OrchestratorDecision(
            action="redispatch",
            task_id=task_id,
            target_role=str(payload.get("assignee") or ""),
            reason="dispatch.silent_stall → re-drive dispatch sweep",
        )

    def _on_gate_failed(self, event: ZfEvent) -> OrchestratorDecision | None:
        task = self.task_store.get(event.task_id)
        if task is None or task.status in ("done", "cancelled", "in_progress"):
            return None
        # ω-1.b (2026-05-18): verdict-aware routing. critic / judge / gate
        # can encode "SUSPEND" in payload.verdict to escalate without
        # burning rework cap. Audit doc 37 Class B1 + r-next-10
        # evt-c1f8bedc5b5b (critic v3 SUSPEND ignored by kernel → arch v4
        # waste). Reuses existing _on_suspended (LH-3 Tri-State) handler
        # so escalation logic stays single-sourced.
        payload = event.payload if isinstance(event.payload, dict) else {}
        verdict = str(payload.get("verdict") or "").strip().upper()
        if verdict == "SUSPEND":
            return self._on_suspended(event)

        # Any non-terminal, non-in-progress state bounces back to
        # in_progress for rework. Bypasses state machine (same as
        # test.failed). Target role resolved per P1-1 rework routing.
        self.task_store.update(event.task_id, status="in_progress")
        return self._route_rework_trigger(
            task,
            event,
            reason=f"gate.failed → rework (from {task.status})",
        )

    def _on_phase_progressed(self, event: ZfEvent) -> OrchestratorDecision | None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = event.task_id or str(payload.get("task_id") or "")
        phase = str(payload.get("phase") or "").strip()
        if not task_id or not phase:
            return None
        try:
            from zf.runtime.progress_projection import phase_regression

            regressed, current = phase_regression(
                self.event_log.read_all(),
                task_id=task_id,
                attempted_phase=phase,
                source_event_id=event.id,
            )
        except Exception:
            return None
        if regressed:
            self.event_writer.append(ZfEvent(
                type="phase.regression.ignored",
                actor="zf-cli",
                task_id=task_id,
                payload={
                    "dispatch_id": str(payload.get("dispatch_id") or ""),
                    "role": str(payload.get("role") or ""),
                    "instance_id": str(payload.get("instance_id") or event.actor or ""),
                    "from_phase": current,
                    "attempted_phase": phase,
                    "source_event_id": event.id,
                    "reason": "phase regression ignored by projection",
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
            return OrchestratorDecision(
                action="observe",
                task_id=task_id,
                reason=f"phase regression ignored: {current} -> {phase}",
            )
        return OrchestratorDecision(
            action="observe",
            task_id=task_id,
            reason=f"phase progressed: {phase}",
        )

    def _on_task_fanout_requested(self, event: ZfEvent) -> OrchestratorDecision | None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = event.task_id or str(payload.get("task_id") or "")
        task = self.task_store.get(task_id) if task_id else None
        if task is None:
            self._emit_task_fanout_rejected(event, "task missing")
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                reason="fanout request rejected: task missing",
            )
        expected_output = str(payload.get("expected_output") or "").strip()
        if not expected_output:
            self._emit_task_fanout_rejected(event, "expected_output missing")
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                reason="fanout request rejected: expected_output missing",
            )
        active_fanout_id = self._active_fanout_for_task(task_id)
        if active_fanout_id:
            self._emit_task_fanout_rejected(
                event,
                "nested fanout denied",
                expected="no active fanout for task",
                actual=active_fanout_id,
            )
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                reason="fanout request rejected: active fanout already exists",
            )
        if _payload_requests_writer_capability(payload):
            self._emit_task_fanout_rejected(event, "reader fanout cannot request write capability")
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                reason="fanout request rejected: write capability requested",
            )
        expected_dispatch = getattr(task, "active_dispatch_id", "") or ""
        actual_dispatch = str(payload.get("dispatch_id") or "")
        if expected_dispatch and actual_dispatch != expected_dispatch:
            self._emit_task_fanout_rejected(
                event,
                "dispatch_id mismatch",
                expected=expected_dispatch,
                actual=actual_dispatch,
            )
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                reason="fanout request rejected: stale dispatch",
            )
        scope = _string_list(payload.get("scope"))
        specialists = _string_list(payload.get("requested_specialists")) or ["review"]
        if len(specialists) > 6:
            self._emit_task_fanout_rejected(event, "capacity exceeded")
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                reason="fanout request rejected: capacity exceeded",
            )
        exclusive = set(getattr(task.contract, "exclusive_files", []) or [])
        if exclusive and set(scope) & exclusive and not getattr(task.contract, "fanout_force", False):
            self.event_writer.append(ZfEvent(
                type="fanout.serialize",
                actor="zf-cli",
                task_id=task_id,
                payload={
                    "source": "task.fanout.requested",
                    "source_event_id": event.id,
                    "task_id": task_id,
                    "dispatch_id": actual_dispatch,
                    "overlap": sorted(set(scope) & exclusive),
                    "reason": "requested fanout overlaps exclusive files",
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
            return OrchestratorDecision(
                action="serialize",
                task_id=task_id,
                reason="fanout request serialized due to exclusive file overlap",
            )
        target_ref = str(payload.get("target_ref") or "")
        from zf.runtime.fanout import FanoutContext

        context = FanoutContext.create(
            stage_id=f"task-fanout-{task_id}",
            topology="kernel_mediated",
            trace_id=event.correlation_id or event.id,
            trigger_event_id=event.id,
            target_ref=target_ref,
            role_instances=specialists,
        )
        request_payload = {
            "fanout_id": context.fanout_id,
            "task_id": task_id,
            "dispatch_id": actual_dispatch,
            "requested_by": str(payload.get("requested_by") or event.actor or ""),
            "reason": str(payload.get("reason") or ""),
            "target_ref": target_ref,
            "scope": scope,
            "requested_specialists": specialists,
            "expected_output": expected_output,
            "risk": str(payload.get("risk") or ""),
            "source_event_id": event.id,
            "source_intent_event_id": str(payload.get("source_intent_event_id") or ""),
            "stage_id": context.stage_id,
            "topology": context.topology,
            "trace_id": context.trace_id,
            "trigger_event_id": event.id,
            "source_refs": dict(payload.get("source_refs") or {})
            if isinstance(payload.get("source_refs"), dict)
            else {},
            "workflow_run_id": str(payload.get("workflow_run_id") or ""),
            "workflow_input_manifest_ref": str(payload.get("workflow_input_manifest_ref") or ""),
            "workflow_prompt_ref": str(payload.get("workflow_prompt_ref") or ""),
            "prompt_kind": str(payload.get("prompt_kind") or ""),
            "artifact_refs": payload.get("artifact_refs")
            if isinstance(payload.get("artifact_refs"), list)
            else [],
        }
        self.event_writer.append(ZfEvent(
            type="fanout.requested",
            actor="zf-cli",
            task_id=task_id,
            payload=request_payload,
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        started = context.started_event(actor="zf-cli")
        started.task_id = task_id
        started.payload["source_event_id"] = event.id
        started.payload["dispatch_id"] = actual_dispatch
        self.event_writer.append(started)
        for child in context.expected_children:
            child_event = context.child_dispatched_event(
                child,
                run_id=f"{context.fanout_id}:{child.child_id}",
                actor="zf-cli",
            )
            child_event.task_id = task_id
            child_event.payload["source_event_id"] = event.id
            child_event.payload["dispatch_id"] = actual_dispatch
            child_event.payload["scope"] = scope
            child_event.payload["expected_output"] = expected_output
            child_event.payload["risk"] = str(payload.get("risk") or "")
            child_event.payload["source_refs"] = (
                dict(payload.get("source_refs") or {})
                if isinstance(payload.get("source_refs"), dict)
                else {}
            )
            child_event.payload["workflow_run_id"] = str(payload.get("workflow_run_id") or "")
            child_event.payload["workflow_input_manifest_ref"] = str(
                payload.get("workflow_input_manifest_ref") or ""
            )
            child_event.payload["workflow_prompt_ref"] = str(payload.get("workflow_prompt_ref") or "")
            child_event.payload["prompt_kind"] = str(payload.get("prompt_kind") or "")
            child_event.payload["artifact_refs"] = (
                payload.get("artifact_refs")
                if isinstance(payload.get("artifact_refs"), list)
                else []
            )
            self.event_writer.append(child_event)
        return OrchestratorDecision(
            action="fanout",
            task_id=task_id,
            reason=f"fanout request accepted: {context.fanout_id}",
        )

    def _on_workflow_invoke_requested(self, event: ZfEvent) -> OrchestratorDecision | None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = event.task_id or str(payload.get("task_id") or "")
        pattern_id = str(payload.get("pattern_id") or payload.get("stage_id") or "")
        task = self.task_store.get(task_id) if task_id else None
        if task is None:
            self._emit_workflow_invoke_rejected(event, "task missing", task_id=task_id, pattern_id=pattern_id)
            return OrchestratorDecision(action="block", task_id=task_id, reason="workflow invoke rejected: task missing")
        if _string_list(payload.get("open_questions")):
            self._emit_workflow_invoke_rejected(
                event,
                "blocking open questions",
                task_id=task_id,
                pattern_id=pattern_id,
            )
            return OrchestratorDecision(action="block", task_id=task_id, reason="workflow invoke rejected: open questions")
        stage = self._workflow_stage_by_id(pattern_id)
        if stage is None:
            self._emit_workflow_invoke_rejected(event, "pattern not declared", task_id=task_id, pattern_id=pattern_id)
            return OrchestratorDecision(action="block", task_id=task_id, reason="workflow invoke rejected: unknown pattern")
        topology = str(getattr(stage, "topology", "") or "")
        if not topology.startswith("fanout_"):
            self._emit_workflow_invoke_rejected(event, "pattern is not a fanout topology", task_id=task_id, pattern_id=pattern_id)
            return OrchestratorDecision(action="block", task_id=task_id, reason="workflow invoke rejected: unsupported topology")
        dispatch_id = str(payload.get("dispatch_id") or getattr(task, "active_dispatch_id", "") or "")
        active_dispatch = getattr(task, "active_dispatch_id", "") or ""
        if active_dispatch and dispatch_id != active_dispatch:
            self._emit_workflow_invoke_rejected(event, "dispatch_id mismatch", task_id=task_id, pattern_id=pattern_id)
            return OrchestratorDecision(action="block", task_id=task_id, reason="workflow invoke rejected: stale dispatch")
        # doc 64 §6 — WorkstreamScopeGuard: refuse the invocation when the
        # declared paths overlap an in-flight task's exclusive_files claim.
        proposed_paths = _string_list(payload.get("paths")) + _string_list(payload.get("scope"))
        scope_check = check_workstream_scope(
            self.state_dir,
            proposed_paths,
            proposed_task_id=task_id,
        )
        if not scope_check.allowed:
            self._emit_workflow_invoke_rejected(
                event,
                f"workstream_scope_overlap: {scope_check.reason}",
                task_id=task_id,
                pattern_id=pattern_id,
            )
            channel_id = str(payload.get("channel_id") or "")
            if channel_id:
                self.event_writer.append(ZfEvent(
                    type="channel.workflow.rejected",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "channel_id": channel_id,
                        "thread_id": str(payload.get("thread_id") or ""),
                        "task_id": task_id,
                        "pattern_id": pattern_id,
                        "reason": "workstream_scope_overlap",
                        "overlaps": [
                            {"task_id": o.task_id, "paths": list(o.paths)}
                            for o in scope_check.overlaps
                        ],
                        "source_event_id": event.id,
                    },
                    causation_id=event.id,
                    correlation_id=event.correlation_id,
                ))
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                reason="workflow invoke rejected: workstream scope overlap",
            )
        accepted_event = ZfEvent(
            type="workflow.invoke.accepted",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "task_id": task_id,
                "pattern_id": pattern_id,
                "channel_id": str(payload.get("channel_id") or ""),
                "thread_id": str(payload.get("thread_id") or ""),
                "source_event_id": event.id,
                "topology": topology,
                "source_refs": dict(payload.get("source_refs") or {})
                if isinstance(payload.get("source_refs"), dict)
                else {},
                "workflow_run_id": str(payload.get("workflow_run_id") or ""),
                "workflow_input_manifest_ref": str(payload.get("workflow_input_manifest_ref") or ""),
                "workflow_prompt_ref": str(payload.get("workflow_prompt_ref") or ""),
                "prompt_kind": str(payload.get("prompt_kind") or ""),
                "artifact_refs": payload.get("artifact_refs")
                if isinstance(payload.get("artifact_refs"), list)
                else [],
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        )
        roles = list(getattr(stage, "roles", []) or [])
        fanout_request = ZfEvent(
            type="task.fanout.requested",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "task_id": task_id,
                "dispatch_id": dispatch_id,
                "requested_by": str(payload.get("requested_by") or event.actor or "channel"),
                "reason": str(payload.get("reason") or "workflow invoke accepted"),
                "scope": _string_list(payload.get("scope")),
                "requested_specialists": _string_list(payload.get("requested_specialists")) or [str(role) for role in roles],
                "expected_output": str(payload.get("expected_output") or f"run execution pattern {pattern_id}"),
                "risk": str(payload.get("risk") or ""),
                "target_ref": str(payload.get("target_ref") or getattr(stage, "target_ref", "") or ""),
                "source_event_id": event.id,
                "source_intent_event_id": accepted_event.id,
                "pattern_id": pattern_id,
                "channel_id": str(payload.get("channel_id") or ""),
                "thread_id": str(payload.get("thread_id") or ""),
                "source_refs": dict(payload.get("source_refs") or {})
                if isinstance(payload.get("source_refs"), dict)
                else {},
                "workflow_run_id": str(payload.get("workflow_run_id") or ""),
                "workflow_input_manifest_ref": str(payload.get("workflow_input_manifest_ref") or ""),
                "workflow_prompt_ref": str(payload.get("workflow_prompt_ref") or ""),
                "prompt_kind": str(payload.get("prompt_kind") or ""),
                "artifact_refs": payload.get("artifact_refs")
                if isinstance(payload.get("artifact_refs"), list)
                else [],
            },
            causation_id=accepted_event.id,
            correlation_id=event.correlation_id,
        )
        accepted_event.payload["fanout_request_event_id"] = fanout_request.id
        self.event_writer.append(accepted_event)
        self.event_writer.append(fanout_request)
        return OrchestratorDecision(
            action="workflow_invoke",
            task_id=task_id,
            reason=f"workflow invoke accepted: {pattern_id}",
        )

    def _workflow_stage_by_id(self, stage_id: str):
        for stage in getattr(self.config.workflow, "stages", []) or []:
            if str(getattr(stage, "id", "") or "") == stage_id:
                return stage
        return None

    def _active_fanout_for_task(self, task_id: str) -> str:
        root = self.state_dir / "fanouts"
        if not root.exists():
            return ""
        for manifest_path in root.glob("*/manifest.json"):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            fanout_id = str(data.get("fanout_id") or manifest_path.parent.name)
            stale_reason, _superseded_by = self._fanout_identity_stale_reason(
                fanout_id,
            )
            if stale_reason:
                continue
            status = str(data.get("status") or "")
            aggregate = data.get("aggregate") if isinstance(data.get("aggregate"), dict) else {}
            aggregate_status = str(aggregate.get("status") or "")
            if status in {"completed", "failed", "timed_out", "cancelled"}:
                continue
            if aggregate_status in {"completed", "failed", "timed_out", "cancelled"}:
                continue
            for child in data.get("children", []) or []:
                if isinstance(child, dict) and str(child.get("task_id") or "") == task_id:
                    return fanout_id
        return ""

    def _emit_workflow_invoke_rejected(
        self,
        event: ZfEvent,
        reason: str,
        *,
        task_id: str,
        pattern_id: str,
    ) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        self.event_writer.append(ZfEvent(
            type="workflow.invoke.rejected",
            actor="zf-cli",
            task_id=task_id or event.task_id,
            payload={
                "task_id": task_id,
                "pattern_id": pattern_id,
                "source_event_id": event.id,
                "reason": reason,
                "channel_id": str(payload.get("channel_id") or ""),
                "thread_id": str(payload.get("thread_id") or ""),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))

    def _emit_task_fanout_rejected(
        self,
        event: ZfEvent,
        reason: str,
        *,
        expected: str = "",
        actual: str = "",
    ) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        self.event_writer.append(ZfEvent(
            type="task.fanout.rejected",
            actor="zf-cli",
            task_id=event.task_id or str(payload.get("task_id") or "") or None,
            payload={
                "reason": reason,
                "expected_dispatch_id": expected,
                "actual_dispatch_id": actual,
                "source_event_id": event.id,
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))

    def _on_channel_message_posted(self, event: ZfEvent) -> OrchestratorDecision | None:
        """Raw `zf emit channel.message.posted` runs the same routing pipeline
        as the web action. The router's `_auto_route_allowed` gate keeps
        agent-authored posts from looping; this handler only adds a
        re-entry guard so reactor-emitted side effects don't re-route.
        """
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.actor == "orchestrator-reactor":
            return None
        route_channel_message(
            state_dir=self.state_dir,
            writer=self.event_writer,
            message_event=event,
            message_payload=payload,
            actor="orchestrator-reactor",
            source="runtime",
            project_root=getattr(self, "project_root", None),
            config=getattr(self, "config", None),
            openclaw_client=getattr(self, "openclaw_client", None),
        )
        return None

    def _on_channel_agent_reply_requested(self, event: ZfEvent) -> OrchestratorDecision | None:
        """Raw `zf emit channel.agent.reply.requested` dispatches to the
        target member's backend via `dispatch_reply_request`. The inline
        router path (`route_channel_message` → `dispatch_reply_request`)
        already emits `channel.agent.reply.started` before this handler
        sees the event, so the started-event idempotency check keeps the
        handler a no-op on that path and only fires on raw operator emits.
        """
        if event.actor == "orchestrator-reactor":
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        channel_id = str(payload.get("channel_id") or event.correlation_id or "")
        request_id = str(payload.get("request_id") or "")
        if not channel_id or not request_id:
            return None
        for prior in self.event_log.read_all():
            if prior.type != "channel.agent.reply.started":
                continue
            prior_payload = prior.payload if isinstance(prior.payload, dict) else {}
            if (
                str(prior_payload.get("channel_id") or "") == channel_id
                and str(prior_payload.get("request_id") or "") == request_id
            ):
                return None
        dispatch_reply_request(
            state_dir=self.state_dir,
            writer=self.event_writer,
            channel_id=channel_id,
            request_id=request_id,
            actor="orchestrator-reactor",
            source="runtime",
            project_root=getattr(self, "project_root", None),
            config=getattr(self, "config", None),
            openclaw_client=getattr(self, "openclaw_client", None),
        )
        return None

    def _on_worker_completed(self, event: ZfEvent) -> OrchestratorDecision | None:
        """Route provider/worker self-completion through completion audit.

        This is intentionally conservative: the handler records the audit
        route and never treats a self-reported completion as task truth.
        Existing test/judge/discriminator paths still own terminal done.
        """
        task_id = event.task_id or ""
        payload = event.payload if isinstance(event.payload, dict) else {}
        if not task_id:
            task_id = str(payload.get("task_id") or payload.get("current_task_id") or "")
        if not task_id:
            return None
        try:
            from zf.runtime.long_horizon import apply_completion_audit

            result = apply_completion_audit(
                state_dir=self.state_dir,
                task_id=task_id,
                event_writer=self.event_writer,
                trigger_event=event,
                task_store=self.task_store,
                config=self.config,
                project_root=self.project_root,
                mutate=False,
            )
        except Exception as exc:
            try:
                self.event_writer.append(ZfEvent(
                    type="task.done.blocked",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "source": "completion_audit",
                        "reason": str(exc),
                        "trigger_event": event.type,
                        "trigger_event_id": event.id,
                    },
                    causation_id=event.id,
                    correlation_id=event.correlation_id,
                ))
            except Exception:
                pass
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                reason="worker.completed completion audit failed",
            )
        return OrchestratorDecision(
            action=result.route,
            task_id=task_id,
            role=result.recommended_role,
            reason=f"worker.completed → completion audit route {result.route}",
        )

    def _on_context_critical(self, event: ZfEvent) -> OrchestratorDecision | None:
        """Route context hard-cap events through completion audit.

        Context pressure is runtime infrastructure state, not product truth.
        The deterministic kernel records a retry/continuation route and
        refreshes the resume packet so a fresh provider session can continue
        the same work unit without treating context exhaustion as success or
        product failure.
        """
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = (
            event.task_id
            or str(payload.get("task_id") or payload.get("current_task_id") or "")
        )
        if not task_id:
            return None
        if self._completion_audit_routed_for_trigger(event.id):
            return None
        try:
            from zf.runtime.long_horizon import apply_completion_audit

            result = apply_completion_audit(
                state_dir=self.state_dir,
                task_id=task_id,
                event_writer=self.event_writer,
                trigger_event=event,
                task_store=self.task_store,
                config=self.config,
                project_root=self.project_root,
                mutate=False,
            )
        except Exception as exc:
            try:
                self.event_writer.append(ZfEvent(
                    type="task.done.blocked",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "source": "context_completion_audit",
                        "reason": str(exc),
                        "trigger_event": event.type,
                        "trigger_event_id": event.id,
                    },
                    causation_id=event.id,
                    correlation_id=event.correlation_id,
                ))
            except Exception:
                pass
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                reason="worker.context.critical completion audit failed",
            )
        return OrchestratorDecision(
            action=result.route,
            task_id=task_id,
            role=result.recommended_role,
            reason=f"worker.context.critical → completion audit route {result.route}",
        )

    def _completion_audit_routed_for_trigger(self, trigger_event_id: str) -> bool:
        if not trigger_event_id:
            return False
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        for candidate in reversed(events):
            if candidate.type != "completion_audit.routed":
                continue
            if candidate.causation_id == trigger_event_id:
                return True
            payload = candidate.payload if isinstance(candidate.payload, dict) else {}
            if payload.get("trigger_event_id") == trigger_event_id:
                return True
        return False

    def _on_completion_scheduled(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Consume completion-audit continuation/retry schedules.

        The audit event is the decision; this handler performs the small
        deterministic state transition that makes the task dispatchable again.
        It does not mark the task done and it does not invent new evidence.
        """
        task_id = event.task_id or ""
        payload = event.payload if isinstance(event.payload, dict) else {}
        if not task_id:
            task_id = str(payload.get("task_id") or "")
        if not task_id:
            return None
        if self._completion_schedule_already_consumed(event.id):
            return OrchestratorDecision(
                action="skip",
                task_id=task_id,
                reason=f"{event.type} already consumed",
            )
        task = self.task_store.get(task_id)
        if task is None:
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                reason=f"{event.type} task missing",
            )
        if task.status in {"done", "cancelled"}:
            return OrchestratorDecision(
                action="skip",
                task_id=task_id,
                reason=f"{event.type} ignored for terminal task",
            )
        scheduled_dispatch_id = str(payload.get("dispatch_id") or "")
        active_dispatch_id = str(getattr(task, "active_dispatch_id", "") or "")
        if (
            scheduled_dispatch_id
            and active_dispatch_id
            and scheduled_dispatch_id != active_dispatch_id
        ):
            self.event_writer.append(ZfEvent(
                type="task.retry.stale_ignored",
                actor="zf-cli",
                task_id=task_id,
                payload={
                    "source": "completion_audit_schedule",
                    "schedule_event_id": event.id,
                    "schedule_event_type": event.type,
                    "expected_dispatch_id": active_dispatch_id,
                    "actual_dispatch_id": scheduled_dispatch_id,
                    "route": str(payload.get("route") or ""),
                    "reason": "scheduled dispatch id no longer matches task",
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
            return OrchestratorDecision(
                action="skip",
                task_id=task_id,
                reason=f"{event.type} stale dispatch ignored",
            )

        assigned_to = (
            str(getattr(task, "assigned_to", "") or "")
            or str(payload.get("recommended_role") or payload.get("role") or "")
        )
        previous_status = task.status
        updates: dict[str, Any] = {
            "status": "backlog",
            "active_dispatch_id": "",
        }
        if assigned_to:
            updates["assigned_to"] = assigned_to
        if getattr(task, "blocked_reason", ""):
            updates["blocked_reason"] = ""
        updated = self.task_store.update(task_id, **updates)
        self._active_dispatch_ids.pop(task_id, None)
        route = str(payload.get("route") or "")
        if not route:
            route = (
                "retry"
                if event.type == "task.retry_scheduled"
                else "continuation"
            )
        self.event_writer.append(ZfEvent(
            type="task.requeued",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "source": "completion_audit_schedule",
                "schedule_event_id": event.id,
                "schedule_event_type": event.type,
                "completion_audit_event_id": str(
                    payload.get("completion_audit_event_id") or ""
                ),
                "route": route,
                "reason": str(payload.get("reason") or event.type),
                "previous_status": previous_status,
                "previous_dispatch_id": active_dispatch_id,
                "assigned_to": assigned_to,
                "next_required_event": str(payload.get("next_required_event") or ""),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        return OrchestratorDecision(
            action="move",
            task_id=task_id,
            role=assigned_to or None,
            reason=(
                f"{event.type} → task.requeued"
                if updated is not None else f"{event.type} requeue attempted"
            ),
        )

    def _completion_schedule_already_consumed(self, event_id: str) -> bool:
        if not event_id:
            return False
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        for candidate in events:
            if candidate.type not in {"task.requeued", "task.retry.stale_ignored"}:
                continue
            if candidate.causation_id == event_id:
                return True
            payload = candidate.payload if isinstance(candidate.payload, dict) else {}
            if payload.get("schedule_event_id") == event_id:
                return True
        return False

    def _on_autoresearch_trigger_accepted(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Turn an accepted autoresearch trigger into a supervised action proposal.

        The trigger itself must not pause/kill workers. It creates a
        token-gated `maintenance-prepare` proposal that the operator or Web
        action path can apply deterministically.
        """
        payload = event.payload if isinstance(event.payload, dict) else {}
        trigger_id = str(payload.get("trigger_id") or event.id or "").strip()
        if not trigger_id:
            return None
        proposal_id = "autoresearch-maint-" + hashlib.sha1(
            trigger_id.encode("utf-8")
        ).hexdigest()[:12]
        if self._automation_proposal_exists(proposal_id):
            return OrchestratorDecision(
                action="skip",
                task_id=event.task_id,
                reason="autoresearch maintenance proposal already exists",
            )
        task_id = event.task_id or str(payload.get("task_id") or "")
        reason = str(payload.get("reason") or "autoresearch trigger accepted")
        action_payload: dict[str, Any] = {
            "trigger_id": trigger_id,
            "reason": reason,
            "source_event_id": event.id,
            "source_event_type": event.type,
            "severity": str(payload.get("severity") or ""),
            "fingerprint": str(payload.get("fingerprint") or ""),
            "evidence_paths": (
                payload.get("evidence_paths")
                if isinstance(payload.get("evidence_paths"), list) else []
            ),
        }
        if task_id:
            action_payload["task_id"] = task_id
        candidate = candidate_from_trigger_event(event)
        candidate_path = write_candidate_artifact(self.state_dir, candidate)
        repair_task_payload = repair_task_payload_from_candidate(
            candidate,
            candidate_path=candidate_path,
        )
        self.event_writer.append(ZfEvent(
            type="autoresearch.bug_candidate.created",
            actor="zf-autoresearch",
            task_id=task_id or None,
            payload={
                "candidate": candidate.to_dict(),
                "candidate_path": str(candidate_path),
                "repair_task_payload": repair_task_payload,
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        self.event_writer.append(ZfEvent(
            type="automation.proposal.created",
            actor="zf-autoresearch",
            task_id=task_id or None,
            payload={
                "automation_id": "autoresearch-self-repair",
                "project_id": getattr(getattr(self.config, "project", None), "name", ""),
                "source": "autoresearch.trigger.accepted",
                "proposal_id": proposal_id,
                "output_mode": "proposal",
                "summary": f"Prepare supervised maintenance for {trigger_id}",
                "reason": reason,
                "action": "maintenance-prepare",
                "action_proposal": {
                    "action": "maintenance-prepare",
                    "payload": action_payload,
                    "reason": reason,
                },
                "repair_task_proposal": {
                    "action": "create-task",
                    "payload": repair_task_payload,
                    "reason": (
                        "create a normal ZaoFu repair task after maintenance "
                        "preparation is accepted"
                    ),
                },
                "candidate_id": candidate.candidate_id,
                "candidate_path": str(candidate_path),
                "trigger_id": trigger_id,
                "severity": str(payload.get("severity") or ""),
                "fingerprint": str(payload.get("fingerprint") or ""),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        loop_payload = build_loop_request_payload(
            payload,
            source_event_id=event.id,
        )
        loop_request_id = loop_request_id_from_payload(
            loop_payload,
            fallback=event.id,
        )
        try:
            existing_loop_events = self.event_log.read_all()
        except Exception:
            existing_loop_events = []
        if not loop_request_exists(existing_loop_events, loop_request_id):
            self.event_writer.append(ZfEvent(
                type=LOOP_REQUESTED,
                actor="zf-autoresearch",
                task_id=task_id or None,
                payload=loop_payload,
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        # doc 78 O-2 (safe half): opt-in (ZF_AUTORESEARCH_AUTO_PREPARE) — elevate
        # the repair candidate into a first-class prepared-for-approval record
        # and surface it to the owner (Feishu via O-7). This NEVER applies or
        # merges anything: the repair scope is the harness's own kernel
        # (src/zf/**), and auto-applying an agent-generated kernel patch is a
        # safety boundary we keep — a human reviews the verified fix and merges.
        try:
            from zf.autoresearch.repair_preparation import (
                REPAIR_PREPARED_EVENT,
                auto_prepare_enabled,
                build_repair_preparation,
                owner_message_for_prepared_repair,
                repair_prepared_payload,
            )

            if auto_prepare_enabled():
                prep = build_repair_preparation({
                    "candidate": candidate.to_dict(),
                    "candidate_path": str(candidate_path),
                    "repair_task_payload": repair_task_payload,
                })
                if prep is not None:
                    self.event_writer.append(ZfEvent(
                        type=REPAIR_PREPARED_EVENT,
                        actor="zf-autoresearch",
                        task_id=task_id or None,
                        payload=repair_prepared_payload(prep),
                        causation_id=event.id,
                        correlation_id=event.correlation_id,
                    ))
                    self.event_writer.append(ZfEvent(
                        type="owner.visible_message.requested",
                        actor="zf-autoresearch",
                        payload=owner_message_for_prepared_repair(prep),
                        causation_id=event.id,
                        correlation_id=event.correlation_id,
                    ))
        except Exception:
            pass

        # backlog 0820 block B: AUTHORIZED auto-repair gate (default OFF). Only
        # when the operator sets ZF_AUTORESEARCH_AUTO_REPAIR=authorized does a
        # candidate auto-dispatch to a zf-self-repair agent; bounded by a
        # per-fingerprint cap (over cap → escalate to human, not a loop). The
        # dispatch consumer turns this event into an isolated repair task with
        # the zf-self-repair skill — the agent then runs the tracked playbook
        # (backlog → fix → verify → done). Default off → behavior unchanged.
        try:
            from zf.runtime.repair_authorization import (
                AUTO_REPAIR_ENV,
                REPAIR_DISPATCH_EVENT,
                SELF_REPAIR_SKILL,
                decide_repair,
            )

            candidate_payload = {
                "candidate": candidate.to_dict(),
                "candidate_path": str(candidate_path),
                "repair_task_payload": repair_task_payload,
            }
            repair_mode = _autoresearch_repair_mode(self.config)
            repair_env = (
                {AUTO_REPAIR_ENV: "authorized"}
                if repair_mode == "bounded_repair" else None
            )
            from zf.runtime.event_window import read_runtime_events

            decision = decide_repair(
                candidate_payload,
                read_runtime_events(self.event_log, self.state_dir),
                env=repair_env,
            )
            if decision.action == "dispatch":
                self.event_writer.append(ZfEvent(
                    type=REPAIR_DISPATCH_EVENT,
                    actor="zf-autoresearch",
                    task_id=task_id or None,
                    payload={
                        "fingerprint": decision.fingerprint,
                        "attempt": decision.attempt,
                        "skill": SELF_REPAIR_SKILL,
                        "candidate_id": candidate.candidate_id,
                        "candidate_path": str(candidate_path),
                        "repair_task_payload": repair_task_payload,
                        "apply_policy": repair_mode,
                        "repair_mode": repair_mode,
                        "failure_class": _autoresearch_failure_class(
                            decision.fingerprint
                        ),
                        "repair_bucket": decision.bucket,
                        "source_event_id": event.id,
                        "resume_checkpoint_ref": str(
                            payload.get("source_event_id")
                            or payload.get("trigger_id")
                            or event.id
                        ),
                    },
                    causation_id=event.id,
                    correlation_id=event.correlation_id,
                ))
            elif decision.action == "escalate":
                self.event_writer.append(ZfEvent(
                    type="human.escalate",
                    actor="zf-autoresearch",
                    task_id=task_id or None,
                    payload={
                        "reason": decision.reason,
                        "fingerprint": decision.fingerprint,
                        "attempt": decision.attempt,
                    },
                    causation_id=event.id,
                    correlation_id=event.correlation_id,
                ))
        except Exception:
            pass

        return OrchestratorDecision(
            action="notify",
            task_id=task_id or None,
            role="operator",
            reason="autoresearch.trigger.accepted → maintenance-prepare proposal",
        )

    def _on_autoresearch_invocation_requested(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Accept only L1/proposal-only Autoresearch diagnosis requests.

        The accepted invocation is bridged to the existing supervised
        maintenance proposal path via ``autoresearch.trigger.accepted``.
        No mainline code changes are applied here.
        """
        payload = event.payload if isinstance(event.payload, dict) else {}
        invocation_id = invocation_id_from_payload(payload, fallback=event.id)
        if not invocation_id:
            return None
        if self._autoresearch_invocation_handled(invocation_id):
            return OrchestratorDecision(
                action="skip",
                task_id=event.task_id,
                reason="autoresearch invocation already handled",
            )
        reason = validate_invocation_request(payload)
        if not reason:
            try:
                from zf.runtime.maintenance import self_repair_active

                if self_repair_active(self.state_dir):
                    reason = "self repair maintenance is already active"
            except Exception:
                pass
        if reason:
            self.event_writer.append(ZfEvent(
                type="autoresearch.invocation.rejected",
                actor="zf-autoresearch",
                task_id=event.task_id,
                payload=rejection_payload(
                    payload,
                    source_event_id=event.id,
                    reason=reason,
                ),
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
            return OrchestratorDecision(
                action="notify",
                task_id=event.task_id,
                role="operator",
                reason=f"autoresearch invocation rejected: {reason}",
            )

        accepted = self.event_writer.append(ZfEvent(
            type="autoresearch.invocation.accepted",
            actor="zf-autoresearch",
            task_id=event.task_id,
            payload=acceptance_payload(payload, source_event_id=event.id),
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        self.event_writer.append(ZfEvent(
            type="autoresearch.trigger.accepted",
            actor="zf-autoresearch",
            task_id=event.task_id,
            payload=trigger_payload_from_invocation(
                payload,
                source_event_id=accepted.id,
            ),
            causation_id=accepted.id,
            correlation_id=event.correlation_id,
        ))
        return OrchestratorDecision(
            action="notify",
            task_id=event.task_id,
            role="operator",
            reason="autoresearch invocation accepted for supervised diagnosis",
        )

    def _on_run_manager_autoresearch_requested(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Bridge Run Manager diagnosis requests into the Autoresearch entry path."""
        try:
            events = self.event_log.read_all()
        except Exception:
            events = [event]
        invocation = build_invocation_request_from_run_manager_event(
            event,
            events=events,
        )
        if invocation is None:
            return OrchestratorDecision(
                action="skip",
                task_id=event.task_id,
                reason="run manager autoresearch request already bridged",
            )
        self.event_writer.append(invocation)
        return OrchestratorDecision(
            action="notify",
            task_id=event.task_id,
            role="operator",
            reason="run manager autoresearch request bridged to invocation",
        )

    def _autoresearch_invocation_handled(self, invocation_id: str) -> bool:
        if not invocation_id:
            return False
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        for candidate in events:
            if candidate.type not in {
                "autoresearch.invocation.accepted",
                "autoresearch.invocation.rejected",
            }:
                continue
            payload = candidate.payload if isinstance(candidate.payload, dict) else {}
            if invocation_id_from_payload(payload, fallback="") == invocation_id:
                return True
        return False

    def _on_replan_proposal_created(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        """Request deterministic replan contract eval for proposal artifacts.

        The proposal event may come from Autoresearch, Project Spine Review, or
        a controlled owner action. Layer 1 only builds a refs-only eval request;
        Product Delivery remains responsible for any later adoption.
        """

        payload = event.payload if isinstance(event.payload, dict) else {}
        proposal = (
            payload.get("proposal")
            if isinstance(payload.get("proposal"), dict)
            else payload
        )
        proposal_ref = str(
            payload.get("proposal_ref")
            or payload.get("artifact_ref")
            or payload.get("path")
            or ""
        ).strip()
        candidate_task_map_ref = str(payload.get("candidate_task_map_ref") or "").strip()
        if not candidate_task_map_ref:
            return OrchestratorDecision(
                action="skip",
                task_id=event.task_id,
                reason="replan proposal has no candidate_task_map_ref",
            )
        feature_id = str(
            payload.get("feature_id")
            or payload.get("feature")
            or event.task_id
            or "replan"
        ).strip()
        request_payload = build_replan_contract_eval_request(
            proposal if isinstance(proposal, dict) else {},
            proposal_ref=proposal_ref,
            trigger_event_id=event.id,
            feature_id=feature_id,
            candidate_task_map_ref=candidate_task_map_ref,
            old_task_map_ref=str(payload.get("old_task_map_ref") or ""),
            expected_current_task_map_ref=str(payload.get("expected_current_task_map_ref") or ""),
            profile=str(payload.get("profile") or "baseline"),
        )
        if self._replan_eval_request_exists(request_payload):
            return OrchestratorDecision(
                action="skip",
                task_id=event.task_id,
                reason="replan contract eval request already exists",
            )
        self.event_writer.append(ZfEvent(
            type="replan.contract_eval.requested",
            actor="zf-product-delivery",
            task_id=event.task_id,
            payload=request_payload,
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        return OrchestratorDecision(
            action="notify",
            task_id=event.task_id,
            role="product_delivery",
            reason="replan proposal created -> contract eval requested",
        )

    def _replan_eval_request_exists(self, request_payload: dict[str, Any]) -> bool:
        request_id = str(request_payload.get("request_id") or "").strip()
        idempotency_key = str(request_payload.get("idempotency_key") or "").strip()
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        for candidate in events:
            if candidate.type != "replan.contract_eval.requested":
                continue
            payload = candidate.payload if isinstance(candidate.payload, dict) else {}
            if request_id and payload.get("request_id") == request_id:
                return True
            if idempotency_key and payload.get("idempotency_key") == idempotency_key:
                return True
        return False

    def _automation_proposal_exists(self, proposal_id: str) -> bool:
        if not proposal_id:
            return False
        try:
            events = self.event_log.read_all()
        except Exception:
            return False
        for candidate in events:
            if not candidate.type.startswith("automation.proposal."):
                continue
            payload = candidate.payload if isinstance(candidate.payload, dict) else {}
            if payload.get("proposal_id") == proposal_id:
                return True
        return False

    # -- dispatching --

    def _refresh_task_doc_projection(
        self,
        task: Task | None,
        *,
        source_event: str = "",
    ) -> None:
        if task is None:
            return
        try:
            from zf.runtime.task_doc import write_task_doc

            result = write_task_doc(
                self.state_dir,
                task,
                dispatch_id=getattr(task, "active_dispatch_id", ""),
                source_event=source_event,
            )
            if task.contract is not None and result.capsule_revision:
                self.task_store.update(task.id, contract=task.contract)
            self.event_writer.append(ZfEvent(
                type="task.doc.updated",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "source_event": source_event,
                    "task_doc": str(result.path),
                    "source_doc": str(result.source_path),
                    "progress_doc": str(result.progress_path),
                    "source_revision": result.source_revision,
                    "contract_revision": result.contract_revision,
                    "capsule_revision": result.capsule_revision,
                },
            ))
        except Exception:
            # task.md is a projection. Status truth is still kanban/events, so a
            # projection write failure must not corrupt the state transition.
            pass

    def _move_task(
        self,
        task_id: str,
        to_status: str,
        *,
        trigger_event: str = "",
    ) -> bool:
        """Move task to new status via state machine."""
        task = self.task_store.get(task_id)
        if task is None:
            return False
        late_terminal_success = (
            task.status == "backlog"
            and to_status == "done"
            and trigger_event in {"review.approved", "verify.passed", "test.passed", "judge.passed"}
        )
        configured_terminal_success = (
            task.status == "in_progress"
            and to_status == "done"
            and self._is_configured_terminal_success(trigger_event)
        )
        plan_only_terminal_success = (
            task.status in {"backlog", "in_progress", "review"}
            and to_status == "done"
            and trigger_event == "artifact.manifest.published"
        )
        try:
            if not (
                (
                    task.status == "in_progress"
                    and to_status == "done"
                    and trigger_event == "judge.passed"
                )
                or configured_terminal_success
                or late_terminal_success
                or plan_only_terminal_success
            ):
                self.sm.transition(task.status, to_status)
            updated_task = self.task_store.update(task_id, status=to_status)
            self._refresh_task_doc_projection(
                updated_task,
                source_event=trigger_event or "reactor_move",
            )
            if to_status == "done":
                if late_terminal_success:
                    try:
                        self.event_writer.append(ZfEvent(
                            type="task.late_success.reconciled",
                            actor="zf-cli",
                            task_id=task_id,
                            payload={
                                "from": task.status,
                                "to": "done",
                                "trigger_event": trigger_event,
                                "dispatch_id": getattr(task, "active_dispatch_id", ""),
                            },
                        ))
                    except Exception:
                        pass
                try:
                    self.event_writer.append(ZfEvent(
                        type="task.status_changed",
                        actor="zf-cli",
                        task_id=task_id,
                        payload={
                            "from": task.status,
                            "to": "done",
                            "source": "reactor_move",
                            "trigger_event": trigger_event,
                        },
                    ))
                except Exception:
                    pass
                try:
                    close_feature_if_all_tasks_done(
                        state_dir=self.state_dir,
                        task=task,
                        task_store=self.task_store,
                        event_writer=self.event_writer,
                        event_log=self.event_log,
                        actor="zf-cli",
                        source="reactor_move",
                        trigger_event=trigger_event,
                    )
                except Exception:
                    pass
                self._unblock_resolved_dependents(
                    task_id,
                    trigger_event=trigger_event,
                )
            return True
        except InvalidTransition:
            try:
                self.event_writer.append(ZfEvent(
                    type="task.invalid_transition",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "kind": "move",
                        "target": to_status,
                        "from_status": task.status,
                        "source": "reactor_move",
                        "trigger_event": trigger_event,
                    },
                ))
            except Exception:
                pass
            return False

    def _unblock_resolved_dependents(
        self,
        blocker_task_id: str,
        *,
        trigger_event: str,
    ) -> None:
        """Move dependency-blocked tasks back to backlog after blockers close."""
        for dependent in list(self.task_store.list_all()):
            if dependent.status != "blocked":
                continue
            if blocker_task_id not in dependent.blocked_by:
                continue
            if dependent.blocked_reason:
                continue
            if not self._blocked_by_chain_resolved(dependent):
                continue
            try:
                self.sm.transition(dependent.status, "backlog")
            except InvalidTransition:
                continue
            updated_dependent = self.task_store.update(dependent.id, status="backlog")
            self._refresh_task_doc_projection(
                updated_dependent,
                source_event="dependency_resolved",
            )
            try:
                self.event_writer.append(ZfEvent(
                    type="task.status_changed",
                    actor="zf-cli",
                    task_id=dependent.id,
                    payload={
                        "from": "blocked",
                        "to": "backlog",
                        "source": "dependency_resolved",
                        "blocker_task_id": blocker_task_id,
                        "blocked_by": list(dependent.blocked_by),
                        "trigger_event": trigger_event,
                    },
                ))
            except Exception:
                pass

    def _blocked_by_chain_resolved(self, task: Task) -> bool:
        if not task.blocked_by:
            return False
        for blocker_id in task.blocked_by:
            blocker = self.task_store.get(blocker_id)
            if blocker is None or blocker.status not in {"done", "cancelled"}:
                return False
        return True


def _action_covered(action: str, evidence_text: str) -> bool:
    action_text = action.lower().strip()
    if not action_text:
        return True
    if action_text in evidence_text:
        return True
    tokens = [
        token
        for token in (
            "".join(ch if ch.isalnum() else " " for ch in action_text).split()
        )
        if len(token) >= 4
    ]
    if not tokens:
        return False
    matches = sum(1 for token in tokens if token in evidence_text)
    return matches >= max(1, min(3, len(tokens)))
