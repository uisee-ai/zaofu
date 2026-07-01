"""zf handoff — auto-generate handoff summary.

ZF-LH-SP-002: ``--format state-packet`` adds a State Packet projection
view (doc 39 §4.1, doc 40 §6 I51). Default ``md`` / ``json`` formats
preserve the v1 contract for backwards compatibility.
"""

from __future__ import annotations

import argparse
import json

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.task.store import TaskStore


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("handoff", help="Generate handoff summary")
    parser.add_argument(
        "--format",
        choices=["md", "json", "state-packet"],
        default="md",
        help=(
            "md (default, human-readable) | json (v1 task list) | "
            "state-packet (SP-001 projection — recommended for "
            "long-horizon resume; doc 39 §4.1)"
        ),
    )
    parser.add_argument(
        "--task",
        dest="task_id",
        default=None,
        help=(
            "When --format state-packet is used, project the packet "
            "for a specific task id. Otherwise the projector picks "
            "the most recently dispatched in_progress task."
        ),
    )
    # EVAL-HANDOFF-SCORE-001 (doc 43 §2.8)
    parser.add_argument(
        "--score",
        action="store_true",
        help=(
            "Append 10-dimension completeness score (state-packet "
            "format only). Useful for measuring resume-ability."
        ),
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context()
    except ConfigError as e:
        print(f"Error: {e}")
        return 1
    state_dir = context.state_dir
    store = TaskStore(state_dir / "kanban.json")
    event_log = event_log_from_project(state_dir, config=context.config)

    format_arg = getattr(args, "format", "md")

    if format_arg == "state-packet":
        return _run_state_packet(
            state_dir=state_dir,
            task_store=store,
            event_log=event_log,
            task_id=getattr(args, "task_id", None),
            with_score=bool(getattr(args, "score", False)),
        )

    tasks = store.list_all_with_archive()
    done = [t for t in tasks if t.status == "done"]
    in_progress = [t for t in tasks if t.status == "in_progress"]
    blocked = [t for t in tasks if t.status == "blocked"]
    backlog = [t for t in tasks if t.status == "backlog"]

    events = event_log.read_all()

    if format_arg == "json":
        data = {
            "done": [{"id": t.id, "title": t.title} for t in done],
            "in_progress": [{"id": t.id, "title": t.title} for t in in_progress],
            "blocked": [{"id": t.id, "title": t.title} for t in blocked],
            "backlog": [{"id": t.id, "title": t.title} for t in backlog],
            "total_events": len(events),
        }
        print(json.dumps(data, indent=2))
    else:
        print("## Handoff Summary\n")
        print(f"### Completed ({len(done)})")
        for t in done:
            print(f"- [x] {t.id}: {t.title}")

        print(f"\n### In Progress ({len(in_progress)})")
        for t in in_progress:
            print(f"- [ ] {t.id}: {t.title}")

        print(f"\n### Blocked ({len(blocked)})")
        for t in blocked:
            print(f"- [ ] {t.id}: {t.title}")

        print(f"\n### Backlog ({len(backlog)})")
        for t in backlog:
            print(f"- [ ] {t.id}: {t.title}")

        print(f"\nTotal events: {len(events)}")

    # Emit handoff event
    from zf.core.events.model import ZfEvent
    event_log.append(ZfEvent(type="handoff.generated", actor="zf-cli"))

    return 0


def _run_state_packet(
    *,
    state_dir,
    task_store,
    event_log,
    task_id: str | None,
    with_score: bool = False,
) -> int:
    """ZF-LH-SP-002: output a State Packet projection."""
    try:
        from zf.runtime.state_packet_projector import StatePacketProjector
    except Exception as exc:
        print(f"Error: state packet projector unavailable: {exc}")
        return 1
    projector = StatePacketProjector(
        state_dir=state_dir,
        task_store=task_store,
        event_log=event_log,
    )
    packet = projector.project(task_id=task_id)
    md = projector.render_md(packet)
    print(md)
    if with_score:
        score = compute_handoff_score(packet, state_dir=state_dir)
        print(render_score_md(score))

    from zf.core.events.model import ZfEvent
    payload: dict = {"format": "state-packet", "task_id": packet.task_id}
    if with_score:
        payload["handoff_score"] = compute_handoff_score(
            packet, state_dir=state_dir,
        )["score"]
    event_log.append(ZfEvent(
        type="handoff.generated",
        actor="zf-cli",
        payload=payload,
    ))
    return 0


# ---------------------------------------------------------------------------
# EVAL-HANDOFF-SCORE-001 — 10-dimension completeness scoring
# ---------------------------------------------------------------------------


def compute_handoff_score(packet, *, state_dir) -> dict:
    """Compute a 10-dimension handoff completeness score.

    Each dimension is binary 0/1; total score range [0, 10].
    Returns dict {score, max_score, dimensions: [{name, passed, hint}]}.
    """
    from pathlib import Path as _P

    dims: list[dict] = []

    def _add(name: str, passed: bool, hint: str = "") -> None:
        dims.append({
            "name": name,
            "passed": bool(passed),
            "hint": hint if not passed else "",
        })

    _add("run_id_present", bool(packet.run_id), "set run_id when invoking handoff")
    _add("task_id_present", bool(packet.task_id), "no active task — nothing to hand off")
    _add(
        "current_stage_filled",
        bool(packet.current_stage and packet.current_stage != "no_task"),
        "task lacks stage progression — check projector",
    )
    next_event_ok = bool(packet.next_event) or (
        packet.current_stage == "ship" and not packet.next_event
    )
    _add(
        "next_event_clear_or_terminal",
        next_event_ok,
        "next_event empty + stage != ship — what should the next worker do?",
    )
    _add(
        "evidence_present",
        len(packet.evidence) >= 1,
        "add at least one evidence_ref to completion payload",
    )
    _add(
        "refs_complete",
        bool(packet.refs.base_ref) and bool(packet.refs.task_ref),
        "base_ref or task_ref missing — git context broken",
    )
    _add(
        "acceptance_criteria_present",
        len(packet.contract.acceptance) >= 1,
        "task.contract.verification_tiers / acceptance_criteria empty",
    )
    # residual_risks — depends on EVAL-PAYLOAD-CONTRACT-001; for now,
    # use packet.risks tuple.
    _add(
        "residual_risks_recorded",
        len(packet.risks) >= 1,
        "no residual_risks recorded — worker did not surface known gaps",
    )

    # Recovery briefing + projection files (depend on commits c118146 +
    # 4cfca9d). Check on disk if they exist.
    if packet.task_id:
        try:
            briefings_dir = _P(state_dir) / "briefings" / packet.task_id
            recovery_ready = any(
                briefings_dir.glob("**/state-packet.md")
            ) if briefings_dir.exists() else False
        except Exception:
            recovery_ready = False
        try:
            proj_dir = _P(state_dir) / "projections" / "tasks" / packet.task_id
            projection_count = (
                sum(
                    1 for f in proj_dir.glob("*.md")
                    if f.name in (
                        "plan.md", "findings.md", "progress.md",
                        "attempt-ledger.md",
                    )
                )
                if proj_dir.exists() else 0
            )
        except Exception:
            projection_count = 0
    else:
        recovery_ready = False
        projection_count = 0
    _add(
        "recovery_briefing_ready",
        recovery_ready,
        "no .zf/briefings/<task>/*/state-packet.md — run SP-001 projector",
    )
    _add(
        "projection_files_ready",
        projection_count >= 4,
        f"only {projection_count}/4 projection files exist (plan/findings/progress/attempt-ledger)",
    )

    passed = sum(1 for d in dims if d["passed"])
    return {
        "score": passed,
        "max_score": len(dims),
        "dimensions": dims,
    }


def render_score_md(score: dict) -> str:
    """Render handoff_score dict as a markdown block."""
    lines = ["", f"## Handoff Quality Score: {score['score']}/{score['max_score']}", ""]
    for d in score["dimensions"]:
        icon = "✓" if d["passed"] else "✗"
        line = f"  {icon} {d['name']}"
        lines.append(line)
        if not d["passed"] and d["hint"]:
            lines.append(f"    → {d['hint']}")
    if score["score"] < score["max_score"]:
        gap = score["max_score"] - score["score"]
        lines.append("")
        lines.append(
            f"To score {score['max_score']}/{score['max_score']}: "
            f"address the {gap} ✗ item(s) above."
        )
    return "\n".join(lines)
