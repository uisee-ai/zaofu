"""ZF-LH-SP-001 — State Packet projector (doc 39 §4.1, doc 40 §6 I51).

Projects zaofu runtime truth (events.jsonl + TaskStore + FeatureStore
+ git refs) into a :class:`StatePacket` and writes it atomically to
``.zf/state/state-packet.json`` (current) plus
``.zf/briefings/<task_id>/<dispatch_id>/state-packet.{json,md}``
(per-dispatch snapshot).

Discipline:
- **Single physical writer**. ``StatePacketProjector.write`` is the
  only path that produces ``state-packet.json``. Direct file writes
  from elsewhere are an I28-equivalent invariant violation.
- **Atomic write**. Uses ``atomic_write_text`` so concurrent writers
  / crashes don't corrupt the file.
- **Schema-versioned**. Emits ``schema_version`` so older readers
  detect incompatible packets and fail loud rather than soft.
- **No truth mutation**. Projector reads stores + event log; it
  never appends to events.jsonl or modifies kanban / feature_list.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.state_packet import (
    SCHEMA_VERSION,
    StatePacket,
    StatePacketContract,
    StatePacketEvidence,
    StatePacketOwner,
    StatePacketRefs,
    packet_to_dict,
    packet_to_json,
)


# Map of success event types → which stage they conclude. Used to
# fill ``current_stage`` and ``next_event`` heuristically.
_STAGE_AFTER_EVENT: dict[str, tuple[str, str]] = {
    # event_type → (current_stage_after, next_event_to_pursue)
    "task.dispatched": ("implement", "dev.build.done"),
    "dev.build.done": ("static_gate", "static_gate.passed"),
    "static_gate.passed": ("review", "review.approved"),
    "review.approved": ("test", "test.passed"),
    "verify.passed": ("judge", "judge.passed"),
    "test.passed": ("judge", "judge.passed"),
    "judge.passed": ("ship", ""),
}


def _dispatch_stage_for_role(role_name: str) -> tuple[str, str]:
    role = role_name.strip()
    if role == "arch":
        return "design", "artifact.manifest.published -> arch.proposal.done"
    if role == "critic":
        return (
            "design_review",
            "artifact.manifest.published -> design.critique.done",
        )
    mapping = {
        "dev": ("implement", "dev.build.done"),
        "review": ("review", "review.approved"),
        "verify": ("verify", "verify.passed"),
        "verifier": ("verify", "verify.passed"),
        "test": ("test", "test.passed"),
        "judge": ("judge", "judge.passed"),
        "static_gate": ("static_gate", "static_gate.passed"),
    }
    return mapping.get(role, _STAGE_AFTER_EVENT["task.dispatched"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_str_tuple(items: Iterable[object]) -> tuple[str, ...]:
    out: list[str] = []
    for x in items or ():
        s = str(x).strip()
        if s:
            out.append(s)
    return tuple(out)


class StatePacketProjector:
    """Project deterministic runtime truth into a StatePacket.

    Construction is dependency-injected so unit tests can pass fakes
    and integration callers (Orchestrator) wire the real stores.
    """

    def __init__(
        self,
        *,
        state_dir: Path,
        task_store=None,
        feature_store=None,
        event_log=None,
        session_store=None,
    ) -> None:
        self.state_dir = state_dir
        self.task_store = task_store
        self.feature_store = feature_store
        self.event_log = event_log
        self.session_store = session_store

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def project(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> StatePacket:
        """Build a StatePacket for ``task_id`` (or the most recently
        dispatched task if not specified).

        Returns an "empty" packet (current_stage="no_task") if no
        active task can be located.
        """
        task = self._resolve_task(task_id)
        if task is None:
            return StatePacket(
                schema_version=SCHEMA_VERSION,
                run_id=run_id or "",
                current_stage="no_task",
                generated_at=_now_iso(),
            )

        contract_obj = getattr(task, "contract", None)
        feature_id = ""
        if contract_obj is not None:
            feature_id = getattr(contract_obj, "feature_id", "") or ""
        feature = self._resolve_feature(feature_id)
        # ZF-ROLECTX-001 (2026-05-18 integration): fill role_context
        # from infer_role_context() so recovery / Agent View can
        # attribute events without re-deriving the dimension.
        from zf.runtime.role_context import infer_role_context

        resolved_role = str(
            getattr(contract_obj, "owner_role", "") or ""
        ) or self._role_from_task(task)
        owner = StatePacketOwner(
            role=resolved_role,
            instance_id=str(getattr(contract_obj, "owner_instance", "") or "")
                or str(getattr(task, "assigned_to", "") or ""),
            role_context=infer_role_context(role_name=resolved_role).value,
        )
        contract = StatePacketContract(
            behavior=str(getattr(contract_obj, "behavior", "") or ""),
            acceptance=_coerce_str_tuple(
                getattr(contract_obj, "verification_tiers", []) or []
            ),
            out_of_scope=_coerce_str_tuple(
                getattr(contract_obj, "exclusions", []) or []
            ),
        )
        refs = StatePacketRefs(
            base_ref="main",
            task_ref=f"refs/zaofu/tasks/{task.id}",
            candidate_ref=(
                f"candidate/{feature_id}" if feature_id else ""
            ),
        )
        events_for_task = self._events_for_task(task.id)
        completed = self._completed_milestones(events_for_task)
        evidence = self._build_evidence(events_for_task)
        current_stage, next_event = self._infer_stage(events_for_task)
        if not current_stage:
            current_stage = "implement"
        next_owner = self._infer_next_owner(next_event)
        objective = ""
        if feature is not None:
            objective = str(getattr(feature, "title", "") or "") \
                or str(getattr(feature, "objective", "") or "")
        return StatePacket(
            schema_version=SCHEMA_VERSION,
            run_id=run_id or "",
            feature_id=feature_id,
            task_id=task.id,
            objective=objective,
            current_stage=current_stage,
            owner=owner,
            contract=contract,
            refs=refs,
            completed=completed,
            decisions=(),
            evidence=evidence,
            risks=(),
            blocked_by=_coerce_str_tuple(
                getattr(task, "blocked_by", []) or []
            ),
            next_owner=next_owner,
            next_event=next_event,
            generated_at=_now_iso(),
            generated_by="zf-cli",
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def write(
        self,
        packet: StatePacket,
        *,
        dispatch_id: str = "",
    ) -> Path:
        """Atomically write the packet's JSON form.

        Writes to two locations (per doc 39 §4.1):
        - ``.zf/state/state-packet.json`` (current)
        - ``.zf/briefings/<task_id>/<dispatch_id>/state-packet.json``
          (per-dispatch snapshot; only when both task_id and
          dispatch_id are present)

        Returns the canonical (.zf/state/...) path.
        """
        json_text = packet_to_json(packet) + "\n"
        state_dir = self.state_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        canonical = state_dir / "state-packet.json"
        atomic_write_text(canonical, json_text)

        if packet.task_id and dispatch_id:
            per_dispatch_dir = (
                self.state_dir
                / "briefings"
                / packet.task_id
                / dispatch_id
            )
            per_dispatch_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                per_dispatch_dir / "state-packet.json", json_text,
            )
            atomic_write_text(
                per_dispatch_dir / "state-packet.md",
                self.render_md(packet),
            )

        return canonical

    def render_md(self, packet: StatePacket) -> str:
        """Human-readable Markdown projection. Recovery briefing and
        operator handoff both consume this."""
        lines: list[str] = []
        lines.append(f"# State Packet · {packet.task_id or '(no task)'}")
        lines.append("")
        lines.append(
            f"> projection only, not runtime truth · "
            f"schema_version: {packet.schema_version} · "
            f"generated_at: {packet.generated_at}"
        )
        lines.append("")
        lines.append(f"- **Objective**: {packet.objective or '(none)'}")
        lines.append(f"- **Stage**: {packet.current_stage or '(unknown)'}")
        lines.append(
            f"- **Owner**: role={packet.owner.role!r} "
            f"instance={packet.owner.instance_id!r}"
        )
        if packet.run_id:
            lines.append(f"- **Run id**: {packet.run_id}")
        if packet.feature_id:
            lines.append(f"- **Feature**: {packet.feature_id}")
        if packet.contract.behavior:
            lines.append("")
            lines.append("## Behavior")
            lines.append(packet.contract.behavior)
        if packet.contract.acceptance:
            lines.append("")
            lines.append("## Acceptance")
            for item in packet.contract.acceptance:
                lines.append(f"- {item}")
        if packet.completed:
            lines.append("")
            lines.append("## Completed milestones")
            for item in packet.completed:
                lines.append(f"- {item}")
        if packet.evidence:
            lines.append("")
            lines.append("## Evidence")
            for ev in packet.evidence:
                lines.append(
                    f"- **{ev.kind}** · {ev.status} · "
                    f"`{ev.path}` (event: {ev.event_id or '-'})"
                )
        if packet.blocked_by:
            lines.append("")
            lines.append("## Blocked by")
            for item in packet.blocked_by:
                lines.append(f"- {item}")
        lines.append("")
        lines.append("## Next")
        lines.append(f"- next_owner: `{packet.next_owner or '-'}`")
        lines.append(f"- next_event: `{packet.next_event or '-'}`")
        lines.append("")
        lines.append("## Refs")
        lines.append(f"- base_ref: `{packet.refs.base_ref}`")
        if packet.refs.task_ref:
            lines.append(f"- task_ref: `{packet.refs.task_ref}`")
        if packet.refs.candidate_ref:
            lines.append(f"- candidate_ref: `{packet.refs.candidate_ref}`")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Internal helpers — kept defensive so projection never raises on
    # missing optional stores.
    # ------------------------------------------------------------------

    def _resolve_task(self, task_id: str | None):
        if self.task_store is None:
            return None
        try:
            if task_id:
                return self.task_store.get(task_id)
            tasks = self.task_store.list_all()
        except Exception:
            return None
        # Prefer most recently dispatched in_progress task.
        for status_pref in ("in_progress", "review", "test", "judge"):
            candidates = [
                t for t in tasks if getattr(t, "status", "") == status_pref
            ]
            if candidates:
                # Pick the one with the most recent dispatched_at /
                # started_at / created_at.
                def _key(t):
                    return (
                        getattr(t, "dispatched_at", "") or "",
                        getattr(t, "started_at", "") or "",
                        getattr(t, "created_at", "") or "",
                    )
                return max(candidates, key=_key)
        return None

    def _resolve_feature(self, feature_id: str):
        if not feature_id or self.feature_store is None:
            return None
        try:
            return self.feature_store.get(feature_id)
        except Exception:
            return None

    def _events_for_task(self, task_id: str) -> list:
        if not task_id or self.event_log is None:
            return []
        try:
            return list(self.event_log.query(task_id=task_id))
        except Exception:
            return []

    def _completed_milestones(self, events: list) -> tuple[str, ...]:
        milestones: list[str] = []
        seen = set()
        for ev in events:
            etype = getattr(ev, "type", "")
            if etype in _STAGE_AFTER_EVENT and etype not in seen:
                seen.add(etype)
                milestones.append(etype)
        return tuple(milestones)

    def _build_evidence(self, events: list) -> tuple[StatePacketEvidence, ...]:
        evidence: list[StatePacketEvidence] = []
        kind_map = {
            "review.approved": ("review", "passed"),
            "review.rejected": ("review", "failed"),
            "verify.passed": ("verify", "passed"),
            "verify.failed": ("verify", "failed"),
            "test.passed": ("test", "passed"),
            "test.failed": ("test", "failed"),
            "judge.passed": ("judge", "passed"),
            "judge.failed": ("judge", "failed"),
            "static_gate.passed": ("static_gate", "passed"),
            "static_gate.failed": ("static_gate", "failed"),
        }
        for ev in events:
            etype = getattr(ev, "type", "")
            spec = kind_map.get(etype)
            if not spec:
                continue
            kind, status = spec
            payload = getattr(ev, "payload", {}) or {}
            path = ""
            if isinstance(payload, dict):
                path = str(payload.get("path") or payload.get("evidence_path") or "")
            evidence.append(StatePacketEvidence(
                kind=kind,
                path=path,
                status=status,
                event_id=getattr(ev, "id", "") or "",
            ))
        return tuple(evidence)

    def _infer_stage(self, events: list) -> tuple[str, str]:
        """Latest stage-progress event determines current stage and
        next event."""
        latest: tuple[str, str] = ("", "")
        for ev in events:
            etype = getattr(ev, "type", "")
            if etype == "task.dispatched":
                payload = getattr(ev, "payload", {}) or {}
                role_name = ""
                if isinstance(payload, dict):
                    role_name = str(payload.get("role") or "")
                latest = _dispatch_stage_for_role(role_name)
                continue
            spec = _STAGE_AFTER_EVENT.get(etype)
            if spec:
                latest = spec
        return latest

    def _infer_next_owner(self, next_event: str) -> str:
        """Map next_event to which role should produce it."""
        if "arch.proposal.done" in next_event:
            return "arch"
        if "design.critique.done" in next_event:
            return "critic"
        mapping = {
            "dev.build.done": "dev",
            "static_gate.passed": "static_gate",
            "review.approved": "review",
            "verify.passed": "verify",
            "test.passed": "test",
            "judge.passed": "judge",
        }
        return mapping.get(next_event, "")

    def _role_from_task(self, task) -> str:
        """Best-effort role hint from task.assigned_to."""
        assigned = str(getattr(task, "assigned_to", "") or "")
        if "-" in assigned:
            return assigned.split("-", 1)[0]
        return assigned


# Convenience: load + verify a packet from disk for downstream readers.


def read_state_packet(state_dir: Path) -> StatePacket | None:
    """Read ``.zf/state/state-packet.json`` if present.

    Returns ``None`` if missing / malformed. Schema-version mismatch
    raises (callers must handle that explicitly so silent reads of
    incompatible packets never happen).
    """
    from zf.core.state.state_packet import packet_from_json

    path = state_dir / "state" / "state-packet.json"
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return packet_from_json(text)
