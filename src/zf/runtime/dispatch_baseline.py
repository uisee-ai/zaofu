"""Qualify the first actual delivery in a Run against the inspected release."""

from __future__ import annotations

import os
import re
from dataclasses import replace
from pathlib import Path

from zf.core.security.hash import sha256_file
from zf.core.skills.provenance import build_skill_lock_entries
from zf.runtime.load_receipt import PROCESS_EPOCH, effective_config_sha256
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash


def briefing_delivery_evidence(runtime, prepared, role: str, path: Path) -> dict:
    """Capture the finalized card before transport; only mark it on a successful send."""
    material = getattr(runtime, "_skill_materialization_events", {}).get(role)
    fields = {"role_instance": role, "runtime_epoch": PROCESS_EPOCH, "kernel_pid": os.getpid()}
    if material and material[0] == prepared.context.task_id:
        fields["skill_materialization_event_id"] = material[1]
    try:
        fields["briefing_digest"] = sha256_file(path)
    except OSError:
        pass  # Legacy transports may not have a local card; qualification stays unknown.
    return fields


def _body(state, ref, schema):
    if not isinstance(ref, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(ref.get("sha256", ""))):
        raise ValueError(f"missing {schema} receipt")
    result = hydrate_sidecar_ref(state, ref)
    if not result.ok or not isinstance(result.payload, dict) or result.payload.get("schema_version") != schema:
        raise ValueError(f"invalid {schema} receipt")
    return result.payload


def _required(condition, reason):
    if not condition:
        raise ValueError(reason)


def _loaded_consumers(receipt, *, worker):
    from zf.runtime.load_receipt import consumer_receipts

    if worker:
        from zf.cli.artifact import _run_attempt_read
        from zf.cli.artifact_input_index import run_input_index
        from zf.cli.artifact_input_recovery import run_input_recovery
        from zf.runtime.artifact_read_capability import authorize_artifact_read_from_environment
        from zf.runtime.worker_load_receipt import record_worker_cli_load

        common = (authorize_artifact_read_from_environment, record_worker_cli_load)
        alternatives = [(fn, *common) for fn in (_run_attempt_read, run_input_index, run_input_recovery)]
    else:
        from zf.runtime.orchestrator import Orchestrator
        from zf.runtime.run_manager import run_manager_tick
        from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler

        alternatives = [(Orchestrator.run_once, run_manager_tick, TaskPipelineReconciler.reconcile)]
    observed = receipt.get("loaded_consumers", {})
    if not isinstance(observed, dict) or not all(isinstance(row, dict) for row in observed.values()):
        return False
    for functions in alternatives:
        expected = consumer_receipts(functions)
        if expected.keys() == observed.keys() and all(
            observed[name].get("portable_code_sha256") == row["portable_code_sha256"]
            for name, row in expected.items()
        ):
            return True
    return False


def _skills(project, state, cfg, events, sent, request, pinned):
    role = next((r for r in cfg.roles if r.instance_id == sent.payload["role_instance"]), None)
    _required(role is not None, "first worker role is not configured")
    declared = request.get("skills", role.skills)
    _required(
        isinstance(declared, list) and all(isinstance(name, str) for name in declared),
        "invalid WRC skill inventory",
    )
    entries = build_skill_lock_entries(
        project_root=project, state_dir=state, config=cfg, role=replace(role, skills=declared)
    )
    material_id = sent.payload.get("skill_materialization_event_id")
    if not entries and not material_id and not pinned:
        return {}
    event = next((e for e in events if e.id == material_id and e.type == "skills.materialized"), None)
    _required(
        event is not None
        and event.actor == "zf-cli"
        and event.task_id == sent.task_id
        and event.payload.get("instance_id") == role.instance_id,
        "materialization is absent or belongs to another delivery",
    )
    p = event.payload
    body = _body(
        state,
        {"ref": p.get("skill_dispatch_treatment_ref"), "sha256": p.get("skill_dispatch_treatment_digest")},
        "skill-dispatch-treatment.v1",
    )
    _required(
        body.get("role_instance") == role.instance_id and body.get("task_id") == sent.task_id,
        "skill receipt identity mismatch",
    )
    rows = body.get("materialized_skills", [])
    _required(isinstance(rows, list) and all(isinstance(r, dict) for r in rows), "invalid skill inventory")
    actual = {
        r["skill_name"]: r.get("digest")
        for r in rows
        if r.get("materialized_to") and r.get("status") == "resolved"
    }
    expected = {entry.name: pinned.get(entry.name, entry.sha256) for entry in entries}
    _required(set(pinned) <= set(expected), "skill pin is absent from this worker")
    _required(
        len(actual) == len(rows)
        and actual == expected
        and all(re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in actual.values()),
        "materialized skills differ from the expected sources",
    )
    return actual


def first_dispatch_baseline(
    project: Path,
    state: Path,
    config,
    events,
    *,
    run_id: str,
    revision: str,
    source_digest: str,
    pins: dict[str, str],
) -> dict:
    """Join a kernel boot, first accepted transport, signed WRC and authenticated CLI.

    The release digest and any externally approved skill pins come from the caller.
    Missing historical evidence never licenses selecting a later, easier worker.
    """
    rows = [e for e in events if e.origin == "kernel"]
    report = {"run_id": run_id, "expected_revision": revision, "status": "unproven"}
    try:
        index = next(
            (
                i
                for i, e in enumerate(rows)
                if e.type in {"task.attempt.sent", "fanout.child.dispatched"}
                and e.payload.get("workflow_run_id") == run_id
            ),
            None,
        )
        _required(index is not None, "no first transport delivery for this run")
        sent = rows[index]
        p = sent.payload
        report["dispatch_event"] = sent.id
        _required(
            sent.type == "task.attempt.sent" and sent.actor == "orchestrator",
            "first worker lacks an attested transport attempt",
        )
        role = p.get("role_instance")
        card = p.get("briefing_digest", "")
        _required(role and re.fullmatch(r"[0-9a-f]{64}", card), "first dispatch lacks an attested briefing")
        report.update(role=role, briefing_sha256=card, operation_id=p.get("operation_id"))
        before = rows[:index]
        boot_i = next(
            (
                i
                for i in range(index - 1, -1, -1)
                if rows[i].type in {"session.started", "loop.started", "loop.stopped"}
            ),
            None,
        )
        _required(
            boot_i is not None
            and rows[boot_i].type in {"session.started", "loop.started"}
            and rows[boot_i].actor == "zf-cli",
            "no active boot before first dispatch",
        )
        boot = rows[boot_i]
        load = _body(state, boot.payload.get("runtime_load_ref"), "runtime-load.v1")
        epoch = p.get("runtime_epoch")
        pid = p.get("kernel_pid")
        _required(
            epoch
            and pid
            and load.get("runtime_epoch") == boot.payload.get("runtime_epoch") == epoch
            and load.get("pid") == boot.payload.get("pid") == pid,
            "first delivery belongs to another controller process",
        )
        _required(
            re.fullmatch(r"[0-9a-f]{40}", revision)
            and load.get("source_revision") == revision
            and load.get("source_tree_sha256") == source_digest,
            "boot does not match the inspected release",
        )
        _required(
            load.get("config_file_sha256") == sha256_file(project / "zf.yaml")
            and load.get("effective_config_sha256")
            == boot.payload.get("effective_config_sha256")
            == effective_config_sha256(config)
            and _loaded_consumers(load, worker=False),
            "boot configuration or loaded consumers unproven",
        )
        report["boot_event"] = boot.id
        issued = next(
            (
                e
                for e in reversed(before)
                if e.type == "workflow.operation.requested"
                and e.payload.get("operation_id") == p.get("operation_id")
            ),
            None,
        )
        _required(
            issued is not None and issued.actor == "zf-cli", "no kernel-issued work request for first dispatch"
        )
        req = issued.payload
        request = _body(state, req.get("request_ref"), "workflow-operation-request.v1")
        _required(operation_request_hash(request) == req.get("request_hash"), "work request hash mismatch")
        _required(
            all(
                request.get(k) == req.get(k) == value and value
                for k, value in {
                    "role_instance": role,
                    "workflow_run_id": run_id,
                    "task_id": sent.task_id,
                    "operation_id": p.get("operation_id"),
                    "active_attempt_id": p.get("dispatch_id"),
                }.items()
            ),
            "work request belongs to another dispatch",
        )
        report["request_event"] = issued.id
        _required(isinstance(request.get("request"), dict), "malformed WRC request")
        report["skills"] = _skills(project, state, config, before[boot_i + 1 :], sent, request["request"], pins)
        next_boot = next(
            (
                i
                for i in range(index + 1, len(rows))
                if rows[i].type in {"session.started", "loop.stopped"}
                or (
                    rows[i].type == "loop.started"
                    and (rows[i].payload.get("runtime_epoch") != epoch or rows[i].payload.get("pid") != pid)
                )
            ),
            len(rows),
        )
        identity = {
            "actor": role,
            "operation_id": p["operation_id"],
            "workflow_run_id": run_id,
            "dispatch_id": p["dispatch_id"],
            "attempt_id": req["active_attempt_id"],
            "task_id": sent.task_id,
        }
        observed = next(
            (
                e
                for e in rows[boot_i + 1 : next_boot]
                if e.type == "worker.runtime.observed"
                and e.actor == role
                and all(e.payload.get(k) == v for k, v in identity.items())
            ),
            None,
        )
        _required(observed is not None, "worker CLI receipt pending or unavailable for this dispatch")
        worker = _body(state, observed.payload.get("worker_load_ref"), "worker-load.v1")
        _required(
            worker.get("scope") == "authenticated_worker_cli"
            and worker.get("source_revision") == revision
            and isinstance(worker.get("identity"), dict)
            and _loaded_consumers(worker, worker=True)
            and all(worker["identity"].get(k) == v for k, v in identity.items()),
            "worker CLI release or identity mismatch",
        )
        manifest = request["request"].get("source_manifest_digest")
        _required(
            manifest and worker["identity"].get("manifest_digest") == manifest,
            "worker source manifest mismatch",
        )
        report.update(status="verified", worker_event=observed.id)
    except (KeyError, TypeError, OSError, ValueError) as exc:
        report["reason"] = str(exc)
    return report
