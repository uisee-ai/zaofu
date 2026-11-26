"""Explicitly re-present newly bound historical evidence to a held PRD/Issue Judge."""

from pathlib import Path

from zf.core.config.project_context import ProjectContext
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.store import TaskStore
from zf.core.state.locks import locked_path
from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
from zf.runtime.goal_closure_bridge import GoalClosureBridgeMixin
from zf.runtime.goal_historical_candidate_evidence import historical_candidate_reports
from zf.runtime.goal_task_verify_evidence import judge_task_verify_evidence
from zf.runtime.run_admission import fold_terminal_run_scope
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


class GoalEvidenceRecovery(GoalClosureBridgeMixin):
    def __init__(
        self, context: ProjectContext, log: EventLog, writer: EventWriter
    ) -> None:
        self.config = context.config
        self.project_root = context.project_root
        self.state_dir = Path(context.state_dir)
        self.event_log = log
        self.event_writer = writer

    def review(self, blocked_id: str, *, apply: bool) -> dict:
        with locked_path(self.state_dir / "goal-evidence-recovery"):
            events = self.event_log.read_all()
            by_id = {e.id: e for e in events}
            blocked = by_id.get(blocked_id)
            if (
                not blocked
                or blocked.type != "goal.closure.blocked"
                or blocked.origin != "kernel"
            ):
                raise ValueError("an admitted Goal closure hold is required")
            body = blocked.payload
            run = body["workflow_run_id"]
            scoped = [e for e in events if e.payload.get("workflow_run_id") == run]
            aliases, terminal = fold_terminal_run_scope(events)
            if (
                aliases.get(run, run) in terminal
                or body.get("recommended_action") != "hold"
            ):
                raise ValueError("terminal Runs and human decisions cannot be reopened")
            prior = next(
                (e for e in reversed(scoped) if e.type == "flow.goal.closed"), None
            )
            judgment = by_id.get(blocked.causation_id)
            if (
                not judgment
                or judgment.type != "goal.closure.synthesized"
                or judgment.origin != "kernel"
                or judgment.payload.get("workflow_run_id") != run
                or not body.get("admitted_call_result_ref")
                or judgment.payload.get("admitted_call_result_ref")
                != body["admitted_call_result_ref"]
                or not prior
                or (prior.origin not in ("kernel", "") or prior.actor != "zf-cli")
                or not prior.payload.get("closure_identity")
                or prior.payload.get("closure_identity")
                != judgment.payload.get("closure_identity")
                or prior.payload.get("closure_fact_digest")
                != body.get("closure_fact_digest")
            ):
                raise ValueError("hold does not belong to the current closure")
            last = next(
                e
                for e in reversed(scoped)
                if e.type
                in {
                    "goal.closure.synthesized",
                    "goal.closure.blocked",
                    "goal.closure.rejected",
                }
            )
            if last.id != blocked.id:
                raise ValueError("hold is no longer current")
            candidate = frozen_candidate_for_payload(events, run, payload={})
            plan = next(
                (
                    e
                    for e in reversed(scoped)
                    if e.type == "task_map.ready" and e.origin == "kernel"
                ),
                None,
            )
            p = prior.payload
            if (
                not candidate
                or not plan
                or candidate.origin != "kernel"
                or candidate.payload["candidate_head_commit"] != body["target_commit"]
                or any(
                    candidate.payload.get(k) != p.get(k)
                    or plan.payload.get(k) != p.get(k)
                    for k in ("task_map_generation", "plan_artifact_package_digest")
                )
            ):
                raise ValueError("the approved plan or frozen candidate has changed")
            fact = hydrate_sidecar_ref(
                self.state_dir,
                {
                    "ref": p["closure_fact_ref"],
                    "sha256": p["closure_fact_digest"],
                },
            ).payload
            added = historical_candidate_reports(
                self.state_dir,
                events,
                run,
                p["task_map_generation"],
                p["plan_artifact_package_digest"],
            )
            new_historical = added and fact.get("historical_candidate_reports") != added
            task_results = []
            if not new_historical:
                task_results = self._missing_verify_results(
                    events, blocked, prior, fact, candidate
                )
                if not task_results:
                    raise ValueError(
                        "no newly bound historical or Task Verify evidence"
                    )
            source = by_id.get(prior.causation_id)
            if (
                not source
                or source.origin != "kernel"
                or source.type
                not in {"flow.discovery.completed", "test.passed", "verify.passed"}
                or (source.payload.get("workflow_run_id") or source.correlation_id)
                != run
            ):
                raise ValueError("original closure source is unavailable")
            result = {
                "status": "eligible",
                "blocked_event_id": blocked.id,
                "candidate_head": body["target_commit"],
                "historical_results": [r["admitted_event_id"] for r in added],
            }
            if task_results:
                result["task_verify_results"] = [
                    row["admitted_event_id"] for row in task_results
                ]
            if apply:
                refreshed = self._emit_current_goal_closure(
                    event_type=prior.type,
                    source_event=source,
                    flow_kind=p["flow_kind"],
                    payload={**fact["source_payload"], "workflow_run_id": run},
                )
                if not refreshed or refreshed.id == prior.id:
                    raise ValueError("closure input did not advance")
                result.update(status="requested", closure_event_id=refreshed.id)
            return result

    def _missing_verify_results(
        self,
        events: list[ZfEvent],
        blocked: ZfEvent,
        prior: ZfEvent,
        fact: dict,
        candidate: ZfEvent,
    ) -> list[dict]:
        """Only a verified control body missing from this Judge's actual grant can advance a hold."""
        from zf.runtime.candidate_continuation import _git_commit
        from zf.runtime.run_continuation import (
            bind_action_dependencies,
            blocking_decisions_for_action,
        )
        from zf.runtime.run_manager import _pending_human_decisions
        from zf.runtime.workflow_config_consumer import approved_config_consumer_error

        p, hold = prior.payload, blocked.payload
        batch = p.get("judge_batch_admissions") or {}
        gaps = hold.get("open_gap_refs")
        if (
            not isinstance(gaps, list)
            or not gaps
            or not all(isinstance(ref, str) and ref for ref in gaps)
            or not batch.get("candidate_event_id")
        ):
            return []
        run, target = hold["workflow_run_id"], hold["target_commit"]
        rows = judge_task_verify_evidence(
            self.state_dir,
            events,
            batch=batch,
            run=run,
            generation=p["task_map_generation"],
            target=target,
        )
        by_ref = {
            row["control_result"]["ref"]: row
            for row in rows
            if row["candidate_event_id"] == candidate.id
        }
        if not set(gaps).issubset(by_ref):
            return []
        already_pinned = {
            row["control_result"]["ref"] for row in fact.get("task_verify_evidence", [])
        }
        if set(gaps).intersection(already_pinned):
            return []
        later = events[events.index(blocked) + 1 :]
        for event in later:
            if (event.payload.get("workflow_run_id") or event.correlation_id) != run:
                continue
            if event.type in {
                "test.failed",
                "verify.failed",
                "review.rejected",
                "candidate.rework.requested",
                "task.pipeline.generation.admitted",
                "plan.artifact_package.admitted",
            } or (
                event.type == "judge.failed"
                and event.causation_id != blocked.causation_id
            ):
                raise ValueError(
                    "a later product or planning decision supersedes this Judge hold"
                )

        dispatch = _held_judge_dispatch(self.state_dir, events, blocked, prior)
        granted = hydrate_sidecar_ref(
            self.state_dir, dispatch.payload["attempt_source_manifest"]
        ).payload
        if (
            granted.get("workflow_run_id") != run
            or granted.get("target_commit") != target
            or granted.get("task_map_generation") != p["task_map_generation"]
            or granted.get("plan_artifact_package_digest")
            != p["plan_artifact_package_digest"]
            or granted.get("handoff_authority_profile") != "thin-judge-goal-closure"
            or any(
                granted.get(k) != dispatch.payload[k]
                for k in ("attempt_id", "dispatch_id")
            )
        ):
            raise ValueError(
                "original Judge input grant does not bind to the held target"
            )
        sources = granted.get("sources")
        if (
            not isinstance(sources, list)
            or not sources
            or not all(
                isinstance(row, dict)
                and isinstance(row.get("ref"), str)
                and row["ref"]
                and isinstance(row.get("sha256"), str)
                and row["sha256"]
                for row in sources
            )
        ):
            raise ValueError("original Judge input grant is incomplete")
        if set(gaps).intersection(row["ref"] for row in sources):
            return []

        invokes = [
            e.payload
            for e in events
            if e.type == "workflow.invoke.requested"
            and e.payload.get("workflow_run_id") == run
        ]
        if len(invokes) != 1 or approved_config_consumer_error(
            self.state_dir, self.config, invokes[0]
        ):
            raise ValueError(
                "held Judge review differs from the approved workflow configuration"
            )
        if candidate.payload.get("candidate_ref") != p.get("candidate_ref") or (
            _git_commit(self.project_root, candidate.payload["candidate_ref"]) != target
        ):
            raise ValueError("held candidate ref no longer points to its frozen commit")
        dependencies = bind_action_dependencies(
            [{"task_id": dispatch.payload["task_id"], "workflow_run_id": run}],
            TaskStore(self.state_dir / "kanban.json").list_all(),
        )
        if blocking_decisions_for_action(
            _pending_human_decisions(events), dependencies[0], events=events
        ):
            raise ValueError(
                "held Judge review requires resolution of the outstanding owner decision"
            )
        return [by_ref[ref] for ref in dict.fromkeys(gaps)]


def _held_judge_dispatch(
    state: Path,
    events: list[ZfEvent],
    blocked: ZfEvent,
    prior: ZfEvent,
) -> ZfEvent:
    """Match the admitted hold and immutable source grant to one actual Judge attempt."""
    hold = blocked.payload
    prefix = events[: events.index(blocked)]
    judged = [
        e
        for e in prefix
        if e.type == "workflow.call.result.admitted"
        and e.origin == "kernel"
        and e.payload.get("workflow_run_id") == hold["workflow_run_id"]
        and e.payload.get("operation_id") == hold.get("operation_id")
        and e.payload.get("envelope_ref") == hold["admitted_call_result_ref"]
        and e.payload.get("control_result_schema") == "goal-closure-result.v1"
        and e.payload.get("semantic_verdict") == "blocked"
    ]
    if len(judged) != 1:
        raise ValueError("held Judge admission is unavailable or ambiguous")
    admission = judged[0]
    verdict = hydrate_sidecar_ref(
        state, admission.payload["control_result_ref"]
    ).payload
    if (
        verdict.get("recommended_action") != "hold"
        or verdict.get("verdict") != "blocked"
        or any(
            verdict.get(k) != hold.get(k)
            for k in (
                "open_gap_refs",
                "closure_fact_digest",
                "target_commit",
                "workflow_run_id",
            )
        )
    ):
        raise ValueError("held gaps do not match the admitted Judge verdict")
    binding = (
        "workflow_run_id",
        "operation_id",
        "request_hash",
        "attempt_id",
        "dispatch_id",
    )
    matches = [
        e
        for e in prefix[: prefix.index(admission)]
        if e.type == "fanout.child.dispatched"
        and e.origin == "kernel"
        and all(
            admission.payload.get(k) and e.payload.get(k) == admission.payload[k]
            for k in binding
        )
        and e.payload.get("payload", {})
        .get("trigger_payload", {})
        .get("closure_identity")
        == prior.payload["closure_identity"]
    ]
    if len(matches) != 1 or not matches[0].payload.get(
        "attempt_source_manifest", {}
    ).get("sha256"):
        raise ValueError("original Judge input grant is unavailable or ambiguous")
    return matches[0]
