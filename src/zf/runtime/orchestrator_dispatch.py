"""DispatchMixin — task dispatch + rework + role/budget gates.

Split from orchestrator.py (P1.2 step 2). Methods rely on host
Orchestrator state (self.task_store, self.wip, self.cost_tracker,
self.config, self.event_log, self.transport, self.state_dir,
self._gan_round, self._cost_block_*, self._set_worker_state,
self._get_spawn_coordinator).
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path

from zf.core.config.schema import RoleConfig
from zf.core.errors import CircuitBreaker
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.safety.path_guard import PathGuard
from zf.core.task.contract_validation import validate_task_contract
from zf.core.task.schema import Task
from zf.runtime.git_capture import (
    capture_git_diff_context,
    capture_git_state,
    render_git_diff_context,
)
from zf.core.feature.store import FeatureStore
from zf.runtime.injection import (
    generate_role_instructions,
    generate_task_briefing,
    write_task_briefing,
    build_task_prompt,
)
from zf.runtime.cli_command import zf_cli_cmd
from zf.core.workflow.topology import WorkflowEventSets
from zf.runtime.orchestrator_types import OrchestratorDecision
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.recovery_sufficiency import build_artifact_recovery_refs
from zf.runtime.rework_triage import REWORK_RETRY_CLASSIFICATIONS
from zf.runtime.terminal_ledger import TERMINAL_SUCCESS_EVENTS
from zf.runtime.transport import transport_error_diagnostics
from zf.runtime.workflow_inputs import render_workflow_input_briefing_section


# PREREQ-B (doc 40 §6 I57): single source of truth for pipeline-event
# classification. Adding a new pipeline event → edit
# WorkflowEventSets.baseline() once.
_WORKFLOW_EVENT_SETS = WorkflowEventSets.baseline()
TASK_REF_REPAIR_REQUESTED_EVENT = "task.ref.repair.requested"
TASK_REF_SCOPE_REJECTION_REASON = "source_commit changes outside task contract scope"


def _discriminator_failure_hints(evidence: object) -> list[str]:
    if not isinstance(evidence, dict):
        return []
    hints: list[str] = []

    checks = evidence.get("checks")
    if isinstance(checks, list):
        for check in checks:
            if not isinstance(check, dict) or check.get("passed") is not False:
                continue
            label = str(check.get("rule") or check.get("category") or "").strip()
            command = str(check.get("command") or "").strip()
            fix = str(check.get("fix") or check.get("fix_hint") or "").strip()
            parts = []
            if label:
                parts.append(f"{label} failed")
            if command:
                parts.append(f"check `{command}`")
            if fix:
                parts.append(f"fix: {fix}")
            if parts:
                hints.append(", ".join(parts))

    gate_checks = evidence.get("gate_checks")
    if isinstance(gate_checks, dict):
        for gate, rows in gate_checks.items():
            if not isinstance(rows, list):
                continue
            for check in rows:
                if not isinstance(check, dict) or check.get("passed") is not False:
                    continue
                command = str(check.get("command") or "").strip()
                if command:
                    hints.append(f"{gate} failed command `{command}`")

    failure_details = evidence.get("failure_details")
    if isinstance(failure_details, dict):
        for gate, values in failure_details.items():
            if isinstance(values, list):
                for value in values:
                    text = str(value).strip()
                    if text:
                        hints.append(f"{gate}: {text}")

    return hints


def _payload_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _rework_required_actions(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    actions: list[str] = []

    def add_action(value: object) -> None:
        text = _payload_text(value)
        if text and text not in actions:
            actions.append(text)

    for key in ("required_action", "action", "next_step", "fix", "fix_hint"):
        add_action(payload.get(key))

    for key in ("must_fix", "required_actions", "actions", "fixes", "next_steps"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    add_action(
                        item.get("required_action")
                        or item.get("action")
                        or item.get("fix")
                        or item.get("summary")
                        or item.get("reason")
                    )
                else:
                    add_action(item)
        else:
            add_action(value)

    findings = payload.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                text = _payload_text(item)
                if text:
                    actions.append(text)
                continue
            parts: list[str] = []
            severity = _payload_text(item.get("severity"))
            evidence = _payload_text(item.get("evidence"))
            required = _payload_text(item.get("required_action"))
            summary = _payload_text(item.get("summary") or item.get("reason"))
            if severity:
                parts.append(f"{severity}:")
            if summary:
                parts.append(summary)
            if required:
                parts.append(f"required action: {required}")
            if evidence:
                parts.append(f"evidence: {evidence}")
            if parts:
                actions.append(" ".join(parts))

    blockers = payload.get("blockers")
    if isinstance(blockers, list):
        for item in blockers:
            text = _payload_text(item)
            if text:
                actions.append(f"blocker: {text}")

    if _task_ref_scope_repair_payload(payload):
        add_action(
            "Produce a new source_commit whose diff contains only this "
            "task's allowed contract scope; do not reuse the rejected "
            "source_commit or emit a metadata-only repair."
        )

    # Preserve order while deduping repeated gate payload fields.
    out: list[str] = []
    seen: set[str] = set()
    for action in actions:
        if action in seen:
            continue
        seen.add(action)
        out.append(action)
    return out


def _task_ref_scope_repair_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    reason = str(payload.get("reason") or "").strip()
    expected_action = str(payload.get("expected_action") or "").strip()
    return (
        reason == TASK_REF_SCOPE_REJECTION_REASON
        or expected_action == "split_or_rebase_source_commit_and_reemit_handoff"
        or bool(payload.get("out_of_scope_files"))
    )


def _payload_excerpt(payload: object, *, limit: int = 3000) -> str:
    if not isinstance(payload, dict) or not payload:
        return ""
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n... <truncated>"


def _new_dispatch_id() -> str:
    return f"disp-{uuid.uuid4().hex[:12]}"


def _dispatch_blocked_recommendation(reason: str, *, target_role: str = "") -> str:
    role_text = f" for role {target_role}" if target_role else ""
    if reason == "no_available_role":
        return (
            f"start, recycle, or free an available worker{role_text}; inspect "
            "worker context warnings and role_sessions before retrying"
        )
    if reason in {"wip_busy_reassign_branch", "cycle_wip_exhausted"}:
        return f"wait for current WIP{role_text} to finish or increase explicit replicas"
    if reason == "worker_not_dispatchable":
        return f"inspect worker lifecycle state{role_text} and restart/recycle if stale"
    if reason == "strict_contract_preflight_failed":
        return "refresh task capsule/source contract before dispatch"
    return "inspect dispatch diagnostics and worker availability"


def _terminal_run_success_context_keys(events: list[ZfEvent]) -> set[str]:
    keys: set[str] = set()
    for event in events:
        if event.type != "judge.passed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.task_id:
            if event.type == "judge.passed":
                keys.add(f"task:{event.task_id}")
            continue
        for name in ("pdd_id", "feature_id", "trace_id", "candidate_ref"):
            value = str(payload.get(name) or "").strip()
            if value:
                keys.add(f"{name}:{value}")
        if event.correlation_id:
            keys.add(f"trace_id:{event.correlation_id}")
    return keys


def _task_quiesced_by_terminal_run(task: Task, terminal_run_keys: set[str]) -> bool:
    if not terminal_run_keys:
        return False
    keys = {f"task:{task.id}"}
    contract = getattr(task, "contract", None)
    feature_id = str(getattr(contract, "feature_id", "") or "").strip()
    if feature_id:
        keys.add(f"feature_id:{feature_id}")
        keys.add(f"pdd_id:{feature_id}")
    key = str(getattr(task, "key", "") or "")
    if ":" in key:
        feature_from_key = key.split(":", 1)[0].strip()
        if feature_from_key:
            keys.add(f"feature_id:{feature_from_key}")
            keys.add(f"pdd_id:{feature_from_key}")
    return bool(keys & terminal_run_keys)


def _render_required_payload_shape(
    trigger_event,
    success_event_type: str,
) -> str:
    """Build an explicit JSON shape example for an evidence-reissue briefing.

    cangjie r3 surfaced the problem: when ZaoFu's terminal_done_hardening
    gate rejects a success event for a missing field (e.g. ``judge.passed
    payload missing score dimensions: completeness, correctness,
    evidence_quality, regression_risk``), the worker has to guess the
    canonical schema shape. This helper inspects the human-readable
    ``missing`` list and renders a concrete JSON template so the worker
    can copy the structure verbatim. Backlog 2026-05-14-1441.
    """
    missing = []
    if isinstance(trigger_event.payload, dict):
        raw = (
            trigger_event.payload.get("missing")
            or trigger_event.payload.get("violations")
            or []
        )
        if isinstance(raw, list):
            missing = [str(item) for item in raw if str(item).strip()]
        elif isinstance(raw, str) and raw.strip():
            missing = [raw.strip()]
    if not missing:
        return ""

    # B-NEW-2 (backlogs/2026-05-16-0052-zaofu-kernel-edge-bugs-from-cangjie-p5-validation.md):
    # extract tiers the worker ALREADY provided in the previous attempt
    # so we render the FULL tier set in the briefing. Without this the
    # worker fixes the currently-missing tier but accidentally drops the
    # previously-present one (whack-a-mole observed in cangjie F-924216).
    previously_provided_tiers: list[str] = []
    if isinstance(trigger_event.payload, dict):
        evidence_block = trigger_event.payload.get("evidence")
        if isinstance(evidence_block, dict):
            payload_evidence = evidence_block.get("payload_evidence")
            if isinstance(payload_evidence, dict):
                prior_checks = payload_evidence.get("checks") or []
                if isinstance(prior_checks, list):
                    for chk in prior_checks:
                        if not isinstance(chk, dict):
                            continue
                        tier = str(chk.get("tier") or "").strip()
                        if tier and tier not in previously_provided_tiers:
                            previously_provided_tiers.append(tier)

    # 2026-05-15 r5 discovery: the previous renderer produced a schema
    # the hardening gate REJECTS:
    #   - emitted `score` (singular) → gate reads `evidence.scores` (plural)
    #   - emitted `verification_tiers` as top-level dict-keyed-by-tier → gate
    #     reads tier coverage from items in `checks`/`commands` list, each
    #     with its own `tier` field
    # So the renderer below now mirrors the EXACT keys
    # zf/core/verification/evidence.py:_payload_{checks,scores,refs} accept.
    shape: dict[str, object] = {}
    bullets: list[str] = []
    needed_checks: list[str] = []  # tiers that must appear in `checks`
    for item in missing:
        low = item.lower()
        if "score dimension" in low or "score dimensions" in low:
            dims = [
                "completeness",
                "correctness",
                "evidence_quality",
                "regression_risk",
            ]
            # Extract any explicit dim names the gate listed.
            tail = item.split(":", 1)[-1]
            for d in [s.strip() for s in tail.split(",")]:
                if d and d.isascii() and d.replace("_", "").isalnum() and d not in dims:
                    dims.append(d)
            evidence_block = shape.setdefault("evidence", {})
            if isinstance(evidence_block, dict):
                evidence_block["scores"] = {d: "<float 0..1>" for d in dims}
            bullets.append(
                f"`evidence.scores`: object with dimensions {', '.join(dims)} "
                "(each `<float 0..1>`). Gate accepts the plural key `scores` "
                "or `scorecard`; SINGULAR `score` is NOT accepted."
            )
        elif "verification tier" in low or "verification_tier" in low:
            tail = item.split(":", 1)[-1].strip().lower()
            # gate lists missing tiers comma-separated, e.g. "runtime, static"
            for t in [s.strip(",.;") for s in tail.split(",")]:
                t = t.strip()
                if t and t not in needed_checks:
                    needed_checks.append(t)
        elif (
            "passing command/check evidence" in low
            or "passing command" in low
            or "check evidence" in low
        ):
            # gate needs at least one `check` with passed:true; renderer
            # adds a default `runtime` tier check if no tier already named.
            if not needed_checks:
                needed_checks.append("runtime")
        elif "artifact_refs" in low or "artifact refs" in low:
            shape["artifact_refs"] = ["<repo-relative path or event_id>"]
            bullets.append("`artifact_refs`: list of repo-relative paths or event ids")
        elif "evidence_refs" in low or "evidence refs" in low:
            shape["evidence_refs"] = ["<repo-relative path or event_id>"]
            bullets.append("`evidence_refs`: list of repo-relative paths or event ids")
        elif "summary" in low:
            shape["summary"] = "<concise zh-CN summary>"
            bullets.append("`summary`: concise zh-CN summary string")
        else:
            bullets.append(f"unmapped missing field: `{item}` — escalate to operator")

    # B-NEW-2: merge previously-provided tiers with the now-missing tiers.
    # The renderer must show the FULL list so the worker doesn't drop tiers
    # it already provided. Order: previously-provided first (familiar shape),
    # then newly-missing tiers appended.
    full_tier_set: list[str] = []
    for tier in previously_provided_tiers:
        if tier not in full_tier_set:
            full_tier_set.append(tier)
    for tier in needed_checks:
        if tier not in full_tier_set:
            full_tier_set.append(tier)

    if full_tier_set:
        evidence_block = shape.setdefault("evidence", {})
        if isinstance(evidence_block, dict):
            evidence_block["checks"] = [
                {
                    "command": f"<verification command for {tier} tier>",
                    "exit_code": 0,
                    "passed": True,
                    "tier": tier,
                    "result": "<command output snippet>",
                }
                for tier in full_tier_set
            ]
        tiers_str = ", ".join(full_tier_set)
        missing_only_str = ", ".join(needed_checks) if needed_checks else ""
        previously_str = (
            ", ".join(previously_provided_tiers)
            if previously_provided_tiers else ""
        )
        bullet_parts = [
            "`evidence.checks`: LIST of per-tier check objects. Each item MUST "
            "be a dict with `command` (non-empty string), `passed: true` "
            "(OR `exit_code: 0`), and `tier` matching one of "
            f"`{tiers_str}`. **You MUST include ALL of these tiers** — the "
            "gate rejects payloads that drop tiers the contract requires."
        ]
        if previously_str and missing_only_str:
            bullet_parts.append(
                f"  → Your previous attempt already provided tier(s) "
                f"`{previously_str}`. KEEP those entries in the new payload "
                f"and ADD entries for the still-missing tier(s) "
                f"`{missing_only_str}`. Do NOT replace; ADD."
            )
        bullet_parts.append(
            "The gate uses isinstance(list) — a single dict will be silently "
            "dropped. `commands` and `command_evidence` are accepted aliases "
            "for `checks`."
        )
        bullets.append("\n".join(bullet_parts))

    if not shape and not bullets:
        return ""

    shape_json = json.dumps(shape, ensure_ascii=False, indent=2)
    bullet_md = "\n".join(f"- {b}" for b in bullets)
    return (
        "\n## Required Payload Shape\n"
        "ZaoFu terminal_done_hardening gate requires the next "
        f"`{success_event_type}` event payload to include the following "
        "fields. Use exactly these keys; values shown below are placeholders.\n\n"
        "```json\n"
        f"{shape_json}\n"
        "```\n\n"
        "Per-field expectations:\n"
        f"{bullet_md}\n"
    )


def _resolved_role_kind(role: RoleConfig) -> str:
    if role.role_kind != "auto":
        return role.role_kind
    if role.name in {"review", "test", "judge", "verify", "critic"}:
        return "reader"
    return "writer"


def _is_writer_role(role: RoleConfig) -> bool:
    return _resolved_role_kind(role) == "writer"


def _role_matches_rework_candidate(role: RoleConfig, candidate: str) -> bool:
    if not candidate:
        return False
    if role.name == candidate or role.instance_id == candidate:
        return True
    if candidate == "dev" and _is_writer_role(role):
        return True
    if candidate == "writer" and _is_writer_role(role):
        return True
    return False


def _capture_head(project_root) -> str:
    try:
        return capture_git_state(project_root).head or ""
    except Exception:
        return ""


def _git_rev_parse(project_root, ref: str) -> str:
    ref = str(ref or "").strip()
    if not ref:
        return ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", ref],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""


def _git_merge_base(project_root, ref_a: str, ref_b: str) -> str:
    ref_a = str(ref_a or "").strip()
    ref_b = str(ref_b or "").strip()
    if not ref_a or not ref_b:
        return ""
    try:
        result = subprocess.run(
            ["git", "merge-base", ref_a, ref_b],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""


def _git_evidence_section(project_root, base_sha: str) -> str:
    try:
        context = capture_git_diff_context(project_root, base_sha=base_sha)
    except Exception:
        return ""
    return "\n\n## Git Evidence Context\n" + render_git_diff_context(context)


def _state_dir_display_ref(state_dir: Path, project_root: Path) -> str:
    try:
        return str(state_dir.resolve(strict=False).relative_to(project_root.resolve(strict=False)))
    except Exception:
        return str(state_dir)


def _safe_artifact_segment(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    chars: list[str] = []
    for char in text:
        if char.isascii() and (char.isalnum() or char in {"-", "_", "."}):
            chars.append(char)
        else:
            chars.append("-")
    segment = "".join(chars).strip(".-")
    if not segment or segment in {".", ".."}:
        segment = fallback
    return segment[:96]


from zf.runtime.dispatch_evidence_queries import (
    DispatchEvidenceQueriesMixin,
)


from zf.runtime.dispatch_routing_queries import (
    DispatchRoutingQueriesMixin,
)


class DispatchMixin(
    DispatchEvidenceQueriesMixin,
    DispatchRoutingQueriesMixin,
):
    """Dispatch methods of Orchestrator. Mixin contract: relies on host
    Orchestrator's instance fields. Do not instantiate standalone."""

    # PREREQ-B (2026-05-18, doc 40 §6 I57): these 3 frozensets previously
    # were 3 separate hardcoded literals here. They now derive from
    # WorkflowEventSets.baseline() — a single source. To add a new
    # pipeline event, edit WorkflowEventSets.baseline() in
    # zf.core.workflow.topology and run `zf validate --cold-start` which
    # cross-checks topology drift. The class attribute pattern is
    # preserved so existing `self._HANDOFF_SUCCESS_EVENTS` call sites
    # keep working unchanged.
    _HANDOFF_SUCCESS_EVENTS: frozenset[str] = (
        _WORKFLOW_EVENT_SETS.handoff_success_events
    )
    _STAGE_PROGRESS_EVENTS: frozenset[str] = (
        _WORKFLOW_EVENT_SETS.stage_progress_events
    )
    _REWORK_TRIGGER_EVENTS: frozenset[str] = (
        _WORKFLOW_EVENT_SETS.rework_trigger_events
    )

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

    def _assign_ready_backlog_task(self, task: Task) -> Task:
        if task.assigned_to or not self._contract_ready_for_backlog_scheduler(task):
            return task
        if self._backlog_scheduler_should_yield_to_handoff(task):
            return task
        target = self._initial_role_for_ready_task(task)
        if not target:
            return task
        updated = self.task_store.update(task.id, assigned_to=target)
        try:
            self.event_writer.append(ZfEvent(
                type="task.assigned",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "role": target,
                    "assignee": target,
                    "source": "feature_backlog_scheduler",
                },
            ))
        except Exception:
            pass
        return updated or task

    def _backlog_scheduler_should_yield_to_handoff(self, task: Task) -> bool:
        """Avoid restarting a progressed task from its initial owner role.

        Graceful stop intentionally requeues in-flight tasks so stale panes do
        not own WIP after restart. If the task already has pipeline progress
        evidence, the pending-handoff reconciler owns the next role; the
        backlog scheduler must not send it back to the contract owner.
        """
        if task.status != "backlog":
            return False
        try:
            from zf.runtime.event_window import read_runtime_events

            events = read_runtime_events(self.event_log, self.state_dir)
        except Exception:
            return False
        for event in reversed(events):
            if event.task_id != task.id:
                continue
            if (
                event.type in self._STAGE_PROGRESS_EVENTS
                or event.type in self._REWORK_TRIGGER_EVENTS
                or event.type
                in {
                    "task.rework.requested",
                    "task.ref.updated",
                    "task.ref.rejected",
                }
            ):
                return True
        return False

    def _emit_contract_preflight_failed(
        self,
        *,
        task: Task,
        role: RoleConfig,
        errors: list[str],
    ) -> bool:
        if self._contract_preflight_already_reported(task, errors):
            return False
        try:
            self.event_writer.append(ZfEvent(
                type="task.contract.invalid",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "source": "dispatch_preflight",
                    "role": role.name,
                    "assignee": role.instance_id,
                    "errors": errors,
                },
            ))
            return True
        except Exception:
            return False

    def _dispatch_ready(self) -> list[OrchestratorDecision]:
        """Dispatch ready tasks to available workers.

        In Layer 2 mode (orchestrator role configured), only tasks that
        Layer 2 has explicitly assigned (task.assigned_to set) are
        dispatched — unassigned tasks wait for Layer 2's next decision.
        In legacy mode, unassigned tasks auto-dispatch to the first
        available non-orchestrator role.

        Protocol notes:
          Run 10: ``task_store.ready()`` only returns *backlog* tasks.
            Briefing forbids Layer 2 from manual ``zf kanban move`` so
            backlog-only is fine for the first hand-off.
          Run 13 (C3): multi-stage flow needs reassignment dispatch.
            After dev-1 emits dev.build.done, Layer 2 reassigns the
            same task to review (still status=in_progress). We pick
            those up by comparing the latest task.assigned.assignee to
            the latest task.dispatched.assignee per task — if they
            differ, the new assignee hasn't received the briefing yet.
        """
        decisions: list[OrchestratorDecision] = []
        layer2_active = self._find_role_by_name("orchestrator") is not None
        candidates = list(self.task_store.ready())

        # R-TASK-STATE-AXIS-01 (2026-04-27): compute latest_dispatched
        # once per cycle and thread it through every WIP check below.
        # Replaces the legacy `assigned_to + status==in_progress` count
        # with the events-derived "actually in flight on this instance"
        # truth — same fix B-REASSIGN-DISPATCH-01 applied locally to
        # the C3 branch, now generalized to all dispatch paths so the
        # gridlock can't reappear elsewhere (e.g. _find_available_role).
        latest_dispatched = self._latest_dispatched_per_task()
        dispatch_limit = self._dispatch_cycle_limit()
        cycle_dispatch_counts: dict[str, int] = {}
        exclusive_reservations = self._exclusive_file_reservations()
        try:
            terminal_run_keys = _terminal_run_success_context_keys(
                self.event_log.read_all()
            )
        except Exception:
            terminal_run_keys = set()

        # C3: in_progress tasks where assignee was rotated and the new
        # assignee hasn't been dispatched.
        reassigned = {
            task_id for task_id in self._reassigned_pending_dispatch()
            if not self._task_has_active_writer_fanout_dispatch(task_id)
        }
        seen_ids = {c.id for c in candidates}
        for tid in reassigned:
            t = self.task_store.get(tid)
            if (
                t is not None
                and t.status not in {"done", "cancelled", "blocked"}
                and t.id not in seen_ids
            ):
                if t.status == "backlog":
                    continue
                candidates.append(t)
                seen_ids.add(t.id)

        if self._dispatch_globally_paused():
            for task in candidates:
                if task.status not in {"done", "cancelled", "blocked"}:
                    self._emit_dispatch_skipped(
                        task=task,
                        role=None,
                        reason="dispatch_paused",
                    )
            return decisions

        for task in candidates:
            if task.status in {"done", "cancelled", "blocked"}:
                continue
            if _task_quiesced_by_terminal_run(task, terminal_run_keys):
                continue
            # G2 remains the default: unassigned Layer 2 tasks wait for
            # the orchestrator. The feature backlog scheduler is the
            # explicit exception for contract-ready work produced by task
            # decomposition; it assigns the first topology entry role so
            # the replica pools can be filled deterministically.
            auto_assignment_source = ""
            if layer2_active and not task.assigned_to:
                if (
                    self._contract_ready_for_backlog_scheduler(task)
                    and not self._backlog_scheduler_should_yield_to_handoff(task)
                ):
                    auto_assignment_source = "feature_backlog_scheduler"
                else:
                    continue

            # C3: for reassigned in_progress tasks, bypass WIP — the
            # task itself counts as the role's only in-flight item, so
            # the standard WIP check would always reject. The task is
            # already reserved for this assignee by Layer 2.
            #
            # B-M30-01 (2026-04-21 multi-task baseline): only bypass WIP
            # when the task is *already* in_progress. First-dispatch
            # tasks (status=backlog with a task.assigned event) also
            # land in ``reassigned`` because no task.dispatched has
            # been written yet, but they must honor WIP=1 — otherwise
            # Layer 2's burst of N ``zf kanban assign`` calls each
            # trigger a dispatch and the tmux pane gets flooded with
            # N briefings back-to-back, only the first of which the
            # worker actually executes.
            reassigned_in_flight = (
                task.id in reassigned and task.status == "in_progress"
            )
            if reassigned_in_flight:
                role = self._find_role_by_instance(
                    task.assigned_to,
                    latest_dispatched=latest_dispatched,
                    route_role_name_pool=True,
                )
                if role is None or role.name == "orchestrator":
                    self._emit_dispatch_skipped(
                        task=task,
                        role=role if role and role.name != "orchestrator" else None,
                        reason="reassign_role_unresolved",
                    )
                    continue
                if not self._worker_dispatchable(role.instance_id):
                    self._emit_dispatch_skipped(
                        task=task,
                        role=role,
                        reason="worker_not_dispatchable",
                    )
                    continue
                # B-M30-01 v2 (2026-04-21 post-fix real run): C3 bypass
                # must still respect WIP when the new assignee already
                # has ANOTHER in-flight task. The original bypass was
                # narrowly for "this task is the one-and-only in-flight
                # item the standard WIP check would mis-count". When a
                # second task arrives at a busy worker (3-module mixed
                # baseline: arch finishes T-B while dev is still on T-A),
                # bypass was flooding the pane. Fix: exclude THIS task
                # from the active set, then check `< limit` like normal.
                #
                # B-REASSIGN-DISPATCH-01 (2026-04-23): "active" must be
                # measured by *dispatched-and-still-here*, not merely by
                # ``assigned_to``. Two tasks reassigned to the same
                # single-replica role in the same dispatcher round both
                # have assigned_to=<role>, but *neither* has been
                # dispatched yet — the old check saw them as active
                # peers and gridlocked (pressure run 2026-04-23 10:37:
                # 5 tasks reassigned to review, 0 review dispatches,
                # pipeline stalled). Use latest task.dispatched + the
                # reassigned set to count only truly in-flight peers.
                # latest_dispatched is now hoisted to the top of this
                # method (R-TASK-STATE-AXIS-01) and reused everywhere.
                active_others = [
                    t for t in self.task_store.list_all()
                    if t.id != task.id
                    and t.status == "in_progress"
                    and self._assignee_equivalent(
                        latest_dispatched.get(t.id, ""), role.instance_id,
                    )
                    and t.id not in reassigned
                ]
                if len(active_others) >= self.wip.limit:
                    self._emit_dispatch_skipped(
                        task=task,
                        role=role,
                        reason="wip_busy_reassign_branch",
                    )
                    continue  # worker still busy — wait for next cycle
            else:
                role = self._find_available_role(
                    task, latest_dispatched=latest_dispatched,
                )
                if role is None:
                    self._emit_dispatch_skipped(
                        task=task,
                        role=None,
                        reason="no_available_role",
                    )
                    continue

            if cycle_dispatch_counts.get(role.instance_id, 0) >= self.wip.limit:
                self._emit_dispatch_skipped(
                    task=task,
                    role=role,
                    reason="cycle_wip_exhausted",
                )
                continue

            preflight_errors = self._strict_contract_preflight_errors(task, role)
            if preflight_errors:
                emitted = self._emit_contract_preflight_failed(
                    task=task,
                    role=role,
                    errors=preflight_errors,
                )
                if emitted:
                    self._emit_dispatch_skipped(
                        task=task,
                        role=role,
                        reason="strict_contract_preflight_failed",
                    )
                continue

            schedule_blocker = self._contract_schedule_blocker(
                task,
                exclusive_reservations=exclusive_reservations,
            )
            if schedule_blocker:
                self._emit_dispatch_skipped(
                    task=task,
                    role=role,
                    reason=schedule_blocker,
                )
                continue

            # G-COST-BLOCK-1: hard budget check before dispatch.
            if self._budget_exceeded(role):
                self._emit_dispatch_skipped(
                    task=task,
                    role=role,
                    reason="budget_exceeded",
                )
                continue

            # LH-4.T3: circuit breaker — if recent failures on this
            # (role, task) pair tripped the breaker, skip dispatch and
            # emit a circuit.tripped marker instead of burning a turn.
            breaker = self._circuit_for(role, task)
            if not breaker.can_proceed():
                self._emit_circuit_tripped(role, task, breaker)
                continue

            if not self._dispatch_task(
                task,
                role,
                assignment_source=auto_assignment_source,
            ):
                continue
            latest_dispatched[task.id] = role.instance_id
            cycle_dispatch_counts[role.instance_id] = (
                cycle_dispatch_counts.get(role.instance_id, 0) + 1
            )
            for path in self._task_exclusive_files(task):
                exclusive_reservations.setdefault(path, task.id)

            decisions.append(OrchestratorDecision(
                action="dispatch",
                task_id=task.id,
                role=role.instance_id,
                reason=f"ready task dispatched to {role.instance_id}",
            ))
            if len(decisions) >= dispatch_limit:
                break

        return decisions

    def _reconcile_pending_handoffs(self) -> list[OrchestratorDecision]:
        """Repair missed mechanical handoffs in Layer 2 mode.

        Layer 2 still owns semantic decisions such as task decomposition,
        rejection reason, and rework target. Once a worker has emitted a
        successful stage event, though, the next worker role is fully
        determined by zf.yaml ``role.triggers``. This sweep prevents bursty
        events from stranding older completions when the orchestrator agent
        only reacts to the latest wake.
        """
        if self._find_role_by_name("orchestrator") is None:
            return []
        try:
            from zf.runtime.event_window import read_runtime_events

            events = read_runtime_events(self.event_log, self.state_dir)
        except Exception:
            return []
        if not events:
            return []
        graph_resync = getattr(self, "_workflow_graph_resync_reconcile", None)
        if callable(graph_resync):
            graph_decisions = graph_resync(events)
            if graph_decisions:
                return graph_decisions

        latest_progress: dict[str, tuple[int, ZfEvent]] = {}
        latest_assigned: dict[str, tuple[int, str]] = {}
        latest_dispatched: dict[str, tuple[int, str]] = {}
        latest_dispatch_failed: dict[str, int] = {}
        latest_orphaned: dict[str, int] = {}
        latest_ref_updated: dict[str, int] = {}
        latest_ref_rejected: dict[str, tuple[int, ZfEvent]] = {}
        latest_task_ref_repair_requested: dict[str, tuple[int, ZfEvent]] = {}
        latest_rework_requested: dict[str, int] = {}
        rework_trigger_ordinals: dict[str, int] = {}
        rework_counts_by_task: dict[str, int] = {}
        ref_status_by_trigger: dict[str, str] = {}
        seen_success_dispatches: set[tuple[str, str, str]] = set()
        fanout_current_cache: dict[str, bool] = {}
        for idx, event in enumerate(events):
            tid = event.task_id
            if not tid:
                continue
            if event.type in self._STAGE_PROGRESS_EVENTS and (
                not self._fanout_progress_event_is_current(
                    event,
                    events,
                    cache=fanout_current_cache,
                )
            ):
                continue
            if (
                event.type in self._REWORK_TRIGGER_EVENTS
                and self._fanout_scoped_stage_progress_event(event)
            ):
                continue
            if event.type in self._REWORK_TRIGGER_EVENTS:
                count = rework_counts_by_task.get(tid, 0) + 1
                rework_counts_by_task[tid] = count
                rework_trigger_ordinals[event.id] = count
            if event.type == "task.ref.repair.requested":
                latest_task_ref_repair_requested[tid] = (idx, event)
            if event.type in self._STAGE_PROGRESS_EVENTS:
                is_handoff_success = self._is_handoff_success_event(event)
                if (
                    is_handoff_success
                    and not self._progress_event_matches_active_dispatch_at(
                        events,
                        idx,
                        event,
                    )
                ):
                    continue
                if is_handoff_success:
                    dispatch_id = ""
                    if isinstance(event.payload, dict):
                        dispatch_id = str(event.payload.get("dispatch_id") or "")
                    if dispatch_id:
                        key = (tid, event.type, dispatch_id)
                        try:
                            terminal_success = (
                                not self._non_orchestrator_subscribers_for_event(
                                    event
                                )
                            )
                        except Exception:
                            terminal_success = event.type == "judge.passed"
                        if key in seen_success_dispatches and not terminal_success:
                            continue
                        seen_success_dispatches.add(key)
                latest_progress[tid] = (idx, event)
            if isinstance(event.payload, dict):
                assignee = (
                    event.payload.get("assignee")
                    or event.payload.get("role")
                    or ""
                )
                if assignee and event.type == "task.assigned":
                    latest_assigned[tid] = (idx, assignee)
                elif assignee and event.type == "task.dispatched":
                    latest_dispatched[tid] = (idx, assignee)
                if event.type == "orchestrator.dispatch_failed":
                    latest_dispatch_failed[tid] = idx
                if event.type == "task.orphaned":
                    latest_orphaned[tid] = idx
                if event.type in {"task.ref.rejected", "task.ref.updated"}:
                    trigger_event_id = str(event.payload.get("trigger_event_id") or "")
                    if trigger_event_id:
                        ref_status_by_trigger[trigger_event_id] = event.type
                        if event.type == "task.ref.updated":
                            latest_ref_updated[trigger_event_id] = idx
                        elif event.type == "task.ref.rejected":
                            latest_ref_rejected[tid] = (idx, event)
                if event.type == "task.rework.requested":
                    trigger_event_id = str(event.payload.get("trigger_event_id") or "")
                    if trigger_event_id:
                        latest_rework_requested[trigger_event_id] = idx

        decisions: list[OrchestratorDecision] = []
        for task in self.task_store.list_all():
            progress = latest_progress.get(task.id)
            late_terminal_success = False
            if progress is not None:
                progress_idx, progress_event = progress
                late_terminal_success = (
                    task.status == "backlog"
                    and latest_orphaned.get(task.id, -1) < progress_idx
                    and self._is_terminal_late_success(progress_event, task)
                )
            if task.status in {"done", "cancelled", "blocked"}:
                continue
            repair = latest_task_ref_repair_requested.get(task.id)
            rejection = latest_ref_rejected.get(task.id)
            if rejection is not None and task.status != "backlog":
                rejection_idx, rejection_event = rejection
                repair_idx = repair[0] if repair is not None else -1
                assigned_after_rejection = (
                    latest_assigned.get(task.id, (-1, ""))[0] > rejection_idx
                )
                dispatched_after_rejection = (
                    latest_dispatched.get(task.id, (-1, ""))[0] > rejection_idx
                )
                payload = (
                    rejection_event.payload
                    if isinstance(rejection_event.payload, dict)
                    else {}
                )
                trigger_event_id = str(payload.get("trigger_event_id") or "")
                ref_updated_after_rejection = bool(trigger_event_id) and (
                    latest_ref_updated.get(trigger_event_id, -1) > rejection_idx
                )
                rework_after_rejection = bool(trigger_event_id) and (
                    latest_rework_requested.get(trigger_event_id, -1)
                    > rejection_idx
                )
                if (
                    repair_idx < rejection_idx
                    and not assigned_after_rejection
                    and not dispatched_after_rejection
                    and not ref_updated_after_rejection
                    and not rework_after_rejection
                ):
                    repair_event = self._emit_task_ref_repair_requested(
                        task,
                        rejection_event,
                    )
                    if repair_event is not None:
                        repair = (len(events), repair_event)
            if repair is not None and task.status != "backlog":
                repair_idx, repair_event = repair
                assigned_after_repair = (
                    latest_assigned.get(task.id, (-1, ""))[0] > repair_idx
                )
                dispatched_after_repair = (
                    latest_dispatched.get(task.id, (-1, ""))[0] > repair_idx
                )
                payload = (
                    repair_event.payload
                    if isinstance(repair_event.payload, dict)
                    else {}
                )
                source_event_id = str(payload.get("source_event_id") or "")
                ref_updated_after_repair = bool(source_event_id) and (
                    latest_ref_updated.get(source_event_id, -1) > repair_idx
                )
                if not (
                    assigned_after_repair
                    or dispatched_after_repair
                    or ref_updated_after_repair
                ):
                    dispatched_role = self._dispatch_rework(task, repair_event)
                    if dispatched_role is None:
                        wait_reason = self._rework_defer_reason(
                            task,
                            repair_event,
                        )
                        if wait_reason:
                            decisions.append(OrchestratorDecision(
                                action="wait",
                                task_id=task.id,
                                reason=(
                                    "task.ref.repair.requested: rework "
                                    f"deferred ({wait_reason})"
                                ),
                            ))
                        else:
                            decisions.append(OrchestratorDecision(
                                action="block",
                                task_id=task.id,
                                reason=(
                                    "task.ref.repair.requested: rework "
                                    "unavailable or capped"
                                ),
                            ))
                    else:
                        decisions.append(OrchestratorDecision(
                            action="dispatch",
                            task_id=task.id,
                            role=dispatched_role,
                            reason=(
                                "task.ref.repair.requested → rework "
                                "(pending handoff reconcile)"
                            ),
                        ))
                    continue
            if progress is None:
                continue
            if task.status == "backlog" and not late_terminal_success:
                continue
            progress_idx, progress_event = progress
            # If this process already delivered the progress event to Layer 2
            # in the current/recent cycle, do not immediately override the
            # decision boundary. Reconciliation is for older stranded handoffs.
            handoff_failed_after_progress = (
                latest_dispatch_failed.get(task.id, -1) > progress_idx
            )
            ref_updated_after_progress = (
                latest_ref_updated.get(progress_event.id, -1) > progress_idx
            )
            # B-NEW-1 (backlogs/2026-05-16-0052-zaofu-kernel-edge-bugs-from-cangjie-p5-validation.md):
            # the previous version skipped when `processed_event_ids` contained
            # the event, regardless of whether a real dispatch followed. But
            # orchestrator (Layer 2 LLM) emits `orchestrator.idle` for events
            # whose next role it thinks "kernel auto-routes". That marks the
            # event as processed without producing a task.assigned/task.dispatched.
            # The reconciler then SKIPS and the handoff strands.
            #
            # Cangjie F-924216 observed: test.passed at 15:26:47 → orchestrator.idle
            # at 15:27:24 → kernel reconciler skipped → 7 minutes of dead air until
            # manual `zf kanban assign judge`.
            #
            # Fix: only consider the handoff "delivered" if a subsequent
            # task.assigned/dispatched DID actually fire. If processed but
            # no dispatch followed, fall through and dispatch via reconcile.
            assigned_after_progress = (
                latest_assigned.get(task.id, (-1, ""))[0] > progress_idx
            )
            dispatched_after_progress = (
                latest_dispatched.get(task.id, (-1, ""))[0] > progress_idx
            )
            if (
                progress_event.id in self._processed_event_ids
                and (assigned_after_progress or dispatched_after_progress)
                and not handoff_failed_after_progress
                and not ref_updated_after_progress
            ):
                continue
            if ref_status_by_trigger.get(progress_event.id) == "task.ref.rejected":
                continue
            if progress_event.type in self._REWORK_TRIGGER_EVENTS:
                assigned_after_progress = (
                    latest_assigned.get(task.id, (-1, ""))[0] > progress_idx
                )
                dispatched_after_progress = (
                    latest_dispatched.get(task.id, (-1, ""))[0] > progress_idx
                )
                rework_after_progress = (
                    latest_rework_requested.get(progress_event.id, -1)
                    > progress_idx
                )
                if (
                    assigned_after_progress
                    or dispatched_after_progress
                    or rework_after_progress
                ):
                    continue
                current = self.task_store.get(task.id) or task
                expected_retry = rework_trigger_ordinals.get(progress_event.id, 0)
                if getattr(current, "retry_count", 0) < expected_retry:
                    self._apply_housekeeping(progress_event)
                    current = self.task_store.get(task.id) or current
                self._processed_event_ids.add(progress_event.id)
                self._settle_progress_actor(progress_event, task.id)
                decisions.append(self._route_rework_trigger(
                    current,
                    progress_event,
                    reason=(
                        f"{progress_event.type} → rework "
                        "(pending handoff reconcile)"
                    ),
                ))
                continue
            if not self._is_handoff_success_event(progress_event):
                continue
            if self._requires_task_ref_for_progress_event(progress_event):
                task_ref_entry = self._task_ref_entry(task.id)
                task_ref_trigger = ""
                if isinstance(task_ref_entry, dict):
                    task_ref_trigger = str(
                        task_ref_entry.get("trigger_event_id") or ""
                    )
                ref_status = ref_status_by_trigger.get(progress_event.id)
                ref_is_current = bool(task_ref_entry) and (
                    task_ref_trigger == progress_event.id
                    or ref_status == "task.ref.updated"
                )
                if not ref_is_current and ref_status not in {
                    "task.ref.updated",
                    "task.ref.rejected",
                }:
                    result = self._process_task_ref_for_progress_event(
                        progress_event
                    )
                    if result is not None and result.status in {
                        "updated",
                        "rejected",
                    }:
                        result_payload = dict(result.payload)
                        result_payload.setdefault(
                            "source",
                            "pending_handoff_reconcile",
                        )
                        event_type = (
                            "task.ref.updated"
                            if result.status == "updated"
                            else "task.ref.rejected"
                        )
                        self.event_writer.append(ZfEvent(
                            type=event_type,
                            actor="zf-cli",
                            task_id=task.id,
                            payload=result_payload,
                            causation_id=progress_event.id,
                            correlation_id=progress_event.correlation_id,
                        ))
                        ref_status_by_trigger[progress_event.id] = event_type
                        ref_status = event_type
                        if event_type == "task.ref.updated":
                            task_ref_entry = result_payload
                            task_ref_trigger = str(
                                task_ref_entry.get("trigger_event_id") or ""
                            )
                            ref_is_current = task_ref_trigger == progress_event.id
                if not ref_is_current:
                    if ref_status != "task.ref.rejected":
                        reason = (
                            f"missing task ref after {progress_event.type} "
                            "in worktree mode"
                        )
                        if task_ref_entry:
                            reason = (
                                f"stale task ref after {progress_event.type} "
                                "in worktree mode"
                            )
                        self.event_writer.append(ZfEvent(
                            type="task.ref.rejected",
                            actor="zf-cli",
                            task_id=task.id,
                            payload={
                                "task_id": task.id,
                                "trigger_event_id": progress_event.id,
                                "reason": reason,
                                "source": "pending_handoff_reconcile",
                            },
                            causation_id=progress_event.id,
                            correlation_id=progress_event.correlation_id,
                        ))
                        ref_status_by_trigger[progress_event.id] = (
                            "task.ref.rejected"
                        )
                        ref_status = "task.ref.rejected"
                        # 2026-05-15 r-next: cangjie dev-4 死循环 in worktree
                        # mode because each fresh dispatch produced a fresh
                        # ref.rejected without ever tripping a cap. Hook into
                        # the same dispatch_failure counter so the existing
                        # cooldown gate (_dispatch_recent_failure_cooldown_active)
                        # park the task after N rejected in the window.
                        self._record_dispatch_failure(task.id)
                    decisions.append(OrchestratorDecision(
                        action="block",
                        task_id=task.id,
                        reason=(
                            f"{progress_event.type} missing current task ref "
                            "(pending handoff reconcile)"
                        ),
                    ))
                    continue
                self._reconcile_writer_fanout_child_completion(progress_event)
            else:
                self._reconcile_writer_fanout_child_completion(progress_event)
            self._settle_progress_actor(progress_event, task.id)

            next_roles = self._non_orchestrator_subscribers_for_event(
                progress_event
            )
            if not next_roles:
                if progress_event.type not in TERMINAL_SUCCESS_EVENTS:
                    continue
                if not self._evaluate_terminal_done(progress_event, task):
                    decisions.append(OrchestratorDecision(
                        action="block",
                        task_id=task.id,
                        reason=(
                            f"{progress_event.type} terminal evidence blocked "
                            "(pending handoff reconcile)"
                        ),
                    ))
                    continue
                if not self._move_task(
                    task.id, "done", trigger_event=progress_event.type,
                ):
                    continue
                decisions.append(OrchestratorDecision(
                    action="move",
                    task_id=task.id,
                    reason=(
                        f"{progress_event.type} terminal → done "
                        "(pending handoff reconcile)"
                    ),
                ))
                continue

            if len(next_roles) != 1:
                continue
            target = next_roles[0]
            if self._handoff_already_recorded(
                task_id=task.id,
                target_role=target,
                progress_idx=progress_idx,
                latest_assigned=latest_assigned,
                latest_dispatched=latest_dispatched,
            ):
                continue

            self.task_store.update(task.id, assigned_to=target)
            self.event_writer.append(ZfEvent(
                type="task.assigned",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "role": target,
                    "assignee": target,
                    "source": "pending_handoff_reconcile",
                    "trigger_event": progress_event.type,
                    "effective_trigger_event": self._effective_handoff_event_type(
                        progress_event
                    ),
                },
            ))
            decisions.append(OrchestratorDecision(
                action="assign",
                task_id=task.id,
                role=target,
                reason=(
                    f"{progress_event.type} → {target} "
                    "(pending handoff reconcile)"
                ),
            ))
        return decisions

    def _emit_task_ref_repair_requested(
        self,
        task: Task,
        rejection_event: ZfEvent,
    ) -> ZfEvent | None:
        payload = rejection_event.payload if isinstance(rejection_event.payload, dict) else {}
        trigger_event_id = str(payload.get("trigger_event_id") or "")
        reason = str(payload.get("reason") or "task ref rejected").strip()
        dirty_files = self._task_ref_rejection_dirty_files(payload)
        if dirty_files:
            expected_action = "commit_or_revert_dirty_files_and_reemit_handoff"
        elif _task_ref_scope_repair_payload(payload):
            expected_action = "split_or_rebase_source_commit_and_reemit_handoff"
        else:
            expected_action = "repair_task_ref_handoff"
        repair_payload = {
            "task_id": task.id,
            "source_event_id": trigger_event_id,
            "blocking_event_id": rejection_event.id,
            "reason": reason,
            "source_commit": str(payload.get("source_commit") or ""),
            "source_branch": str(payload.get("source_branch") or ""),
            "workdir": str(payload.get("workdir") or ""),
            "dirty_files": dirty_files,
            "expected_action": expected_action,
        }
        for key in ("scope", "changed_files", "out_of_scope_files"):
            value = payload.get(key)
            if isinstance(value, list):
                repair_payload[key] = list(value)
        repair_role = self._resolve_task_ref_repair_role_from_payload(
            task,
            repair_payload,
        )
        if repair_role is not None:
            repair_payload["target_role"] = repair_role.name
            repair_payload["target_assignee"] = repair_role.instance_id
        event = ZfEvent(
            type=TASK_REF_REPAIR_REQUESTED_EVENT,
            actor="zf-cli",
            task_id=task.id,
            payload=repair_payload,
            causation_id=rejection_event.id,
            correlation_id=rejection_event.correlation_id,
        )
        self.event_writer.append(event)
        return event

    def _task_ref_rejection_dirty_files(self, payload: dict[str, object]) -> list[str]:
        files = payload.get("dirty_files")
        if isinstance(files, list):
            return [str(item) for item in files if str(item).strip()]
        workdir = str(payload.get("workdir") or "").strip()
        if not workdir:
            return []
        path = Path(workdir)
        if not path.is_absolute():
            path = self.project_root / path
        try:
            PathGuard.assert_under(path, self.state_dir / "workdirs")
            result = subprocess.run(
                ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=all"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:
            return []
        if result.returncode != 0:
            return []
        out: list[str] = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            path_text = line[3:].strip()
            if " -> " in path_text:
                path_text = path_text.rsplit(" -> ", 1)[-1].strip()
            if path_text and path_text not in out:
                out.append(path_text)
        return out

    def _reconcile_writer_fanout_child_completion(self, event: ZfEvent) -> None:
        source_event = event
        if event.type in {"static_gate.passed", "static_gate.skipped"}:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.type == "static_gate.skipped" and not (
                payload.get("skipped") is True and payload.get("passed") is True
            ):
                return
            trigger_event_id = str(payload.get("trigger_event_id") or "")
            trigger_event_type = str(payload.get("trigger_event_type") or "")
            if trigger_event_type != "dev.build.done" or not trigger_event_id:
                return
            try:
                from zf.runtime.event_window import read_runtime_events

                source_event = next(
                    item for item in reversed(
                        read_runtime_events(self.event_log, self.state_dir)
                    )
                    if item.id == trigger_event_id
                )
            except Exception:
                return
        if source_event.type != "dev.build.done":
            return
        updater = getattr(self, "_maybe_update_writer_fanout", None)
        if callable(updater):
            updater(source_event)

    def _process_task_ref_for_progress_event(self, event: ZfEvent):
        from zf.runtime.task_refs import TaskRefManager

        manager = TaskRefManager(
            state_dir=self.state_dir,
            project_root=self.project_root,
            config=self.config,
        )
        if event.type == "arch.proposal.done":
            return manager.process_arch_proposal_done(event)
        if event.type == "dev.build.done":
            return manager.process_dev_build_done(event)
        return None

    def _progress_event_matches_active_dispatch_at(
        self,
        events: list[ZfEvent],
        progress_idx: int,
        event: ZfEvent,
    ) -> bool:
        """Fail closed for stale handoff reconciliation.

        Reconciliation scans the append-only log, so a worker can emit an old
        success after its dispatch was requeued or superseded. Such an event
        must not mechanically hand the task to the next role.
        """
        try:
            if not self._dispatch_token_required():
                return True
        except Exception:
            return True
        payload = event.payload if isinstance(event.payload, dict) else {}
        actual = str(payload.get("dispatch_id") or "")
        # B-NEW-9 defense-in-depth (2026-05-17): kernel-emitted progress
        # events (actor=zf-cli, e.g. static_gate.passed) historically did
        # not carry dispatch_id in payload. Without this guard, such
        # events would always fail the actual == expected comparison
        # below — even when a matching task.dispatched is the most-recent
        # dispatch and no task.requeued has intervened.
        #
        # Layered fix:
        #   1. static_gate.py now inherits dispatch_id from trigger event
        #      (commit alongside this change).
        #   2. THIS guard, narrowly: if actor is the zf-cli kernel AND
        #      payload has no dispatch_id, accept the event when the
        #      most-recent backward task.{dispatched,requeued} is a
        #      task.dispatched. This prevents future kernel-emitted
        #      progress events from being silently rejected.
        is_kernel_emitted = (event.actor or "") == "zf-cli" and not actual
        for candidate in reversed(events[:progress_idx]):
            if candidate.task_id != event.task_id:
                continue
            if candidate.type == "task.requeued":
                return False
            if candidate.type != "task.dispatched":
                continue
            candidate_payload = (
                candidate.payload if isinstance(candidate.payload, dict) else {}
            )
            expected = str(candidate_payload.get("dispatch_id") or "")
            if not expected:
                return True
            if is_kernel_emitted:
                # Kernel-emitted events without dispatch_id pass when a
                # task.dispatched is the most-recent backward event for
                # this task and no requeue intervened (we already short-
                # circuited on task.requeued above).
                return True
            return actual == expected
        # No dispatch in the retained event window. Keep legacy/late-success
        # recovery behavior; task.active_dispatch_id is checked later for
        # terminal backlog completions.
        return True

    def _fanout_progress_event_is_current(
        self,
        event: ZfEvent,
        events: list[ZfEvent],
        *,
        cache: dict[str, bool] | None = None,
    ) -> bool:
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "")
        if not fanout_id:
            return True
        if cache is not None and fanout_id in cache:
            return cache[fanout_id]
        try:
            from zf.runtime.fanout_identity import fanout_current_status

            current = fanout_current_status(events, fanout_id).current
        except Exception:
            current = True
        if cache is not None:
            cache[fanout_id] = current
        return current

    def _fanout_scoped_stage_progress_event(self, event: ZfEvent) -> bool:
        """Return True when a task event is owned by writer-fanout runtime.

        Writer-fanout child events carry a normal ``task_id`` plus
        ``fanout_id`` and ``child_id`` in the payload. Those events are already
        consumed by the fanout runtime for lane release, queue refill, and child
        retry. Routing the same event through ordinary task rework can
        redispatch the failed task and overwrite the lane fanout just refilled.
        """
        if (
            event.type not in self._STAGE_PROGRESS_EVENTS
            and event.type not in self._REWORK_TRIGGER_EVENTS
        ):
            return False
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "").strip()
        child_id = str(payload.get("child_id") or "").strip()
        return bool(fanout_id and child_id)

    def _settle_progress_actor(self, event: ZfEvent, task_id: str) -> None:
        actor = event.actor or ""
        if not actor:
            return
        instances = {role.instance_id for role in self.config.roles}
        if actor not in instances:
            return
        if event.type == "dev.build.done":
            state = "awaiting_review"
        elif event.type in {
            "review.approved",
            "test.passed",
            "judge.passed",
            *self._REWORK_TRIGGER_EVENTS,
        }:
            state = "idle"
        else:
            return
        try:
            self._set_worker_state(
                actor,
                state,
                reason=(
                    f"{event.type} for task {task_id} "
                    "(pending handoff reconcile)"
                ),
            )
        except Exception:
            pass

    def _non_orchestrator_subscribers(self, event_type: str) -> list[str]:
        """Return unique role.name subscribers for ``event_type``.

        Replica-expanded configs contain multiple RoleConfig entries with
        the same role.name; handoff should assign to the logical role so
        Layer 1 can choose an available instance.
        """
        out: list[str] = []
        for role in self.config.roles:
            if role.name == "orchestrator":
                continue
            if event_type not in role.triggers:
                continue
            if role.name not in out:
                out.append(role.name)
        return out

    def _reassigned_pending_dispatch(self) -> set[str]:
        """C3: tasks whose latest assignment has not been dispatched.

        The common case is ``latest task.assigned.assignee`` differing
        from ``latest task.dispatched.assignee``. Same-assignee assignments
        are also pending when the assignment event is newer than the dispatch
        event; Layer 2 uses that shape for bounded reissues such as terminal
        evidence repair.

        Walks the bounded runtime event window so cross-day lazy rotation does
        not erase yesterday's assignments. The dedup is per task_id: only the
        most recent assigned/dispatched per task counts.

        Empty events.jsonl (e.g. fresh test fixtures) → empty set, so
        unit tests that build kanban state directly aren't surprised
        by spurious dispatches.
        """
        latest_assigned: dict[str, tuple[str, int, dict]] = {}
        latest_dispatched: dict[str, tuple[str, int]] = {}
        try:
            from zf.runtime.event_window import read_runtime_events

            for idx, event in enumerate(
                read_runtime_events(self.event_log, self.state_dir)
            ):
                tid = event.task_id
                if not tid or not isinstance(event.payload, dict):
                    continue
                if event.type == "task.assigned":
                    a = event.payload.get("assignee") or event.payload.get("role")
                    if a:
                        latest_assigned[tid] = (str(a), idx, dict(event.payload))
                elif event.type == "task.dispatched":
                    a = event.payload.get("assignee") or event.payload.get("role")
                    if a:
                        latest_dispatched[tid] = (str(a), idx)
        except Exception:
            return set()
        pending: set[str] = set()
        for tid, (assignee, assigned_idx, payload) in latest_assigned.items():
            dispatched = latest_dispatched.get(tid)
            if dispatched is None:
                pending.add(tid)
                continue
            dispatched_assignee, dispatched_idx = dispatched
            if assigned_idx <= dispatched_idx:
                continue
            if self._assignee_equivalent(assignee, dispatched_assignee):
                if not self._same_assignee_assignment_requests_dispatch(payload):
                    continue
            pending.add(tid)
        return pending

    def _emit_circuit_tripped(
        self, role: RoleConfig, task: Task, breaker: CircuitBreaker,
    ) -> None:
        """LH-4.T3: emit circuit.tripped when breaker refuses dispatch.

        Cooldown: only emit once per (role, task) until the breaker
        moves back to CLOSED / HALF_OPEN — otherwise every cycle would
        spam the log while the breaker is open.
        """
        key = (role.name, task.id)
        last = self._circuit_tripped_last.get(key, 0.0)
        now = time.time()
        if now - last < 60.0:
            return
        self._circuit_tripped_last[key] = now
        try:
            self.event_writer.append(ZfEvent(
                type="circuit.tripped",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "role": role.name,
                    "state": breaker.state().value,
                    "max_failures": breaker.max_failures,
                    "window_seconds": breaker.window_seconds,
                },
            ))
        except Exception:
            pass

    def _emit_cost_block(
        self,
        *,
        scope: str,
        role_name: str,
        budget: float,
        current: float,
        now: float,
    ) -> None:
        """Emit cost.budget.exceeded with per-(scope, role) cooldown."""
        key = (scope, role_name if scope == "role" else "")
        last = self._cost_block_last_emit.get(key, 0.0)
        if now - last < self._cost_block_cooldown_seconds:
            return
        self._cost_block_last_emit[key] = now
        try:
            self.event_writer.append(ZfEvent(
                type="cost.budget.exceeded",
                actor="zf-cli",
                payload={
                    "scope": scope,
                    "role": role_name if scope == "role" else None,
                    "budget_usd": budget,
                    "current_usd": round(current, 4),
                },
            ))
        except Exception:
            pass

    def _emit_dispatch_skipped(
        self,
        *,
        task: Task,
        role: RoleConfig | None,
        reason: str,
    ) -> None:
        """Record why an assigned task did not receive a briefing.

        ``role=None`` is allowed for skips at the "no role found"
        stage (the dispatcher couldn't even resolve a candidate
        instance). In that case ``payload.role`` / ``payload.assignee``
        are empty strings; ``reason`` carries the actual cause.

        2026-05-18 B2 review: caller list widened to cover the
        previously-silent stalls (WIP-busy, worker-not-dispatchable,
        no-available-role, cycle WIP exhausted, assignee-resolve
        failure). B-NEW-6-class bugs ("task.assigned but no
        task.dispatched, no other signal") now self-report root
        cause via the ``reason`` field.
        """
        role_name = role.name if role is not None else ""
        instance_id = role.instance_id if role is not None else ""
        key = (
            task.id,
            instance_id,
            reason,
            task.assigned_to or "",
            task.status,
        )
        cache = getattr(self, "_dispatch_skip_last_emit", None)
        if cache is None:
            cache = {}
            self._dispatch_skip_last_emit = cache
        now = time.time()
        cooldown_s = 30.0
        if now - cache.get(key, 0.0) < cooldown_s:
            return
        cache[key] = now
        counts = getattr(self, "_dispatch_skip_counts", None)
        if counts is None:
            counts = {}
            self._dispatch_skip_counts = counts
        count_key = (task.id, instance_id, reason)
        skip_count = int(counts.get(count_key, 0)) + 1
        counts[count_key] = skip_count
        try:
            self.event_writer.append(ZfEvent(
                type="orchestrator.dispatch_skipped",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "role": role_name,
                    "assignee": instance_id,
                    "reason": reason,
                    "assigned_to": task.assigned_to or "",
                    "status": task.status,
                    "skip_count": skip_count,
                },
            ))
            if skip_count >= 3:
                target_role = role_name or task.assigned_to or ""
                self.event_writer.append(ZfEvent(
                    type="dispatch.blocked",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={
                        "role": role_name,
                        "target_role": target_role,
                        "assignee": instance_id,
                        "reason": reason,
                        "skip_count": skip_count,
                        "severity": "warning",
                        "recommended_action": _dispatch_blocked_recommendation(
                            reason,
                            target_role=target_role,
                        ),
                    },
                ))
        except Exception:
            pass

    # Dispatch retry guards — prevent infinite loops when an upstream
    # phase (writer_workdir_sync, briefing render, etc.) deterministically
    # fails. The orphan path requeues, the dispatcher tries again, and
    # without these caps the loop spins every ~5 seconds.
    _DISPATCH_FAILURE_WINDOW_SECONDS = 60.0
    _DISPATCH_FAILURE_MAX_PER_WINDOW = 3
    _DISPATCH_FAILURE_BACKOFF_SECONDS = 120.0

    def _dispatch_recent_failure_cooldown_active(self, task: Task) -> bool:
        """True when the task has accumulated too many recent dispatch_failed
        events and is inside the backoff window.

        Uses a single in-process counter per task. Resets when a successful
        ``task.dispatched`` is observed for the same task in ``_dispatch_task``
        below. Best-effort across watcher restarts: counters live in memory
        only; persistence via events.jsonl is enough for diagnostics.
        """
        registry = getattr(self, "_dispatch_failure_registry", None)
        if registry is None:
            registry = {}
            self._dispatch_failure_registry = registry
        entry = registry.get(task.id)
        if entry is None:
            return False
        count, window_start, cooldown_until = entry
        now = self._now() if hasattr(self, "_now") else 0.0
        if cooldown_until and now < cooldown_until:
            return True
        if window_start and (now - window_start) > self._DISPATCH_FAILURE_WINDOW_SECONDS:
            registry.pop(task.id, None)
        return False

    def _record_dispatch_failure(self, task_id: str) -> None:
        """Bump the per-task failure counter; arm cooldown after cap."""
        registry = getattr(self, "_dispatch_failure_registry", None)
        if registry is None:
            registry = {}
            self._dispatch_failure_registry = registry
        now = self._now() if hasattr(self, "_now") else 0.0
        entry = registry.get(task_id)
        if entry is None or (
            entry[1] and (now - entry[1]) > self._DISPATCH_FAILURE_WINDOW_SECONDS
        ):
            registry[task_id] = (1, now, 0.0)
            return
        count, window_start, cooldown_until = entry
        count += 1
        if count >= self._DISPATCH_FAILURE_MAX_PER_WINDOW:
            cooldown_until = now + self._DISPATCH_FAILURE_BACKOFF_SECONDS
            try:
                self.event_writer.append(ZfEvent(
                    type="orchestrator.dispatch_cooldown",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "failures_in_window": count,
                        "window_seconds": self._DISPATCH_FAILURE_WINDOW_SECONDS,
                        "cooldown_seconds": self._DISPATCH_FAILURE_BACKOFF_SECONDS,
                    },
                ))
            except Exception:
                pass
        registry[task_id] = (count, window_start, cooldown_until)

    def _clear_dispatch_failure(self, task_id: str) -> None:
        """Reset the failure counter once a dispatch lands successfully."""
        registry = getattr(self, "_dispatch_failure_registry", None)
        if registry is not None:
            registry.pop(task_id, None)

    def _project_state_packet_on_dispatch(
        self,
        *,
        task: Task,
        role: RoleConfig,
        dispatch_id: str,
    ) -> None:
        """ZF-LH-SP-001 + ZF-PWF-MEM-001 + ZF-TR-CTXMAN-001 + ATTEST
        integration: after every successful dispatch, project a State
        Packet snapshot + 4-file working-memory projection + context
        manifest + attestation. Best-effort — never raises, never
        blocks the dispatch path.

        Verify-before-overwrite: if a prior attestation exists for
        the canonical state-packet.json, verify it against the
        on-disk file. Mismatch → emit a task.contract.invalid
        warning event (audit only — does not block dispatch since
        the projector is about to overwrite the file anyway, but
        operators / Agent View will see the tamper detection).
        """
        try:
            from zf.core.security.attestation import (
                ATTESTATION_KIND_STATE_PACKET,
                attest_file_artifact,
                verify_file_artifact,
            )
            from zf.runtime.context_manifest import (
                ContextRef,
                write_context_manifest,
            )
            from zf.runtime.state_packet_projector import StatePacketProjector
            from zf.runtime.working_memory_projection import (
                ProjectionInputs,
                write_projection_files,
            )

            projector = StatePacketProjector(
                state_dir=self.state_dir,
                task_store=getattr(self, "task_store", None),
                feature_store=getattr(self, "feature_store", None),
                event_log=getattr(self, "event_log", None),
            )
            canonical_candidate = (
                self.state_dir / "state" / "state-packet.json"
            )
            # Tamper-detection: if a previous attestation exists,
            # verify the on-disk file before we overwrite it. Audit
            # event surfaces drift to operators / Agent View.
            try:
                if canonical_candidate.exists():
                    prior = verify_file_artifact(
                        self.state_dir,
                        artifact_path=canonical_candidate,
                    )
                    if prior.tampered:
                        self.event_writer.append(ZfEvent(
                            type="task.contract.invalid",
                            actor="zf-cli",
                            task_id=task.id,
                            payload={
                                "reason": "state_packet_hash_mismatch",
                                "expected": prior.expected_sha256,
                                "actual": prior.actual_sha256,
                                "artifact": str(canonical_candidate),
                            },
                        ))
            except Exception:
                pass
            packet = projector.project(task_id=task.id)
            canonical = projector.write(packet, dispatch_id=dispatch_id)
            # 4-file projection
            try:
                write_projection_files(self.state_dir, ProjectionInputs(
                    packet=packet,
                    state_packet_ref=str(canonical),
                    source_event_ids=tuple(),
                ))
            except Exception:
                pass
            # Context manifest stub — list state_packet + briefing as
            # required refs (downstream sprints can populate more).
            try:
                task_doc_ref = (
                    self.state_dir / "task_docs" / task.id / "task.md"
                )
                briefing_path = (
                    self.state_dir / "briefings" / f"{role.instance_id}-{task.id}.md"
                )
                write_context_manifest(
                    state_dir=self.state_dir,
                    task_id=task.id,
                    dispatch_id=dispatch_id,
                    refs=[
                        ContextRef(
                            kind="state_packet",
                            path=str(canonical),
                            required=True,
                            role=role.name,
                        ),
                        ContextRef(
                            kind="task_contract",
                            path=str(task_doc_ref),
                            summary="kernel-managed agent-facing task.md",
                            required=True,
                            role=role.name,
                        ),
                        ContextRef(
                            kind="artifact",
                            path=str(briefing_path),
                            summary="dispatch briefing",
                            required=False,
                            role=role.name,
                        ),
                    ],
                )
                # X15:动作分面 reading surface(implement/check/research/
                # closeout),kernel 写 worker 读;missing required 分级。
                # This path is best-effort dispatch materialization, so even
                # strict/release gaps are emitted as observe-first STOP signals
                # for Supervisor/gate handling instead of pretending this
                # projection helper can synchronously block dispatch.
                from zf.runtime.task_context_manifest import (
                    build_task_context_manifest,
                    missing_required_refs,
                    write_task_context_manifest,
                )
                tcm = build_task_context_manifest(
                    task=task,
                    dispatch_id=dispatch_id,
                    state_dir=self.state_dir,
                    payload=getattr(task, "payload", None) or {},
                )
                write_task_context_manifest(
                    tcm,
                    briefing_dir=(
                        self.state_dir / "briefings" / task.id / dispatch_id
                    ),
                )
                tcm_missing = missing_required_refs(tcm)
                if tcm_missing:
                    profile = str(getattr(
                        self.config.workflow, "harness_profile", "baseline",
                    ))
                    self.event_writer.append(ZfEvent(
                        type="task.context_manifest.gap",
                        actor="zf-cli",
                        task_id=task.id,
                        payload={
                            "dispatch_id": dispatch_id,
                            "missing": tcm_missing[:10],
                            "profile": profile,
                            "mode": "observe_first",
                            "blocking": False,
                            "severity": (
                                "STOP" if profile in ("strict", "release")
                                else "WARN"
                            ),
                        },
                    ))
            except Exception:
                pass
            # Attestation on the State Packet for tamper detection.
            try:
                attest_file_artifact(
                    self.state_dir,
                    artifact_path=canonical,
                    kind=ATTESTATION_KIND_STATE_PACKET,
                    task_id=task.id,
                    dispatch_id=dispatch_id,
                )
            except Exception:
                pass
        except Exception:
            # Defensive: never break dispatch because of projection.
            pass

    def _write_dispatch_runtime_snapshot(
        self,
        *,
        task: Task,
        role: RoleConfig,
        dispatch_id: str,
        briefing_path: Path,
        task_doc: object,
        resume_path: Path | None,
    ):
        """Build/write a dispatch runtime snapshot.

        Snapshot materialization is a projection-only path. Callers must treat
        failures as diagnostics and keep dispatch moving.
        """
        from zf.runtime.runtime_snapshot import (
            RuntimeSnapshotInput,
            build_runtime_snapshot,
            runtime_snapshot_event_payload,
            write_runtime_snapshot,
        )

        capability_snapshot: dict = {}
        try:
            from zf.runtime.provider_capabilities import (
                provider_capability_for_backend,
            )

            capability_snapshot = provider_capability_for_backend(role.backend)
        except Exception:
            capability_snapshot = {}
        per_dispatch_dir = self.state_dir / "briefings" / task.id / dispatch_id
        refs = {
            "state_packet_ref": per_dispatch_dir / "state-packet.json",
            "resume_packet_ref": resume_path or (per_dispatch_dir / "resume-packet.json"),
            "context_manifest_ref": per_dispatch_dir / "context.jsonl",
            "task_doc_ref": getattr(task_doc, "path", ""),
            "source_doc_ref": getattr(task_doc, "source_path", ""),
            "progress_doc_ref": getattr(task_doc, "progress_path", ""),
            "briefing_ref": briefing_path,
        }
        project_id = ""
        try:
            project_id = str(getattr(self.config.project, "name", "") or "")
        except Exception:
            project_id = ""
        snapshot = build_runtime_snapshot(RuntimeSnapshotInput(
            state_dir=self.state_dir,
            project_root=self.project_root,
            project_id=project_id,
            source="dispatch",
            task=task,
            role=role,
            dispatch_id=dispatch_id,
            run_id=self._current_run_id(),
            trace_id=self._trace_id_for_task(task.id),
            capability_snapshot=capability_snapshot,
            refs=refs,
        ))
        result = write_runtime_snapshot(
            snapshot,
            state_dir=self.state_dir,
            project_root=self.project_root,
        )
        return result, runtime_snapshot_event_payload(result)

    def _write_fanout_child_runtime_snapshot(
        self,
        *,
        role: RoleConfig,
        payload: dict,
        briefing_path: Path,
    ):
        from zf.runtime.runtime_snapshot import (
            RuntimeSnapshotInput,
            build_runtime_snapshot,
            runtime_snapshot_event_payload,
            write_runtime_snapshot,
        )

        task = None
        task_id = str(payload.get("task_id") or "")
        if task_id:
            try:
                task = self.task_store.get(task_id)
            except Exception:
                task = None
        fanout_id = str(payload.get("fanout_id") or "")
        child_id = str(payload.get("child_id") or "")
        run_id = str(payload.get("run_id") or "")
        refs = {
            "fanout_manifest_ref": self.state_dir / "fanouts" / fanout_id / "manifest.json",
            "child_briefing_ref": briefing_path,
        }
        if task_id:
            refs.update({
                "task_doc_ref": self.state_dir / "task_docs" / task_id / "task.md",
                "source_doc_ref": self.state_dir / "task_docs" / task_id / "source.md",
                "progress_doc_ref": self.state_dir / "task_docs" / task_id / "progress.md",
            })
        project_id = ""
        try:
            project_id = str(getattr(self.config.project, "name", "") or "")
        except Exception:
            project_id = ""
        snapshot = build_runtime_snapshot(RuntimeSnapshotInput(
            state_dir=self.state_dir,
            project_root=self.project_root,
            project_id=project_id,
            source="fanout_child",
            task=task,
            role=role,
            dispatch_id=run_id,
            run_id=run_id,
            trace_id=str(payload.get("trace_id") or ""),
            fanout_id=fanout_id,
            fanout_child_id=child_id,
            stage_id=str(payload.get("stage_id") or ""),
            refs=refs,
            output_contract={
                "expected_event": str(
                    payload.get("expected_event")
                    or payload.get("child_success_event")
                    or ""
                ),
                "verification_tiers": [],
                "evidence_contract": {},
            },
        ))
        result = write_runtime_snapshot(
            snapshot,
            state_dir=self.state_dir,
            project_root=self.project_root,
        )
        return result, runtime_snapshot_event_payload(result)

    def _dispatch_task(
        self,
        task: Task,
        role: RoleConfig,
        *,
        assignment_source: str = "",
    ) -> bool:
        """Full dispatch sequence: update state → write briefing → inject to agent."""
        # Backoff cap: if this task has accumulated repeated dispatch_failed
        # in a short window, refuse to retry until the cooldown elapses.
        # Prevents the every-5-second infinite retry that occurs when a
        # downstream phase (writer_workdir_sync, briefing render, etc.)
        # keeps failing deterministically.
        if self._dispatch_recent_failure_cooldown_active(task):
            return False

        if self._split_quality_blocks_dispatch(task, role):
            return False

        # ω-1.a (2026-05-18): kernel takes ownership of task ref baseline
        # sync. Before any role gets dispatched, fast-forward the task
        # branch onto main HEAD when safe. This is the audit doc 37
        # Class A1 fix — replaces the prior "LLM agent will git rebase
        # if it feels like it" path that produced the r-next-10
        # baseline-drift reject loop. See docs/design/38 §3 + backlog
        # backlogs/2026-05-18-0243-omega-1a-kernel-fast-forward-task-ref.md.
        try:
            from zf.runtime.baseline_sync import (
                fast_forward_task_ref_onto_main,
            )
            git_cfg = getattr(self.config.runtime, "git", None)
            main_ref = (
                getattr(git_cfg, "candidate_base_ref", "main") if git_cfg else "main"
            ) or "main"
            task_ref_prefix = (
                getattr(git_cfg, "task_ref_prefix", "task") if git_cfg else "task"
            ) or "task"
            sync = fast_forward_task_ref_onto_main(
                self.project_root,
                task_id=task.id,
                main_ref=main_ref,
                task_ref_prefix=task_ref_prefix,
            )
            if sync.ok:
                self.event_writer.append(ZfEvent(
                    type="task.baseline_synced",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={**sync.to_payload(), "source": "pre_dispatch"},
                ))
            elif sync.diverged:
                self.event_writer.append(ZfEvent(
                    type="task.baseline_diverged",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={**sync.to_payload(), "source": "pre_dispatch"},
                ))
            # sync.no_op → quietly continue (first dispatch / already at main /
            # transient git glitch)
        except Exception:
            # Defensive: baseline sync is opportunistic. Any failure
            # must not break the dispatch loop. r-next-10 evidence:
            # production must keep moving even when this experimental
            # path errors.
            pass

        dispatch_id = _new_dispatch_id()
        writer_sync = self._sync_writer_workdir_to_dispatch_base(
            role=role,
            task=task,
            dispatch_id=dispatch_id,
        )
        if writer_sync is None:
            return False
        self._remember_dispatch_id(task.id, dispatch_id)  # B-STUCK-1
        task.active_dispatch_id = dispatch_id
        # 1. Update kanban state — assigned_to is the instance_id so multi-
        # instance dispatch can tell replicas apart (G-INST-4).
        current = self.task_store.get(task.id) or task
        if current.status == "backlog":
            self._move_task(task.id, "in_progress")
        updated_task = self.task_store.update(
            task.id,
            assigned_to=role.instance_id,
            active_dispatch_id=dispatch_id,
        )
        if updated_task is not None:
            task = updated_task
        target_ref = self._checkout_reader_target_ref(role, task.id)
        source_commit = self._reader_source_commit(task.id, target_ref)
        project_root = self.project_root
        dispatch_head = _capture_head(project_root)
        evidence_root = project_root
        if _is_writer_role(role):
            project_path = str(writer_sync.get("project_path") or "")
            if project_path:
                evidence_root = Path(project_path)
            base_git_head = str(writer_sync.get("after") or "") or dispatch_head
        elif target_ref:
            evidence_root = self._reader_evidence_project_root(role)
            base_ref = str(
                getattr(self.config.runtime.git, "candidate_base_ref", "main")
                or "main"
            )
            base_git_head = (
                _git_merge_base(project_root, target_ref, base_ref)
                or self._dispatch_heads.get(task.id, "")
                or _git_rev_parse(project_root, base_ref)
                or source_commit
                or dispatch_head
            )
        else:
            base_git_head = (
                self._dispatch_heads.get(task.id, "")
                or source_commit
                or dispatch_head
            )

        try:
            from zf.runtime.task_doc import verify_task_capsule, write_task_doc

            task_doc = write_task_doc(
                self.state_dir,
                task,
                dispatch_id=dispatch_id,
            )
            preflight_errors = verify_task_capsule(self.state_dir, task)
            if preflight_errors:
                raise RuntimeError(
                    "task capsule preflight failed: "
                    + ", ".join(preflight_errors)
                )
            updated_task = self.task_store.update(task.id, contract=task.contract)
            if updated_task is not None:
                task = updated_task
            self.event_writer.append(ZfEvent(
                type="task.source.published",
                actor="orchestrator",
                task_id=task.id,
                payload={
                    "dispatch_id": dispatch_id,
                    "source_doc": str(task_doc.source_path),
                    "source_revision": task_doc.source_revision,
                },
            ))
            self.event_writer.append(ZfEvent(
                type="task.doc.published",
                actor="orchestrator",
                task_id=task.id,
                payload={
                    "dispatch_id": dispatch_id,
                    "task_doc": str(task_doc.path),
                    "manifest": str(task_doc.manifest_path),
                    "source_revision": task_doc.source_revision,
                    "contract_revision": task_doc.contract_revision,
                    "capsule_revision": task_doc.capsule_revision,
                },
            ))
        except Exception as exc:
            self._active_dispatch_ids.pop(task.id, None)
            try:
                self.task_store.update(
                    task.id,
                    status=current.status,
                    assigned_to=current.assigned_to or "",
                    active_dispatch_id=getattr(current, "active_dispatch_id", ""),
                )
            except Exception:
                pass
            try:
                self.event_writer.append(ZfEvent(
                    type="orchestrator.dispatch_failed",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={
                        "role": role.name,
                        "assignee": role.instance_id,
                        "dispatch_id": dispatch_id,
                        "stage": "task_doc",
                        "error": str(exc),
                    },
                ))
                self.event_writer.append(ZfEvent(
                    type="orchestrator.dispatch_skipped",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={
                        "role": role.name,
                        "assignee": role.instance_id,
                        "dispatch_id": dispatch_id,
                        "reason": "task_capsule_preflight_failed",
                        "stage": "task_doc",
                        "error": str(exc),
                    },
                ))
            except Exception:
                pass
            self._record_dispatch_failure(task.id)
            return False

        # 2. Generate and write task briefing
        # α-5 (2026-05-17): look up parent Feature so generate_task_briefing
        # can inject the persistent objective + codex-style discipline at
        # the briefing top. None when the task is an orphan (no feature_id)
        # or the feature is missing — generate_task_briefing then preserves
        # the original briefing shape.
        feature_for_briefing = None
        feature_id = (
            getattr(task.contract, "feature_id", "")
            if task.contract is not None
            else ""
        )
        if feature_id:
            try:
                feature_store = FeatureStore(self.state_dir / "feature_list.json")
                feature_for_briefing = feature_store.get(feature_id)
            except Exception:
                feature_for_briefing = None
        briefing = generate_task_briefing(
            self.config,
            role,
            task,
            feature=feature_for_briefing,
            task_doc_path=task_doc.path,
            source_doc_path=task_doc.source_path,
            progress_doc_path=task_doc.progress_path,
            source_revision=task_doc.source_revision,
            contract_revision=task_doc.contract_revision,
            capsule_revision=task_doc.capsule_revision,
            state_dir_ref=_state_dir_display_ref(self.state_dir, self.project_root),
            project_root=self.project_root,
        )
        resume_path: Path | None = None
        try:
            from zf.runtime.long_horizon import (
                build_resume_packet,
                write_resume_packet,
            )

            resume_packet = build_resume_packet(
                self.state_dir,
                task.id,
                dispatch_id=dispatch_id,
                config=self.config,
                project_root=self.project_root,
            )
            resume_path = write_resume_packet(
                self.state_dir,
                resume_packet,
                dispatch_id=dispatch_id,
            )
            briefing += (
                "\n\n## Runtime Resume Packet\n"
                f"- path: `{resume_path}`\n"
                f"- next_required_action: {resume_packet.get('next_required_action', '')}\n"
                f"- missing_evidence_count: {len(resume_packet.get('missing_evidence') or [])}\n"
                "- Use this short runtime fact packet after retry/respawn/context recovery.\n"
            )
        except Exception:
            pass
        briefing += self._rework_context_for_dispatch(task, role)
        briefing += self._artifact_refs_context_for_dispatch(task)
        briefing += self._workflow_input_context_for_dispatch(task)
        if target_ref:
            briefing += (
                "\n\n## Runtime Workdir\n"
                f"- target_ref: `{target_ref}`\n"
                f"- source_commit: `{source_commit}`\n"
                "- This reader role must not modify project source files.\n"
            )
        briefing += _git_evidence_section(evidence_root, base_git_head)
        briefing_path = write_task_briefing(
            self.state_dir,
            role.instance_id,
            task,
            briefing,
            task_doc_path=task_doc.path,
            source_doc_path=task_doc.source_path,
            progress_doc_path=task_doc.progress_path,
            source_revision=task_doc.source_revision,
            contract_revision=task_doc.contract_revision,
            capsule_revision=task_doc.capsule_revision,
        )

        snapshot_ref = ""
        runtime_snapshot_payload: dict | None = None
        runtime_snapshot_error = ""
        try:
            snapshot_result, runtime_snapshot_payload = (
                self._write_dispatch_runtime_snapshot(
                    task=task,
                    role=role,
                    dispatch_id=dispatch_id,
                    briefing_path=briefing_path,
                    task_doc=task_doc,
                    resume_path=resume_path,
                )
            )
            snapshot_ref = snapshot_result.snapshot_ref
            briefing += (
                "\n\n## Runtime Snapshot\n"
                f"- snapshot_ref: `{snapshot_ref}`\n"
                "- source: `dispatch`\n"
                "- Projection only; runtime truth remains events.jsonl and stores.\n"
            )
            briefing_path = write_task_briefing(
                self.state_dir,
                role.instance_id,
                task,
                briefing,
                task_doc_path=task_doc.path,
                source_doc_path=task_doc.source_path,
                progress_doc_path=task_doc.progress_path,
                source_revision=task_doc.source_revision,
                contract_revision=task_doc.contract_revision,
                capsule_revision=task_doc.capsule_revision,
            )
        except Exception as exc:
            runtime_snapshot_error = str(exc)

        # 3. Materialize skills and write full role instructions.
        skill_entries = self._record_skill_provenance(role=role, task_id=task.id)
        instructions = generate_role_instructions(
            self.config,
            role,
            task=task,
            skill_entries=skill_entries,
        )
        instructions_dir = self.state_dir / "instructions"
        instructions_dir.mkdir(parents=True, exist_ok=True)
        (instructions_dir / f"{role.instance_id}.md").write_text(instructions)

        # 4. Deliver briefing via transport — routed by instance_id
        prompt = build_task_prompt(role.instance_id, briefing_path)
        context = self._dispatch_context(
            role=role,
            briefing_path=briefing_path,
            task_id=task.id,
        )
        try:
            self._send_transport_task(role.instance_id, briefing_path, prompt, context)
        except Exception as exc:
            self._active_dispatch_ids.pop(task.id, None)
            try:
                self.task_store.update(
                    task.id,
                    status=current.status,
                    assigned_to=current.assigned_to or "",
                    active_dispatch_id="",
                )
            except Exception:
                pass
            try:
                payload = {
                    "role": role.name,
                    "assignee": role.instance_id,
                    "briefing": str(briefing_path),
                    "dispatch_id": dispatch_id,
                    "error": str(exc),
                }
                payload.update(transport_error_diagnostics(exc))
                self.event_writer.append(ZfEvent(
                    type="orchestrator.dispatch_failed",
                    actor="zf-cli",
                    task_id=task.id,
                    payload=payload,
                ))
                if str(payload.get("dead_reason") or "") == "pane_dead":
                    self.event_writer.append(ZfEvent(
                        type="worker.respawn.requested",
                        actor="zf-cli",
                        task_id=task.id,
                        payload={
                            "role": role.name,
                            "instance_id": role.instance_id,
                            "task_id": task.id,
                            "dispatch_id": dispatch_id,
                            "reason": "pane_dead_dispatch_failed",
                            "source_event_type": "orchestrator.dispatch_failed",
                            "source": "dispatch_failure_recovery",
                        },
                    ))
                    self.event_writer.append(ZfEvent(
                        type="orchestrator.dispatch.retry_requested",
                        actor="zf-cli",
                        task_id=task.id,
                        payload={
                            "role": role.name,
                            "assignee": role.instance_id,
                            "task_id": task.id,
                            "dispatch_id": dispatch_id,
                            "reason": "retry_after_pane_respawn",
                            "source": "dispatch_failure_recovery",
                            "max_attempts": 1,
                        },
                    ))
            except Exception:
                pass
            self._record_dispatch_failure(task.id)
            return False
        # Codex hook: trigger background observe of session file (no-op
        # for claude/mock).
        self._get_spawn_coordinator().notify_first_dispatch(role)

        # 5. Emit dispatch event
        self._clear_dispatch_failure(task.id)
        if runtime_snapshot_payload:
            try:
                self.event_writer.append(ZfEvent(
                    type="runtime.snapshot.recorded",
                    actor="orchestrator",
                    task_id=task.id,
                    payload=runtime_snapshot_payload,
                ))
            except Exception:
                pass
        elif runtime_snapshot_error:
            try:
                self.event_writer.append(ZfEvent(
                    type="runtime.snapshot.invalid",
                    actor="orchestrator",
                    task_id=task.id,
                    payload={
                        "source": "dispatch",
                        "reason": runtime_snapshot_error,
                        "task_id": task.id,
                        "dispatch_id": dispatch_id,
                        "role": role.name,
                        "instance_id": role.instance_id,
                    },
                ))
            except Exception:
                pass
        if assignment_source:
            self.event_writer.append(ZfEvent(
                type="task.assigned",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "role": role.name,
                    "assignee": role.instance_id,
                    "source": assignment_source,
                    "dispatch_id": dispatch_id,
                },
            ))
        self.event_writer.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id=task.id,
            payload={
                "role": role.name,
                "assignee": role.instance_id,  # C3: canonical for dedup
                "briefing": str(briefing_path),
                "target_ref": target_ref or "",
                "base_git_head": base_git_head or "",
                "dispatch_id": dispatch_id,
                "snapshot_ref": snapshot_ref,
            },
        ))
        self.event_writer.append(ZfEvent(
            type="task.dispatch_context.bound",
            actor="orchestrator",
            task_id=task.id,
            payload={
                "role": role.name,
                "assignee": role.instance_id,
                "briefing": str(briefing_path),
                "task_doc": str(task_doc.path),
                "source_doc": str(task_doc.source_path),
                "progress_doc": str(task_doc.progress_path),
                "dispatch_id": dispatch_id,
                "source_revision": task_doc.source_revision,
                "contract_revision": task_doc.contract_revision,
                "capsule_revision": task_doc.capsule_revision,
                "snapshot_ref": snapshot_ref,
                "source_index_ref": (
                    task.contract.source_index_ref if task.contract else ""
                ),
                "source_key": task.contract.source_key if task.contract else "",
                "source_mode": task.contract.source_mode if task.contract else "",
            },
        ))

        # ZF-LH-SP-001 + PWF-MEM-001 + ATTEST integration (2026-05-18):
        # after every successful dispatch, project a State Packet
        # snapshot + 4-file working memory projection. Best-effort —
        # projection failures must not break the dispatch.
        self._project_state_packet_on_dispatch(
            task=task, role=role, dispatch_id=dispatch_id,
        )

        # B3: worker state transition idle/awaiting_review → busy
        self._set_worker_state(
            role.instance_id, "busy",
            reason=f"dispatched task {task.id}",
        )

        # 6. G-WIRE-1: snapshot workspace for scope ratchet (only when
        # the task carries a contract.scope; unconstrained tasks skip).
        if task.contract and task.contract.scope:
            try:
                self._scope_snapshots[task.id] = self._scope_ratchet.snapshot()
            except Exception as exc:
                # Snapshot failure must not block dispatch, but it must be
                # observable (P9): without a snapshot the completion-side
                # scope check cannot run, which previously degraded scope
                # enforcement silently (2026-06-10 review).
                try:
                    self.event_writer.append(ZfEvent(
                        type="scope.snapshot.failed",
                        actor="zf-cli",
                        task_id=task.id,
                        payload={
                            "reason": str(exc),
                            "fail_closed": self._scope_fail_closed(),
                        },
                    ))
                except Exception:
                    pass

        # GAP-1: record writer HEAD at dispatch so completion can diff. Reader
        # roles must not overwrite the implementation base captured from dev.
        if _is_writer_role(role) and dispatch_head:
            self._dispatch_heads[task.id] = dispatch_head
        elif source_commit and task.id not in self._dispatch_heads:
            self._dispatch_heads[task.id] = source_commit

        # LH-0.T3: mark dispatch epoch for orphan-timeout tracking.
        # Reset any prior warning mark — a fresh dispatch restarts the clock.
        self._dispatch_epoch[task.id] = self._now()
        self._orphan_warned.discard(task.id)
        return True

    def _split_quality_blocks_dispatch(
        self,
        task: Task,
        role: RoleConfig | None = None,
    ) -> bool:
        """Fail closed before worker dispatch when work-unit split is invalid.

        The guard is opt-in via ``workflow.work_units.enabled`` and only blocks
        findings with severity ``blocking``. Default projects keep the existing
        dispatch behavior.
        """
        if role is not None and not _is_writer_role(role):
            return False
        work_units_cfg = getattr(getattr(self.config, "workflow", None), "work_units", None)
        if not getattr(work_units_cfg, "enabled", False):
            return False
        split_cfg = getattr(work_units_cfg, "split_quality", None)
        try:
            from zf.runtime.long_horizon import (
                check_split_quality,
                work_unit_from_task,
            )

            work_unit = work_unit_from_task(task, config=self.config)
            findings = check_split_quality(
                work_unit,
                mode=getattr(split_cfg, "mode", "warning"),
                max_scope_files=int(getattr(split_cfg, "max_scope_files", 12) or 0),
                require_validation_surface=bool(
                    getattr(split_cfg, "require_validation_surface", True)
                ),
            )
        except Exception:
            return False
        blocking = [
            {
                "kind": item.kind,
                "severity": item.severity,
                "message": item.message,
            }
            for item in findings
            if item.severity == "blocking"
        ]
        if not blocking:
            return False
        reason = "; ".join(item["message"] for item in blocking)
        try:
            self.task_store.update(
                task.id,
                status="blocked",
                blocked_reason=f"split quality blocked dispatch: {reason}",
            )
        except Exception:
            pass
        try:
            self.event_writer.append(ZfEvent(
                type="task.split_quality.blocked",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "findings": blocking,
                    "work_unit_id": f"WU-{task.id}",
                },
            ))
        except Exception:
            pass
        return True

    def _sync_writer_workdir_to_dispatch_base(
        self,
        *,
        role: RoleConfig,
        task: Task,
        dispatch_id: str,
    ) -> dict[str, object] | None:
        if not _is_writer_role(role):
            return {}
        dependency_task_ids = self._writer_dependency_task_ids(task)
        try:
            from zf.runtime.workdirs import WorkdirManager

            manager = WorkdirManager(
                state_dir=self.state_dir,
                project_root=self.project_root,
                config=self.config,
            )
            sync = manager.sync_writer_to_source_ref(
                role,
                source_ref_override=self._writer_dispatch_source_ref(task),
            )
            dependency_result = manager.apply_dependency_task_refs(
                role,
                dependency_task_ids,
            )
            if dependency_result:
                sync = {**(sync or {}), **dependency_result}
        except Exception as exc:
            try:
                if dependency_task_ids:
                    self.event_writer.append(ZfEvent(
                        type="workdir.dependency_apply.failed",
                        actor="zf-cli",
                        task_id=task.id,
                        payload={
                            "role": role.name,
                            "instance_id": role.instance_id,
                            "dispatch_id": dispatch_id,
                            "blocked_by": dependency_task_ids,
                            "error": str(exc),
                        },
                    ))
                self.event_writer.append(ZfEvent(
                    type="orchestrator.dispatch_failed",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={
                        "role": role.name,
                        "assignee": role.instance_id,
                        "dispatch_id": dispatch_id,
                        "error": str(exc),
                        "phase": "writer_workdir_sync",
                    },
                ))
            except Exception:
                pass
            self._record_dispatch_failure(task.id)
            return None
        if sync and (
            sync.get("synced") == "true"
            or sync.get("applied_dependency_refs")
            or sync.get("skipped_dependency_refs")
        ):
            try:
                self.event_writer.append(ZfEvent(
                    type="workdir.writer_synced",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={
                        "role": role.name,
                        "instance_id": role.instance_id,
                        "dispatch_id": dispatch_id,
                        **sync,
                    },
                ))
            except Exception:
                pass
        return sync or {}

    def _writer_dependency_task_ids(self, task: Task) -> list[str]:
        deps: list[str] = []
        for raw in getattr(task, "blocked_by", []) or []:
            dep_id = str(raw or "").strip()
            if not dep_id or dep_id in deps:
                continue
            blocker = self.task_store.get(dep_id)
            if blocker is None:
                continue
            if blocker.status in {"done", "cancelled"}:
                deps.append(dep_id)
        return deps

    def _writer_dispatch_source_ref(self, task: Task) -> str:
        rework_ref = self._writer_rework_source_ref(task)
        if rework_ref:
            return rework_ref
        return self._writer_candidate_source_ref(task)

    def _writer_rework_source_ref(self, task: Task) -> str:
        if getattr(task, "retry_count", 0) <= 0:
            return ""
        try:
            from zf.runtime.workdirs import WorkdirManager

            metadata = WorkdirManager(
                state_dir=self.state_dir,
                project_root=self.project_root,
                config=self.config,
            ).task_ref_metadata(task.id)
        except Exception:
            return ""
        return str(metadata.get("source_commit") or "").strip()

    def _writer_candidate_source_ref(self, task: Task) -> str:
        contract = getattr(task, "contract", None)
        feature_id = str(getattr(contract, "feature_id", "") or "").strip()
        if not feature_id:
            return ""
        try:
            prefix = getattr(
                self.config.runtime.git,
                "candidate_branch_prefix",
                "candidate",
            )
            candidate_ref = f"{prefix}/{feature_id}"
            result = subprocess.run(
                [
                    "git",
                    "rev-parse",
                    "--verify",
                    f"refs/heads/{candidate_ref}^{{commit}}",
                ],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except Exception:
            return ""
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _rework_context_for_dispatch(self, task: Task, role: RoleConfig) -> str:
        if getattr(task, "retry_count", 0) <= 0:
            return ""
        event = self._latest_rework_trigger_event(task.id)
        if event is None:
            return ""
        actions = _rework_required_actions(event.payload)
        action_section = ""
        if actions:
            action_section = (
                "\n### Required Rework Items\n"
                + "\n".join(f"- {item}" for item in actions)
                + "\n"
            )
        payload = event.payload if isinstance(event.payload, dict) else {}
        trigger_summary = _payload_text(
            payload.get("summary")
            or payload.get("verdict")
            or payload.get("reason")
        )
        summary_section = ""
        if trigger_summary:
            summary_section = f"\n### Trigger Summary\n{trigger_summary}\n"
        payload_excerpt = _payload_excerpt(event.payload, limit=2400)
        payload_section = ""
        if payload_excerpt:
            payload_section = (
                "\n### Trigger Payload Evidence\n"
                "```json\n"
                f"{payload_excerpt}\n"
                "```\n"
            )
        feedback_artifact_ref = str(payload.get("feedback_artifact_ref") or "").strip()
        if not feedback_artifact_ref:
            rework_request = self._latest_rework_request_event(task.id)
            rework_payload = (
                rework_request.payload
                if rework_request is not None and isinstance(rework_request.payload, dict)
                else {}
            )
            if str(rework_payload.get("trigger_event_id") or "") == event.id:
                feedback_artifact_ref = str(
                    rework_payload.get("feedback_artifact_ref") or ""
                ).strip()
        feedback_artifact_section = ""
        if feedback_artifact_ref:
            feedback_artifact_section = (
                "\n### Feedback Artifact\n"
                f"- feedback_artifact_ref: `{feedback_artifact_ref}`\n"
                "Load this file before editing; it is the durable rejection "
                "summary for this rework attempt.\n"
            )
        return (
            "\n\n## Rework Context\n"
            f"- trigger_event: `{event.type}`\n"
            f"- trigger_event_id: `{event.id}`\n"
            f"- trigger_actor: `{event.actor or ''}`\n"
            f"{summary_section}"
            f"{action_section}"
            f"{payload_section}"
            f"{feedback_artifact_section}"
            "Address the rework evidence above before emitting the success event.\n"
        )

    def _artifact_refs_context_for_dispatch(self, task: Task) -> str:
        entry = self._task_ref_entry(task.id)
        if not isinstance(entry, dict):
            return ""
        artifact_refs = entry.get("artifact_refs")
        contract_refs = entry.get("contract_refs")
        handoff_contract = entry.get("handoff_contract")
        if not artifact_refs and not contract_refs and not handoff_contract:
            return ""
        payload = {
            "manifest_event_id": entry.get("manifest_event_id", ""),
            "manifest_role": entry.get("manifest_role", ""),
            "contract_refs": contract_refs if isinstance(contract_refs, dict) else {},
            "handoff_contract": handoff_contract if isinstance(handoff_contract, dict) else {},
            "artifact_refs": artifact_refs if isinstance(artifact_refs, list) else [],
            "hash_status": entry.get("hash_status", []),
        }
        return (
            "\n\n## Artifact Manifest Refs\n"
            "These refs were accepted by Layer 1 from "
            "`artifact.manifest.published`; treat paths as stable handoff "
            "inputs and do not rely on `workdir_path` as the only source. "
            "If any accepted ref hash is `missing` or `mismatch`, stop and "
            "emit a blocking evidence event instead of continuing from stale context.\n"
            "```json\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
            "```\n"
        )

    def _workflow_input_context_for_dispatch(self, task: Task) -> str:
        try:
            events = self.event_log.read_all()
        except Exception:
            return ""
        for event in reversed(events):
            if event.task_id != task.id or not isinstance(event.payload, dict):
                continue
            if event.type not in {
                "fanout.child.dispatched",
                "fanout.requested",
                "task.fanout.requested",
                "workflow.invoke.accepted",
                "workflow.invoke.requested",
            }:
                continue
            section = render_workflow_input_briefing_section(event.payload)
            if section:
                return section
        return ""

    def _latest_rework_trigger_event(self, task_id: str) -> ZfEvent | None:
        try:
            events = self.event_log.read_all()
        except Exception:
            return None
        fanout_current_cache: dict[str, bool] = {}
        for event in reversed(events):
            if event.task_id != task_id:
                continue
            if event.type in self._REWORK_TRIGGER_EVENTS:
                if not self._fanout_progress_event_is_current(
                    event,
                    events,
                    cache=fanout_current_cache,
                ):
                    continue
                if self._fanout_scoped_stage_progress_event(event):
                    continue
                return event
        return None

    def _latest_rework_request_event(self, task_id: str) -> ZfEvent | None:
        try:
            events = self.event_log.read_all()
        except Exception:
            return None
        for event in reversed(events):
            if event.task_id == task_id and event.type == "task.rework.requested":
                return event
        return None

    def _checkout_reader_target_ref(self, role: RoleConfig, task_id: str) -> str | None:
        manager = None
        try:
            from zf.runtime.workdirs import WorkdirManager

            manager = WorkdirManager(
                state_dir=self.state_dir,
                project_root=self.project_root,
                config=self.config,
            )
            status = manager.reset_reader_if_dirty(role)
            if status.strip():
                status_classification = manager.classify_reader_status(status)
                self.event_writer.append(ZfEvent(
                    type="reader.write_violation",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "role": role.name,
                        "instance_id": role.instance_id,
                        "trigger_event": "dispatch_preflight",
                        "status": status,
                        **status_classification,
                        **self._reader_write_policy_payload(status),
                        "reset": True,
                    },
                ))
            return manager.checkout_reader_task_ref(role, task_id)
        except Exception as exc:
            expected_pre_ref = False
            if manager is not None:
                try:
                    expected_pre_ref = (
                        role.name in {
                            "arch",
                            "critic",
                            "orchestrator",
                            "prd-author",
                            "prd-critic",
                            "task-map-synth",
                        }
                        and not manager.task_ref_metadata(task_id).get("task_ref")
                    )
                except Exception:
                    expected_pre_ref = False
            event_type = (
                "reader.checkout_skipped"
                if expected_pre_ref
                else "reader.checkout_failed"
            )
            try:
                self.event_writer.append(ZfEvent(
                    type=event_type,
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "role": role.name,
                        "instance_id": role.instance_id,
                        "reason": str(exc),
                        "classification": (
                            "pre_task_ref_unavailable"
                            if expected_pre_ref
                            else "checkout_failed"
                        ),
                        "required": not expected_pre_ref,
                    },
                ))
            except Exception:
                pass
            return None

    def _reader_evidence_project_root(self, role: RoleConfig) -> Path:
        try:
            from zf.runtime.workdirs import WorkdirManager

            plan = WorkdirManager(
                state_dir=self.state_dir,
                project_root=self.project_root,
                config=self.config,
            ).plan(role)
            project_path = Path(plan.project_path)
            if project_path.exists() and (project_path / ".git").exists():
                return project_path
        except Exception:
            pass
        return Path(self.project_root)

    def _reader_source_commit(self, task_id: str, target_ref: str | None) -> str:
        if not target_ref:
            return ""
        try:
            from zf.runtime.workdirs import WorkdirManager

            metadata = WorkdirManager(
                state_dir=self.state_dir,
                project_root=self.project_root,
                config=self.config,
            ).task_ref_metadata(task_id)
        except Exception:
            return ""
        if metadata.get("task_ref") != target_ref:
            return ""
        return metadata.get("source_commit", "")

    def _resolve_rework_role(
        self, task: Task, trigger_event: ZfEvent,
    ) -> RoleConfig | None:
        """P1-1 (2026-04-20): determine which role gets re-dispatched
        on failure. Order of precedence:

          1. task.contract.rework_to         (per-task override)
          2. config.workflow.rework_routing  (per-project default)
          3. "dev"                           (legacy fallback)

        Returns None if resolution points at a role that doesn't exist
        in the config (caller emits dispatch.rework.unresolvable).
        """
        if trigger_event.type == "task.completion.stale_rejected":
            assigned_to = str(getattr(task, "assigned_to", "") or "")
            if assigned_to:
                same_lane = self._find_role_by_instance(assigned_to)
                if same_lane is not None:
                    return same_lane
        if trigger_event.type == TASK_REF_REPAIR_REQUESTED_EVENT:
            repair_role = self._resolve_task_ref_repair_role(task, trigger_event)
            if repair_role is not None:
                return repair_role

        candidate = ""

        # Layer 1: task.contract.rework_to
        if task.contract and getattr(task.contract, "rework_to", ""):
            candidate = task.contract.rework_to

        backedge = self._workflow_stage_backedge_for_event(trigger_event.type)
        if not candidate:
            same_lane_role = self._resolve_backedge_same_lane_role(
                task,
                trigger_event,
                backedge,
            )
            if same_lane_role is not None:
                return same_lane_role

        # Layer 2: workflow.rework_routing for this event type
        if not candidate:
            routing = getattr(self.config.workflow, "rework_routing", {}) or {}
            candidate = routing.get(trigger_event.type, "")

        # Layer 3: legacy default
        if not candidate:
            candidate = "dev"

        role = self._resolve_rework_candidate_role(task, candidate)
        if role is not None:
            return role

        # Unresolvable — emit signal for diagnostics (don't raise, the
        # dispatch path is best-effort and the escalation path below
        # catches the human-attention case).
        try:
            self.event_writer.append(ZfEvent(
                type="orchestrator.dispatch_failed",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "reason": f"rework_to role '{candidate}' not found",
                    "trigger": trigger_event.type,
                    "resolution_order": [
                        task.contract.rework_to if task.contract else "",
                        (getattr(self.config.workflow, "rework_routing", {}) or {}
                         ).get(trigger_event.type, ""),
                        "dev",
                    ],
                },
            ))
        except Exception:
            pass
        self._record_dispatch_failure(task.id)
        return None

    def _resolve_task_ref_repair_role(
        self,
        task: Task,
        trigger_event: ZfEvent,
    ) -> RoleConfig | None:
        if trigger_event.type != TASK_REF_REPAIR_REQUESTED_EVENT:
            return None
        payload = trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
        return self._resolve_task_ref_repair_role_from_payload(task, payload)

    def _resolve_task_ref_repair_role_from_payload(
        self,
        task: Task,
        payload: dict[str, object],
    ) -> RoleConfig | None:
        for candidate in self._task_ref_repair_role_candidates(task, payload):
            role = self._find_role_by_instance(candidate) or self._find_role_by_name(
                candidate,
            )
            if role is not None and _is_writer_role(role):
                return role
        return None

    def _task_ref_repair_role_candidates(
        self,
        task: Task,
        payload: dict[str, object],
    ) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()

        def add(value: object) -> None:
            text = str(value or "").strip()
            if not text or text in seen:
                return
            seen.add(text)
            values.append(text)

        for key in (
            "target_assignee",
            "assignee",
            "instance_id",
            "source_actor",
            "actor",
            "target_role",
            "role",
        ):
            add(payload.get(key))
        add(self._task_ref_repair_workdir_instance(payload.get("workdir")))

        source_event_id = str(payload.get("source_event_id") or "").strip()
        if source_event_id:
            try:
                events = self.event_log.read_all()
            except Exception:
                events = []
            for event in reversed(events):
                if event.id != source_event_id:
                    continue
                add(event.actor)
                event_payload = event.payload if isinstance(event.payload, dict) else {}
                for key in ("assignee", "instance_id", "role"):
                    add(event_payload.get(key))
                break

        try:
            add(self._task_ref_entry(task.id).get("actor"))
        except Exception:
            pass

        contract = getattr(task, "contract", None)
        if contract is not None:
            add(getattr(contract, "owner_instance", ""))
            add(getattr(contract, "owner_role", ""))
        add(getattr(task, "assigned_to", ""))
        return values

    def _task_ref_repair_workdir_instance(self, workdir: object) -> str:
        text = str(workdir or "").strip()
        if not text:
            return ""
        try:
            parts = Path(text).parts
        except Exception:
            return ""
        for idx, part in enumerate(parts):
            if part == "workdirs" and idx + 1 < len(parts):
                return parts[idx + 1]
        return ""

    def _workflow_stage_backedge_for_event(self, event_type: str):
        if not event_type:
            return None
        for stage in getattr(self.config.workflow, "stages", []) or []:
            for backedge in (
                getattr(stage, "on_reject", None),
                getattr(stage, "on_fail", None),
            ):
                if backedge is not None and getattr(backedge, "event", "") == event_type:
                    return backedge
        return None

    def _resolve_backedge_same_lane_role(
        self,
        task: Task,
        trigger_event: ZfEvent,
        backedge,
    ) -> RoleConfig | None:
        if backedge is None:
            return None
        if str(getattr(backedge, "target_affinity", "") or "") != "same_lane":
            return None
        restart_stage_id = str(getattr(backedge, "restart_stage", "") or "")
        if not restart_stage_id:
            return None
        stage = self._fanout_stage_by_id(restart_stage_id)
        if stage is None:
            return None
        lane_id = self._backedge_lane_id(task, trigger_event)
        if not lane_id:
            return None
        stage_slot = str(
            getattr(getattr(stage, "assignment", None), "stage_slot", "") or ""
        )
        return self._fanout_affinity_lane_role(
            stage,
            lane_id=lane_id,
            stage_slot=stage_slot,
        )

    def _backedge_lane_id(self, task: Task, trigger_event: ZfEvent) -> str:
        payload = trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
        lane_id = str(payload.get("lane_id") or payload.get("affinity_lane_id") or "")
        if lane_id:
            return lane_id
        for instance_id in (
            payload.get("assignee"),
            payload.get("assigned_to"),
            payload.get("role_instance"),
            payload.get("instance_id"),
            payload.get("source_actor"),
            payload.get("actor"),
            trigger_event.actor,
            getattr(task, "assigned_to", ""),
            getattr(getattr(task, "contract", None), "owner_instance", ""),
        ):
            lane_id = self._lane_id_for_role_instance(instance_id)
            if lane_id:
                return lane_id
        try:
            events = self.event_log.read_all()
        except Exception:
            return ""
        task_id = str(task.id or trigger_event.task_id or "")
        for event in reversed(events):
            event_payload = event.payload if isinstance(event.payload, dict) else {}
            event_task_id = str(event.task_id or event_payload.get("task_id") or "")
            if task_id and event_task_id and event_task_id != task_id:
                continue
            lane_id = str(
                event_payload.get("lane_id")
                or event_payload.get("affinity_lane_id")
                or ""
            )
            if lane_id:
                return lane_id
            for instance_id in (
                event.actor,
                event_payload.get("assignee"),
                event_payload.get("assigned_to"),
                event_payload.get("role_instance"),
                event_payload.get("instance_id"),
            ):
                lane_id = self._lane_id_for_role_instance(instance_id)
                if lane_id:
                    return lane_id
        return ""

    def _lane_id_for_role_instance(self, instance_id: object) -> str:
        role_instance = str(instance_id or "").strip()
        if not role_instance:
            return ""
        profiles = getattr(self.config.workflow, "affinity_lanes", {}) or {}
        for profile in profiles.values():
            for lane in getattr(profile, "lanes", []) or []:
                lane_values = vars(lane) if hasattr(lane, "__dict__") else {}
                for key, value in lane_values.items():
                    if key == "id":
                        continue
                    if str(value or "").strip() == role_instance:
                        return str(getattr(lane, "id", "") or "")
        return ""

    def _rework_feedback_artifact_ref(
        self,
        task: Task,
        trigger_event: ZfEvent,
        backedge,
    ) -> str:
        feedback_artifact = str(
            getattr(backedge, "feedback_artifact", "") or ""
        ).strip()
        if not feedback_artifact:
            return ""
        task_segment = _safe_artifact_segment(task.id, "task")
        trigger_segment = _safe_artifact_segment(trigger_event.id, "trigger")
        artifact_name = _safe_artifact_segment(
            Path(feedback_artifact).name,
            "feedback.md",
        )
        return str(
            self.state_dir
            / "artifacts"
            / "rework-feedback"
            / task_segment
            / f"{trigger_segment}-{artifact_name}"
        )

    def _write_rework_feedback_artifact(
        self,
        *,
        artifact_ref: str,
        task: Task,
        role: RoleConfig,
        trigger_event: ZfEvent,
        rework_request: ZfEvent,
        feedback: str,
        required_actions: list[str],
        max_attempts: int,
    ) -> None:
        if not artifact_ref:
            return
        payload = trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
        lane_id = self._backedge_lane_id(task, trigger_event)
        artifact_path = Path(artifact_ref)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Rework Feedback",
            "",
            f"- task_id: `{task.id}`",
            f"- task_title: `{task.title}`",
            f"- trigger_event: `{trigger_event.type}`",
            f"- trigger_event_id: `{trigger_event.id}`",
            f"- rework_request_event_id: `{rework_request.id}`",
            f"- assignee: `{role.instance_id}`",
            f"- role: `{role.name}`",
            f"- attempt: {task.retry_count}/{max_attempts}",
        ]
        if lane_id:
            lines.append(f"- lane_id: `{lane_id}`")
        lines.extend([
            "",
            "## Reason",
            "",
            feedback or trigger_event.type,
            "",
        ])
        if required_actions:
            lines.extend(["## Required Actions", ""])
            lines.extend(f"- {item}" for item in required_actions)
            lines.append("")
        lines.extend([
            "## Trigger Payload",
            "",
            "```json",
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
            "```",
            "",
        ])
        artifact_path.write_text("\n".join(lines))

    def _resolve_rework_candidate_role(
        self,
        task: Task,
        candidate: str,
    ) -> RoleConfig | None:
        """Resolve rework to the worker that owns the failed task slice.

        For replica pools, a contract usually says ``rework_to: dev`` while
        the actual implementation lives in ``owner_instance`` or in the
        latest task-ref actor. Reusing an arbitrary available dev replica can
        put the rework on the wrong branch and contaminate the next handoff.
        """
        contract = getattr(task, "contract", None)
        owner_instance = str(getattr(contract, "owner_instance", "") or "")
        if owner_instance:
            owner = self._find_role_by_instance(owner_instance)
            if owner is not None and _role_matches_rework_candidate(owner, candidate):
                return owner

        owner_role = str(getattr(contract, "owner_role", "") or "")
        if owner_role:
            owner = (
                self._find_role_by_instance(owner_role)
                or self._find_role_by_name(owner_role)
            )
            if owner is not None and _role_matches_rework_candidate(owner, candidate):
                return owner

        try:
            task_ref_actor = str(self._task_ref_entry(task.id).get("actor") or "")
        except Exception:
            task_ref_actor = ""
        if task_ref_actor:
            ref_owner = self._find_role_by_instance(task_ref_actor)
            if (
                ref_owner is not None
                and _role_matches_rework_candidate(ref_owner, candidate)
            ):
                return ref_owner

        exact = self._find_role_by_instance(candidate)
        if exact is not None and exact.instance_id == candidate:
            return exact

        return self._find_role_by_name(candidate)

    def _dispatch_rework(
        self, task: Task, trigger_event: ZfEvent,
    ) -> str | None:
        """Re-dispatch a task for rework after rejection/failure.

        Returns the role.name actually dispatched to, or None if
        skipped (unresolvable target / rework cap exceeded). Reactor
        handlers use this to populate OrchestratorDecision.role so the
        decision trail reflects the real routing, not a hardcoded "dev".

        LH-0.T1: guards against infinite rework. The housekeeping path
        has already bumped task.retry_count for product/design failures by
        this point, so task.retry_count is "this attempt's number" for normal
        rework. Evidence/harness/environment triage classifications do not
        increment product retry and should be routed through
        _route_rework_trigger before this method is called.

        P1-1 (2026-04-20): the rework target used to be hardcoded to
        "dev"; it's now resolved from task.contract.rework_to →
        config.workflow.rework_routing → "dev" fallback.

        β-4 (2026-05-17): fix-task spawn. If the trigger
        event carries severity=critical/major + scope=local +
        affected_task_ids≥2, spawn a NEW fix-task on the same feature
        backlog instead of requeueing the original. The original stays
        ``done`` — failure doesn't discard completed work.
        """
        # β-4: fix-task short-circuit. Falls through when payload
        # lacks severity/scope/affected_task_ids fields (the
        # current cangjie events).
        from zf.runtime.fix_task_spawn import (
            should_spawn_fix_task,
            build_fix_task,
            build_fix_spawned_event,
        )

        should_spawn, spawn_reason = should_spawn_fix_task(trigger_event)
        if should_spawn:
            try:
                fix_task = build_fix_task(task, trigger_event)
                self.task_store.add(fix_task)
                self.event_writer.append(build_fix_spawned_event(
                    parent_task_id=task.id,
                    fix_task_id=fix_task.id,
                    trigger_event=trigger_event,
                    reason=spawn_reason,
                ))
                # Original task stays at its current status (done /
                # awaiting_review etc.); the fix-task picks up the work.
                return None
            except Exception:
                # On spawn failure, fall back to standard rework so we
                # don't lose the trigger — defensive.
                pass

        role = self._resolve_rework_role(task, trigger_event)
        if role is None:
            return None
        backedge = self._workflow_stage_backedge_for_event(trigger_event.type)

        # LH-0.T1 rework cap.
        backedge_max_attempts = int(getattr(backedge, "max_attempts", 0) or 0)
        max_attempts = backedge_max_attempts or role.max_rework_attempts
        max_attempts_source = (
            "workflow_stage_backedge" if backedge_max_attempts else "role"
        )
        if task.retry_count > max_attempts:
            self._emit_rework_capped(
                task,
                role,
                trigger_event,
                max_attempts=max_attempts,
                max_attempts_source=max_attempts_source,
            )
            return None
        busy_reason = self._rework_dispatch_block_reason(task, role, trigger_event)
        if busy_reason:
            self._emit_dispatch_skipped(
                task=task,
                role=role,
                reason=busy_reason,
            )
            return None

        # Add feedback context to briefing
        feedback = self._rework_feedback(trigger_event)
        required_actions = _rework_required_actions(trigger_event.payload)
        feedback_artifact_ref = self._rework_feedback_artifact_ref(
            task,
            trigger_event,
            backedge,
        )
        previous_dispatch_id = getattr(task, "active_dispatch_id", "")
        dispatch_id = _new_dispatch_id()
        self._remember_dispatch_id(task.id, dispatch_id)  # B-STUCK-1
        task.active_dispatch_id = dispatch_id
        base_git_head = _capture_head(self.project_root)
        base_git_context = None
        try:
            base_git_context = capture_git_diff_context(
                self.project_root,
                base_sha=base_git_head,
            )
        except Exception:
            base_git_context = None
        rework_request = self.event_writer.append(ZfEvent(
            type="task.rework.requested",
            actor="zf-cli",
            task_id=task.id,
            payload={
                "attempt": task.retry_count,
                "max_attempts": max_attempts,
                "role": role.name,
                "assignee": role.instance_id,
                "reason": feedback,
                "trigger_event_type": trigger_event.type,
                "trigger_event_id": trigger_event.id,
                "required_actions": required_actions,
                "base_git_head": base_git_head,
                "base_files_touched": (
                    list(base_git_context.files_touched)
                    if base_git_context is not None
                    else []
                ),
                "base_dirty_files": (
                    list(base_git_context.dirty_files)
                    if base_git_context is not None
                    else []
                ),
                "base_diff_hash": (
                    base_git_context.diff_hash
                    if base_git_context is not None
                    else ""
                ),
                "base_dispatch_id": previous_dispatch_id,
                "backedge_event": (
                    str(getattr(backedge, "event", "") or "")
                    if backedge is not None
                    else ""
                ),
                "restart_stage": (
                    str(getattr(backedge, "restart_stage", "") or "")
                    if backedge is not None
                    else ""
                ),
                "target_affinity": (
                    str(getattr(backedge, "target_affinity", "") or "")
                    if backedge is not None
                    else ""
                ),
                "lane_id": (
                    self._backedge_lane_id(task, trigger_event)
                    if backedge is not None
                    else ""
                ),
                "feedback_artifact": (
                    str(getattr(backedge, "feedback_artifact", "") or "")
                    if backedge is not None
                    else ""
                ),
                "feedback_artifact_ref": feedback_artifact_ref,
                "backedge_emit": (
                    str(getattr(backedge, "emit", "") or "")
                    if backedge is not None
                    else ""
                ),
                "failed_d": (
                    trigger_event.payload.get("failed_d", [])
                    if isinstance(trigger_event.payload, dict)
                    else []
                ),
            },
            causation_id=trigger_event.id,
            correlation_id=trigger_event.correlation_id,
        ))
        self._write_rework_feedback_artifact(
            artifact_ref=feedback_artifact_ref,
            task=task,
            role=role,
            trigger_event=trigger_event,
            rework_request=rework_request,
            feedback=feedback,
            required_actions=required_actions,
            max_attempts=max_attempts,
        )
        backedge_emit = str(getattr(backedge, "emit", "") or "")
        if backedge_emit:
            self.event_writer.append(ZfEvent(
                type=backedge_emit,
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "task_id": task.id,
                    "attempt": task.retry_count,
                    "max_attempts": max_attempts,
                    "role": role.name,
                    "assignee": role.instance_id,
                    "trigger_event_type": trigger_event.type,
                    "trigger_event_id": trigger_event.id,
                    "task_rework_request_event_id": rework_request.id,
                    "restart_stage": str(getattr(backedge, "restart_stage", "") or ""),
                    "restart_role": str(getattr(backedge, "restart_role", "") or ""),
                    "target_affinity": str(
                        getattr(backedge, "target_affinity", "") or ""
                    ),
                    "lane_id": self._backedge_lane_id(task, trigger_event),
                    "feedback_artifact": str(
                        getattr(backedge, "feedback_artifact", "") or ""
                    ),
                    "feedback_artifact_ref": feedback_artifact_ref,
                    "reason": feedback,
                },
                causation_id=rework_request.id,
                correlation_id=rework_request.correlation_id,
            ))
        try:
            self.task_store.update(
                task.id,
                status="in_progress",
                assigned_to=role.instance_id,
                active_dispatch_id=dispatch_id,
            )
        except Exception:
            pass
        self.event_writer.append(ZfEvent(
            type="task.assigned",
            actor="zf-cli",
            task_id=task.id,
            payload={
                "role": role.name,
                "assignee": role.instance_id,
                "source": "rework",
                "trigger_event": trigger_event.type,
                "rework_request_event_id": rework_request.id,
                "dispatch_id": dispatch_id,
            },
            causation_id=rework_request.id,
            correlation_id=rework_request.correlation_id,
        ))
        # Completion command in the briefing derives from role.publishes
        # (P2-1) so critic / arch / doc roles see their own success event
        # rather than hardcoded dev.build.done.
        from zf.runtime.injection import infer_completion_protocol
        protocol = infer_completion_protocol(role)
        required_section = ""
        if required_actions:
            required_section = (
                "\n## Required Rework Items\n"
                "Address every item below before emitting the success event. "
                "If no source change is needed, state the concrete evidence in "
                "the final response and add/update a regression test or doc "
                "that prevents the same gate failure from recurring.\n"
                + "\n".join(f"- {item}" for item in required_actions)
                + "\n"
            )
        payload_excerpt = _payload_excerpt(trigger_event.payload)
        payload_section = ""
        if payload_excerpt:
            payload_section = (
                "\n## Trigger Payload Evidence\n"
                "```json\n"
                f"{payload_excerpt}\n"
                "```\n"
            )
        feedback_artifact_section = ""
        if feedback_artifact_ref:
            feedback_artifact_section = (
                "\n## Feedback Artifact\n"
                f"- feedback_artifact_ref: `{feedback_artifact_ref}`\n"
                "Load this file before editing; it contains the durable "
                "rejection summary for this rework attempt.\n"
            )
        task_ref_repair_section = ""
        if trigger_event.type == "task.ref.repair.requested":
            if _task_ref_scope_repair_payload(trigger_event.payload):
                task_ref_repair_section = (
                    "\n## Task Ref Source Scope Repair Contract\n"
                    "The rejected `source_commit` contains files outside this "
                    "task's contract scope. This is a source-scope repair, not "
                    "a metadata-only handoff repair.\n"
                    "- Do not emit a metadata-only repair and do not reuse the "
                    "rejected `source_commit`.\n"
                    "- Create or select a new `source_commit` whose diff "
                    "contains only repo-relative files allowed by this task's "
                    "contract scope, then emit that new commit with "
                    "`source_branch`, `workdir`, and `files_touched`.\n"
                    "- Keep `changed_files`, `files_touched`, and "
                    "`artifact_refs` to repo-relative source/artifact paths "
                    "inside the task contract scope.\n"
                    "- Put non-file evidence such as `git:<sha>`, "
                    "`branch:<name>`, briefing paths, and diagnostics in "
                    "`evidence_refs` only.\n"
                    "- If the rejected commit cannot be split without losing "
                    "required work, emit `dev.failed` with the concrete blocker "
                    "instead of re-emitting the same rejected commit.\n"
                )
            else:
                task_ref_repair_section = (
                    "\n## Task Ref Repair Handoff Contract\n"
                    "This repair is complete only when the next `dev.build.done` "
                    "payload can be accepted by TaskRefManager in worktree mode.\n"
                    "- Emit top-level `source_commit`, `source_branch`, `workdir`, "
                    "and `files_touched` fields for the current writer worktree.\n"
                    "- Keep `changed_files`, `files_touched`, and `artifact_refs` "
                    "to repo-relative source/artifact paths inside the task contract "
                    "scope; use `[]` when this is a metadata-only repair.\n"
                    "- Do not put `git:<sha>`, `branch:<name>`, `briefing:<file>`, "
                    "or other non-file evidence URIs in `changed_files`, "
                    "`files_touched`, or `artifact_refs`; put those in "
                    "`evidence_refs` only.\n"
                    "- Do not commit generated Codex hook state. If the only dirty "
                    "file is `.codex/hooks.json`, either clean it before emitting or "
                    "declare `worktree_dirty: true`, "
                    "`dirty_files: [\".codex/hooks.json\"]`, and a "
                    "`dirty_scope_note`.\n"
                )
        if base_git_context is not None:
            git_section = (
                "\n\n## Git Evidence Context\n"
                + render_git_diff_context(base_git_context)
            )
        else:
            git_section = _git_evidence_section(self.project_root, base_git_head)
        rework_briefing = (
            f"## Rework Required: {task.id}\n"
            f"**Attempt**: {task.retry_count}/{max_attempts}\n"
            f"**Title**: {task.title}\n"
            f"**Role**: {role.name}\n"
            f"**Dispatch ID**: `{dispatch_id}`\n"
            f"**Trigger**: {trigger_event.type}\n"
            f"**Reason**: {feedback}\n\n"
            f"{required_section}"
            f"{payload_section}"
            f"{feedback_artifact_section}"
            f"{task_ref_repair_section}"
            f"{git_section}\n\n"
            f"Please fix the issues and run:\n"
            f"```bash\n{zf_cli_cmd()} emit {protocol.success_event} --task {task.id} "
            f"--actor {role.instance_id} --dispatch-id {dispatch_id}\n```\n"
        )
        briefing_dir = self.state_dir / "briefings"
        briefing_dir.mkdir(parents=True, exist_ok=True)
        briefing_path = briefing_dir / f"{role.name}-{task.id}-rework.md"
        briefing_path.write_text(rework_briefing)
        skill_entries = self._record_skill_provenance(role=role, task_id=task.id)
        instructions = generate_role_instructions(
            self.config,
            role,
            task=task,
            skill_entries=skill_entries,
        )
        instructions_dir = self.state_dir / "instructions"
        instructions_dir.mkdir(parents=True, exist_ok=True)
        (instructions_dir / f"{role.instance_id}.md").write_text(instructions)

        prompt = build_task_prompt(role.instance_id, briefing_path)
        context = self._dispatch_context(
            role=role,
            briefing_path=briefing_path,
            task_id=task.id,
            trace_id=trigger_event.correlation_id,
        )
        self._send_transport_task(role.instance_id, briefing_path, prompt, context)
        self._get_spawn_coordinator().notify_first_dispatch(role)
        self._clear_dispatch_failure(task.id)
        self.event_writer.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id=task.id,
            payload={
                "role": role.name,
                "assignee": role.instance_id,
                "briefing": str(briefing_path),
                "source": "rework",
                "trigger_event": trigger_event.type,
                "rework_request_event_id": rework_request.id,
                "base_git_head": base_git_head or "",
                "dispatch_id": dispatch_id,
            },
            causation_id=rework_request.id,
            correlation_id=rework_request.correlation_id,
        ))
        if _is_writer_role(role) and base_git_head:
            self._dispatch_heads[task.id] = base_git_head
        self._set_worker_state(
            role.instance_id, "busy",
            reason=f"rework dispatched for task {task.id}",
        )
        self._dispatch_epoch[task.id] = self._now()
        self._orphan_warned.discard(task.id)
        return role.name

    def _rework_dispatch_block_reason(
        self,
        task: Task,
        role: RoleConfig,
        trigger_event: ZfEvent | None = None,
    ) -> str:
        if not self._worker_dispatchable(role.instance_id):
            if not self._can_repair_task_ref_on_blocked_owner(
                task,
                role,
                trigger_event,
            ):
                return "rework_target_not_dispatchable"
        try:
            runtime_events = self.event_log.read_all()
        except Exception:
            runtime_events = []
        latest_dispatch_meta = self._latest_dispatch_meta_by_task(runtime_events)
        active_others: list[str] = []
        for other in self.task_store.list_all():
            if other.id == task.id or other.status != "in_progress":
                continue
            dispatch_idx, dispatched_to, dispatch_id = latest_dispatch_meta.get(
                other.id,
                (-1, "", ""),
            )
            if not dispatched_to:
                continue
            if self._dispatch_has_terminal_after(
                events=runtime_events,
                task_id=other.id,
                dispatch_idx=dispatch_idx,
                dispatch_id=dispatch_id,
            ):
                continue
            try:
                same_worker = self._assignee_equivalent(
                    dispatched_to,
                    role.instance_id,
                )
            except Exception:
                same_worker = dispatched_to == role.instance_id
            if same_worker:
                active_others.append(other.id)
        if len(active_others) >= self.wip.limit:
            return "rework_target_busy:" + ",".join(sorted(active_others))
        return ""

    def _latest_dispatch_meta_by_task(
        self,
        events: list[ZfEvent],
    ) -> dict[str, tuple[int, str, str]]:
        latest: dict[str, tuple[int, str, str]] = {}
        for idx, event in enumerate(events):
            if event.type != "task.dispatched" or not event.task_id:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            assignee = str(payload.get("assignee") or payload.get("role") or "")
            if not assignee:
                continue
            latest[event.task_id] = (
                idx,
                assignee,
                str(payload.get("dispatch_id") or ""),
            )
        return latest

    def _dispatch_has_terminal_after(
        self,
        *,
        events: list[ZfEvent],
        task_id: str,
        dispatch_idx: int,
        dispatch_id: str,
    ) -> bool:
        if dispatch_idx < 0:
            return False
        for event in events[dispatch_idx + 1:]:
            payload = event.payload if isinstance(event.payload, dict) else {}
            payload_task_id = str(payload.get("task_id") or "")
            if event.task_id != task_id and payload_task_id != task_id:
                continue
            if event.type in {"fanout.child.completed", "fanout.child.failed"}:
                return True
            if (
                event.type in self._STAGE_PROGRESS_EVENTS
                or event.type in self._REWORK_TRIGGER_EVENTS
            ):
                event_dispatch_id = str(payload.get("dispatch_id") or "")
                if dispatch_id and event_dispatch_id and event_dispatch_id != dispatch_id:
                    continue
                return True
        return False

    def _can_repair_task_ref_on_blocked_owner(
        self,
        task: Task,
        role: RoleConfig,
        trigger_event: ZfEvent | None,
    ) -> bool:
        if trigger_event is None or trigger_event.type != "task.ref.repair.requested":
            return False
        state = getattr(self, "_last_worker_state", {}).get(role.instance_id, "idle")
        if state != "blocked_human":
            return False
        assignee = str(getattr(task, "assigned_to", "") or "")
        try:
            return self._assignee_equivalent(assignee, role.instance_id)
        except Exception:
            return assignee == role.instance_id

    def _emit_remediation_shadow(
        self, task, trigger_event, triage,
    ) -> None:
        """K3 相 3:remediation 统一决策的影子发射(只记录不执行)。

        分类共享 rework_triage(单一分类源,防 7→N 爆炸);切换条件 =
        真实 round 影子对比零分歧(docs/records 归档),在那之前现行
        路径是唯一权威。失败静默(影子不得影响主路)。
        """
        try:
            from zf.runtime.remediation_pipeline import route as remediation_route

            classification = str(getattr(triage, "classification", "") or "")
            failure_class = {
                "evidence_payload_gap": "kernel-logic",
            }.get(classification, "content")
            attempts = int(getattr(task, "rework_attempts", 0) or 0)
            decision = remediation_route(
                failure_class,
                attempts=attempts,
                authorized=False,
            )
            self.event_writer.append(ZfEvent(
                type="remediation.decision.shadow",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "trigger_event_type": trigger_event.type,
                    "triage_classification": classification,
                    "shadow_tier": str(getattr(decision, "tier", "")),
                    "shadow_action": str(getattr(decision, "action", "")),
                    "attempts": attempts,
                    "mode": "shadow_only",
                },
                causation_id=trigger_event.id,
            ))
        except Exception:
            return

    def _route_rework_trigger(
        self,
        task: Task,
        trigger_event: ZfEvent,
        *,
        reason: str,
    ) -> OrchestratorDecision:
        """失败路由主枢纽 —— K3 相 1 决策表(2026-06-11 审计 Q1 收敛)。

        事件 → 分支 → cap 全映射(本表是唯一权威;reactor 的
        _on_review_rejected/_on_test_failed/_on_judge_failed/
        _on_discriminator_failed/_on_task_done_blocked/
        _on_completion_stale_rejected 全部汇入此处):

        | triage 分类 | 分支 | cap(三者独立计数,勿混) |
        |---|---|---|
        | REWORK_RETRY_* | _dispatch_rework → 同 lane/角色重做 | max_rework_attempts(yaml 可配,backedge) |
        | evidence_payload_gap | _dispatch_evidence_reissue → 证据补发 | _MAX_EVIDENCE_REISSUE=3(硬编码,非重做) |
        | infra/respawn 族 | 不进本枢纽 —— lifecycle watchdog 独立路径 | respawn 连败熔断(_consecutive_respawn_failures) |
        | terminal/plan 级 | block / orchestrator.replan_requested | — |

        候选级(无 task_id)失败不进本枢纽 → candidate_rework.plan_candidate_rework
        (分界符 = event.task_id,见该模块头注互链)。
        dev.blocked 也不进(阻塞 ≠ 失败,_on_dev_blocked 直接 block+escalate)。

        K3 相 3(影子):remediation_pipeline.route 的统一三层决策以
        shadow 事件并行发射,零执行;分歧即 bug 线索,切换前必须零分歧。
        """
        if self._fanout_scoped_stage_progress_event(trigger_event):
            return OrchestratorDecision(
                action="ignore",
                task_id=task.id,
                reason=f"{reason}: fanout-scoped progress owned by fanout runtime",
            )
        triage = self._ensure_rework_triage(trigger_event)
        self._emit_remediation_shadow(task, trigger_event, triage)
        if triage.classification in REWORK_RETRY_CLASSIFICATIONS:
            if task.status not in {"done", "cancelled", "blocked"}:
                try:
                    self.task_store.update(task.id, status="in_progress")
                    task = self.task_store.get(task.id) or task
                except Exception:
                    pass
            dispatched_role = self._dispatch_rework(task, trigger_event)
            if dispatched_role is None:
                wait_reason = self._rework_defer_reason(task, trigger_event)
                if wait_reason:
                    return OrchestratorDecision(
                        action="wait",
                        task_id=task.id,
                        reason=f"{reason}: rework deferred ({wait_reason})",
                    )
                return OrchestratorDecision(
                    action="block",
                    task_id=task.id,
                    reason=f"{reason}: rework unavailable or capped",
                )
            return OrchestratorDecision(
                action="dispatch",
                task_id=task.id,
                role=dispatched_role,
                reason=reason,
            )

        if triage.classification == "evidence_payload_gap":
            dispatched_role = self._dispatch_evidence_reissue(
                task,
                trigger_event,
                triage,
            )
            if dispatched_role is None:
                return OrchestratorDecision(
                    action="block",
                    task_id=task.id,
                    reason=f"{reason}: evidence reissue target unavailable",
                )
            return OrchestratorDecision(
                action="dispatch",
                task_id=task.id,
                role=dispatched_role,
                reason=f"{reason}: evidence reissue",
            )

        self._block_rework_for_triage(task, trigger_event, triage)
        return OrchestratorDecision(
            action="block",
            task_id=task.id,
            role=triage.suspected_owner,
            reason=f"{reason}: {triage.classification}",
        )

    def _rework_defer_reason(
        self,
        task: Task,
        trigger_event: ZfEvent,
    ) -> str:
        """Return a transient reason when rework should stay pending.

        ``_dispatch_rework`` returns ``None`` for both hard failures
        (unresolvable role / cap) and soft availability failures. The caller
        must not turn a busy same-lane repair into a terminal block: in lane
        pipelines, a writer lane can be reused by another queued module before
        an older task-ref repair is discovered. That repair should wait until
        the lane is free, then dispatch from the same pending event.
        """
        try:
            role = self._resolve_rework_role(task, trigger_event)
        except Exception:
            role = None
        if role is None:
            return ""
        try:
            reason = self._rework_dispatch_block_reason(
                task,
                role,
                trigger_event,
            )
        except Exception:
            return ""
        if reason.startswith("rework_target_busy:"):
            return reason
        return ""

    # Evidence reissue retry cap (backlog 2026-05-14-1440). Same shape
    # as dispatch retry cap (1311) and respawn retry cap (1439): when
    # a task accumulates ≥3 evidence_reissue dispatches whose triggering
    # judge.passed (or other terminal) payload keeps failing the
    # terminal_done_hardening gate, the harness must stop trying and
    # park the task as blocked for operator intervention.
    _MAX_EVIDENCE_REISSUE = 3

    def _evidence_reissue_exhausted(self, task_id: str) -> int:
        registry = getattr(self, "_evidence_reissue_registry", None)
        if registry is None:
            return 0
        return registry.get(task_id, 0)

    def _record_evidence_reissue(self, task_id: str) -> int:
        registry = getattr(self, "_evidence_reissue_registry", None)
        if registry is None:
            registry = {}
            self._evidence_reissue_registry = registry
        registry[task_id] = registry.get(task_id, 0) + 1
        return registry[task_id]

    def _clear_evidence_reissue(self, task_id: str) -> None:
        registry = getattr(self, "_evidence_reissue_registry", None)
        if registry is not None:
            registry.pop(task_id, None)

    def _dispatch_evidence_reissue(
        self,
        task: Task,
        trigger_event: ZfEvent,
        triage,
    ) -> str | None:
        # Cap: if this task has already had MAX reissue attempts that
        # have not produced a clean terminal payload, refuse another
        # dispatch and park the task blocked.
        prior = self._evidence_reissue_exhausted(task.id)
        if prior >= self._MAX_EVIDENCE_REISSUE:
            try:
                self.event_writer.append(ZfEvent(
                    type="task.evidence.reissue.exhausted",
                    actor="zf-cli",
                    task_id=task.id,
                    payload={
                        "attempts": prior,
                        "max_attempts": self._MAX_EVIDENCE_REISSUE,
                        "trigger_event_id": trigger_event.id,
                        "last_missing": list(
                            (trigger_event.payload or {}).get("missing", []),
                        ) if isinstance(trigger_event.payload, dict) else [],
                    },
                    causation_id=trigger_event.id,
                    correlation_id=trigger_event.correlation_id,
                ))
            except Exception:
                pass
            try:
                self.task_store.update(task.id, status="blocked")
            except Exception:
                pass
            try:
                self.escalation.escalate(
                    f"task {task.id}: evidence reissue exhausted "
                    f"({prior} attempts); operator review required"
                )
            except Exception:
                pass
            return None

        role = self._evidence_reissue_role(task, trigger_event, triage)
        if role is None or role.name == "orchestrator":
            self._emit_triage_blocked(
                task,
                trigger_event,
                triage,
                reason="evidence reissue role not found",
            )
            return None
        self._record_evidence_reissue(task.id)

        dispatch_id = _new_dispatch_id()
        previous_dispatch_id = getattr(task, "active_dispatch_id", "")
        self._remember_dispatch_id(task.id, dispatch_id)  # B-STUCK-1
        task.active_dispatch_id = dispatch_id
        try:
            self.task_store.update(
                task.id,
                status="in_progress",
                assigned_to=role.instance_id,
                active_dispatch_id=dispatch_id,
            )
        except Exception:
            pass

        missing = []
        if isinstance(trigger_event.payload, dict):
            raw_missing = (
                trigger_event.payload.get("missing")
                or trigger_event.payload.get("violations")
            )
            if isinstance(raw_missing, list):
                missing = list(raw_missing)
            elif isinstance(raw_missing, str) and raw_missing.strip():
                missing = [raw_missing.strip()]
            elif trigger_event.payload.get("reason"):
                missing = [str(trigger_event.payload.get("reason"))]
        if not missing and getattr(triage, "notes", ""):
            missing = [str(triage.notes)]

        request_event = self.event_writer.append(ZfEvent(
            type="task.evidence.reissue.requested",
            actor="zf-cli",
            task_id=task.id,
            payload={
                "role": role.name,
                "assignee": role.instance_id,
                "classification": triage.classification,
                "gate_rule": triage.gate_rule,
                "trigger_event_type": trigger_event.type,
                "trigger_event_id": trigger_event.id,
                "base_dispatch_id": previous_dispatch_id,
                "dispatch_id": dispatch_id,
                "missing": missing,
            },
            causation_id=trigger_event.id,
            correlation_id=trigger_event.correlation_id,
        ))
        self.event_writer.append(ZfEvent(
            type="task.assigned",
            actor="zf-cli",
            task_id=task.id,
            payload={
                "role": role.name,
                "assignee": role.instance_id,
                "source": "evidence_reissue",
                "trigger_event": trigger_event.type,
                "evidence_reissue_event_id": request_event.id,
                "dispatch_id": dispatch_id,
                "reissue": True,
                "force_dispatch": True,
            },
            causation_id=request_event.id,
            correlation_id=request_event.correlation_id,
        ))

        from zf.runtime.injection import infer_completion_protocol
        protocol = infer_completion_protocol(role)
        payload_excerpt = _payload_excerpt(trigger_event.payload)
        payload_section = ""
        if payload_excerpt:
            payload_section = (
                "\n## Trigger Payload Evidence\n"
                "```json\n"
                f"{payload_excerpt}\n"
                "```\n"
            )
        # Backlog 2026-05-14-1441: synthesize a "Required Payload Shape"
        # section that turns the gate's free-text `missing` list into a
        # concrete JSON shape example. Without this the worker has to
        # guess the canonical field names from the rejection message —
        # which caused judge to loop in cangjie r3.
        shape_section = _render_required_payload_shape(
            trigger_event,
            protocol.success_event,
        )
        reissue_briefing = (
            f"## Evidence Reissue Required: {task.id}\n"
            f"**Title**: {task.title}\n"
            f"**Role**: {role.name}\n"
            f"**Dispatch ID**: `{dispatch_id}`\n"
            f"**Trigger**: {trigger_event.type}\n"
            f"**Classification**: {triage.classification}\n"
            f"**Gate Rule**: {triage.gate_rule}\n\n"
            "The previous completion claim was blocked by missing or malformed "
            "evidence. Do not change product code unless the missing evidence "
            "proves the implementation is actually wrong. Re-emit your success "
            "event with concrete artifact_refs, evidence_refs, command/check "
            "output, and required verification tiers.\n"
            f"{payload_section}"
            f"{shape_section}\n"
            "When the evidence is ready, run:\n"
            "```bash\n"
            f"{zf_cli_cmd()} emit {protocol.success_event} --task {task.id} "
            f"--actor {role.instance_id} --dispatch-id {dispatch_id}\n"
            "```\n"
        )
        briefing_dir = self.state_dir / "briefings"
        briefing_dir.mkdir(parents=True, exist_ok=True)
        briefing_path = briefing_dir / f"{role.name}-{task.id}-evidence.md"
        briefing_path.write_text(reissue_briefing)

        skill_entries = self._record_skill_provenance(role=role, task_id=task.id)
        instructions = generate_role_instructions(
            self.config,
            role,
            task=task,
            skill_entries=skill_entries,
        )
        instructions_dir = self.state_dir / "instructions"
        instructions_dir.mkdir(parents=True, exist_ok=True)
        (instructions_dir / f"{role.instance_id}.md").write_text(instructions)

        prompt = build_task_prompt(role.instance_id, briefing_path)
        context = self._dispatch_context(
            role=role,
            briefing_path=briefing_path,
            task_id=task.id,
            trace_id=trigger_event.correlation_id,
        )
        try:
            self._send_transport_task(role.instance_id, briefing_path, prompt, context)
        except Exception as exc:
            self._active_dispatch_ids.pop(task.id, None)
            try:
                payload = {
                    "role": role.name,
                    "assignee": role.instance_id,
                    "briefing": str(briefing_path),
                    "dispatch_id": dispatch_id,
                    "source": "evidence_reissue",
                    "error": str(exc),
                }
                payload.update(transport_error_diagnostics(exc))
                self.event_writer.append(ZfEvent(
                    type="orchestrator.dispatch_failed",
                    actor="zf-cli",
                    task_id=task.id,
                    payload=payload,
                    causation_id=request_event.id,
                    correlation_id=request_event.correlation_id,
                ))
            except Exception:
                pass
            self._record_dispatch_failure(task.id)
            return None

        self._get_spawn_coordinator().notify_first_dispatch(role)
        self._clear_dispatch_failure(task.id)
        self.event_writer.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id=task.id,
            payload={
                "role": role.name,
                "assignee": role.instance_id,
                "briefing": str(briefing_path),
                "source": "evidence_reissue",
                "trigger_event": trigger_event.type,
                "evidence_reissue_event_id": request_event.id,
                "dispatch_id": dispatch_id,
            },
            causation_id=request_event.id,
            correlation_id=request_event.correlation_id,
        ))
        self._set_worker_state(
            role.instance_id,
            "busy",
            reason=f"evidence reissue dispatched for task {task.id}",
        )
        self._dispatch_epoch[task.id] = self._now()
        self._orphan_warned.discard(task.id)
        return role.name

    def _evidence_reissue_role(self, task: Task, trigger_event: ZfEvent, triage):
        candidates: list[str] = []
        payload = trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
        for value in (
            task.assigned_to or "",
            trigger_event.actor or "",
            str(payload.get("actor") or ""),
            str(payload.get("role") or ""),
            str(triage.suspected_owner or ""),
        ):
            if value and value != "zf-cli" and value not in candidates:
                candidates.append(value)
        trigger = str(payload.get("trigger_event") or "")
        if trigger.startswith("judge.") and "judge" not in candidates:
            candidates.append("judge")
        elif trigger.startswith("test.") and "test" not in candidates:
            candidates.append("test")
        elif trigger.startswith("review.") and "review" not in candidates:
            candidates.append("review")
        for candidate in candidates:
            role = (
                self._find_role_by_instance(candidate)
                or self._find_role_by_name(candidate)
            )
            if role is not None:
                return role
        return None

    def _block_rework_for_triage(self, task: Task, trigger_event: ZfEvent, triage) -> None:
        try:
            self.task_store.update(
                task.id,
                status="blocked",
                blocked_reason=f"rework_triage:{triage.classification}",
            )
        except Exception:
            pass
        self._emit_triage_blocked(
            task,
            trigger_event,
            triage,
            reason=triage.notes or triage.recommended_action,
        )
        try:
            self.event_writer.append(ZfEvent(
                type="human.escalate",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "reason": (
                        f"rework triage blocked {trigger_event.type}: "
                        f"{triage.classification}"
                    ),
                    "origin_event": trigger_event.type,
                    "origin_event_id": trigger_event.id,
                    "classification": triage.classification,
                    "recommended_action": triage.recommended_action,
                },
                causation_id=trigger_event.id,
                correlation_id=trigger_event.correlation_id,
            ))
        except Exception:
            pass

    def _emit_triage_blocked(
        self,
        task: Task,
        trigger_event: ZfEvent,
        triage,
        *,
        reason: str,
    ) -> None:
        try:
            self.event_writer.append(ZfEvent(
                type="task.rework.triage.blocked",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "classification": triage.classification,
                    "gate_rule": triage.gate_rule,
                    "suspected_owner": triage.suspected_owner,
                    "recommended_action": triage.recommended_action,
                    "should_increment_retry": triage.should_increment_retry,
                    "trigger_event_type": trigger_event.type,
                    "trigger_event_id": trigger_event.id,
                    "reason": reason,
                },
                causation_id=trigger_event.id,
                correlation_id=trigger_event.correlation_id,
            ))
        except Exception:
            pass

    def _rework_feedback(self, trigger_event: ZfEvent) -> str:
        payload = (
            trigger_event.payload
            if isinstance(trigger_event.payload, dict)
            else {}
        )
        reason = str(payload.get("reason") or "").strip()
        if reason:
            return reason
        summary = str(payload.get("summary") or "").strip()
        if summary:
            return summary
        required_actions = _rework_required_actions(payload)
        if required_actions:
            return "; ".join(required_actions[:3])
        details = payload.get("details")
        if isinstance(details, list):
            parts: list[str] = []
            for item in details[:3]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("d") or item.get("name") or "").strip()
                why = str(item.get("reason") or "").strip()
                hints = _discriminator_failure_hints(item.get("evidence"))
                if name and why:
                    text = f"{name}: {why}"
                elif why:
                    text = why
                else:
                    text = name
                if text and hints:
                    text = f"{text} ({'; '.join(hints[:3])})"
                if text:
                    parts.append(text)
            if parts:
                return "; ".join(parts)
        failed_d = payload.get("failed_d")
        if isinstance(failed_d, list) and failed_d:
            return f"{trigger_event.type}: {', '.join(map(str, failed_d))}"
        return trigger_event.type

    def _emit_rework_capped(
        self,
        task: Task,
        role: RoleConfig,
        trigger_event: ZfEvent,
        *,
        max_attempts: int | None = None,
        max_attempts_source: str = "role",
    ) -> None:
        """LH-0.T1: emit task.rework.capped + escalate when retry_count
        exceeds max_rework_attempts. Called instead of dispatching."""
        effective_max_attempts = (
            int(max_attempts)
            if max_attempts is not None
            else int(role.max_rework_attempts)
        )
        reason = trigger_event.payload.get("reason") if isinstance(
            trigger_event.payload, dict
        ) else None
        try:
            self.event_writer.append(ZfEvent(
                type="task.rework.capped",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "role": role.name,
                    "retry_count": task.retry_count,
                    "max_attempts": effective_max_attempts,
                    "max_attempts_source": max_attempts_source,
                    "last_reason": reason or trigger_event.type,
                    "trigger_event_type": trigger_event.type,
                },
            ))
        except Exception:
            pass
        # Route to human — rework cap is a genuine dead-end.
        try:
            self.escalation.escalate(
                f"task {task.id}: rework cap "
                f"({task.retry_count}/{effective_max_attempts}) exceeded"
            )
        except Exception:
            pass

    # -- log capture --

    _NON_DISPATCHABLE_WORKER_STATES: frozenset[str] = frozenset({
        "stuck",
        "dead",
        "respawning",
        "recycling",
        "pending_recycle",
        "draining",
        "retired",
        "stopped",
        "cancelling",
        "blocked_human",
    })
