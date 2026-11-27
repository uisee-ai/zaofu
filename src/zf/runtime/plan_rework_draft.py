"""Issue editable copies of the admitted author candidate explicitly returned for rework."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import PurePosixPath

from zf.runtime.artifact_access import require_artifact_access
from zf.runtime.call_result_envelope import canonical_json_sha256, write_immutable_json_sidecar
from zf.runtime.plan_artifact_ports import PLAN_DRAFT_PORTS
from zf.runtime.plan_candidate_ports import read_plan_port_bytes
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, safe_sidecar_ref, sidecar_path
from zf.runtime.workflow_operation import load_workflow_operation, operation_request_hash


KEY = "editable_rework_candidate"
SCHEMA = "plan-rework-edit-grant.v1"


def _one(rows, message):
    rows = list(rows)
    if len(rows) != 1:
        raise ValueError(message)
    return rows[0]


def _kernel(event, kind, run):
    return (
        event
        and event.type == kind
        and event.origin == "kernel"
        and event.actor == "zf-cli"
        and (event.payload.get("workflow_run_id") or event.correlation_id) == run
    )


def _grant(state, events, scope, request, manifest, operation_id):
    run, stage = request["workflow_run_id"], request["stage_id"]
    by_id = {e.id: e for e in events}
    start = by_id.get(manifest.get("source_event_id"))
    trigger = by_id.get(start.causation_id) if start else None
    if (
        request.get("source_manifest_digest") != canonical_json_sha256(manifest)
        or request.get("active_attempt_id") != manifest.get("attempt_id")
        or manifest.get("workflow_run_id") != run
    ):
        raise ValueError("recipient request and manifest differ")
    if (
        not _kernel(start, "fanout.started", run)
        or start.payload.get("fanout_id") != request.get("fanout_id")
        or start.payload.get("stage_id") != stage
        or not trigger
        or start.payload.get("trigger_event_id") != trigger.id
    ):
        raise ValueError("recipient is not bound to its kernel fanout")
    _one(
        (
            c
            for c in start.payload.get("expected_children", [])
            if c.get("child_id") == request["child_id"] and c.get("role_instance") == request["role_instance"]
        ),
        "unbound recipient",
    )
    if (
        not scope.get("plan_artifact_package_digest")
        or manifest.get("plan_artifact_package_digest") != scope["plan_artifact_package_digest"]
        or manifest.get("task_map_generation") != scope.get("task_map_generation")
    ):
        raise ValueError("no matching formal Plan comparison baseline")
    from zf.runtime.plan_rework_signed_rejection import signed_rejection_source

    failure = by_id.get(scope.get("rework_of"))
    if not failure:
        raise ValueError("missing bound rework failure")
    source = (
        _preflight_source
        if failure.payload.get("failure_class") == "plan_candidate_preflight"
        else signed_rejection_source
    )
    old_start, synth, failure, previous, provenance = source(
        state, events, scope=scope, start=start, trigger=trigger, request=request
    )
    admission, control_ref, control = _admitted_producer(
        state, events[: events.index(synth)], old_start, previous, run, stage
    )
    files, markdown = _editable_files(state, previous, manifest, control)
    return {
        "schema_version": SCHEMA,
        "workflow_run_id": run,
        "operation_id": operation_id,
        "attempt_id": request["active_attempt_id"],
        "role_instance": request["role_instance"],
        "source_manifest_digest": request["source_manifest_digest"],
        "stage_replan_generation": scope.get("stage_replan_generation"),
        "authority_package_digest": scope["plan_artifact_package_digest"],
        "failure_event_id": failure.id,
        "producer_admission_event_id": admission.id,
        "control_sha256": control_ref["sha256"],
        **provenance,
        "files": files,
        **markdown,
    }


def _preflight_source(state, events, *, scope, start, trigger, request):
    run, stage = request["workflow_run_id"], request["stage_id"]
    by_id = {event.id: event for event in events}
    failure = by_id.get(scope.get("rework_of"))
    synth = by_id.get(failure.causation_id) if failure else None
    old = by_id.get(synth.causation_id) if synth else None
    if (
        trigger.origin != "kernel"
        or not failure
        or trigger.causation_id != failure.id
        or not old
        or not _kernel(synth, "fanout.synth.completed", run)
        or not _kernel(failure, (start.payload.get("aggregate") or {}).get("failure_event"), run)
        or trigger.payload.get("workflow_run_id") != run
        or trigger.payload.get("stage_id") != stage
    ):
        raise ValueError("no direct kernel preflight rework for this Plan author")
    if any(
        trigger.payload.get(k) != scope.get(k) or failure.payload.get(k) != scope.get(k)
        for k in ("stage_replan_generation", "task_map_generation", "plan_artifact_package_digest")
    ):
        raise ValueError("rework generation or formal Plan baseline differs")
    if not (
        events.index(old)
        < events.index(synth)
        < events.index(failure)
        < events.index(trigger)
        < events.index(start)
    ):
        raise ValueError("candidate recovery events are not in causal order")
    prev = scope.get("previous_plan_candidate_refs")
    if (
        not isinstance(prev, list)
        or not prev
        or any(e.payload.get("previous_plan_candidate_refs") != prev for e in (trigger, failure, synth))
    ):
        raise ValueError("rework candidate source list differs")
    fid = failure.payload.get("fanout_id")
    if not fid or any(
        e.payload.get("fanout_id") != fid
        or e.payload.get("stage_id") != stage
        or e.payload.get("failure_class") != "plan_candidate_preflight"
        or e.payload.get("status") != "failed"
        for e in (failure, synth)
    ):
        raise ValueError("missing rejected candidate preflight")
    ref = failure.payload.get("plan_candidate_preflight_ref") or {}
    if (
        ref != synth.payload.get("plan_candidate_preflight_ref")
        or ref.get("kind") != "plan_candidate_preflight"
        or ref.get("source_event_id") != old.id
        or not ref.get("sha256")
    ):
        raise ValueError("preflight ref or original trigger differs")
    check = hydrate_sidecar_ref(state, ref).payload
    if (
        check.get("schema_version") != "plan-candidate-preflight.v1"
        or check.get("status") != "failed"
        or check.get("fanout_id") != fid
        or check.get("stage_id") != stage
    ):
        raise ValueError("preflight body does not bind the failed fanout")
    prior = events[: events.index(synth)]
    old_start = _one(
        (
            e
            for e in prior
            if _kernel(e, "fanout.started", run)
            and e.payload.get("fanout_id") == fid
            and e.causation_id == old.id
            and e.payload.get("stage_id") == stage
        ),
        "unbound producer fanout",
    )
    if (
        old.origin != "kernel"
        or old_start.payload.get("trigger_event_id") != old.id
        or old.payload.get("workflow_run_id") != run
        or any(
            old.payload.get(k) != scope.get(k)
            for k in ("stage_replan_generation", "task_map_generation", "plan_artifact_package_digest")
        )
    ):
        raise ValueError("producer stage generation differs")
    return old_start, synth, failure, prev, {"preflight_sha256": ref["sha256"]}


def _admitted_producer(state, prior, old_start, prev, run, stage):
    fid = old_start.payload["fanout_id"]
    candidates = [
        e
        for e in prior
        if _kernel(e, "fanout.child.completed", run)
        and e.payload.get("fanout_id") == fid
        and e.payload.get("stage_id") == stage
        and any(
            r.get("logical_name") == "task_map"
            for r in (e.payload.get("report") or {}).get("plan_ports", [])
            if isinstance(r, dict)
        )
    ]
    child = _one(candidates, "expected one admitted producer with a declared task_map port")
    c = child.payload
    admission = _one(
        (
            e
            for e in prior
            if _kernel(e, "workflow.call.result.admitted", run)
            and e.payload.get("admission_status") == "admitted"
            and c.get("result_event_id")
            and e.payload.get("source_event_id") == c["result_event_id"] == child.causation_id
            and all(
                c.get(k) and c[k] == e.payload.get(k)
                for k in ("operation_id", "attempt_id", "request_hash", "control_result_ref")
            )
        ),
        "missing exact prior author result admission",
    )
    registration = _one(
        (
            e
            for e in prior
            if _kernel(e, "workflow.operation.requested", run)
            and e.causation_id == old_start.id
            and e.payload.get("operation_id") == c["operation_id"]
            and e.payload.get("active_attempt_id") == c["attempt_id"]
            and e.payload.get("request_hash") == c["request_hash"]
        ),
        "prior author operation lacks its issuing fanout",
    )
    issued = hydrate_sidecar_ref(state, registration.payload["request_ref"]).payload
    if (
        operation_request_hash(issued) != c["request_hash"]
        or issued.get("operation_id") != c["operation_id"]
        or issued.get("active_attempt_id") != c["attempt_id"]
        or any(issued["request"].get(k) != c[k] for k in ("fanout_id", "stage_id", "child_id"))
    ):
        raise ValueError("prior author request and admitted control differ")
    control_ref = c["control_result_ref"]
    _one(
        (
            r
            for r in prev
            if r.get("kind") == "call_control_result"
            and all(r.get(k) == control_ref.get(k) for k in ("ref", "sha256"))
        ),
        "control result is not a rework input",
    )
    control = hydrate_sidecar_ref(state, control_ref).payload
    if (
        control.get("workflow_run_id") != run
        or any(
            c.get(k) != control.get(k) or not c.get(k)
            for k in ("fanout_id", "child_id", "stage_id", "operation_id", "attempt_id", "request_hash")
        )
        or c["report"].get("plan_ports") != control.get("plan_ports")
    ):
        raise ValueError("producer and immutable control port list differ")
    return admission, control_ref, control


def _editable_files(state, prev, manifest, control):
    from zf.runtime.plan_rework_input_overlap import partition_repeated_candidate_bodies

    # Reproduce the kernel's exact read selections from the original full grants.
    # A partition label on a path-limited source cannot grant an editable copy.
    rebound = [{**row, "source_id": f"previous-plan-candidate-{index}"} for index, row in enumerate(prev, 1)]
    projected = {row["source_id"]: row for row in partition_repeated_candidate_bodies(state, rebound)}
    root = PurePosixPath("artifacts/fanouts", control["fanout_id"], control["child_id"])
    files = {}

    def add(name, source_path, sha):
        if not sha or name in files:
            raise ValueError("missing digest or duplicate editable port")
        relative = PurePosixPath(source_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("candidate file escapes its producer")
        path = str(safe_sidecar_ref(root / relative))
        original = _one(
            (
                r
                for r in prev
                if r.get("kind") == "fanout_child_artifact"
                and r.get("ref") == path
                and r.get("sha256") == sha
            ),
            "candidate port not uniquely granted",
        )
        delivered = _one(
            (
                r
                for r in manifest["sources"]
                if r.get("source_id", "").startswith("previous-plan-candidate-")
                and r.get("ref") == path
                and r.get("sha256") == sha
            ),
            "candidate port is not in the recipient manifest",
        )
        expected = projected.get(delivered["source_id"], {})
        if any(
            delivered.get(key) != expected.get(key) for key in ("kind", "ref", "sha256", "allowed_paths")
        ) or (delivered.get("access_scope") or {}) != (expected.get("access_scope") or {}):
            raise ValueError("candidate edit source differs from its kernel read projection")
        acl = original.get("access_scope") or {}
        effective = dict(delivered.get("access_scope") or {})
        if delivered.get("kind") == "plan_rework_candidate_partition":
            effective.pop("read_equivalents", None)
        elif delivered.get("kind") != "fanout_child_artifact" or delivered.get("allowed_paths") != ["$"]:
            raise ValueError("candidate has no whole-file edit authority")
        if original.get("allowed_paths") != ["$"] or acl != effective:
            raise ValueError("candidate whole-file grant or ACL differs")
        if hashlib.sha256(read_plan_port_bytes(sidecar_path(state, path), name)).hexdigest() != sha:
            raise ValueError(f"candidate bytes differ for {name}")
        files[name] = {"ref": path, "sha256": sha, "access_scope": acl, "source_id": delivered["source_id"]}

    for port in control["plan_ports"]:
        name = port.get("logical_name")
        if name in PLAN_DRAFT_PORTS:
            add(name, port["ref"], port["sha256"])
    if "task_map" not in files:
        raise ValueError("candidate task_map is missing")
    markdown = {}
    if control.get("plan_md") or control.get("plan_artifact_ref"):
        if not isinstance(control.get("plan_md"), str) or not control.get("plan_artifact_ref"):
            raise ValueError("Plan Markdown lacks a bound source")
        source_path = control["plan_artifact_ref"]
        physical = _one(
            (
                r
                for r in prev
                if r.get("kind") == "fanout_child_artifact" and r.get("ref") == str(root / source_path)
            ),
            "missing or ambiguous Plan Markdown",
        )
        add("plan.md", source_path, physical["sha256"])
        raw = read_plan_port_bytes(sidecar_path(state, physical["ref"]), "plan.md")
        inline = control["plan_md"].encode()
        if raw != inline and raw != inline + b"\n" and raw + b"\n" != inline:
            raise ValueError("candidate Markdown differs from the admitted inline Plan body")
        markdown = {
            "plan_inline_sha256": hashlib.sha256(inline).hexdigest(),
            "plan_file_sha256": physical["sha256"],
            "single_trailing_lf_difference": raw != inline,
        }
    return files, markdown


def bind_authoring_context(runtime, payload, request, manifest, previous, operation_id):
    payload.pop(KEY, None)
    ctx = payload.get("plan_candidate_validation")
    if not isinstance(ctx, Mapping):
        return
    request["plan_candidate_validation"] = dict(ctx)
    scope = (ctx.get("manifest") or {}).get("trigger_payload") or {}
    role = request.get("role_instance")
    planner = role == "planner" or any(
        r.name == "planner" and r.instance_id == role
        for r in getattr(getattr(runtime, "config", None), "roles", [])
    )
    if (
        not planner
        or not scope.get("previous_plan_candidate_refs")
        or request.get("operation_type") != "fanout_reader_child"
    ):
        return
    if previous is not None:
        grant = previous.get(KEY)
    else:
        try:
            body = _grant(
                runtime.state_dir, runtime.event_log.read_all(), scope, request, manifest, operation_id
            )
            grant = write_immutable_json_sidecar(
                runtime.state_dir,
                body,
                root="plan-synth/edit-grants",
                kind="plan_rework_edit_grant",
                schema_version=SCHEMA,
                created_by="plan-draft-authority",
                source_event_id=body["producer_admission_event_id"],
            )
        except (ValueError, OSError, KeyError, TypeError) as exc:
            grant = {"unavailable": str(exc)}
    if grant:
        request[KEY] = payload[KEY] = grant


def editable_candidate_files(state, log, manifest, identity, names):
    if identity.get("role") != "planner" or not identity.get("operation_id"):
        raise ValueError("a current Planner operation is required")
    op = load_workflow_operation(log, identity["operation_id"]) or {}
    if not op.get("request_ref"):
        raise ValueError("no issued author request")
    envelope = hydrate_sidecar_ref(state, op["request_ref"]).payload
    request = envelope["request"]
    if (
        operation_request_hash(envelope) != op.get("request_hash")
        or envelope["operation_id"] != identity["operation_id"]
        or envelope.get("active_attempt_id") != manifest.get("attempt_id")
        or request["source_manifest_digest"] != canonical_json_sha256(manifest)
    ):
        raise ValueError("current operation or source manifest differs")
    grant = request.get(KEY) or {}
    if not grant.get("ref"):
        raise ValueError(
            "no complete editable rework candidate grant: " + str(grant.get("unavailable") or "not issued")
        )
    body = hydrate_sidecar_ref(state, grant).payload
    if (
        body.get("schema_version") != SCHEMA
        or body.get("operation_id") != identity["operation_id"]
        or body.get("attempt_id") != manifest["attempt_id"]
        or body.get("workflow_run_id") != manifest["workflow_run_id"]
        or body.get("role_instance") != identity["actor"]
        or body.get("source_manifest_digest") != request["source_manifest_digest"]
        or body.get("authority_package_digest") != manifest.get("plan_artifact_package_digest")
    ):
        raise ValueError("editable candidate grant identity mismatch")
    files = {}
    selected = list(dict.fromkeys(names))
    if "plan.md" in body["files"]:
        selected.append("plan.md")
    for name in selected:
        if name not in body["files"] or name not in {*PLAN_DRAFT_PORTS, "plan.md"}:
            raise ValueError(f"no authorized editable candidate port: {name}")
        desc = body["files"][name]
        require_artifact_access(
            desc["access_scope"], actor=identity["actor"], role=identity["role"], purpose=identity["purpose"]
        )
        raw = read_plan_port_bytes(sidecar_path(state, desc["ref"]), name)
        if hashlib.sha256(raw).hexdigest() != desc["sha256"]:
            raise ValueError(f"editable candidate bytes differ: {name}")
        files[name if name == "plan.md" else name + ".json"] = raw
    return files, {
        k: body[k]
        for k in (
            "authority_package_digest",
            "producer_admission_event_id",
            "control_sha256",
            "preflight_sha256",
            "critic_admission_event_id",
            "critic_control_sha256",
            "generation_request_event_id",
            "generation_approval_event_id",
            "plan_inline_sha256",
            "plan_file_sha256",
            "single_trailing_lf_difference",
        )
        if k in body
    }
