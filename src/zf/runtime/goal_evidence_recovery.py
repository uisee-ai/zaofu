"""Explicitly re-present newly bound historical evidence to a held PRD/Issue Judge."""

from pathlib import Path

from zf.core.config.project_context import ProjectContext
from zf.core.events import EventLog, EventWriter
from zf.core.state.locks import locked_path
from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
from zf.runtime.goal_closure_bridge import GoalClosureBridgeMixin
from zf.runtime.goal_historical_candidate_evidence import historical_candidate_reports
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
            if not added or fact.get("historical_candidate_reports") == added:
                raise ValueError("no newly bound historical verification evidence")
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
