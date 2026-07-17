"""Generate per-role instructions, task briefings, and the prompt that points an agent at them."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from zf.core.config.schema import ZfConfig, RoleConfig
from zf.core.feature.schema import Feature
from zf.core.skills.provenance import SkillLockEntry
from zf.core.task.schema import Task
from zf.runtime.cli_command import zf_cli_cmd


@dataclass
class CompletionProtocol:
    """What events a role should emit when finishing a task.

    Inferred from role.publishes rather than a hardcoded role-name map,
    so custom YAML roles get correct protocols without Python changes.

    Priority ordering (higher = preferred as the "primary" event):
      success:  approved > done > passed
      failure:  rejected > failed > blocked
      suspend:  any *.suspended (LH-3 Tri-State)
    """
    success_event: str
    failure_event: str | None = None
    suspend_event: str | None = None
    other_events: tuple[str, ...] = ()


_SUCCESS_PRIORITY = (".approved", ".done", ".passed")
_FAILURE_PRIORITY = (".rejected", ".failed", ".blocked")


def _pick_by_suffix(
    events: list[str], suffixes: tuple[str, ...]
) -> str | None:
    """Return the first event matching a suffix (priority order)."""
    for suffix in suffixes:
        for event in events:
            if event.endswith(suffix):
                return event
    return None


def infer_completion_protocol(role: RoleConfig) -> CompletionProtocol:
    """Derive a role's completion protocol from role.publishes.

    If publishes is empty or no recognized suffix matches, falls back
    to `{role.name}.done` so callers never crash.
    """
    publishes = list(role.publishes) if role.publishes else []

    success = _pick_by_suffix(publishes, _SUCCESS_PRIORITY)
    failure = _pick_by_suffix(publishes, _FAILURE_PRIORITY)
    suspend = next(
        (e for e in publishes if e.endswith(".suspended")), None
    )

    if success is None:
        if publishes:
            # Publishes exist but none match success suffixes. Pick the
            # first event not already classified as failure/suspend — a
            # reasonable guess for custom roles like `doc` with
            # `doc.updated`.
            non_negative = [
                e for e in publishes if e != failure and e != suspend
            ]
            success = non_negative[0] if non_negative else publishes[0]
        else:
            # publishes is empty — use the conventional `{role}.done`
            success = f"{role.name}.done"

    other = tuple(
        e for e in publishes
        if e not in (success, failure, suspend)
    )

    return CompletionProtocol(
        success_event=success,
        failure_event=failure,
        suspend_event=suspend,
        other_events=other,
    )


def generate_role_instructions(
    config: ZfConfig,
    role: RoleConfig,
    *,
    task: Task | None = None,
    skill_entries: list[SkillLockEntry] | None = None,
    state_dir_ref: Path | str | None = None,
    project_root: Path | str | None = None,
) -> str:
    """Generate CLAUDE.md content for a role."""
    sections: list[str] = []

    # Header
    sections.append(f"# {config.project.name} — Role: {role.name}")
    sections.append("")

    # Role identity
    if role.name == "orchestrator":
        sections.append("## Role: Orchestrator")
        sections.append("You are the orchestrator. You read events, kanban state, and worker output")
        sections.append("to make dispatch and recovery decisions. You do NOT write code.")
    elif role.name == "run-manager":
        sections.append("## Role: Resident Run Manager")
        sections.append(
            "You observe the whole run, summarize status, and recommend bounded "
            "recovery actions through zf events or CLI commands."
        )
        sections.append(
            "You do NOT edit kernel-managed runtime state directly, do NOT merge "
            "branches, and do NOT modify source code unless a bounded repair task "
            "explicitly assigns that work."
        )
    else:
        sections.append(f"## Role: {role.name}")
        sections.append(f"You are the **{role.name}** worker in the {config.project.name} harness.")

    sections.append("")

    # Constraints
    sections.append("## Constraints")
    if role.constraints.allowed_paths:
        sections.append(f"- Allowed paths: {', '.join(role.constraints.allowed_paths)}")
    if role.constraints.blocked_paths:
        sections.append(f"- Blocked paths (DO NOT modify): {', '.join(role.constraints.blocked_paths)}")
    if role.constraints.max_steps:
        sections.append(f"- Max steps: {role.constraints.max_steps}")
    sections.append("")

    # Tools
    if role.allowed_tools:
        sections.append("## Allowed Tools")
        for tool in role.allowed_tools:
            sections.append(f"- {tool}")
        sections.append("")

    # Stages
    if role.stages:
        sections.append("## Stages")
        sections.append(f"You participate in: {', '.join(role.stages)}")
        sections.append("")

    if state_dir_ref is not None:
        try:
            from zf.runtime.briefing_hydration import render_run_contract_context

            run_contract_context = render_run_contract_context(
                state_dir_ref,
                project_root=project_root,
            )
        except Exception:
            run_contract_context = ""
        if run_contract_context:
            sections.append(run_contract_context)

    # P-Y2/P-SKILL: skills are declared by role.skills and resolved by the
    # runtime materializer. Render name + description so providers that do
    # not auto-discover skills still receive enough activation context.
    # Route auto-inject and load-on-demand skills separately so a role with a
    # large skill set does not receive every skill as an equally active prompt.
    if role.skills:
        entries_by_name = {
            entry.name: entry for entry in (skill_entries or [])
        }
        auto_lines: list[str] = []
        demand_lines: list[str] = []
        ordered_skills = list(role.skills)
        for entry in skill_entries or []:
            if entry.name not in ordered_skills:
                ordered_skills.append(entry.name)
        for skill in ordered_skills:
            entry = entries_by_name.get(skill)
            if entry is None:
                demand_lines.append(f"- `/{skill}`")
                continue
            line = _render_skill_instruction_line(skill, entry)
            if entry.auto_inject:
                auto_lines.append(line)
            else:
                demand_lines.append(line)
        if auto_lines:
            sections.append("## Auto-Injected Skills")
            sections.append(
                "These skills are already injected for this role. Use only "
                "the listed skills when their description matches the task:"
            )
            sections.extend(auto_lines)
            sections.append("")
        if demand_lines:
            sections.append("## Load-On-Demand Skills")
            sections.append(
                "These skills are available for this role as references. Load "
                "one only when the current task needs it:"
            )
            sections.extend(demand_lines)
            sections.append("")
        sections.extend(_backend_skill_discipline_lines(role))

    # Event commands
    sections.append("## Event Commands")
    sections.append("Use these to communicate with the harness:")
    cli_cmd = zf_cli_cmd()
    sections.append(f"- `{cli_cmd} emit <event-type> --task <TASK-ID>` — emit an event")
    sections.append(f"- `{cli_cmd} events --last 10` — view recent events")
    sections.append(f"- `{cli_cmd} status` — view harness status")
    sections.append(f"- `{cli_cmd} kanban` — view task board")
    sections.append("")

    if task is None:
        protocol = infer_completion_protocol(role)
        _append_configured_completion_contract(sections, config, role, protocol)
        if sections[-1] != "":
            sections.append("")

    # Current task
    if task:
        sections.append("## Current Task")
        sections.append(f"- **ID**: {task.id}")
        sections.append(f"- **Title**: {task.title}")
        if task.contract and task.contract.behavior:
            sections.append(f"- **Behavior**: {task.contract.behavior}")
        if task.contract and task.contract.verification:
            sections.append(f"- **Verification**: `{task.contract.verification}`")
        if task.contract and task.contract.scope:
            sections.append(f"- **Scope**: {', '.join(task.contract.scope)}")
            sections.append(
                "- Scope entries should be raw relative paths only; put prose "
                "in acceptance/exclusions, not in scope."
            )
        sections.append("")

        # Completion protocol
        _add_completion_protocol(sections, role, task, config=config)

    return "\n".join(sections)


def _backend_skill_discipline_lines(role: RoleConfig) -> list[str]:
    backend = str(role.backend or "").strip().lower()
    if backend == "codex":
        guidance = [
            "Codex receives a role-local `CODEX_HOME` with the enabled skills "
            "materialized under `skills/` when materialization is enabled.",
            "Treat the `Auto-Injected Skills` and `Load-On-Demand Skills` lists "
            "above as the allowed skill index for this role; do not use global "
            "or unlisted Codex skills as task authority.",
        ]
    elif backend == "claude-code":
        guidance = [
            "Claude Code receives enabled skills through the role worktree's "
            "project-local `.claude/skills/` directory when materialization is "
            "enabled.",
            "Treat the `Auto-Injected Skills` and `Load-On-Demand Skills` lists "
            "above as the allowed skill index for this role; do not use global "
            "or unlisted Claude skills as task authority.",
        ]
    else:
        guidance = [
            "Treat the `Auto-Injected Skills` and `Load-On-Demand Skills` lists "
            "above as the allowed skill index for this role.",
        ]
    return ["## Backend Skill Discipline", "", *guidance, ""]


def _render_skill_instruction_line(skill: str, entry: SkillLockEntry) -> str:
    status = entry.status
    source = entry.source or "missing"
    materialized = entry.materialized_to or "not materialized"
    description = entry.description or "No description available."
    load_state = (
        "load-on-demand"
        if entry.load_on_demand and not entry.auto_inject
        else "auto-inject"
        if entry.auto_inject
        else "indexed"
    )
    dependency_suffix = (
        f"; dependency of: {', '.join(entry.dependency_of)}"
        if entry.dependency_of else ""
    )
    return (
        f"- `/{skill}` - {description} "
        f"(status: {status}; mode: {load_state}; source: {source}; "
        f"runtime: {materialized}{dependency_suffix})"
    )


def _add_completion_protocol(
    sections: list[str],
    role: RoleConfig,
    task: Task,
    *,
    config: ZfConfig | None = None,
) -> None:
    """Add explicit completion instructions so the agent knows how to
    signal done. Derived from role.publishes via infer_completion_protocol
    so custom YAML roles (e.g. `doc` publishing `doc.updated`) get correct
    briefings without code changes.
    """
    protocol = infer_completion_protocol(role)
    cli_cmd = zf_cli_cmd()

    sections.append("## Completion Protocol")
    sections.append("")
    sections.append(_render_ownership_guard_instruction(role, task))
    sections.append("")
    sections.append("When you finish this task, you MUST run the following command:")
    sections.append("")
    sections.append("```bash")
    sections.append(
        f"{cli_cmd} emit {protocol.success_event} --task {task.id} "
        f"--actor {role.instance_id}{_dispatch_arg(task)}"
    )
    sections.append("```")
    sections.append("")

    if protocol.failure_event:
        sections.append(
            "If the work does NOT meet its acceptance criteria, instead run:"
        )
        sections.append("```bash")
        sections.append(
            f"{cli_cmd} emit {protocol.failure_event} --task {task.id} "
            f"--actor {role.instance_id}{_dispatch_arg(task)}"
        )
        sections.append("```")
        sections.append("")

    if protocol.suspend_event:
        sections.append(
            "If you cannot proceed because of missing info / broken "
            "environment / external dependency, SUSPEND with:"
        )
        sections.append("```bash")
        sections.append(
            f"{cli_cmd} emit {protocol.suspend_event} --task {task.id} "
            f"--actor {role.instance_id}{_dispatch_arg(task)} "
            "--payload '{\"reason\": \"<one-line description>\"}'"
        )
        sections.append("```")
        sections.append(
            "SUSPEND is different from the failure path: it blocks the "
            "task + escalates to a human, rather than bouncing back to "
            "the prior worker for rework."
        )
        sections.append("")

    if config is not None:
        _append_configured_completion_contract(sections, config, role, protocol)
        if sections[-1] != "":
            sections.append("")

    sections.append("**Do NOT forget this step.** The harness cannot proceed until you emit the event.")
    sections.append("")

    # G-MEM-1: memory.note is a decision checkpoint — evaluate, then emit
    # only if something worth carrying forward came up. The kernel also
    # auto-promotes select system events (candidate.conflict, dev.blocked)
    # into memory.note, so workers focus on captures the kernel can't infer.
    # K5(2026-06-11):降级为单行可选提示 —— 审计 Q2 判定原"必须评估"
    # 块为高危纯自觉规则(无机器门);kernel auto-promote 兜底关键事件,
    # 长清单徒增 briefing 噪音。
    sections.append("## 可选:记录跨会话经验")
    sections.append("")
    sections.append(
        "若本任务产生了值得下次直接借鉴的非平凡经验(workaround/约定/"
        "环境陷阱),可在完成事件前补一条 `memory.note`;例行工作跳过"
        "(kernel 已自动 promote candidate.conflict 与 dev.blocked)。"
    )
    sections.append("")
    sections.append("```bash")
    sections.append(
        f"{cli_cmd} emit memory.note --actor {role.instance_id} "
        '--payload \'{"mem_type":"decision","content":"use bcrypt for password hashing"}\''
    )
    sections.append("```")
    sections.append("")
    sections.append("Memory types and decay (max_days):")
    sections.append("- `decision`  — architecture / design choices (30 days)")
    sections.append("- `pattern`   — reusable code patterns (60 days)")
    sections.append("- `fix`       — bug fixes worth remembering (7 days)")
    sections.append("- `context`   — environment / project context (14 days)")
    sections.append("")
    sections.append(
        "Memory is stored in `.zf/memory/" + role.name + ".md` (today's active "
        "file) and rotates to `.zf/memory/" + role.name + "/YYYY-MM-DD.md` the "
        "next day. Future sessions read recent memory to rebuild context."
    )
    sections.append("")


def _dispatch_arg(task: Task) -> str:
    return (
        f" --dispatch-id {task.active_dispatch_id}"
        if getattr(task, "active_dispatch_id", "")
        else ""
    )


def _render_feature_context(feature: Feature) -> str:
    """α-5 (2026-05-17): inject parent Feature objective + codex-style
    discipline at the TOP of every worker task briefing.

    The persistent objective is restated at every turn so the agent does not
    redefine success around the smallest stable-looking subset.

    Without this, dev modifies a typo and emits dev.build.done; review
    sees the diff but not the feature objective; the multi-agent chain
    quietly drifts away from what the operator actually asked for.
    """
    lines: list[str] = []
    lines.append("## Feature Context")
    lines.append("")
    lines.append(
        f"This task is part of **{feature.id}: {feature.title}**.",
    )
    lines.append("")
    if feature.description:
        lines.append("**Description**:")
        lines.append("")
        lines.append(feature.description)
        lines.append("")
    if feature.user_message:
        lines.append("**Original user message**:")
        lines.append("")
        lines.append(feature.user_message)
        lines.append("")
    lines.append("### Discipline (applies to every role)")
    lines.append("")
    lines.append(
        "- **Keep the full feature scope intact.** Do not redefine "
        "success around the smallest stable-looking subset that fits "
        "in this single task.",
    )
    lines.append(
        "- Small tasks reduce execution granularity only; they do not reduce "
        "the planned product scope. If the assigned slice is too small to "
        "prove the feature, finish the slice and report the remaining planned "
        "dependencies instead of declaring the product done.",
    )
    lines.append(
        "- Do not substitute a narrower / safer / smaller / easier-to-test "
        "solution because it is more likely to pass current tests.",
    )
    lines.append(
        "- An edit is aligned only if it makes the requested end state "
        "more true. Useful-looking behavior that preserves a different "
        "end state is misaligned.",
    )
    lines.append(
        "- Temporary rough edges are acceptable while the work is moving "
        "in the right direction. Completion still requires the requested "
        "end state to be true and verified.",
    )
    lines.append("")
    return "\n".join(lines)


def _append_read_order(
    lines: list[str],
    task: Task,
    *,
    task_doc_path: Path | str | None = None,
    source_doc_path: Path | str | None = None,
    progress_doc_path: Path | str | None = None,
    source_revision: str = "",
    contract_revision: str = "",
    capsule_revision: str = "",
) -> None:
    """Render the authoritative context-loading order for this dispatch.

    This is intentionally a briefing projection, not a second task schema. It
    tells the worker how to consume existing kernel-managed state and contract
    refs before editing or emitting terminal events.
    """
    lines.append("## Read Order (authoritative)")
    lines.append("")
    lines.append(
        "1. Trust the first-line `Active task` marker and this briefing's "
        "`Dispatch ID` as the dispatch binding."
    )
    if task_doc_path:
        lines.append(
            f"2. Read the kernel-managed task document `{task_doc_path}` as the "
            "runtime envelope."
        )
        if source_doc_path:
            lines.append(
                f"   - read semantic source `{source_doc_path}` before editing."
            )
        if progress_doc_path:
            lines.append(
                f"   - read progress projection `{progress_doc_path}` before resuming."
            )
        if source_revision or contract_revision or capsule_revision:
            lines.append("   - expected revisions:")
            if source_revision:
                lines.append(f"     - source_revision: `{source_revision}`")
            if contract_revision:
                lines.append(f"     - contract_revision: `{contract_revision}`")
            if capsule_revision:
                lines.append(f"     - capsule_revision: `{capsule_revision}`")
        lines.append(
            "   - workers must not edit task.md to mark completion; emit the "
            "role completion event and let the kernel refresh the task capsule."
        )
        lines.append(
            "   - when following plan/source artifact refs from a worker "
            "worktree, use the `Resolved Source References` / `resolved_path` "
            "entries inside task.md instead of guessing relative `.zf` paths."
        )
    else:
        lines.append(
            "2. Read `Task Assigned` and `Task Contract Details` below as the "
            "current runtime task contract. Repo `backlogs/` and `tasks/` are "
            "not the runtime queue."
        )
    lines.append(
        "3. Read your role instructions from the launch prompt for role "
        "constraints, allowed tools, and completion protocol."
    )
    lines.append(
        "4. If this briefing includes `Runtime Resume Packet`, rework context, "
        "artifact refs, or git evidence, load those before editing or emitting "
        "completion."
    )
    lines.append(
        "5. Follow any `spec_ref` / `plan_ref` / `tdd_ref` / critic refs listed "
        "below. If they are missing, unreadable, or conflict with this task, "
        "emit the configured suspend/failure event instead of guessing."
    )
    if task.active_dispatch_id:
        lines.append(
            f"6. Completion events must include dispatch id "
            f"`{task.active_dispatch_id}` and the expected capsule revisions; "
            f"stale local context must stop at `{zf_cli_cmd()} guard ownership`."
        )
    else:
        lines.append(
            f"6. Completion events still go through `{zf_cli_cmd()} guard ownership`; stop "
            "if the guard says the task was reassigned or cleared."
        )
    lines.append("")


def _append_task_contract_details(lines: list[str], task: Task) -> None:
    contract = task.contract
    if contract is None:
        return

    lines.append("## Task Contract Details")
    lines.append("")

    _append_contract_refs(lines, contract)
    _append_contract_acceptance(lines, contract)
    _append_contract_evidence(lines, contract)

    extra_lists = (
        ("Verification tiers", getattr(contract, "verification_tiers", [])),
        ("Affected files", getattr(contract, "affected_files", [])),
        ("Explicit non-goals", getattr(contract, "explicit_non_goals", [])),
    )
    for label, values in extra_lists:
        if values:
            lines.append(f"**{label}**:")
            for value in values:
                lines.append(f"- {value}")
            lines.append("")


def _append_contract_refs(lines: list[str], contract: object) -> None:
    refs = (
        ("source_backlog_task_id", getattr(contract, "source_backlog_task_id", "")),
        ("source_key", getattr(contract, "source_key", "")),
        ("source_ref", getattr(contract, "source_ref", "")),
        ("source_task_id", getattr(contract, "source_task_id", "")),
        ("source_index_ref", getattr(contract, "source_index_ref", "")),
        ("source_mode", getattr(contract, "source_mode", "")),
        ("product_contract_ref", getattr(contract, "product_contract_ref", "")),
        ("spec_skip_reason", getattr(contract, "spec_skip_reason", "")),
        ("spec_ref", getattr(contract, "spec_ref", "")),
        ("plan_ref", getattr(contract, "plan_ref", "")),
        ("tdd_ref", getattr(contract, "tdd_ref", "")),
        ("critic_gate_ref", getattr(contract, "critic_gate_ref", "")),
        ("critic_event_id", getattr(contract, "critic_event_id", "")),
        ("reviewed_arch_event_id", getattr(contract, "reviewed_arch_event_id", "")),
        ("source_arch_dispatch_id", getattr(contract, "source_arch_dispatch_id", "")),
    )
    present = [(key, str(value).strip()) for key, value in refs if str(value or "").strip()]
    if not present:
        return
    lines.append("### Contract References")
    for key, value in present:
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")


def _append_contract_acceptance(lines: list[str], contract: object) -> None:
    acceptance = str(getattr(contract, "acceptance", "") or "").strip()
    criteria = list(getattr(contract, "acceptance_criteria", []) or [])
    evidence = getattr(contract, "acceptance_evidence", {}) or {}
    if not acceptance and not criteria and not evidence:
        return

    lines.append("### Acceptance")
    if acceptance:
        lines.append(f"- `acceptance`: `{acceptance}`")
    if criteria:
        lines.append("- `acceptance_criteria`:")
        for index, item in enumerate(criteria, start=1):
            lines.append(f"  {index}. {item}")
    if evidence:
        lines.append("- `acceptance_evidence`:")
        lines.append("```json")
        lines.append(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2))
        lines.append("```")
    lines.append("")


def _append_contract_evidence(lines: list[str], contract: object) -> None:
    validation = getattr(contract, "validation", {}) or {}
    evidence_contract = getattr(contract, "evidence_contract", {}) or {}
    if not validation and not evidence_contract:
        return

    lines.append("### Evidence Contract")
    if validation:
        lines.append("- `validation`:")
        lines.append("```json")
        lines.append(json.dumps(validation, ensure_ascii=False, sort_keys=True, indent=2))
        lines.append("```")
    if evidence_contract:
        lines.append("- `evidence_contract`:")
        lines.append("```json")
        lines.append(
            json.dumps(evidence_contract, ensure_ascii=False, sort_keys=True, indent=2)
        )
        lines.append("```")
        _append_replan_amendment_context(lines, evidence_contract)
    lines.append("")


def _append_replan_amendment_context(
    lines: list[str],
    evidence_contract: dict,
) -> None:
    replan_history_ref = str(evidence_contract.get("replan_history_ref") or "").strip()
    affected_tasks = evidence_contract.get("affected_tasks")
    gate_changes = evidence_contract.get("gate_changes")
    if not replan_history_ref and not affected_tasks and not gate_changes:
        return
    lines.append("")
    lines.append("### Replan Amendment Context")
    if replan_history_ref:
        lines.append(f"- `replan_history_ref`: `{replan_history_ref}`")
    if isinstance(affected_tasks, list) and affected_tasks:
        lines.append("- `affected_tasks`: " + ", ".join(str(item) for item in affected_tasks))
    if isinstance(gate_changes, list) and gate_changes:
        lines.append("- `gate_changes`: " + ", ".join(str(item) for item in gate_changes))


def _render_judge_completion_audit() -> str:
    """α-6 (2026-05-17): codex-style completion audit for judge role.

    Treat completion as unproven until requirement-by-requirement evidence is
    gathered. Judge previously could pass on verification tier output without
    checking that the verifiers actually cover the feature objective.

    Applies by role identity (judge), not by feature presence — even
    feature-less tasks get the audit when going through judge.
    """
    lines: list[str] = []
    lines.append("## Completion Audit (强制 — judge only)")
    lines.append("")
    lines.append(
        "Before emitting `judge.passed`, treat completion as **unproven** "
        "and verify against actual current state:",
    )
    lines.append("")
    lines.append(
        "1. Derive concrete requirements from the feature description, "
        "the task contract behavior, the acceptance criteria, and any "
        "spec / plan / tdd refs.",
    )
    lines.append(
        "2. Preserve the original scope; do not redefine success around "
        "the work that already exists.",
    )
    lines.append(
        "3. For every explicit requirement, numbered item, named "
        "artifact, command, test, gate, invariant, and deliverable, "
        "identify the **authoritative evidence** that would prove it. "
        "Then inspect: file contents, command output, test results, "
        "runtime behavior.",
    )
    lines.append(
        "4. For each item, determine: evidence proves completion / "
        "contradicts completion / shows incomplete work / is too weak "
        "or indirect to verify / is missing.",
    )
    lines.append(
        "5. Match verification scope to requirement scope. Do not use "
        "a narrow check to support a broad claim. (e.g. one passing "
        "test does not prove a feature spanning 5 files.)",
    )
    lines.append(
        "6. Treat tests, manifests, verifiers, green checks, and search "
        "results as evidence **only after confirming they cover the "
        "relevant requirement** — do not treat their existence as proof.",
    )
    lines.append(
        "7. Treat uncertain or indirect evidence as **not achieved**. "
        "Gather stronger evidence or emit `judge.failed`.",
    )
    lines.append("")
    lines.append(
        "**The audit must prove completion**, not merely fail to find "
        "obvious remaining work. Do not rely on intent, partial progress, "
        "memory of earlier work, or a plausible final answer as proof.",
    )
    lines.append("")
    lines.append(
        "If evidence is incomplete, weak, indirect, merely consistent "
        "with completion, or leaves any requirement missing, "
        "**emit `judge.failed` instead of `judge.passed`** "
        "and report which requirement-by-requirement check failed.",
    )
    lines.append("")
    return "\n".join(lines)


def _render_repo_guidance_digest(
    role: RoleConfig,
    project_root: Path | str | None,
) -> str:
    """B-SKILL-10: per-task read-only digest of repo guidance + enabled skills.

    Role-level CLAUDE.md / skills are delivered once at launch via
    ``generate_role_instructions``; this projection re-surfaces them per task so
    they survive context compaction. It is context only — it does not touch the
    control plane.
    """
    lines: list[str] = ["## Repo Guidance (read-only)"]
    if project_root is not None:
        root = Path(project_root)
        present = [
            f"{name} {'✓' if (root / name).exists() else '✗'}"
            for name in ("AGENTS.md", "CLAUDE.md")
        ]
        lines.append(f"- Guidance files: {', '.join(present)} (read before working)")
    skills = ", ".join(role.skills) if role.skills else "none"
    lines.append(f"- Enabled skills ({role.name}): {skills}")
    return "\n".join(lines)


def generate_task_briefing(
    config: ZfConfig,
    role: RoleConfig,
    task: Task,
    *,
    feature: Feature | None = None,
    task_doc_path: Path | str | None = None,
    source_doc_path: Path | str | None = None,
    progress_doc_path: Path | str | None = None,
    source_revision: str = "",
    contract_revision: str = "",
    capsule_revision: str = "",
    state_dir_ref: Path | str | None = None,
    project_root: Path | str | None = None,
) -> str:
    """Generate a focused task briefing for dispatch (shorter than full instructions).

    ``feature`` (α-5, 2026-05-17): when provided, prepend the parent
    Feature objective + codex-style discipline block. Callers (notably
    ``orchestrator_dispatch.py``) look up the feature via FeatureStore
    by ``task.contract.feature_id`` before invoking. ``None`` for
    orphan tasks keeps backward-compat (original briefing shape).
    """
    lines: list[str] = []
    # ZF-TR-NESTED-GUARD-001 (doc 40 §6 I55): every briefing's first line
    # is a deterministic ``Active task: <task_id>`` marker. Recovery /
    # operator-side scripts grep this line to confirm a worker is
    # actually working on the expected task; missing marker = fail
    # closed.
    lines.append(f"Active task: {task.id}")
    lines.append("")
    # ZF-TR-WFSTATE-001 (doc 39 §4.6, doc 40 §6 I53): inject the
    # per-turn ``<zf-workflow-state>`` breadcrumb so the worker
    # always knows current_stage / required_next_event /
    # forbidden_completion_reason / state_packet_ref even after
    # context compaction. Derived from a synthetic StatePacket
    # projected from the live task (full projector wiring lands
    # in a follow-up sprint; this gives the breadcrumb shape now).
    breadcrumb = _render_workflow_state_for_task(
        task,
        role,
        state_dir_ref=state_dir_ref,
    )
    if breadcrumb:
        lines.append(breadcrumb)
        lines.append("")
    # B-SKILL-10: per-task repo-guidance + enabled-skills digest (projection).
    lines.append(_render_repo_guidance_digest(role, project_root))
    lines.append("")
    # α-5: feature context at briefing TOP (before task section)
    if feature is not None:
        lines.append(_render_feature_context(feature))
    _append_read_order(
        lines,
        task,
        task_doc_path=task_doc_path,
        source_doc_path=source_doc_path,
        progress_doc_path=progress_doc_path,
        source_revision=source_revision,
        contract_revision=contract_revision,
        capsule_revision=capsule_revision,
    )
    lines.append(f"## Task Assigned: {task.id}")
    lines.append(f"**Title**: {task.title}")
    lines.append(f"**Role**: {role.name}")
    if task.active_dispatch_id:
        lines.append(f"**Dispatch ID**: `{task.active_dispatch_id}`")
    lines.append("")

    if task.contract:
        if task.contract.behavior:
            lines.append(f"**Behavior**: {task.contract.behavior}")
        if task.contract.verification:
            lines.append(f"**Verification**: `{task.contract.verification}`")
        # avbs-r4 F6: operator waive 走事件持久化,briefing 自动带出——
        # 裁决一次生效,worker respawn 不再丢语境(doc 124 STOP waive-trail)。
        if state_dir_ref:
            from zf.runtime.waivers import (
                load_active_waivers,
                render_waiver_lines,
            )

            lines.extend(render_waiver_lines(
                load_active_waivers(Path(state_dir_ref), task.id),
            ))
        if task.contract.validation:
            validation_text = json.dumps(
                task.contract.validation,
                ensure_ascii=False,
                sort_keys=True,
            )
            lines.append(f"**Validation**: `{validation_text}`")
        if task.contract.scope:
            lines.append(f"**Scope**: {', '.join(task.contract.scope)}")
            lines.append(
                "**Scope format**: raw relative paths only; put prose in "
                "acceptance/exclusions, not in scope."
            )
        if task.contract.exclusions:
            lines.append(f"**Exclusions**: {', '.join(task.contract.exclusions)}")
        if task.contract.wave:
            lines.append(f"**Wave**: {task.contract.wave}")
        if task.contract.shared_files:
            lines.append(f"**Shared files**: {', '.join(task.contract.shared_files)}")
        if task.contract.exclusive_files:
            lines.append(
                f"**Exclusive files**: {', '.join(task.contract.exclusive_files)}"
            )
        if task.contract.handoff_artifacts:
            lines.append(
                f"**Handoff artifacts**: {', '.join(task.contract.handoff_artifacts)}"
            )
        lines.append("")
        _append_task_contract_details(lines, task)

    # Completion command — derived from role.publishes (not a hardcoded map).
    protocol = infer_completion_protocol(role)
    cli_cmd = zf_cli_cmd()
    lines.append(_render_ownership_guard_instruction(role, task))
    lines.append("")
    lines.append(
        f"**When done, run**: `{cli_cmd} emit {protocol.success_event} "
        f"--task {task.id} --actor {role.instance_id}{_dispatch_arg(task)}`"
    )
    if protocol.failure_event:
        lines.append(
            f"**If failed, run**: `{cli_cmd} emit {protocol.failure_event} "
            f"--task {task.id} --actor {role.instance_id}{_dispatch_arg(task)}`"
        )
    if protocol.suspend_event:
        lines.append(
            f"**If blocked (missing info / env broken), SUSPEND via**: "
            f"`{cli_cmd} emit {protocol.suspend_event} --task {task.id} "
            f"--actor {role.instance_id}{_dispatch_arg(task)}`"
        )
    lines.append("")
    _add_role_handoff_guidance(lines, config, role, protocol)
    lines.append("")

    # α-2 (2026-05-17): every worker role (everything except orchestrator)
    # must emit periodic worker.heartbeat. Without periodic heartbeat, the
    # kernel falls back to a 4-min wall-clock stuck timer (cangjie r-next-9
    # B-NEW-15 false-positive case) and proactive dispatch can't sense
    # idle workers.
    if role.name != "orchestrator":
        lines.append(_render_worker_heartbeat_instructions(role))
        lines.append("")
        # ZF-TR-NESTED-GUARD-001 (doc 40 §6 I55): recursion guard for
        # worker roles. Worker MUST NOT spawn nested sub-agents of its
        # own role, MUST NOT bypass orchestrator to mutate truth, and
        # MUST NOT self-declare release/ship.
        lines.append(_render_recursion_guard(role))
        lines.append("")

    # α-6 (2026-05-17): judge role gets the completion audit appended.
    # Applies by role identity, independent of feature presence.
    if role.name == "judge":
        lines.append(_render_judge_completion_audit())

    return "\n".join(lines)


def _render_workflow_state_for_task(
    task: Task,
    role: RoleConfig,
    *,
    state_dir_ref: Path | str | None = None,
) -> str:
    """ZF-TR-WFSTATE-001 — build a lightweight workflow-state
    breadcrumb for inclusion in a worker briefing.

    Constructs a synthetic StatePacket from the task / role currently
    in hand (full projector run requires event_log + feature_store
    and lives in StatePacketProjector). The breadcrumb is rendered by
    :func:`zf.runtime.workflow_state_breadcrumb.render_workflow_state_breadcrumb`.
    """
    try:
        from zf.core.state.state_packet import (
            StatePacket,
            StatePacketContract,
            StatePacketOwner,
        )
        from zf.runtime.workflow_state_breadcrumb import (
            render_workflow_state_breadcrumb,
        )
    except Exception:
        return ""

    contract = getattr(task, "contract", None)
    behavior = str(getattr(contract, "behavior", "") or "")
    feature_id = str(getattr(contract, "feature_id", "") or "")
    instance_id = getattr(role, "instance_id", "") or role.name
    current_stage, next_event = _workflow_stage_for_role(role)
    packet = StatePacket(
        run_id="",
        feature_id=feature_id,
        task_id=task.id,
        objective=behavior[:200],
        current_stage=current_stage,
        owner=StatePacketOwner(role=role.name, instance_id=instance_id),
        contract=StatePacketContract(behavior=behavior),
        next_owner=role.name,
        next_event=next_event,
    )
    state_packet_ref = ""
    dispatch_id = task.active_dispatch_id or ""
    if task.id and dispatch_id:
        state_base = str(state_dir_ref or ".zf").strip() or ".zf"
        state_base = state_base.rstrip("/")
        state_packet_ref = (
            f"{state_base}/briefings/{task.id}/{dispatch_id}/state-packet.json"
        )
    return render_workflow_state_breadcrumb(
        packet,
        dispatch_id=dispatch_id,
        state_packet_ref=state_packet_ref,
    )


def _workflow_stage_for_role(role: RoleConfig) -> tuple[str, str]:
    """Infer the stage/next event shown in the per-dispatch breadcrumb."""
    protocol = infer_completion_protocol(role)
    success = protocol.success_event
    configured_stage = role.stages[0] if role.stages else ""
    if success == "arch.proposal.done":
        return (
            configured_stage or "design",
            "artifact.manifest.published -> arch.proposal.done",
        )
    if success == "design.critique.done":
        return (
            configured_stage or "design_review",
            "artifact.manifest.published -> design.critique.done",
        )
    stage_by_role = {
        "dev": "implement",
        "review": "review",
        "test": "test",
        "judge": "judge",
        "static_gate": "static_gate",
    }
    stage = stage_by_role.get(role.name)
    if stage is None:
        stage = configured_stage or (role.name or "implement")
    elif configured_stage:
        stage = configured_stage
    return stage, success


def _render_ownership_guard_instruction(role: RoleConfig, task: Task) -> str:
    return (
        "**Before any terminal completion event, verify you still own this "
        "task**: "
        f"`{zf_cli_cmd()} guard ownership --task {task.id} "
        f"--actor {role.instance_id}`. "
        "If this command exits non-zero, stop and do not emit completion; "
        "your local context is stale and Layer 1 has reassigned or cleared "
        "the task."
    )


def _render_recursion_guard(role: RoleConfig) -> str:
    """ZF-TR-NESTED-GUARD-001 (doc 40 §6 I55): worker recursion guard.

    Worker roles operate under three boundaries enforced by this
    section in the dispatch briefing:

    1. **No nested sub-agent of same role.** A dev worker may not spawn
       another ``dev`` instance via the agent's own tool surface
       (Codex's sub-agent invocation, Claude Code's Task tool with the
       same role name). All same-role work is routed by the
       orchestrator, never self-fanned-out by the worker.

    2. **No direct truth mutation.** The four canonical stores
       (TaskStore / FeatureStore / SessionStore / MemoryStore) and the
       append-only events.jsonl are kernel-owned. Worker reports
       intent via ``zf emit`` only; the kernel decides if state
       advances.

    3. **No self-declared release.** Worker cannot emit ``ship.*`` /
       ``release.*`` / ``candidate.integration.*`` events. Those are
       Layer 1 deterministic transitions triggered by gate evidence,
       not by worker assertion.

    Grep proof: the literal substring ``Recursion Guard`` appears in
    every worker briefing. ``test_recursion_guard_in_worker_briefing``
    locks this contract.
    """
    return (
        "## Recursion Guard (强制)\n"
        "\n"
        "Three hard boundaries for this worker role:\n"
        "\n"
        f"1. **No nested {role.name} sub-agent.** Do not spawn another "
        f"`{role.name}` instance via your provider's sub-agent tool "
        "(Codex sub-agent, Claude Code `Task`, etc.). All same-role "
        "work is dispatched by the orchestrator. If you think the task "
        f"needs parallel `{role.name}` work, emit a follow-up event and "
        "let the orchestrator decide.\n"
        "2. **No direct truth mutation.** TaskStore / FeatureStore / "
        "SessionStore / MemoryStore and `.zf/events.jsonl` are "
        "kernel-managed. Use `zf emit` to report intent. Do not write "
        "those files directly.\n"
        "3. **No self-declared release.** You cannot emit `ship.*`, "
        "`release.*`, or `candidate.integration.*` — those transitions "
        "are kernel-driven from gate evidence. Emit your role's "
        "completion event only.\n"
        "\n"
        "Violation will be detected by Layer 1 verification and may "
        "result in the dispatch being rolled back.\n"
    )


def _render_worker_heartbeat_instructions(role: RoleConfig) -> str:
    """α-2 (2026-05-17): emit periodic worker.heartbeat instructions
    for every worker role. The kernel persists them into
    role_sessions.yaml; α-3 sweep uses last_heartbeat_at to detect
    idle / silent / stuck workers.
    """
    return (
        "## Periodic Heartbeat (强制)\n"
        "\n"
        "While this task is active, emit a `worker.heartbeat` event "
        "approximately every **60 seconds** (or right before each\n"
        "long tool call), so the orchestrator can detect liveness and "
        "current activity. Skipping heartbeats causes the kernel to fall\n"
        "back to a 4-min wall-clock stuck timer and may trip false-positive "
        "respawn cascades.\n"
        "\n"
        "Run:\n"
        "```\n"
        "# ZF_STATE_DIR is injected by `zf start`; keep it set when copying commands.\n"
        f"{zf_cli_cmd()} emit worker.heartbeat --task <task-id> "
        f"--actor {role.instance_id} \\\n"
        "  --payload '{\"instance_id\": \"" f"{role.instance_id}" "\", "
        "\"current_task_id\": \"<task-id>\", \"state\": \"busy\", "
        "\"last_action_ts\": \"<ISO8601 UTC>\"}'\n"
        "```\n"
        "\n"
        "Set `state` to one of `idle` / `busy` / `blocked`. Include "
        "optional `context_used_ratio` (0.0–1.0) when known.\n"
    )


def _append_completion_contract_block(lines: list[str], *, advisory: bool) -> None:
    """EVAL-PAYLOAD-CONTRACT-001 6-field reminder.

    Live e2e (2026-05-18) showed advisory roles (arch/critic) skipping
    ``changed_files`` and ``residual_risks`` / ``next_agent_input``,
    tripping ``task.contract.invalid`` on every completion event.
    Spell the contract out in the briefing so the LLM emits all six.
    """
    lines.append("")
    lines.append("### Completion payload — 6-field contract (强制)")
    lines.append(
        "Kernel verifies your completion event against the 6-field contract "
        "(EVAL-PAYLOAD-CONTRACT-001). Missing fields trip "
        "`task.contract.invalid` and the gate will reject your handoff. "
        "Include EVERY field below in the JSON payload of your `zf emit` "
        "completion event."
    )
    lines.append("")
    lines.append("- `summary` (required): one-sentence zh-CN description of what you did.")
    if advisory:
        lines.append(
            "- `changed_files` (required): pass `[]` — advisory roles do not "
            "edit project files, but the field must be present as an empty list."
        )
    else:
        lines.append(
            "- `changed_files` (required): list of repo-relative paths you "
            "wrote / modified (no globs, empty list if you literally changed nothing)."
        )
    lines.append(
        "- `evidence_refs` (required): non-empty list of pointers. Each entry "
        "is EITHER a structured `<scheme>:<value>` reference (`git:<sha>`, "
        "`branch:<name>`, `cmd:<command> -> <result>`, `test:<command>`, "
        "`task_map:<ref>`, `event:<id>`) OR a repo-relative file path that "
        "actually exists on disk — bare paths are mechanically verified "
        "(completion honesty gate) and a missing path is flagged as an "
        "unverified claim."
    )
    lines.append(
        "- `residual_risks` (WARN): list of known limitations / edge cases / "
        "open questions; pass `[]` if truly none — do NOT omit the field."
    )
    lines.append(
        "- `next_agent_input` (WARN): one-sentence pointer telling the next "
        "role what to focus on; pass `\"\"` if none — do NOT omit the field."
    )
    lines.append("")


def _append_manifest_contract_block(lines: list[str]) -> None:
    """artifact.manifest.published ``artifact_refs`` field contract.

    cj-mono / calc full-flow e2e showed arch emitting ``version: "v0.1"`` and
    critic an out-of-enum ``status``; both were rejected as
    ``artifact.manifest.rejected`` and the handoff stalled / retried. Spell the
    field contract out so the LLM emits valid values the first time.
    """
    lines.append("")
    lines.append(
        "### artifact.manifest.published — artifact_refs field contract (强制)"
    )
    lines.append(
        "Each `artifact_refs` entry is validated by the kernel; an invalid "
        "entry trips `artifact.manifest.rejected` and your handoff stalls. Use "
        "exactly these field types:"
    )
    lines.append("- `path` (required): repo-relative path, never absolute.")
    lines.append(
        "- If the file only exists in this role worktree, also set "
        "`workdir_path` to that worktree's absolute project path. Downstream "
        "roles must consume `hash_status.resolved_path` + `sha256`, not assume "
        "`path` resolves in their own worktree."
    )
    lines.append("- `sha256` (required): hex sha256 of the file at `path`.")
    lines.append("- `kind`, `summary` (required): short strings.")
    lines.append(
        "- `version` (required): a positive integer (1, 2, 3 …) — NOT a string "
        "like \"v0.1\" or \"1.0.0\"."
    )
    lines.append(
        "- `status` (required): exactly one of draft, proposed, accepted, "
        "superseded, rejected."
    )
    lines.append("")


_KERNEL_ENVELOPE_FIELDS = frozenset({
    "fanout_id",
    "stage_id",
    "child_id",
    "run_id",
    "task_id",
    "status",
})


def _event_schema_required_fields(
    config: ZfConfig,
    event_type: str,
    *,
    flow_kind: str = "",
) -> list[str]:
    from zf.core.verification.event_schema import event_schemas_for_config

    schemas = event_schemas_for_config(config, flow_kind=flow_kind)
    schema = schemas.get(event_type) if isinstance(schemas, dict) else None
    if not isinstance(schema, dict):
        return []
    required = schema.get("required")
    if not isinstance(required, list):
        return []
    out: list[str] = []
    for field in required:
        text = str(field).strip()
        if text and text not in out:
            out.append(text)
    return out


def _example_value_for_completion_field(
    field: str,
    *,
    role: RoleConfig,
) -> object:
    if field in {"status", "state"}:
        return "completed"
    if field == "summary":
        return "<zh-CN one-sentence summary>"
    if field == "changed_files":
        if getattr(role, "role_kind", "") == "reader":
            return []
        return ["<repo-relative path>"]
    if field in {"artifact_refs", "evidence_refs", "source_refs"}:
        return ["<artifact path or git:<sha>>"]
    if field.endswith("_refs"):
        return [f"<{field} item>"]
    if field.endswith("_ref"):
        return f"<{field} path>"
    if field == "source_commit":
        return "<git commit sha>"
    if field == "source_branch":
        return "<worker branch>"
    if field == "workdir":
        return "<managed workdir path>"
    if field == "files_touched":
        return ["<repo-relative path>"]
    return f"<{field}>"


def _append_configured_completion_contract(
    lines: list[str],
    config: ZfConfig,
    role: RoleConfig,
    protocol: CompletionProtocol,
) -> None:
    role_name = str(role.name or "").strip().lower()
    flow_kind = next(
        (
            kind for kind in ("issue", "prd", "refactor")
            if role_name.startswith(f"{kind}-")
        ),
        "",
    )
    required = [
        field for field in _event_schema_required_fields(
            config,
            protocol.success_event,
            flow_kind=flow_kind,
        )
        if field not in _KERNEL_ENVELOPE_FIELDS
    ]
    if not required:
        return
    example = {
        "dispatch_id": "<dispatch_id from briefing header>",
    }
    for field in required:
        example[field] = _example_value_for_completion_field(field, role=role)

    lines.append("## Configured Completion Payload Contract")
    lines.append(
        f"`{protocol.success_event}` is declared in `workflow.dag.event_schemas`; "
        "the worker completion payload must include these role-owned fields: "
        + ", ".join(f"`{field}`" for field in required)
        + ". Kernel envelope fields such as fanout/stage/status may be supplied "
        "by runtime wrappers, but role-owned refs/evidence must be real."
    )
    if getattr(role, "role_kind", "") == "reader":
        lines.append(
            "Read-only role note: use `changed_files: []` unless this role is "
            "explicitly configured to write artifacts; report required source "
            "changes through the failure/rework event instead."
        )
    lines.append("Configured payload example:")
    lines.append("```json")
    lines.append(json.dumps(example, ensure_ascii=False, indent=2))
    lines.append("```")


def _add_role_handoff_guidance(
    lines: list[str],
    config: ZfConfig,
    role: RoleConfig,
    protocol: CompletionProtocol,
) -> None:
    _append_configured_completion_contract(lines, config, role, protocol)
    if role.name == "arch" and protocol.success_event == "arch.proposal.done":
        lines.append("## Handoff Evidence Required")
        lines.append(
            "This is a design-stage role. Do not edit implementation files. "
            "Do not produce the final accepted runtime deliverable or run "
            "full/long verification. You may produce rich candidate design "
            "artifacts when the task needs them: spec, full-stage plan, backlog "
            "draft, dependency/task map, TDD strategy, and risk register. "
            "Emit `arch.proposal.done` with a compact JSON payload containing "
            "`summary`, `file_plan`, `test_plan`, candidate `artifact_refs` or "
            "`evidence_refs`, so critic/orchestrator do not need to scrape your "
            "transcript."
        )
        lines.append(
            "Artifact-first rule: write durable spec/plan/backlog/tdd/process "
            "artifacts "
            "before declaring success, then emit `artifact.manifest.published` "
            "with `artifact_refs` containing repo-relative paths, sha256 hashes, "
            "kind, summary, and version/status metadata. Mark arch-authored "
            "planning artifacts as `draft` or `proposed` unless the task contract "
            "explicitly authorizes an accepted artifact. `arch.proposal.done` "
            "should reference the manifest/event ids; do not paste the full plan "
            "markdown into the chat transcript as the only handoff. Orchestrator "
            "owns final acceptance/merge into task contracts."
        )
        _append_manifest_contract_block(lines)
        _append_completion_contract_block(lines, advisory=True)
    elif role.name == "critic" and protocol.success_event == "design.critique.done":
        lines.append("## Handoff Evidence Required")
        lines.append(
            "This is a design-gate role. Do not edit implementation files, "
            "do not implement the task, and do not replace dev/review/test/judge. "
            "Do not run full test suites, e2e suites, long commands, or background "
            "terminals unless the task contract explicitly assigns that work to "
            "critic. Evaluate the arch proposal in one bounded pass. "
            "Emit `design.critique.done` with JSON payload containing "
            "`verdict`, `summary`, `risks`, and `evidence_refs`. If rejecting, "
            "emit the configured failure event with concrete fix items."
        )
        lines.append(
            "Artifact-first rule: review arch's `artifact.manifest.published` "
            "refs (`draft`, `proposed`, or `accepted`), not transcript-only "
            "prose. Your verdict approves or rejects the candidate package; it "
            "does not itself mutate implementation contracts. On approval, emit "
            "a critic review/gate artifact manifest with stable path + sha256, "
            "then point `design.critique.done.evidence_refs` at that manifest "
            "or artifact path."
        )
        _append_manifest_contract_block(lines)
        _append_completion_contract_block(lines, advisory=True)
    elif protocol.success_event in {
        "prd.child.completed",
        "prd.ready",
        "prd.critic.completed",
        "prd.approved",
        "task_map.child.completed",
        "task_map.ready",
    }:
        # PRD-flow planning/gate readers (prd-author, prd-critic, task-map-synth)
        # previously matched no handoff branch, so they received zero guidance on
        # the structured completion payload the DAG `event_schemas` demand. A
        # ledger e2e (2026-06-20) stalled at `prd.blocked: prd.ready requires
        # evidence_refs` because prd-author emitted a payload without
        # evidence_refs, then degenerated. Give these roles the exact required
        # fields + a copy-paste example so the contract gate passes first try.
        is_task_map = protocol.success_event in {
            "task_map.child.completed",
            "task_map.ready",
        }
        lines.append("## Handoff Evidence Required")
        lines.append(
            "This is a PRD-flow planning/gate role (reader). Do not implement "
            "product source or run full verification. Produce a durable artifact "
            "and emit your completion event with a STRUCTURED JSON payload — an "
            "empty or `{dispatch_id}`-only payload is rejected by the DAG "
            "contract gate (`prd.blocked` / `task_map.blocked`) and routes "
            "rework."
        )
        if is_task_map:
            lines.append(
                "Required completion payload fields: `task_map_ref` (repo- or "
                "state-relative path to the task-map artifact you wrote) and "
                "`evidence_refs` (non-empty list of pointers — `git:<sha>`, "
                "`branch:<name>`, artifact paths, or event ids). Include "
                "`summary` and `status: completed`."
            )
            example_payload = {
                "dispatch_id": "<dispatch_id from briefing header>",
                "status": "completed",
                "summary": "<zh-CN one-sentence summary>",
                "task_map_ref": "<path to task_map.json>",
                "evidence_refs": ["<artifact path>", "git:<commit-sha>"],
            }
        else:
            lines.append(
                "Required completion payload fields: `prd_ref` (path to the PRD "
                "artifact you wrote), `artifact_refs` (non-empty list that MUST "
                "include `prd_ref`), and `evidence_refs` (non-empty list of "
                "pointers — `git:<sha>`, `branch:<name>`, artifact paths, or "
                "event ids). Include `summary` and `status: completed`. A missing "
                "`evidence_refs` is the single most common gate failure here — "
                "never omit it."
            )
            example_payload = {
                "dispatch_id": "<dispatch_id from briefing header>",
                "status": "completed",
                "summary": "<zh-CN one-sentence summary>",
                "prd_ref": "<repo-relative path to PRD markdown>",
                "artifact_refs": ["<repo-relative path to PRD markdown>"],
                "evidence_refs": ["<artifact path>", "git:<commit-sha>"],
            }
        import json as _json
        lines.append("**Concrete payload shape (copy and fill):**")
        lines.append("```json")
        lines.append(_json.dumps(example_payload, ensure_ascii=False, indent=2))
        lines.append("```")
        _append_completion_contract_block(lines, advisory=True)
    elif (
        role.name in {"dev", "review", "test", "judge"}
        or protocol.success_event in {
            "dev.build.done",
            "review.approved",
            "test.passed",
            "judge.passed",
            # LB-3: fanout verify-lane reader completes on lane.stage.completed /
            # verify.* — without these it missed the evidence clause and shipped
            # empty evidence_refs (light baseline U20 stage.report.evidence_missing).
            "verify.passed",
            "lane.stage.completed",
            "verify.child.completed",
        }
    ):
        lines.append("## Handoff Evidence Required")
        lines.append(
            "Your completion event payload should include `summary`, "
            "`artifact_refs`, `evidence_refs`, and command/check evidence. "
            "Use the active dispatch id from this briefing for your `zf emit` "
            "completion event."
        )
        lines.append(
            "For each contract `verification_tiers` entry, include at least one "
            "passing replayable check with the matching `tier`; behavior-flow "
            "tests such as `test/behavior/...`, browser flows, and API flows "
            "should be marked `tier: e2e`."
        )
        lines.append(
            "Dispatch semantics: the active dispatch id is a transient role/gate "
            "routing id. Do not require product source, committed fixtures, "
            "static scorecards, or tests to hard-code the current role dispatch "
            "unless the task contract explicitly defines a stable product field "
            "for it. Runtime artifacts may carry a caller-supplied dispatch id "
            "when that is part of the product evidence."
        )
        readonly_gate_success = protocol.success_event in {
            "review.approved",
            "test.passed",
            "judge.passed",
        }
        if role.name in {"review", "test", "judge"} or readonly_gate_success:
            lines.append(
                "Read-only gate rule: this role must not modify, stage, commit, "
                "or rewrite project source or handoff artifacts. If the artifact "
                "needs a change to satisfy the contract, emit the configured "
                "failure event and route rework; successful gate payloads must "
                "use `changed_files: []`."
            )
            lines.append(
                "Verification-command fidelity: the kernel runs the project's "
                "configured quality gate deterministically and that result is "
                "authoritative. If you run a check yourself, use the configured "
                "command verbatim — prefer `python3` over bare `python` (and "
                "`python3 -m pytest`/`-m unittest`); `python`/`pip` are often "
                "absent (exit 127). Do not fail the gate over a missing-binary "
                "(127) from a substituted interpreter when the configured "
                "command passes."
            )
        # r-next backlog B-7: cangjie dev-4 反复 emit payload={dispatch_id only}
        # triggered task.ref.rejected death-loop. The wordy prose above wasn't
        # enough — workers emit empty payloads when in doubt. Provide a
        # concrete copy-paste JSON example with every required field so the
        # worker has zero ambiguity.
        lines.append("")
        lines.append(
            "**Concrete payload shape (copy and fill — do NOT emit just "
            "`{dispatch_id}`)**:"
        )
        verifier_success_event = protocol.success_event in {"test.passed", "judge.passed"}
        writer_success = role.name == "dev" or protocol.success_event == "dev.build.done"
        if writer_success:
            example_payload = {
                "dispatch_id": "<dispatch_id from briefing header>",
                "snapshot_ref": "<snapshot_ref from Runtime Snapshot section>",
                "state": "DONE",
                "summary": "<zh-CN one-sentence summary of what landed>",
                "changed_files": [
                    "<repo-relative path 1>",
                    "<repo-relative path 2>",
                ],
                "artifact_refs": [
                    "<repo-relative path 1>",
                    "<repo-relative path 2>",
                ],
                "evidence_refs": [
                    "git:<commit-sha>",
                    "branch:worker/<role>",
                ],
                "residual_risks": [
                    "<known edge case or limitation, or empty list>",
                ],
                "next_agent_input": "<one-sentence pointer to where review/test should focus>",
                "skill_used": ["<skill-name>", "..."],
            }
        else:
            example_payload = {
                "dispatch_id": "<dispatch_id from briefing header>",
                "snapshot_ref": "<snapshot_ref from Runtime Snapshot section>",
                "summary": "<zh-CN one-sentence summary>",
                "changed_files": [],
                "artifact_refs": ["<repo-relative path>"],
                "evidence_refs": ["git:<commit-sha>"],
                "residual_risks": ["<finding or empty list>"],
                "next_agent_input": "<one-sentence handoff hint for the next role>",
                "tests_run": ["<command 1>", "<command 2>"] if verifier_success_event else None,
                "evidence": {
                    "scores": {
                        "completeness": 0.95,
                        "correctness": 0.95,
                        "evidence_quality": 0.9,
                        "regression_risk": 0.1,
                    },
                    "checks": [
                        {
                            "command": "<verification command>",
                            "exit_code": 0,
                            "passed": True,
                            "tier": "runtime",
                        },
                    ],
                },
            }
        import json as _json
        example_payload = {k: v for k, v in example_payload.items() if v is not None}
        example_json = _json.dumps(
            example_payload, ensure_ascii=False, indent=2,
        )
        lines.append("```json")
        lines.append(example_json)
        lines.append("```")
        lines.append(
            "**Submit checklist** (worker must verify before `zf emit`):"
        )
        if writer_success:
            lines.append(
                "- Do NOT stop after green tests or final prose. The task is "
                f"not complete until `{zf_cli_cmd()} guard ownership`, a local commit when "
                "files changed, and the terminal `zf emit` command below have "
                "all completed successfully."
            )
            git_config = getattr(getattr(config, "runtime", None), "git", None)
            remote_policy = getattr(git_config, "remote_policy", "local")
            lines.append(
                "- `git add` + `git commit` completed; capture the local "
                "commit sha in `evidence_refs[0]`."
            )
            if remote_policy == "required":
                lines.append(
                    "- `runtime.git.remote_policy=required`: remote publication "
                    "is required after local verification. If no approved remote "
                    "is configured, emit `dev.blocked` with evidence instead of "
                    "retrying blind pushes."
                )
            elif remote_policy == "optional":
                lines.append(
                    "- `runtime.git.remote_policy=optional`: remote publication "
                    "may run once when an approved remote exists; keep the local "
                    "commit sha as the primary evidence and report push failures "
                    "as residual risk."
                )
            elif remote_policy == "local_only":
                lines.append(
                    "- `runtime.git.remote_policy=local_only`: do not push to "
                    "external remotes. Only a harness/operator-provided local "
                    "bare remote is allowed when the task explicitly asks for it."
                )
            else:
                lines.append(
                    "- `runtime.git.remote_policy=local`: Do NOT `git push` "
                    "unless the task contract or operator explicitly requires "
                    "remote publication for this run."
                )
            lines.append(
                "- `artifact_refs` lists EVERY file you wrote / modified "
                "(no globs, repo-relative paths)"
            )
            lines.append(
                "- Acceptance commands from the task contract all returned "
                "exit_code 0 locally"
            )
        else:
            lines.append(
                "- Every contract `verification_tiers` entry covered by "
                "≥1 passing `checks` item"
            )
            lines.append(
                "- `scores` includes all four canonical dimensions "
                "(completeness, correctness, evidence_quality, regression_risk)"
            )
            lines.append(
                "- `artifact_refs` and `evidence_refs` are non-empty"
            )
        lines.append(
            "- `snapshot_ref` matches the `Runtime Snapshot` section in this "
            "briefing. Do not omit it from terminal completion payloads."
        )


def write_task_briefing(
    state_dir: Path,
    role_name: str,
    task: Task,
    briefing: str,
    *,
    task_doc_path: Path | str | None = None,
    source_doc_path: Path | str | None = None,
    progress_doc_path: Path | str | None = None,
    source_revision: str = "",
    contract_revision: str = "",
    capsule_revision: str = "",
) -> Path:
    """Write task briefing to a file and return its path."""
    briefing_dir = state_dir / "briefings"
    briefing_dir.mkdir(parents=True, exist_ok=True)
    path = briefing_dir / f"{role_name}-{task.id}.md"
    path.write_text(briefing)

    # Also write task JSON for structured access
    task_json_path = briefing_dir / f"{task.id}.json"
    task_json_path.write_text(json.dumps({
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "assigned_to": role_name,
        "contract": {
            "schema_version": task.contract.schema_version if task.contract else "",
            "locale": task.contract.locale if task.contract else "",
            "feature_id": task.contract.feature_id if task.contract else "",
            "parent_task_id": task.contract.parent_task_id if task.contract else "",
            "campaign": task.contract.campaign if task.contract else "",
            "phase": task.contract.phase if task.contract else "",
            "source_backlog_task_id": (
                task.contract.source_backlog_task_id if task.contract else ""
            ),
            "source_key": task.contract.source_key if task.contract else "",
            "source_ref": task.contract.source_ref if task.contract else "",
            "source_task_id": task.contract.source_task_id if task.contract else "",
            "source_index_ref": (
                task.contract.source_index_ref if task.contract else ""
            ),
            "source_mode": task.contract.source_mode if task.contract else "",
            "source_title": task.contract.source_title if task.contract else "",
            "product_contract_ref": (
                task.contract.product_contract_ref if task.contract else ""
            ),
            "spec_skip_reason": (
                task.contract.spec_skip_reason if task.contract else ""
            ),
            "behavior": task.contract.behavior if task.contract else "",
            "verification": task.contract.verification if task.contract else "",
            "verification_tiers": (
                task.contract.verification_tiers if task.contract else []
            ),
            "validation": task.contract.validation if task.contract else {},
            "spec_ref": task.contract.spec_ref if task.contract else "",
            "plan_ref": task.contract.plan_ref if task.contract else "",
            "tdd_ref": task.contract.tdd_ref if task.contract else "",
            "critic_gate_ref": (
                task.contract.critic_gate_ref if task.contract else ""
            ),
            "critic_event_id": (
                task.contract.critic_event_id if task.contract else ""
            ),
            "reviewed_arch_event_id": (
                task.contract.reviewed_arch_event_id if task.contract else ""
            ),
            "source_arch_dispatch_id": (
                task.contract.source_arch_dispatch_id if task.contract else ""
            ),
            "scope": task.contract.scope if task.contract else [],
            "affected_files": task.contract.affected_files if task.contract else [],
            "acceptance": task.contract.acceptance if task.contract else "",
            "acceptance_criteria": (
                task.contract.acceptance_criteria if task.contract else []
            ),
            "acceptance_evidence": (
                task.contract.acceptance_evidence if task.contract else {}
            ),
            "evidence_contract": (
                task.contract.evidence_contract if task.contract else {}
            ),
            "exclusions": task.contract.exclusions if task.contract else [],
            "explicit_non_goals": (
                task.contract.explicit_non_goals if task.contract else []
            ),
            "owner_role": task.contract.owner_role if task.contract else "",
            "owner_instance": task.contract.owner_instance if task.contract else "",
            "wave": task.contract.wave if task.contract else 0,
            "shared_files": task.contract.shared_files if task.contract else [],
            "exclusive_files": task.contract.exclusive_files if task.contract else [],
            "handoff_artifacts": (
                task.contract.handoff_artifacts if task.contract else []
            ),
            "unknowns": task.contract.unknowns if task.contract else [],
            "review_profile": task.contract.review_profile if task.contract else "",
        },
        "task_doc": {
            "path": str(task_doc_path) if task_doc_path else "",
            "source_doc": str(source_doc_path) if source_doc_path else "",
            "progress_doc": str(progress_doc_path) if progress_doc_path else "",
            "source_revision": source_revision,
            "contract_revision": contract_revision,
            "capsule_revision": capsule_revision,
            "source": "kernel_projection" if task_doc_path else "",
            "worker_may_mark_done": False,
        },
        "active_dispatch_id": task.active_dispatch_id,
        "dispatch_semantics": {
            "active_dispatch_id": (
                "Transient role/gate routing id for zf emit completion events."
            ),
            "product_artifacts": (
                "Do not require product source, committed fixtures, static "
                "scorecards, or tests to hard-code the current role dispatch "
                "unless the task contract explicitly defines a stable product "
                "field for it."
            ),
            "runtime_artifacts": (
                "Runtime artifacts may record a caller-supplied dispatch id "
                "when that is part of the product evidence."
            ),
        },
    }, indent=2))

    return path


def build_task_prompt(
    role_name: str,
    briefing_path: Path,
    prompt_kind: str = "task",
) -> str:
    """Short prompt that points the agent at its briefing + instructions files.

    The briefing carries the dispatch binding. For task dispatches it points
    at the kernel-managed task.md; for workflow child dispatches the fanout
    briefing itself is the authoritative run contract. ``prompt_kind`` remains
    positional-compatible because older dispatch sites may pass it as the third
    argument.
    """
    instructions_path = briefing_path.parent.parent / "instructions" / f"{role_name}.md"
    if prompt_kind == "fanout_child":
        return (
            f"Read the fanout child briefing at {briefing_path}. Use its "
            "fanout_id, run_id, target_ref, success command, failure command, "
            "and output contract as the authoritative workflow-child contract; "
            "do not look for a task.md unless the briefing explicitly names one. "
            f"Also read your role instructions at {instructions_path} for constraints."
        )
    if prompt_kind == "fanout_synth":
        return (
            f"Read the fanout synthesis briefing at {briefing_path}. Use the "
            "listed child reports, aggregate contract, success command, and "
            "failure command as the authoritative synthesis contract; do not "
            "look for a task.md unless the briefing explicitly names one. "
            f"Also read your role instructions at {instructions_path} for constraints."
        )
    return (
        f"Read the task briefing at {briefing_path}; follow its Read Order to "
        "load the kernel-managed task.md before you work. "
        f"Also read your role instructions at {instructions_path} for constraints and completion protocol."
    )


def materialize_instruction_refs(
    payload: dict,
    *,
    project_root,
) -> dict:
    """W1(doc 90 §6.1 消费半边):payload 的 *_ref 在 briefing 构建期物化。

    - ``instruction_ref``/``criteria_ref`` → 读 repo 内文件内容填入
      ``instruction``/``criteria``(原 key 保留作 provenance);
    - 与显式同名字段互斥:两者都给时显式者胜,ref 标记 ignored;
    - 缺失/越权文件不静默:填 ``[instruction_ref missing: <path>]`` 形
      标记(baseline WARN 语义,worker 与 operator 都看得见);
    - 不参与状态推进,不碰 skills provenance(doc 90 §6.1 边界)。
    """
    from pathlib import Path

    if not isinstance(payload, dict):
        return payload
    pairs = (("instruction_ref", "instruction"), ("criteria_ref", "criteria"))
    if not any(payload.get(ref) for ref, _ in pairs):
        return payload
    out = dict(payload)
    root = Path(project_root).resolve()
    for ref_key, target_key in pairs:
        raw_ref = str(out.get(ref_key) or "").strip()
        if not raw_ref:
            continue
        if str(out.get(target_key) or "").strip():
            out[f"{ref_key}_note"] = "ignored: explicit value present"
            continue
        ref_path = Path(raw_ref)
        if ref_path.is_absolute() or ".." in ref_path.parts:
            out[target_key] = f"[{ref_key} rejected (escape): {raw_ref}]"
            continue
        resolved = (root / ref_path).resolve()
        if not str(resolved).startswith(str(root)) or not resolved.exists():
            out[target_key] = f"[{ref_key} missing: {raw_ref}]"
            continue
        try:
            out[target_key] = resolved.read_text(encoding="utf-8").strip()
        except OSError:
            out[target_key] = f"[{ref_key} unreadable: {raw_ref}]"
    return out


def render_guardrails_block(role) -> str:
    """V4:role.guardrails 单句提示注入 briefing。

    不参与任何门判定(doc 90 rev2.1:规则必须有机器门,guardrails
    只是提示)。空列表 → 空串。
    """
    guardrails = list(getattr(role, "guardrails", []) or [])
    if not guardrails:
        return ""
    lines = ["## Guardrails(提示,非门)", ""]
    lines += [f"- {g}" for g in guardrails]
    return "\n".join(lines) + "\n"


def render_on_fail_hint(trigger_payload: dict | None) -> str:
    """V4:门失败修复文案的显眼渲染。

    gate.failed 类 payload 携带 on_fail(emitter 侧从 quality_gates
    配置取)时,briefing 顶部给一行"怎么修"。
    """
    if not isinstance(trigger_payload, dict):
        return ""
    hint = str(trigger_payload.get("on_fail") or "").strip()
    if not hint:
        return ""
    return f"**修复提示(来自门配置)**: {hint}\n"
