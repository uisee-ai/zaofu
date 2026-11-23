"""Opt-in fixed-scope inspection jobs and notifications for a bound operation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import (
    canonical_json_sha256,
    write_immutable_json_sidecar,
)
from zf.runtime.workflow_calendar import WorkflowCalendar
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.worker_reply_binding import resolve_reply_operation

ACTOR = "workflow-scheduler"
PREFIX = "workflow.schedule."


def instant(value: str) -> datetime:
    value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        raise ValueError("schedule time requires an explicit UTC offset")
    return value.astimezone(timezone.utc)


def definitions(config: Any) -> list[dict]:
    metadata = getattr(getattr(config, "workflow", None), "flow_metadata", {}) or {}
    rows = metadata.get("orchestration_schedules", [])
    if not isinstance(rows, list):
        raise ValueError("orchestration_schedules requires a list")
    seen = set()
    for definition in rows:
        if not isinstance(definition, dict):
            raise ValueError("schedule definition requires a mapping")
        if any(
            not isinstance(definition.get(k), str) or not definition[k].strip()
            for k in (
                "id",
                "revision",
                "owner",
                "goal",
                "workflow_run_id",
                "effective_at",
            )
        ):
            raise ValueError(
                "schedule requires identity, revision, owner, goal, run and effective_at"
            )
        if definition["id"] in seen:
            raise ValueError("duplicate schedule ID")
        seen.add(definition["id"])
        instant(definition["effective_at"])
        if type(definition.get("enabled", False)) is not bool:
            raise ValueError("enabled must be boolean")
        if definition.get("action") == "inspect":
            WorkflowCalendar(definition["cron"], definition["timezone"])
            if (
                type(definition.get("max_occurrences")) is not int
                or definition["max_occurrences"] < 1
            ):
                raise ValueError(
                    "independent inspection requires a positive occurrence budget"
                )
        elif definition.get("action") == "notify":
            instant(definition["deadline"])
            if instant(definition["deadline"]) < instant(definition["effective_at"]):
                raise ValueError("deadline precedes wait")
            for key in ("operation_id", "request_hash", "actor", "attempt_id"):
                if (
                    not isinstance(definition.get(key), str)
                    or not definition[key].strip()
                ):
                    raise ValueError(
                        "wait requires the original operation, request, actor and attempt"
                    )
            if bool(definition.get("event_type")) != bool(
                definition.get("event_actor")
            ):
                raise ValueError(
                    "an event wait requires both type and trusted source actor"
                )
        else:
            raise ValueError("unsupported workflow schedule action")
        for key, default in (
            ("catchup", 1),
            ("retry_seconds", 1),
            ("max_backoff_seconds", 60),
            ("max_attempts", 1),
        ):
            limit = definition.get(key, default)
            if type(limit) is not int or limit < 1:
                raise ValueError(f"schedule {key} must be a positive integer")
        if definition.get("expires_at"):
            if instant(definition["expires_at"]) < instant(definition["effective_at"]):
                raise ValueError("schedule expires before activation")
    return rows


def _record(runtime, name, definition, digest, *, now, **fields):
    return runtime.event_writer.append(
        ZfEvent(
            type=PREFIX + name,
            origin="kernel",
            actor=ACTOR,
            correlation_id=definition["workflow_run_id"],
            payload={
                "definition_id": definition["id"],
                "revision": definition["revision"],
                "definition_digest": digest,
                "owner": definition["owner"],
                "workflow_run_id": definition["workflow_run_id"],
                "at": now.isoformat(),
                **fields,
            },
        )
    )


def _due(d, history, events, now, manual_id):
    activation = instant(d["effective_at"])
    if now < activation or (d.get("expires_at") and now > instant(d["expires_at"])):
        return []
    if manual_id:
        return [(f"manual:{manual_id}", now, "manual", "")]
    if d["action"] == "notify":
        deadline = instant(d["deadline"])
        for e in events:
            if (
                e.origin == "kernel"
                and d.get("event_type")
                and e.type == d["event_type"]
                and e.actor == d["event_actor"]
                and e.payload.get("workflow_run_id") == d["workflow_run_id"]
                and e.payload.get("operation_id") == d["operation_id"]
                and activation <= instant(e.ts) <= min(now, deadline)
            ):
                return [("wait", instant(e.ts), "event", e.id)]
        return [("wait", deadline, "deadline", "")] if now >= deadline else []
    prior = [
        instant(e.payload["scheduled_at"])
        for e in history
        if e.type == PREFIX + "claimed" and e.payload["reason"] == "cron"
    ]
    after = max(prior) if prior else activation - timedelta(microseconds=1)
    times = WorkflowCalendar(d["cron"], d["timezone"]).due(
        after, now, limit=d.get("catchup", 1)
    )
    return [(t.isoformat(), t, "cron", after.isoformat()) for t in times]


def _execute(runtime, d, claim, events):
    run = d["workflow_run_id"]
    if d["action"] == "inspect":
        operations = [
            op
            for op in reduce_workflow_operations(events).values()
            if op.get("workflow_run_id") == run
        ]
        report = {
            "goal": d["goal"],
            "workflow_run_id": run,
            "owner": d["owner"],
            "occurrence": claim.payload["occurrence"],
            "authorizes_execution": False,
            "operations": [
                {
                    k: op.get(k)
                    for k in (
                        "operation_id",
                        "request_hash",
                        "status",
                        "role_instance",
                        "active_attempt_id",
                        "admitted_call_result_ref",
                    )
                }
                for op in operations
            ],
        }
        return {
            "report_ref": write_immutable_json_sidecar(
                runtime.state_dir,
                report,
                root="schedule-inspections",
                kind="schedule_inspection",
                schema_version="schedule-inspection.v1",
                created_by=ACTOR,
            )
        }
    existing = next(
        (
            e
            for e in events
            if e.type == "worker.reply.requested"
            and e.origin == "kernel"
            and e.actor == ACTOR
            and e.causation_id == claim.id
        ),
        None,
    )
    if existing:
        return {"notification_event_id": existing.id}
    binding = {
        "operation_id": d["operation_id"],
        "attempt_id": d["attempt_id"],
        "workflow_run_id": run,
    }
    op = resolve_reply_operation(runtime, d["actor"], binding)
    if op["request_hash"] != d["request_hash"]:
        raise ValueError("wait request was replaced")
    wake = runtime.event_writer.append(
        ZfEvent(
            type="worker.reply.requested",
            actor=ACTOR,
            origin="kernel",
            task_id=op.get("task_id"),
            causation_id=claim.id,
            correlation_id=run,
            payload={
                **binding,
                "instance_id": d["actor"],
                "lease_id": op.get("lease_id"),
                "request_hash": d["request_hash"],
                "notification_key": claim.id,
                "message": f"Bound wait {d['id']} reached {claim.payload['reason']}. "
                "Consult the current operation and its original authorization before continuing.",
            },
        )
    )
    return {"notification_event_id": wake.id}


def reconcile_schedules(runtime, *, now=None, manual_id="", definition_id="") -> list:
    definitions_ = definitions(runtime.config)
    locks = runtime.state_dir / "locks" / "schedule"
    if not definitions_ and not locks.exists() and not manual_id:
        return []
    wall_clock = now is None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("schedule clock must be timezone aware")
    if manual_id and not definition_id:
        raise ValueError("manual run requires a definition")
    emitted = []
    if locks.exists() and not definition_id:
        live_ids = {d["id"] for d in definitions_}
        history = [
            e
            for e in runtime.event_log.read_all()
            if e.origin == "kernel"
            and e.actor == ACTOR
            and e.type in {PREFIX + "claimed", PREFIX + "finished"}
            and e.payload.get("definition_id") not in live_ids
        ]
        finished = {
            e.payload["claim_id"] for e in history if e.type == PREFIX + "finished"
        }
        for claim in history:
            if claim.type != PREFIX + "claimed" or claim.id in finished:
                continue
            p = claim.payload
            with locked_path(locks / canonical_json_sha256(p["definition_id"])):
                if any(
                    e.type == PREFIX + "finished"
                    and e.origin == "kernel"
                    and e.actor == ACTOR
                    and e.payload.get("claim_id") == claim.id
                    for e in runtime.event_log.read_all()
                ):
                    continue
                definition = {
                    "id": p["definition_id"],
                    **{k: p[k] for k in ("revision", "owner", "workflow_run_id")},
                }
                emitted.append(
                    _record(
                        runtime,
                        "finished",
                        definition,
                        p["definition_digest"],
                        now=now,
                        occurrence=p["occurrence"],
                        claim_id=claim.id,
                        status="cancelled",
                        reason="definition_removed",
                    )
                )
    for d in definitions_:
        if definition_id and d["id"] != definition_id:
            continue
        # Serialize all attempts and retries of this definition, including manual calls.
        with locked_path(
            runtime.state_dir / "locks" / "schedule" / canonical_json_sha256(d["id"])
        ):
            events = runtime.event_log.read_all()
            prior = [
                e
                for e in events
                if e.type.startswith(PREFIX)
                and e.origin == "kernel"
                and e.actor == ACTOR
                and e.payload.get("definition_id") == d["id"]
                and e.payload.get("revision") != d["revision"]
            ]
            closed = {
                e.payload["occurrence"] for e in prior if e.type == PREFIX + "finished"
            }
            for old in prior:
                if (
                    old.type == PREFIX + "claimed"
                    and old.payload["occurrence"] not in closed
                ):
                    old_definition = {
                        **d,
                        **{
                            k: old.payload[k]
                            for k in ("revision", "owner", "workflow_run_id")
                        },
                    }
                    emitted.append(
                        _record(
                            runtime,
                            "finished",
                            old_definition,
                            old.payload["definition_digest"],
                            now=now,
                            occurrence=old.payload["occurrence"],
                            claim_id=old.id,
                            status="cancelled",
                            reason="definition_superseded",
                        )
                    )
            history = [
                e
                for e in events
                if e.type.startswith(PREFIX)
                and e.origin == "kernel"
                and e.actor == ACTOR
                and e.payload.get("definition_id") == d["id"]
                and e.payload.get("revision") == d["revision"]
            ]
            digest = canonical_json_sha256(
                {k: v for k, v in d.items() if k != "enabled"}
            )
            if any(e.payload["definition_digest"] != digest for e in history):
                raise ValueError(
                    "schedule authorization or goal changed without a new revision"
                )
            claims = {
                e.payload["occurrence"]: e
                for e in history
                if e.type == PREFIX + "claimed"
            }
            terminal = {
                e.payload["occurrence"]
                for e in history
                if e.type == PREFIX + "finished"
            }
            pending = [c for k, c in claims.items() if k not in terminal]
            if not d.get("enabled") or (
                d.get("expires_at") and now > instant(d["expires_at"])
            ):
                for claim in pending:
                    emitted.append(
                        _record(
                            runtime,
                            "finished",
                            d,
                            digest,
                            now=now,
                            occurrence=claim.payload["occurrence"],
                            claim_id=claim.id,
                            status="cancelled",
                            reason="disabled_or_expired",
                        )
                    )
                continue
            # Reconcile an interrupted claim before a new occurrence can acquire the slot.
            due = [] if pending else _due(d, history, events, now, manual_id)
            for key, when, reason, source in due:
                occurrence = canonical_json_sha256([d["id"], d["revision"], key])
                if occurrence in claims or len(claims) >= d.get("max_occurrences", 1):
                    continue
                claim = _record(
                    runtime,
                    "claimed",
                    d,
                    digest,
                    now=now,
                    occurrence=occurrence,
                    scheduled_at=when.isoformat(),
                    reason=reason,
                    trigger_ref=source,
                )
                pending.append(claim)
                claims[occurrence] = claim
                emitted.append(claim)
                # Finish this occurrence before reserving its successor.
                break
            for claim in pending:
                occurrence = claim.payload["occurrence"]
                failures = [
                    e
                    for e in history
                    if e.type == PREFIX + "retry"
                    and e.payload["occurrence"] == occurrence
                ]
                if failures and instant(failures[-1].payload["retry_at"]) > now:
                    continue
                started_at = datetime.now(timezone.utc) if wall_clock else now
                try:
                    result = _execute(runtime, d, claim, events)
                except OSError as exc:
                    attempts = len(failures) + 1
                    delay = min(
                        d.get("max_backoff_seconds", 60),
                        d.get("retry_seconds", 1) * 2 ** min(attempts - 1, 30),
                    )
                    when = now + timedelta(seconds=delay)
                    if attempts < d.get("max_attempts", 1) and (
                        not d.get("expires_at") or when <= instant(d["expires_at"])
                    ):
                        emitted.append(
                            _record(
                                runtime,
                                "retry",
                                d,
                                digest,
                                now=now,
                                occurrence=occurrence,
                                attempt=attempts,
                                retry_at=when.isoformat(),
                                reason=type(exc).__name__,
                            )
                        )
                        continue
                    result = {"status": "needs_owner", "reason": type(exc).__name__}
                except ValueError as exc:
                    result = {"status": "stale", "reason": str(exc)}
                completed_at = datetime.now(timezone.utc) if wall_clock else now
                emitted.append(
                    _record(
                        runtime,
                        "finished",
                        d,
                        digest,
                        now=completed_at,
                        occurrence=occurrence,
                        started_at=started_at.isoformat(),
                        claim_id=claim.id,
                        **{"status": "completed", **result},
                    )
                )
    return emitted
