"""Layer 1 housekeeping handlers — apply side effects from worker / Layer 2 events.

These are NOT business decisions. They are mechanical state-write handlers
that translate emitted events into Layer 1 state file updates. Layer 1 is the
housekeeper: when an agent says "I used N tokens", Layer 1 records it. When an
agent says "remember this decision", Layer 1 writes it to memory. When Layer 2
says "this task should have this contract", Layer 1 writes it to kanban.json.

Each handler is a pure function: (store, event) -> None. They are wired into
Orchestrator._react_to_events alongside _notify_orchestrator_agent.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from zf.core.cost.tracker import CostTracker
from zf.core.events.model import ZfEvent
from zf.core.memory.store import MemoryStore, _MEMORY_TYPES
from zf.core.task.schema import TaskContract
from zf.core.task.store import TaskStore
from zf.core.verification.validation import coerce_validation_spec
from zf.core.workflow.topology import WorkflowEventSets


def apply_agent_usage_event(
    tracker: CostTracker,
    event: ZfEvent,
    *,
    role_backends: dict[str, str] | None = None,
) -> None:
    """Record an agent.usage event into the cost tracker.

    Expected payload shape (from B2 stream-json ingestion):
        {"session_id": str, "total_cost_usd": float,
         "usage": {"input_tokens": int, "output_tokens": int, ...},
         "num_turns": int, "duration_ms": int}

    G-INST-6: ``event.actor`` may be an instance_id (``dev-1``) or a
    plain role.name (``dev``). We split the numeric suffix so
    per_role_totals still aggregates across replicas, while
    per_instance_totals keeps them distinct. Custom instance_ids that
    don't match ``<role>-<N>`` are stored with role_type == actor.

    1204: ``role_backends`` maps role_type → backend string
    (claude-code / codex). Caller (orchestrator) builds this from the
    loaded config so CostTracker gets the backend dimension without
    housekeeping importing config directly.
    """
    if not event.actor:
        return
    usage = event.payload.get("usage") or {}
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    if input_tokens == 0 and output_tokens == 0:
        return
    instance_id = event.actor
    m = re.match(r"^(.*?)-(\d+)$", instance_id)
    role_type = m.group(1) if m else instance_id
    backend = (role_backends or {}).get(role_type, "") or str(
        event.payload.get("backend", "")
    )
    # B-COST-01: model rides the payload (disk-reader / transport tag it);
    # absent → "default" still resolves to a sane rate.
    model = str(event.payload.get("model") or "default")
    # Provider self-reported cost (Claude stream-json `total_cost_usd`) is
    # authoritative when present — record_usage prefers it over token×rate.
    provider_cost = event.payload.get("total_cost_usd")
    provider_cost_usd = (
        float(provider_cost) if isinstance(provider_cost, (int, float)) else None
    )
    # Cache tokens are priced separately ONLY for backends whose input is
    # fresh-only. Codex bundles cache into input_tokens already, so passing
    # its cache fields again would double-count → pass 0 for codex.
    if backend == "codex":
        cache_creation = cache_read = 0
    else:
        cache_creation = int(usage.get("cache_creation_input_tokens", 0))
        cache_read = int(usage.get("cache_read_input_tokens", 0))
    tracker.record_usage(
        role=role_type,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
        instance_id=instance_id,
        backend=backend,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        provider_cost_usd=provider_cost_usd,
        source_event_id=str(event.id or ""),
        usage_sample_id=_usage_sample_id_for_event(event),
    )


def _usage_sample_id_for_event(event: ZfEvent) -> str:
    payload = event.payload or {}
    explicit = payload.get("usage_sample_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    source = str(payload.get("source") or "")
    # Provider stream events may legitimately report identical token counts
    # across different turns. Without an explicit sample id, fall back to the
    # event id for those. Disk-reader events are snapshots and need content
    # identity so repeated reads of the same backend file stay idempotent.
    if source != "disk_reader":
        return ""

    stable_payload = {
        "actor": event.actor,
        "backend": payload.get("backend"),
        "model": payload.get("model"),
        "model_context_window": payload.get("model_context_window"),
        "session_id": payload.get("session_id"),
        "source": source,
        "transcript_path": payload.get("transcript_path"),
        "usage": payload.get("usage") or {},
        "usage_timestamp": (
            payload.get("usage_timestamp")
            or payload.get("timestamp")
            or payload.get("sample_timestamp")
        ),
    }
    data = json.dumps(
        stable_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:24]


def promote_to_memory_note_event(event: ZfEvent) -> ZfEvent | None:
    """Auto-promote a system event into a memory.note event.

    Some events represent cross-round learning that workers rarely capture
    themselves (candidate.conflict, dev.blocked). The reactor calls this
    promoter at run_once entry to materialize a memory.note carrying the
    learning, then appends it through the normal event writer so it lands
    in events.jsonl + MemoryStore via the existing housekeeping path.

    Returns None when the event type is not promotable. The returned
    memory.note has actor=None (shared memory), causation_id pointing to
    the trigger event, and payload tagged with source="auto_promote" so
    readers can distinguish it from worker-emitted notes.
    """
    if event.type == "candidate.conflict":
        payload = event.payload or {}
        branch = payload.get("branch") or payload.get("pdd_id") or "?"
        files = payload.get("conflict_files") or []
        files_str = ",".join(files) if files else "?"
        failed = payload.get("failed_task_id") or payload.get("task_id") or "?"
        base = payload.get("base_commit") or "?"
        content = (
            f"candidate {branch} conflict on {files_str}; "
            f"failed_task={failed}, base={base[:12]}"
        )
        return ZfEvent(
            type="memory.note",
            actor=None,
            task_id=event.task_id,
            payload={
                "mem_type": "context",
                "content": content,
                "source": "auto_promote",
                "trigger_event_id": event.id,
                "trigger_event_type": event.type,
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        )

    if event.type == "dev.blocked":
        payload = event.payload or {}
        reason = (
            payload.get("reason")
            or payload.get("summary")
            or payload.get("error")
            or "unspecified"
        )
        task_id = event.task_id or "?"
        content = f"{task_id} dev blocked: {reason}"
        return ZfEvent(
            type="memory.note",
            actor=None,
            task_id=event.task_id,
            payload={
                "mem_type": "fix",
                "content": content,
                "source": "auto_promote",
                "trigger_event_id": event.id,
                "trigger_event_type": event.type,
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        )

    return None


def apply_worker_heartbeat_event(
    registry,  # RoleSessionRegistry — imported lazily to avoid circular dep
    event: ZfEvent,
) -> None:
    """α-2 (2026-05-17): persist worker.heartbeat into role_sessions.yaml.

    Expected payload shape:
        {"instance_id": "dev-1", "current_task_id": "TASK-...",
         "state": "idle|busy|blocked", "last_action_ts": "ISO8601",
         "context_used_ratio"?: 0.0-1.0, "checkpoint_ref"?: "<opaque>"}

    The instance_id is taken from ``event.actor`` first (worker emits
    its own heartbeat so actor == instance_id), with payload.instance_id
    as fallback. Empty actor → no-op (defensive).

    Consumed by α-3 EventWatcher sweep for proactive dispatch / true
    stuck detection (heartbeat-driven, not 4-min wall-clock timer).
    """
    instance_id = (event.actor or "").strip()
    if not instance_id:
        payload_iid = ""
        if isinstance(event.payload, dict):
            payload_iid = str(event.payload.get("instance_id") or "").strip()
        instance_id = payload_iid
    if not instance_id:
        return
    payload = event.payload if isinstance(event.payload, dict) else {}
    try:
        registry.record_heartbeat(instance_id, payload)
    except Exception:
        # heartbeat persistence failures must never break housekeeping
        pass


def apply_task_dispatched_heartbeat_seed(
    registry,  # RoleSessionRegistry — imported lazily to avoid circular dep
    event: ZfEvent,
) -> None:
    """Seed worker liveness from a successful Layer 1 dispatch.

    Workers can only emit ``worker.heartbeat`` after they receive and start
    executing a briefing. A re-dispatch that follows an old heartbeat would
    otherwise make the next heartbeat sweep compare against stale liveness
    metadata and falsely emit ``worker.stuck`` while the worker is active.

    ``task.dispatched`` and ``fanout.child.dispatched`` are deterministic
    kernel truth that the worker has just been handed work, so it is safe to
    refresh the role session as ``busy``.
    """
    if event.type not in {"task.dispatched", "fanout.child.dispatched"}:
        return
    payload = event.payload if isinstance(event.payload, dict) else {}
    instance_id = str(
        payload.get("assignee")
        or payload.get("instance_id")
        or payload.get("role_instance")
        or payload.get("role")
        or ""
    ).strip()
    task_id = str(event.task_id or payload.get("task_id") or "").strip()
    if not task_id and event.type == "fanout.child.dispatched":
        fanout_id = str(payload.get("fanout_id") or "").strip()
        child_id = str(payload.get("child_id") or "").strip()
        if fanout_id and child_id:
            task_id = f"fanout:{fanout_id}:{child_id}"
    if not instance_id or not task_id:
        return
    heartbeat_payload = {
        "instance_id": instance_id,
        "current_task_id": task_id,
        "state": "busy",
        "last_action_ts": event.ts,
        "source": "task.dispatched",
    }
    for key in (
        "dispatch_id",
        "run_id",
        "role",
        "role_instance",
        "fanout_id",
        "child_id",
        "stage_id",
    ):
        value = payload.get(key)
        if value:
            heartbeat_payload[key] = value
    heartbeat_payload["source"] = event.type
    try:
        registry.record_heartbeat(instance_id, heartbeat_payload)
    except Exception:
        # Dispatch liveness seeding is best-effort runtime projection.
        pass


def apply_worker_state_changed_event(
    registry,  # RoleSessionRegistry — imported lazily to avoid circular dep
    event: ZfEvent,
) -> None:
    """Mirror worker.state.changed into role_sessions liveness state.

    ``worker.state.changed`` is kernel truth for role availability. Workers may
    stop heartbeating after finishing a task, so an idle transition must refresh
    ``role_sessions.yaml``; otherwise the sweep can compare against the previous
    busy heartbeat and emit a stale ``worker.stuck`` after the gate completed.
    """
    if event.type != "worker.state.changed":
        return
    payload = event.payload if isinstance(event.payload, dict) else {}
    instance_id = str(payload.get("instance_id") or event.actor or "").strip()
    new_state = str(payload.get("to") or payload.get("state") or "").strip()
    if not instance_id or not new_state:
        return
    current_task_id = str(event.task_id or payload.get("task_id") or "").strip()
    previous_payload: dict = {}
    if not current_task_id and new_state.lower() not in {
        "idle",
        "awaiting_review",
    }:
        try:
            _, previous = registry.get_last_heartbeat(instance_id)
        except Exception:
            previous = None
        if isinstance(previous, dict):
            previous_payload = previous
            previous_source = str(previous.get("source") or "").strip()
            previous_task_id = str(
                previous.get("current_task_id") or previous.get("task_id") or ""
            ).strip()
            if previous_source in {
                "task.dispatched",
                "fanout.child.dispatched",
            }:
                current_task_id = previous_task_id
    heartbeat_payload = {
        "instance_id": instance_id,
        "current_task_id": current_task_id,
        "state": new_state,
        "last_action_ts": event.ts,
        "source": "worker.state.changed",
    }
    for key in ("from", "reason"):
        value = payload.get(key)
        if value:
            heartbeat_payload[key] = value
    if current_task_id and previous_payload:
        for key in (
            "dispatch_id",
            "run_id",
            "role",
            "role_instance",
            "fanout_id",
            "child_id",
            "stage_id",
        ):
            value = previous_payload.get(key)
            if value and key not in heartbeat_payload:
                heartbeat_payload[key] = value
    try:
        registry.record_heartbeat(instance_id, heartbeat_payload)
    except Exception:
        # State projection is best-effort and must never block event handling.
        pass


def apply_memory_note_event(store: MemoryStore, event: ZfEvent) -> None:
    """Write a memory.note event into MemoryStore.

    Expected payload shape:
        {"mem_type": "decision|pattern|fix|context", "content": "...",
         "source"?: "worker|auto_promote",
         "trigger_event_id"?: "evt-..."}

    actor=None → shared memory; actor="role" → role-specific memory.
    Extra payload fields (source, trigger_event_id) are accepted for
    forward-compatibility with kernel auto-promoted notes; behavior is
    unchanged.
    """
    mem_type = event.payload.get("mem_type")
    content = event.payload.get("content", "")
    if mem_type not in _MEMORY_TYPES or not content:
        return
    try:
        store.add(role=event.actor, mem_type=mem_type, content=content)
    except ValueError:
        pass


_REWORK_FAILURE_TYPES = (
    WorkflowEventSets.baseline().rework_triage_trigger_events
    - frozenset({"task.done.blocked"})
)


def apply_circuit_breaker_failure(
    event: ZfEvent, store_path,
) -> None:
    """LH-4.T3: register a failure against the (role_from_event, task)
    circuit breaker. Called alongside apply_rework_failure_event so the
    breaker + rework counter stay consistent.

    The breaker key is (role_name, task_id). Role is inferred from the
    event actor (e.g. review.rejected actor=review → role=review).
    """
    if event.type not in _REWORK_FAILURE_TYPES or not event.task_id:
        return
    from zf.core.errors.circuit_breaker import CircuitBreaker

    role_name = event.actor or "unknown"
    # Strip the -N suffix for instance_id like "dev-1".
    if "-" in role_name:
        prefix, suffix = role_name.rsplit("-", 1)
        if suffix.isdigit():
            role_name = prefix
    breaker = CircuitBreaker(
        key=(role_name, event.task_id),
        max_failures=5,
        window_seconds=1800.0,
        store_path=store_path,
    )
    breaker.record_failure(reason=event.type)


def apply_rework_failure_event(
    store: TaskStore,
    event: ZfEvent,
    *,
    events: list[ZfEvent] | None = None,
) -> None:
    """LH-0.T1: bump task.retry_count on rework-triggering failure.

    Runs for both Layer 1 legacy and Layer 2 modes because it lives in
    _apply_housekeeping (the one path both modes share). The subsequent
    dispatch — whether legacy _dispatch_rework or Layer 2's reassign +
    _dispatch_ready — reads retry_count to decide cap.

    avbs-r4 F12: 传入 events 时按 (task_id, fanout_id) 去重——echo 重派
    /重放会让同一个 fanout 的失败事件多次进流,机制性重放不是任务真实
    失败,不得刷爆 rework cap(r4 实测 cap 4/3 全部来自重放记账)。
    """
    if event.type not in _REWORK_FAILURE_TYPES or not event.task_id:
        return
    payload = event.payload if isinstance(event.payload, dict) else {}
    fanout_id = str(payload.get("fanout_id") or "")
    if events and fanout_id:
        for prior in events:
            if prior.id == event.id:
                break  # 只看当前事件之前的,否则窗口内两条重放互相抵消成零计数
            if prior.type not in _REWORK_FAILURE_TYPES:
                continue
            if str(prior.task_id or "") != str(event.task_id):
                continue
            prior_payload = prior.payload if isinstance(prior.payload, dict) else {}
            if str(prior_payload.get("fanout_id") or "") == fanout_id:
                return  # 同 fanout 已计数,重放不再 bump
    task = store.get(event.task_id)
    if task is None:
        return
    store.update(event.task_id, retry_count=task.retry_count + 1)


def apply_task_contract_event(store: TaskStore, event: ZfEvent) -> None:
    """Write task contract from a task.contract.update event.

    Expected payload shape:
        {"contract": {"behavior": str, "verification": str,
                      "scope": list[str], "exclusions": list[str],
                      "acceptance": str}}

    Layer 2 (Claude Code Orchestrator) fires this event when it has decided
    on a task contract. Layer 1 mechanically writes it to kanban.json on
    the matching task.

    avbs-r4 F9: payload.additional_task_ids 把同一份修订一次应用到多个
    同型任务——r4 中 Layer-2 修 flow 契约后没修 scene,同类 escalate 的
    处理一致性只靠 agent 记性;字段级回退到各任务现值,批量应用安全。
    """
    payload = event.payload if isinstance(event.payload, dict) else {}
    extra_ids = [
        str(x).strip() for x in (payload.get("additional_task_ids") or [])
        if str(x).strip()
    ]
    if extra_ids:
        base_payload = {
            k: v for k, v in payload.items() if k != "additional_task_ids"
        }
        for extra_id in dict.fromkeys(extra_ids):
            if extra_id == event.task_id:
                continue
            apply_task_contract_event(store, ZfEvent(
                type=event.type,
                actor=event.actor,
                task_id=extra_id,
                payload=base_payload,
            ))
    if not event.task_id:
        return
    task = store.get(event.task_id)
    if task is None:
        return
    contract_data = event.payload.get("contract") or {}
    existing = task.contract or TaskContract()
    behavior = contract_data.get("behavior", existing.behavior)
    if not behavior:
        behavior = _first_contract_text(
            contract_data, "summary", "goal", "objective", "description",
        )
    verification = contract_data.get("verification", existing.verification)
    if not verification and "verify" in contract_data:
        verification = contract_data.get("verify", "")
    if not verification:
        verification = _first_contract_text(
            contract_data,
            "verification_command",
            "test_command",
            "command",
            "check",
        )
    if not verification:
        verification = _infer_contract_verification(contract_data)
    verification = _coerce_contract_text(verification)
    extracted_verification = _extract_command_like(verification)
    if extracted_verification:
        verification = extracted_verification
    verification_tiers = contract_data.get(
        "verification_tiers",
        existing.verification_tiers,
    )
    validation = coerce_validation_spec(
        contract_data.get(
            "validation",
            contract_data.get("validation_spec", existing.validation),
        ),
    )
    shared_files = _coerce_contract_list(
        contract_data.get("shared_files", existing.shared_files),
    )
    scope = _contract_scope_from_payload(
        contract_data,
        existing_scope=existing.scope,
        fallback_files=shared_files,
    )
    acceptance = _coerce_contract_text(
        contract_data.get("acceptance", existing.acceptance),
    )
    new_contract = TaskContract(
        schema_version=contract_data.get(
            "schema_version",
            existing.schema_version,
        ),
        locale=contract_data.get("locale", existing.locale),
        feature_id=contract_data.get("feature_id", existing.feature_id),
        parent_task_id=contract_data.get(
            "parent_task_id",
            existing.parent_task_id,
        ),
        campaign=contract_data.get("campaign", existing.campaign),
        phase=contract_data.get("phase", existing.phase),
        source_backlog_task_id=contract_data.get(
            "source_backlog_task_id",
            existing.source_backlog_task_id,
        ),
        source_key=contract_data.get("source_key", existing.source_key),
        source_ref=contract_data.get("source_ref", existing.source_ref),
        source_task_id=contract_data.get(
            "source_task_id",
            existing.source_task_id,
        ),
        source_index_ref=contract_data.get(
            "source_index_ref",
            existing.source_index_ref,
        ),
        source_mode=contract_data.get("source_mode", existing.source_mode),
        source_title=contract_data.get("source_title", existing.source_title),
        source_excerpt=contract_data.get(
            "source_excerpt",
            existing.source_excerpt,
        ),
        product_contract_ref=contract_data.get(
            "product_contract_ref",
            existing.product_contract_ref,
        ),
        spec_skip_reason=contract_data.get(
            "spec_skip_reason",
            existing.spec_skip_reason,
        ),
        unknowns=_coerce_contract_list(
            contract_data.get("unknowns", existing.unknowns),
        ),
        review_profile=contract_data.get(
            "review_profile",
            existing.review_profile,
        ),
        behavior=str(behavior or ""),
        verification=verification,
        verification_tiers=(
            [str(item).strip() for item in verification_tiers if str(item).strip()]
            if isinstance(verification_tiers, list)
            else [
                part.strip()
                for part in str(verification_tiers or "").split(",")
                if part.strip()
            ]
        ),
        validation=validation,
        spec_ref=contract_data.get("spec_ref", existing.spec_ref),
        plan_ref=contract_data.get("plan_ref", existing.plan_ref),
        tdd_ref=contract_data.get("tdd_ref", existing.tdd_ref),
        critic_gate_ref=contract_data.get(
            "critic_gate_ref",
            existing.critic_gate_ref,
        ),
        critic_event_id=contract_data.get(
            "critic_event_id",
            existing.critic_event_id,
        ),
        critic_dispatch_id=contract_data.get(
            "critic_dispatch_id",
            existing.critic_dispatch_id,
        ),
        reviewed_arch_event_id=contract_data.get(
            "reviewed_arch_event_id",
            existing.reviewed_arch_event_id,
        ),
        source_arch_dispatch_id=contract_data.get(
            "source_arch_dispatch_id",
            existing.source_arch_dispatch_id,
        ),
        dispatch_id=contract_data.get("dispatch_id", existing.dispatch_id),
        dispatch_id_requirement=contract_data.get(
            "dispatch_id_requirement",
            existing.dispatch_id_requirement,
        ),
        canonical_case_id=contract_data.get(
            "canonical_case_id",
            existing.canonical_case_id,
        ),
        case_alias=contract_data.get("case_alias", existing.case_alias),
        canonical_behavior_test=contract_data.get(
            "canonical_behavior_test",
            existing.canonical_behavior_test,
        ),
        package_namespace=contract_data.get(
            "package_namespace",
            existing.package_namespace,
        ),
        scope=scope,
        affected_files=_coerce_contract_list(
            contract_data.get("affected_files", existing.affected_files),
        ),
        exclusions=contract_data.get("exclusions", existing.exclusions),
        explicit_non_goals=_coerce_contract_list(
            contract_data.get(
                "explicit_non_goals",
                existing.explicit_non_goals,
            ),
        ),
        acceptance=acceptance,
        evidence_contract=(
            contract_data.get("evidence_contract", existing.evidence_contract)
            if isinstance(
                contract_data.get("evidence_contract", existing.evidence_contract),
                dict,
            )
            else existing.evidence_contract
        ),
        review_route=(
            contract_data.get("review_route", existing.review_route)
            if isinstance(contract_data.get("review_route", existing.review_route), dict)
            else existing.review_route
        ),
        owner_role=contract_data.get("owner_role", existing.owner_role),
        owner_instance=contract_data.get("owner_instance", existing.owner_instance),
        wave=_coerce_contract_wave(
            contract_data.get("wave", existing.wave),
            default=existing.wave,
        ),
        shared_files=shared_files,
        exclusive_files=_coerce_contract_list(
            contract_data.get("exclusive_files", existing.exclusive_files),
        ),
        handoff_artifacts=_coerce_contract_list(
            contract_data.get("handoff_artifacts", existing.handoff_artifacts),
        ),
        task_doc_ref=contract_data.get("task_doc_ref", existing.task_doc_ref),
        source_doc_ref=contract_data.get("source_doc_ref", existing.source_doc_ref),
        progress_doc_ref=contract_data.get(
            "progress_doc_ref",
            existing.progress_doc_ref,
        ),
        evidence_doc_ref=contract_data.get(
            "evidence_doc_ref",
            existing.evidence_doc_ref,
        ),
        source_revision=contract_data.get(
            "source_revision",
            existing.source_revision,
        ),
        contract_revision=contract_data.get(
            "contract_revision",
            existing.contract_revision,
        ),
        capsule_revision=contract_data.get(
            "capsule_revision",
            existing.capsule_revision,
        ),
        rework_to=contract_data.get("rework_to", existing.rework_to),
        fanout_force=_coerce_contract_bool(
            contract_data.get("fanout_force", existing.fanout_force),
            default=existing.fanout_force,
        ),
        fix_of=contract_data.get("fix_of", existing.fix_of),
        # EVAL-ACCEPTANCE-CRITERIA-001 (doc 43 §2.5): per-criterion list
        # + evidence dict. Contract-update events can carry these
        # directly under contract.acceptance_criteria /
        # contract.acceptance_evidence.
        acceptance_criteria=_coerce_contract_list(
            contract_data.get(
                "acceptance_criteria", existing.acceptance_criteria,
            ),
        ),
        acceptance_evidence=_coerce_acceptance_evidence(
            contract_data.get("acceptance_evidence"),
            existing=existing.acceptance_evidence,
        ),
        quality_gates_override=(
            contract_data.get(
                "quality_gates_override",
                existing.quality_gates_override,
            )
            if isinstance(
                contract_data.get(
                    "quality_gates_override",
                    existing.quality_gates_override,
                ),
                dict,
            )
            else existing.quality_gates_override
        ),
    )
    updates = {"contract": asdict(new_contract)}
    if "blocked_by" in event.payload or "blocked_by" in contract_data:
        updates["blocked_by"] = _coerce_contract_list(
            event.payload.get("blocked_by", contract_data.get("blocked_by")),
        )
    store.update(event.task_id, **updates)


def spec_ingest_suggested_event(event: ZfEvent) -> ZfEvent | None:
    """When arch.proposal.done declares a spec_path pointing to a md file
    with frontmatter, suggest running ``zf spec ingest`` on it.

    Does NOT mutate kanban state — just emits a `spec.ingest.suggested`
    event so the operator (or a future automated subscriber) can act on
    it. Validating that the spec has frontmatter (and that the schema
    parses) is left to `zf spec validate` — here we only confirm the
    file exists and has a leading `---` block so we don't suggest
    ingesting non-spec markdown.

    Returns the event to emit, or None when nothing to suggest.
    """
    if event.type != "arch.proposal.done" or not event.task_id:
        return None
    payload = event.payload if isinstance(event.payload, dict) else {}
    candidates: list[str] = []
    for key in ("spec_path", "spec", "spec_refs", "specs"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw:
            candidates.append(raw)
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, str) and item:
                    candidates.append(item)
    # Heuristic: also accept evidence_refs / artifact_refs entries that
    # look like a markdown path under docs/ or specs/
    for key in ("evidence_refs", "artifact_refs"):
        raw = payload.get(key)
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, str):
                    continue
                if item.endswith(".md") and ("docs/" in item or "specs/" in item):
                    candidates.append(item)
    if not candidates:
        return None
    # Dedup preserve order
    seen: set[str] = set()
    paths: list[str] = []
    for c in candidates:
        if c not in seen:
            paths.append(c)
            seen.add(c)
    # Probe each candidate: file must exist locally AND start with `---`
    eligible: list[str] = []
    for p in paths:
        candidate = Path(p)
        if not candidate.is_absolute():
            # Best-effort: try relative to cwd. This is fine because the
            # orchestrator runs from the project root.
            candidate = Path.cwd() / candidate
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        if text.lstrip().startswith("---"):
            eligible.append(p)
    if not eligible:
        return None
    return ZfEvent(
        type="spec.ingest.suggested",
        actor="zf-cli",
        task_id=event.task_id,
        payload={
            "source": "arch.proposal.done",
            "spec_paths": eligible,
            "command": f"zf spec ingest {eligible[0]}",
        },
        causation_id=event.id,
        correlation_id=event.correlation_id,
    )


def arch_proposal_contract_update_event(
    store: TaskStore,
    event: ZfEvent,
) -> ZfEvent | None:
    """Build a deterministic final contract update from arch.proposal.done.

    The orchestrator may create the initial task contract for the arch design
    phase. Once arch emits a structured proposal, Layer 1 can project the
    actual implementation contract from that proposal so final verification
    does not judge the temporary design-phase contract.
    """
    if event.type != "arch.proposal.done" or not event.task_id:
        return None
    task = store.get(event.task_id)
    if task is None:
        return None
    payload = event.payload if isinstance(event.payload, dict) else {}
    scope = _paths_from_file_plan(payload.get("file_plan"))
    if not scope:
        scope = _coerce_contract_list(payload.get("files"))
    test_plan = payload.get("test_plan")
    verification = _verification_from_arch_test_plan(test_plan)
    if not verification:
        verification = _infer_contract_verification(payload)
    verification = _extract_command_like(verification) or verification
    if not scope or not verification:
        return None

    existing = task.contract or TaskContract()
    summary = _coerce_contract_text(payload.get("summary"))
    behavior = summary or existing.behavior
    acceptance = _arch_acceptance_text(payload, verification)
    exclusive_files = list(existing.exclusive_files or [])
    exclusive_set = set(exclusive_files)
    shared_files = [path for path in scope if path not in exclusive_set]
    project_root = store.path.parent.parent
    handoff_artifacts = _normalize_handoff_artifact_refs(
        [
            f"arch.proposal.done:{event.id}",
            *_coerce_contract_list(payload.get("artifact_refs")),
            *_coerce_contract_list(payload.get("evidence_refs")),
        ],
        project_root=project_root,
    )
    contract = {
        "behavior": behavior,
        "verification": verification,
        "verification_tiers": ["runtime"],
        "scope": scope,
        "exclusions": list(existing.exclusions or []),
        "acceptance": acceptance,
        "owner_role": "dev",
        "owner_instance": "",
        "wave": int(existing.wave or 0),
        "shared_files": shared_files,
        "exclusive_files": exclusive_files,
        "handoff_artifacts": handoff_artifacts,
        "rework_to": existing.rework_to or "dev",
    }
    return ZfEvent(
        type="task.contract.update",
        actor="zf-cli",
        task_id=event.task_id,
        payload={
            "source": "arch.proposal.done",
            "contract": contract,
        },
        causation_id=event.id,
        correlation_id=event.correlation_id,
    )


def _contract_scope_from_payload(
    contract_data: dict,
    *,
    existing_scope: list[str],
    fallback_files: list[str],
) -> list[str]:
    scope_explicit = "scope" in contract_data
    raw_scope = contract_data.get("scope", existing_scope)
    scope: object = raw_scope
    if isinstance(raw_scope, dict):
        scope = (
            raw_scope.get("files")
            or raw_scope.get("paths")
            or raw_scope.get("create")
            or raw_scope.get("allowed_files")
            or raw_scope.get("allowed")
            or raw_scope.get("in")
            or []
        )
    if not scope and "files" in contract_data:
        scope = contract_data.get("files", [])
    scope_list = _coerce_contract_list(scope)
    if not scope_list and fallback_files:
        return list(fallback_files)
    if (
        not scope_explicit
        and _scope_looks_like_prose(scope_list)
        and fallback_files
    ):
        return list(fallback_files)
    return scope_list


def _scope_looks_like_prose(values: list[str]) -> bool:
    for value in values:
        if " " in value.strip():
            return True
    return False


def _paths_from_file_plan(value: object) -> list[str]:
    paths: list[str] = []
    if not isinstance(value, list):
        return paths
    for item in value:
        raw = ""
        if isinstance(item, dict):
            raw = str(
                item.get("path")
                or item.get("file")
                or item.get("target")
                or ""
            )
        else:
            raw = str(item)
        raw = raw.strip()
        if raw and raw not in paths:
            paths.append(raw)
    return paths


def _verification_from_arch_test_plan(value: object) -> str:
    if isinstance(value, dict):
        return _coerce_contract_text(
            value.get("verification_command")
            or value.get("verification")
            or value.get("command")
            or value.get("test_command")
            or value.get("check")
        )
    if isinstance(value, list):
        for item in value:
            verification = _verification_from_arch_test_plan(item)
            if verification:
                return verification
    return ""


def _arch_acceptance_text(payload: dict, verification: str) -> str:
    lines: list[str] = []
    summary = _coerce_contract_text(payload.get("summary"))
    if summary:
        lines.append(summary)
    for test_plan in _iter_arch_test_plans(payload.get("test_plan")):
        cases = test_plan.get("cases")
        if isinstance(cases, list):
            for case in cases:
                if isinstance(case, dict):
                    name = str(case.get("name") or "").strip()
                    expected = str(case.get("expected") or "").strip()
                    if name and expected:
                        lines.append(f"{name}: expected {expected}")
                    elif name:
                        lines.append(name)
                else:
                    text = str(case).strip()
                    if text:
                        lines.append(text)
    lines.append(f"Verification command passes: {verification}")
    return "\n".join(lines)


def _iter_arch_test_plans(value: object) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _first_contract_text(data: dict, *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        text = _coerce_contract_text(value)
        if text:
            return text
    return ""


def _coerce_contract_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _coerce_acceptance_evidence(
    value: object,
    *,
    existing: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """EVAL-ACCEPTANCE-CRITERIA-001: coerce a payload value into the
    canonical {criterion_key: [event_id, ...]} shape, merging with
    ``existing`` so accumulated evidence from earlier review/judge
    events isn't lost.
    """
    out: dict[str, list[str]] = {}
    if existing:
        for k, v in existing.items():
            if isinstance(v, (list, tuple)):
                out[str(k)] = [str(e) for e in v if str(e).strip()]
    if not isinstance(value, dict):
        return out
    for key, refs in value.items():
        key_str = str(key).strip()
        if not key_str:
            continue
        if not isinstance(refs, (list, tuple)):
            continue
        bucket = out.setdefault(key_str, [])
        for ref in refs:
            ref_str = str(ref).strip()
            if ref_str and ref_str not in bucket:
                bucket.append(ref_str)
    return out


def apply_acceptance_evidence_event(
    store: "TaskStore", event: ZfEvent,
) -> None:
    """EVAL-ACCEPTANCE-CRITERIA-001 (doc 43 §2.5): when review/judge
    completion event carries ``acceptance_evidence_update``, merge
    those refs into task.contract.acceptance_evidence.

    Idempotent: re-applying same event yields no change.
    """
    if not event.task_id:
        return
    if event.type not in (
        "review.approved", "test.passed", "judge.passed",
    ):
        return
    payload = event.payload if isinstance(event.payload, dict) else {}
    update = payload.get("acceptance_evidence_update")
    if not isinstance(update, dict) or not update:
        return
    task = store.get(event.task_id)
    if task is None or task.contract is None:
        return
    contract = task.contract
    # Auto-tag the source event id when the update contains "$source"
    # token (rare but useful) or simply append event.id.
    merged_update: dict[str, list[str]] = {}
    for key, refs in update.items():
        if not isinstance(refs, (list, tuple)):
            continue
        resolved: list[str] = []
        for ref in refs:
            ref_str = str(ref).strip()
            if not ref_str:
                continue
            if ref_str == "$source":
                ref_str = event.id
            resolved.append(ref_str)
        if resolved:
            merged_update[str(key).strip()] = resolved
    if not merged_update:
        return
    new_evidence = _coerce_acceptance_evidence(
        merged_update, existing=contract.acceptance_evidence,
    )
    if new_evidence == contract.acceptance_evidence:
        return  # idempotent no-op
    new_contract = TaskContract(**{
        **asdict(contract),
        "acceptance_evidence": new_evidence,
    })
    store.update(event.task_id, contract=asdict(new_contract))


def _normalize_handoff_artifact_refs(
    values: list[str],
    *,
    project_root: Path,
) -> list[str]:
    """Keep handoff refs compatible with strict contract path validation."""
    normalized: list[str] = []
    seen: set[str] = set()
    root = project_root.resolve()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        path = Path(text)
        if path.is_absolute():
            try:
                rel = path.resolve().relative_to(root)
            except ValueError:
                continue
            if not rel.parts or rel.parts[0] in {".zf", ".git"}:
                continue
            text = rel.as_posix()
            path = Path(text)
        if any(part == ".." for part in path.parts):
            continue
        if path.parts and path.parts[0] in {".zf", ".git"}:
            continue
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _coerce_contract_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        for key in (
            "required_command",
            "command",
            "check",
            "verification",
            "verify",
            "test_command",
            "text",
        ):
            if key in value:
                return _coerce_contract_text(value.get(key))
    return str(value).strip()


def _coerce_contract_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _coerce_contract_wave(value: object, *, default: int = 0) -> int:
    try:
        fallback = int(default or 0)
    except (TypeError, ValueError):
        fallback = 0
    if value is None or value == "":
        return fallback
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return fallback
    try:
        return int(text)
    except ValueError:
        match = re.search(r"\d+", text)
        return int(match.group(0)) if match else fallback


def _infer_contract_verification(data: dict) -> str:
    values: list[object] = []
    for key in ("acceptance", "checks", "required_checks", "steps"):
        value = data.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value:
            values.append(value)
    for value in values:
        command = _extract_command_like(str(value))
        if command:
            return command
    return ""


def _extract_command_like(text: str) -> str:
    match = re.search(
        r"\b(?:(?:[A-Z_][A-Z0-9_]*=[^\s]+\s+)*)"
        r"(?:(?:[~./\w-]+/)*(?:python3?|pytest|pnpm|npm|node|uv|ruff|mypy)"
        r"|python3?|pytest|pnpm|npm|node|uv|ruff|mypy)\b",
        text,
    )
    if match is None:
        return ""
    command = text[match.start():].strip()
    for marker in (" passes", " pass", " succeeds", " succeed", " and fix", " then "):
        if marker in command:
            command = command.split(marker, 1)[0]
    command = command.strip(" .。;;、。；`")
    # #A fix (cangjie 2026-05-21): when verification is wrapped like
    # `bash -lc 'PATH=...:$PATH cmd'`, the regex above matches from
    # inside the wrap (at `PATH=`), strip-cuts `bash -lc '` prefix but
    # leaves a trailing unmatched quote. ContractD's `sh -n -c "..."`
    # then fails with "Unterminated quoted string" (exit 2), even
    # though the original wrapped command was syntactically valid.
    # Trim trailing unmatched quote(s) to restore a runnable command.
    # Refs: cangjie incidents/2026-05-21-bug-A-path-sanitizer.md
    #       (manifests on TASK-P0V01 + P0V04 ContractD reject loop)
    if command.endswith("'") and command.count("'") % 2 == 1:
        command = command[:-1].rstrip()
    if command.endswith('"') and command.count('"') % 2 == 1:
        command = command[:-1].rstrip()
    return command
