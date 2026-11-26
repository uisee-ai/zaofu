"""Resolve a mailbox address against the current admitted roster and operation."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path

from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.pause_lifecycle import is_run_dispatch_paused
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.stage_team_projection import project_stage_teams
from zf.runtime.stage_team_sources import read_stage_team_inputs
from zf.runtime.workflow_operation import reduce_workflow_operations, _safe_component
from zf.runtime.workflow_proposal import load_workflow_proposal

# Bind an accepted write to the very ledger snapshot used to authorize it.
# All existing transitions use the same EventLog append lock, including WRC
# cancellation and task-plan replacement outside this mailbox serializer.
lease_expires = ContextVar("mailbox_lease_expires", default="")
admission_offset = ContextVar("mailbox_admission_offset", default=None)

ACTOR = "durable-mailbox"
PREFIX = "workflow.mailbox."


def enabled(config):
    return (
        getattr(getattr(getattr(config, "runtime", None), "durable_mailbox", None), "enabled", False) is True
    )


def record(e, kind):
    return e.origin == "kernel" and e.actor == ACTOR and e.type == PREFIX + kind


def owner_role(config, owner):
    if owner == "orchestrator":
        from zf.runtime.orchestrator_agent_role import configured_orchestrator_role_from_config

        role = configured_orchestrator_role_from_config(config)
    elif owner == "run_manager":
        from zf.runtime.run_manager_resident import build_resident_run_manager_role

        role = build_resident_run_manager_role(config)
    else:
        role = None
    if not role:
        raise ValueError("mailbox coordination owner is not configured")
    return role.instance_id or role.name


def terminal(events, run):
    from zf.runtime.run_admission import build_run_admission_projection

    projection = build_run_admission_projection([e for e in events if e.origin == "kernel"])
    entry = projection.runs.get(run)
    return bool(entry and entry.terminal)


def operation_binding(op):
    if not all(
        op.get(k)
        for k in ("workflow_run_id", "role_instance", "operation_id", "active_attempt_id", "request_hash")
    ):
        raise ValueError("mailbox operation binding is incomplete")
    if (
        op.get("status") != "running"
        or op.get("admitted_call_result_ref")
        or op.get("call_result_admitted_event_id")
    ):
        raise ValueError("mailbox consumer operation is stale")
    return {
        "actor": op["role_instance"],
        "operation_id": op["operation_id"],
        "attempt_id": op["active_attempt_id"],
        "workflow_run_id": op["workflow_run_id"],
        "request_hash": op["request_hash"],
        "lease_id": op.get("lease_id") or "",
        "dispatch_id": op.get("dispatch_id") or "",
        "task_id": op.get("task_id") or "",
    }


def snapshot(runtime, identity, attempt):
    from zf.runtime.durable_mailbox_index import MailboxIndex

    def select_versions(run, facts):
        if not run:
            return []
        ops = reduce_workflow_operations([e for e in facts if e.origin == "kernel"])
        return [
            addr["version"]
            for addr, ids in roster(runtime, facts, ops, run)
            if identity.get("operation_id") in ids
        ]

    index = MailboxIndex(runtime)
    events = index.read(identity, select_versions=select_versions)
    kernel = [e for e in events if e.origin == "kernel"]
    operations = reduce_workflow_operations(kernel)
    actor = identity.get("actor")
    if not actor:
        raise ValueError("mailbox requires an authenticated actor")
    if identity.get("operation_id"):
        op = operations.get(identity["operation_id"]) or {}
        current = operation_binding(op)
        current["authority_event_id"] = op.get("started_event_id") or op.get("last_event_id")
        if "credential_generation" in identity:
            from zf.runtime.artifact_read_capability import _credential_metadata

            metadata = _credential_metadata(runtime.state_dir, actor)
            generation = identity["credential_generation"]
            if (
                not generation
                or metadata.get("generation") != generation
                or metadata.get("role_instance") != actor
            ):
                raise ValueError("mailbox worker credential generation changed")
        from zf.runtime.task_attempt_runtime import _expired

        if attempt and (
            attempt.get("status") not in {"prepared", "delivering", "sent"}
            or _expired(attempt.get("lease_expires_at") or "")
            or attempt.get("lease_id") != current["lease_id"]
            or attempt.get("dispatch_id") != current["dispatch_id"]
            or attempt.get("operation_id") != current["operation_id"]
            or attempt.get("instance_id") != current["actor"]
            or attempt.get("task_id") != current["task_id"]
            or attempt.get("run_id") != current["workflow_run_id"]
        ):
            raise ValueError("mailbox attempt lease is stale")
        if not identity.get("attempt_id") or any(identity[k] != current[k] for k in current if k in identity):
            raise ValueError("mailbox consumer identity or lease changed")
        if identity.get("credential_generation"):
            current["credential_generation"] = identity["credential_generation"]
    elif identity.get("assistance_request_id") and actor and identity.get("credential_generation"):
        from zf.runtime.artifact_read_capability import _credential_metadata

        metadata = _credential_metadata(runtime.state_dir, actor)
        if (
            metadata.get("generation") != identity["credential_generation"]
            or metadata.get("role_instance") != actor
        ):
            raise ValueError("mailbox resident credential generation changed")
        aid = identity["assistance_request_id"]
        run = identity.get("workflow_run_id")
        matches = [
            e
            for e in kernel
            if record(e, "accepted")
            and e.payload.get("kind") == "question"
            and e.payload.get("question_id") == aid
            and e.payload.get("workflow_run_id") == run
        ]
        if len(matches) != 1:
            raise ValueError("no unique accepted advisory question for this resident")
        p = matches[0].payload
        addresses = [
            r
            for r in p["recipients"]
            if r.get("mode") == "coordination"
            and r.get("role") == actor == owner_role(runtime.config, r["owner"])
        ]
        asked = any(
            e.type == "worker.assistance.requested"
            and e.payload.get("request_id") == aid
            and e.payload.get("workflow_run_id") == run
            and e.payload.get("owner_instance") == actor
            and e.payload.get("durable_mailbox")
            for e in kernel
        )
        if (
            not asked
            or len(addresses) != 1
            or not any(
                record(e, "notification")
                and e.payload.get("workflow_run_id") == run
                and e.payload.get("message_key") == p["message_key"]
                and e.payload.get("request_id") == aid
                and e.payload.get("owner_instance") == actor
                and e.payload.get("status") in {"attempted", "confirmed", "unknown"}
                for e in kernel
            )
        ):
            raise ValueError("resident lacks a bound advisory delivery")
        current = {
            "actor": actor,
            "workflow_run_id": run,
            "assistance_request_id": aid,
            "credential_generation": identity["credential_generation"],
            "authority_event_id": matches[0].id,
        }
        for key in ("provider_session_id", "provider_turn_id"):
            if identity.get(key):
                current[key] = identity[key]
    else:
        raise ValueError("authenticated mailbox consumer required")
    coordination_roles = []
    for owner in ("orchestrator", "run_manager"):
        try:
            if owner_role(runtime.config, owner) == actor:
                coordination_roles.append(owner)
        except (ValueError, AttributeError):
            continue
    if coordination_roles:
        current["coordination_owners"] = sorted(set(coordination_roles))
    return events, operations, current, index.offset


@contextmanager
def guarded(runtime, identity, *, mutation=True):
    operation = identity.get("operation_id")
    lock = (
        locked_path(
            Path(runtime.state_dir)
            / "projections/workflow-operations"
            / (_safe_component(operation) + ".guard")
        )
        if operation
        else nullcontext()
    )
    from zf.runtime.artifact_read_capability import role_credential_lock

    credential = (
        role_credential_lock(runtime.state_dir, identity["actor"])
        if identity.get("actor") and identity.get("credential_generation")
        else nullcontext()
    )
    from zf.core.state.task_attempts import TaskAttemptStore

    store = TaskAttemptStore(Path(runtime.state_dir) / "task_attempts.json")
    lease = (
        store.fence_consumer(identity["attempt_id"])
        if operation and identity.get("attempt_id")
        else nullcontext()
    )
    with locked_path(Path(runtime.state_dir) / "locks/durable-mailbox"), lock, credential, lease as attempt:
        events, ops, actor, offset = snapshot(runtime, identity, attempt)
        if mutation and (
            terminal(events, actor["workflow_run_id"])
            or is_run_dispatch_paused(events, actor["workflow_run_id"])
        ):
            raise ValueError("mailbox dispatch paused or run terminal")
        token = admission_offset.set(offset)
        expiry = lease_expires.set(attempt.get("lease_expires_at", "") if attempt else "")
        try:
            yield events, ops, actor
        finally:
            lease_expires.reset(expiry)
            admission_offset.reset(token)


def roster(runtime, events, ops, run):
    """Borrow the 215 membership/operation join, using only immutable approved inputs."""
    kernel = [e for e in events if e.origin == "kernel"]
    request_ids = {run}
    for e in kernel:
        p = e.payload
        if (
            e.type in {"run.admission.admitted", "workflow.submit.accepted", "run.goal.started"}
            and (p.get("workflow_run_id") or p.get("run_id") or e.correlation_id) == run
            and p.get("request_id")
        ):
            request_ids.add(p["request_id"])
    changes = [
        e
        for e in kernel
        if e.type
        in {
            "workflow.request.approved",
            "workflow.request.rejected",
            "workflow.request.proposed",
            "workflow.request.updated",
        }
        and e.payload.get("request_id") in request_ids
    ]
    if not changes or changes[-1].type != "workflow.request.approved":
        return []
    approved = changes[-1]
    proposed = next(
        (
            e
            for e in reversed(changes[:-1])
            if e.type == "workflow.request.proposed"
            and all(e.payload.get(k) == approved.payload.get(k) for k in ("request_id", "revision"))
        ),
        None,
    )
    if not proposed:
        return []
    desc = proposed.payload
    proposal = load_workflow_proposal(runtime.state_dir, desc["proposal_ref"])
    if (
        proposal["proposal_digest"] != desc["proposal_digest"]
        or proposal["request_id"] != desc["request_id"]
        or proposal["request_revision"] != desc["revision"]
    ):
        raise ValueError("mailbox stage proposal authority changed")
    graph = hydrate_sidecar_ref(runtime.state_dir, proposal["effective_config_ref"]).payload["compiled_graph"]
    scope = {
        "run_id": run,
        "revision": desc["revision"],
        "request_id": desc["request_id"],
        "proposal_digest": desc["proposal_digest"],
    }
    source = {"compiled": graph, "scope": scope, "events": list(enumerate(kernel, 1))}
    source["stage_team_inputs"], _ = read_stage_team_inputs(runtime.state_dir, source)
    nodes = {
        n["stage_id"]: {
            "id": "workflow:stage:" + n["stage_id"],
            "data": {"stage_id": n["stage_id"], "definition": n},
        }
        for n in graph["nodes"]
        if n.get("node_id", "").startswith("stage:") and n.get("stage_id")
    }
    project_stage_teams(nodes, source, {k: o for k, o in ops.items() if o.get("workflow_run_id") == run})
    positions = {e.id: i for i, e in enumerate(kernel)}
    requested = {
        e.payload.get("operation_id"): i
        for i, e in enumerate(kernel)
        if e.type == "workflow.operation.requested"
    }
    addresses = {}
    for node in nodes.values():
        team = node["data"].get("team", {})
        if team.get("authority") != "approved":
            continue
        for m in team.get("members", []):
            if m.get("status") in {"reassigned", "cancelled", "superseded"} or m.get("disposition"):
                continue
            for task in m.get("task_ids") or [""]:
                address = member_address(
                    m, task, proposal_digest=desc["proposal_digest"], freeze_id=team.get("freeze_id", "")
                )
                key = tuple(address.items())
                active = [
                    oid
                    for oid in m["operation_ids"]
                    if ops.get(oid, {}).get("status") == "running"
                    and not ops[oid].get("admitted_call_result_ref")
                    and not ops[oid].get("call_result_admitted_event_id")
                    and (
                        m.get("binding")
                        or team.get("freeze_id")
                        or requested.get(oid, -1) > positions[approved.id]
                    )
                ]
                addresses[key] = (address, active if len(active) == 1 else [])
    return list(addresses.values())


def member_address(member, task, *, proposal_digest, freeze_id=""):
    address = {
        "mode": "obligation",
        "stage": member["stage"],
        "obligation_id": member["obligation_id"],
        "role": member["role"],
        "work_unit_id": member.get("work_unit_id") or member["obligation_id"],
        "task_id": task,
        "pipeline_stage": member.get("pipeline_stage", ""),
        "contract_revision": member.get("contract_revision", ""),
    }
    scope = {
        k: member[k]
        for k in (
            "question",
            "expected_output",
            "input_refs",
            "task_map_generation",
            "acceptance",
            "acceptance_ids",
            "admission_evidence_id",
        )
        if k in member
    }
    binding = member.get("binding") or {}
    authorization = binding.get("execution_plan_ref") or freeze_id or proposal_digest
    scope.update(authorization=authorization, address=address, binding=binding, freeze=freeze_id)
    address["version"] = canonical_json_sha256(scope)
    return address


def resolve(runtime, requested, ops, run, members):
    if not isinstance(requested, dict) or requested.get("workflow_run_id", run) != run:
        raise ValueError("invalid or cross-run mailbox address")
    mode = requested.get("mode")
    if mode == "execution" and set(requested) <= {
        "mode",
        "operation_id",
        "workflow_run_id",
        "attempt_id",
        "lease_id",
    }:
        op = ops.get(requested.get("operation_id")) or {}
        addr = {"mode": mode, **operation_binding(op)}
        if addr["workflow_run_id"] != run or any(
            addr[k] != requested[k] for k in ("attempt_id", "lease_id") if k in requested
        ):
            raise ValueError("mailbox exact target changed")
        return addr
    if mode == "coordination" and set(requested) <= {"mode", "owner", "workflow_run_id"}:
        who = requested.get("owner")
        return {"mode": mode, "owner": who, "role": owner_role(runtime.config, who)}
    if mode == "obligation" and set(requested) <= {
        "mode",
        "workflow_run_id",
        "stage",
        "obligation_id",
        "task_id",
        "work_unit_id",
        "pipeline_stage",
        "version",
        "contract_revision",
        "role",
    }:
        fields = {k: v for k, v in requested.items() if k not in {"mode", "workflow_run_id"}}
        if not fields.get("stage") or not fields.get("obligation_id"):
            raise ValueError("mailbox obligation requires a stage and stable identifier")
        matches = [a for a, _ in members if all(a.get(k) == v for k, v in fields.items())]
        if len(matches) == 1:
            return matches[0]
    raise ValueError("mailbox address is not a unique current admitted obligation")


def can_receive(addr, actor, members, message):
    if actor.get("assistance_request_id") and actor["assistance_request_id"] != message.get("question_id"):
        return False
    if addr["mode"] == "coordination":
        if message.get("question_id") and actor.get("assistance_request_id") != message["question_id"]:
            return False
        return actor["actor"] == addr["role"] and addr["owner"] in actor.get("coordination_owners", ())
    if addr["mode"] == "execution":
        return all(actor.get(k) == v for k, v in addr.items() if k != "mode")
    return any(a == addr and actor.get("operation_id") in ids for a, ids in members)


def sender_address(actor, members):
    matched = [a for a, ids in members if actor.get("operation_id") in ids]
    if len(matched) == 1:
        return matched[0]
    return {"mode": "execution", **{k: v for k, v in actor.items() if k != "coordination_owners"}}
