"""ZF-LH-SP-001 — State Packet schema (doc 39 §4.1, doc 40 §6 I51).

The State Packet is a deterministic projection of zaofu runtime
truth (events.jsonl + 4 stores + git) into a single recoverable
fact bundle. Slogan from doc 39 §9:

    Chat history can be discarded.
    State Packet can resume the next agent.

Design constraints (preserved here):

- **Projection, not truth.** Truth lives in events.jsonl + stores.
  This dataclass is read-only after a projector writes it. Workers
  cannot mutate the on-disk state-packet.json; they can only
  *suggest* updates via completion payload, kernel decides whether
  to accept.
- **Schema-versioned.** ``schema_version`` is frozen at "1.0" for
  this sprint plus a 1-week freeze window. Future field
  additions require version bump + a compatibility shim.
- **Frozen dataclasses.** Once projected, the packet is
  immutable — prevents accidental in-memory mutation by callers.

This module declares the schema only. The projector that builds a
StatePacket from truth is in
``zf.runtime.state_packet_projector``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class StatePacketOwner:
    """Who is currently working on the task.

    ``role_context`` is the ROLECTX-001 field (coordinator / worker /
    reviewer / verifier). This sprint always leaves it empty;
    ROLECTX-001 will fill it once it lands.
    """

    role: str = ""
    instance_id: str = ""
    role_context: str = ""


@dataclass(frozen=True)
class StatePacketContract:
    """Task contract slice the State Packet snapshots."""

    behavior: str = ""
    acceptance: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()


@dataclass(frozen=True)
class StatePacketRefs:
    """Git refs needed to resume work on this task."""

    base_ref: str = "main"
    task_ref: str = ""
    candidate_ref: str = ""


@dataclass(frozen=True)
class StatePacketEvidence:
    """One piece of verification evidence (test / review / judge /
    static_gate / git). ``event_id`` ties back to the events.jsonl
    entry so a reader can audit the source."""

    kind: str = ""
    path: str = ""
    status: str = ""
    event_id: str = ""


@dataclass(frozen=True)
class StatePacket:
    """The recoverable-state fact bundle for one task / campaign."""

    schema_version: str = SCHEMA_VERSION
    run_id: str = ""
    feature_id: str = ""
    task_id: str = ""
    objective: str = ""
    current_stage: str = ""
    owner: StatePacketOwner = field(default_factory=StatePacketOwner)
    contract: StatePacketContract = field(default_factory=StatePacketContract)
    refs: StatePacketRefs = field(default_factory=StatePacketRefs)
    completed: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    evidence: tuple[StatePacketEvidence, ...] = ()
    risks: tuple[str, ...] = ()
    blocked_by: tuple[str, ...] = ()
    next_owner: str = ""
    next_event: str = ""
    generated_at: str = ""
    generated_by: str = "zf-cli"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def packet_to_dict(packet: StatePacket) -> dict[str, Any]:
    """Convert to a plain dict for JSON serialization.

    Tuples become lists. Nested dataclasses unfold recursively.
    """
    return asdict(packet)


def packet_to_json(packet: StatePacket, *, indent: int | None = 2) -> str:
    """Serialize to JSON string with stable key order."""
    return json.dumps(
        packet_to_dict(packet),
        sort_keys=True,
        indent=indent,
        ensure_ascii=False,
    )


def packet_from_dict(data: dict[str, Any]) -> StatePacket:
    """Reconstruct a StatePacket from its dict form.

    Strict-but-tolerant: unknown top-level keys are ignored (forward
    compat for v1.0 readers of v1.1 packets). Schema version is
    enforced at major-version mismatch — minor differences accepted.
    """
    if not isinstance(data, dict):
        raise ValueError(f"expected dict, got {type(data).__name__}")
    version = str(data.get("schema_version") or SCHEMA_VERSION)
    if version.split(".", 1)[0] != SCHEMA_VERSION.split(".", 1)[0]:
        raise ValueError(
            f"incompatible state-packet schema version {version!r}; "
            f"this build expects {SCHEMA_VERSION}.x"
        )

    owner_raw = data.get("owner") or {}
    contract_raw = data.get("contract") or {}
    refs_raw = data.get("refs") or {}
    evidence_raw = data.get("evidence") or []

    owner = StatePacketOwner(
        role=str(owner_raw.get("role", "") or ""),
        instance_id=str(owner_raw.get("instance_id", "") or ""),
        role_context=str(owner_raw.get("role_context", "") or ""),
    )
    contract = StatePacketContract(
        behavior=str(contract_raw.get("behavior", "") or ""),
        acceptance=tuple(
            str(x) for x in (contract_raw.get("acceptance") or [])
        ),
        out_of_scope=tuple(
            str(x) for x in (contract_raw.get("out_of_scope") or [])
        ),
    )
    refs = StatePacketRefs(
        base_ref=str(refs_raw.get("base_ref", "main") or "main"),
        task_ref=str(refs_raw.get("task_ref", "") or ""),
        candidate_ref=str(refs_raw.get("candidate_ref", "") or ""),
    )
    evidence = tuple(
        StatePacketEvidence(
            kind=str(e.get("kind", "") or ""),
            path=str(e.get("path", "") or ""),
            status=str(e.get("status", "") or ""),
            event_id=str(e.get("event_id", "") or ""),
        )
        for e in evidence_raw
        if isinstance(e, dict)
    )
    return StatePacket(
        schema_version=version,
        run_id=str(data.get("run_id", "") or ""),
        feature_id=str(data.get("feature_id", "") or ""),
        task_id=str(data.get("task_id", "") or ""),
        objective=str(data.get("objective", "") or ""),
        current_stage=str(data.get("current_stage", "") or ""),
        owner=owner,
        contract=contract,
        refs=refs,
        completed=tuple(
            str(x) for x in (data.get("completed") or [])
        ),
        decisions=tuple(
            str(x) for x in (data.get("decisions") or [])
        ),
        evidence=evidence,
        risks=tuple(
            str(x) for x in (data.get("risks") or [])
        ),
        blocked_by=tuple(
            str(x) for x in (data.get("blocked_by") or [])
        ),
        next_owner=str(data.get("next_owner", "") or ""),
        next_event=str(data.get("next_event", "") or ""),
        generated_at=str(data.get("generated_at", "") or ""),
        generated_by=str(data.get("generated_by", "zf-cli") or "zf-cli"),
    )


def packet_from_json(text: str) -> StatePacket:
    return packet_from_dict(json.loads(text))
