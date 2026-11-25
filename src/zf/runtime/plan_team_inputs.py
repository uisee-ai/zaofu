"""Bind the approved Plan member's sources to its own 212 attempt and workdir."""
import hashlib
import re
from collections.abc import Collection
from pathlib import Path
from urllib.parse import unquote

from zf.runtime.artifact_access import artifact_access_allowed
from zf.runtime.artifact_navigation import _headings
from zf.runtime.artifact_read_ledger import ArtifactReadError, materialize_attempt_source_ref
from zf.runtime.call_result_envelope import write_immutable_json_sidecar


def _unavailable(obligation: str, ref: str, reason: str) -> ArtifactReadError:
    return ArtifactReadError(f"Plan obligation {obligation}: {ref} {reason}; correct the source binding before retry")


def _input_id(ref: str) -> str:
    return "plan-input-" + hashlib.sha256(ref.encode()).hexdigest()[:16]


def _retry_inputs(runtime, charter, refs, payload, predecessor):
    from zf.runtime.artifact_input_index import load_attempt_source_manifest
    from zf.runtime.call_result_envelope import canonical_json_sha256
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.workflow_operation import load_workflow_operation

    previous = load_workflow_operation(runtime.event_log, predecessor)
    obligation = charter["obligation_id"]
    if not previous or previous.get("operation_type") != "fanout_reader_child":
        raise _unavailable(obligation, predecessor, "has no admitted predecessor")
    request = hydrate_sidecar_ref(runtime.state_dir, previous["request_ref"]).payload["request"]
    keys = ("workflow_run_id", "stage_id", "fanout_id", "child_id", "role_instance")
    if any(request.get(k) != payload.get(k) for k in keys):
        raise _unavailable(obligation, predecessor, "belongs to another assignment")
    try:
        prior = load_attempt_source_manifest(runtime.state_dir, request["active_attempt_id"])
    except ValueError as exc:
        raise _unavailable(obligation, predecessor, "lost its unique input manifest") from exc
    selected = {s["source_id"]: s for s in prior["sources"] if s["source_id"].startswith("plan-input-")}
    if (canonical_json_sha256(prior) != request["source_manifest_digest"]
            or set(selected) != {_input_id(ref) for ref in refs}):
        raise _unavailable(obligation, predecessor, "does not bind the same inputs")
    sources = []
    for ref in refs:
        saved = selected[_input_id(ref)]
        pinned = materialize_attempt_source_ref(state_dir=runtime.state_dir, project_root=runtime.project_root,
                                                ref=saved["ref"], source_id=saved["source_id"])
        if not pinned or pinned["sha256"] != saved["sha256"]:
            raise _unavailable(obligation, ref, "lost its pinned snapshot")
        sources.append({key: value for key, value in saved.items() if key != "occurrence_id"})
    return sources


def _admitted_requirement(runtime, trigger: dict, run: str, obligation: str, ref: str) -> dict:
    from zf.runtime.workflow_requests import hydrate_workflow_requirement, workflow_requirement_path

    keys = ("request_id", "request_revision", "requirement_spec_ref", "requirement_spec_digest")
    admission = next((
        event for event in runtime.event_log.query(type="workflow.invoke.accepted")
        if event.origin == "kernel"
        and run and event.payload.get("workflow_run_id") == run == trigger.get("workflow_run_id")
        and all(trigger.get(key) and event.payload.get(key) == trigger[key] for key in keys)
    ), None)
    if not admission:
        raise _unavailable(obligation, ref, "does not match the admitted Workflow requirement")
    binding = admission.payload
    try:
        revision = int(binding["request_revision"])
        state = runtime.state_dir.resolve()
        expected = workflow_requirement_path(state, binding["request_id"], revision, binding["requirement_spec_digest"])
        path = state / binding["requirement_spec_ref"]
        if path != expected or path.resolve() != expected:
            raise ValueError("noncanonical Workflow requirement path")
        hydrate_workflow_requirement(state, {**binding, "revision": revision})
    except (OSError, TypeError, ValueError) as exc:
        raise _unavailable(obligation, ref, str(exc)) from exc
    return binding


def _project_file(path: Path, root: Path, state: Path, obligation: str, ref: str) -> Path:
    if not path.is_relative_to(root) or path.is_relative_to(state) or path.is_relative_to(root / ".git"):
        raise _unavailable(obligation, ref, "is missing or outside the approved project scope")
    if not path.is_file():
        raise _unavailable(obligation, ref, "must name an existing regular file; select a scoped file or index")
    return path


def _authorize_sources(descriptors, matches, path, root, state, role, obligation, ref):
    # Match aliases to the same filesystem object, including existing
    # hardlinks. Changing the spelling of a restricted ref cannot widen it.
    for source in descriptors:
        named = source.get("ref") or source.get("path")
        if source in matches or not isinstance(named, str) or not named:
            continue
        location = Path(named)
        if named.startswith(".zf/") or (named.startswith("artifacts/") and (state / named).exists()):
            location = state / named.removeprefix(".zf/")
        else:
            location = root / location
        location = location.resolve()
        if ((location.is_relative_to(root) or location.is_relative_to(state))
                and location.is_file() and location.samefile(path)):
            matches.append(source)
    for source in matches:
        if (not artifact_access_allowed(source.get("access_scope"), actor=role,
                                        role=role, purpose="workflow-read")
                or (source.get("allowed_paths") and "$" not in source["allowed_paths"])):
            raise _unavailable(obligation, ref, "is not authorized for this member")


def _markdown_section(body: bytes, fragment: str, obligation: str, ref: str) -> str:
    try:
        content = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _unavailable(obligation, ref, "does not have UTF-8 Markdown sections") from exc
    headings, complete = _headings(content, entry_limit=None)
    anchor = unquote(fragment).strip()
    matches = [h for h in headings if h["title"] == anchor or
               re.sub(r"[^\w\s-]", "", h["title"].casefold()).replace(" ", "-") == anchor.casefold()]
    if not complete or not anchor or len(matches) != 1:
        raise _unavailable(obligation, ref, "has a missing or ambiguous section")
    start, stop = matches[0]["page_start"], matches[0]["section_end"]
    return content[start:stop]


def validate_plan_project_sources(
    plan: dict, root: Path, state: Path, requirement: dict, *, frozen_file_refs: Collection[str] = (),
) -> list[str]:
    """Inspect existing project files without materializing attempts or resolving future results."""
    if not plan.get("members"):
        return []
    observed = set()
    root, state = root.resolve(), state.resolve()
    descriptors = [s for s in requirement.get("artifact_refs", []) if isinstance(s, dict)]
    for member in plan["members"]:
        role = member["coverage_owner"] if member.get("disposition") else member["role"]
        for ref in member["input_refs"]:
            try:
                if _check_project_ref(ref, role, member["obligation_id"], root, state, descriptors,
                                      previously_file=ref in frozen_file_refs):
                    observed.add(ref)
            except (ValueError, OSError, RuntimeError) as exc:
                raise ValueError(f"stage_teams.plan {role} input_refs: {exc}") from exc
    return sorted(observed)


def _check_project_ref(ref: str, role: str, obligation: str, root: Path, state: Path,
                       descriptors: list[dict], *, previously_file: bool) -> bool:
    name, sep, fragment = ref.partition("#")
    if name == "requirement":
        return False  # Only the later Run admission can resolve this label.
    labels = [s for s in descriptors if name in (s.get("source_id"), s.get("artifact_id"))]
    if labels:
        bindings = {(s.get("ref") or s.get("path"), s.get("sha256")) for s in labels}
        if len(bindings) != 1:
            raise _unavailable(obligation, ref, "identifies several distinct authorized inputs")
        name = labels[0].get("ref") or labels[0].get("path")
        if not isinstance(name, str) or not name:
            return False
    if Path(name).is_absolute() or ".." in Path(name).parts:
        raise _unavailable(obligation, ref, "is not a bound project input")
    matches = [s for s in descriptors if (s.get("ref") or s.get("path")) == name]
    scoped = name.removeprefix(".zf/")
    if matches and scoped.startswith("artifacts/"):
        candidate = (state / scoped).resolve()
        if candidate.is_relative_to(state) and candidate.is_file():
            return False  # WRC must resolve the admitted state artifact and bind its own permission checks.
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise _unavailable(obligation, ref, "resolves outside the project")
    if path.is_relative_to(state) or path.is_relative_to(root / ".git"):
        raise _unavailable(obligation, ref, "is outside the approved project scope")
    if not previously_file and not matches and not name.endswith("/"):
        if not path.exists() and not (root / name).is_symlink():
            return False  # A future source has no project object to inspect yet.
    _project_file(path, root, state, obligation, ref)
    _authorize_sources(descriptors, matches, path, root, state, role, obligation, ref)
    if sep:
        _markdown_section(path.read_bytes(), fragment, obligation, ref)
    return True


def _slice(runtime, charter: dict, ref: str, trigger: dict, run: str) -> dict:
    original, sep, fragment = ref.partition("#")
    obligation = charter["obligation_id"]
    descriptors = [s for field in ("artifact_refs", "input_refs") for s in trigger.get(field) or []
                   if isinstance(s, dict)]
    labeled = [s for s in descriptors if original in (s.get("source_id"), s.get("artifact_id"))]
    if len(labeled) > 1 and len({(s.get("ref") or s.get("path"), s.get("sha256")) for s in labeled}) > 1:
        raise _unavailable(obligation, ref, "identifies several distinct authorized inputs")
    bound_requirement = {}
    if original == "requirement" and trigger.get("requirement_spec_digest") and trigger.get("requirement_spec_ref"):
        bound_requirement = _admitted_requirement(runtime, trigger, run, obligation, ref)
        original = bound_requirement["requirement_spec_ref"]
        if labeled and (labeled[0].get("ref") or labeled[0].get("path")) != original:
            raise _unavailable(obligation, ref, "conflicts with the admitted Workflow requirement")
        descriptors.extend(s for field in ("artifact_refs", "input_refs")
                           for s in bound_requirement.get(field) or [] if isinstance(s, dict))
    elif labeled:
        original = labeled[0].get("ref") or labeled[0].get("path") or ""
    elif original == "requirement":
        original = next((trigger[k] for k in ("requirement_spec_ref", "requirement_ref", "prd_ref", "source_ref")
                         if trigger.get(k)), "")
    if not original or (Path(original).is_absolute() and not bound_requirement) or ".." in Path(original).parts:
        raise _unavailable(obligation, ref, "is not a bound project input")
    root = runtime.project_root.resolve()
    state = runtime.state_dir.resolve()
    matches = [s for s in descriptors if (s.get("ref") or s.get("path")) == original]
    # Only a bound requirement or an explicitly admitted artifact can name a
    # source inside the state directory. Never turn an agent path into authority.
    scoped_ref = original.removeprefix(".zf/")
    candidate = (state / scoped_ref).resolve()
    is_bound_artifact = bool(matches or (ref.partition("#")[0] == "requirement" and
                             trigger.get("requirement_spec_ref") == original))
    if bound_requirement:
        path = state / original
    elif (is_bound_artifact and scoped_ref.startswith("artifacts/") and candidate.is_relative_to(state)
          and candidate.is_file()):
        path = candidate
    else:
        path = _project_file((root / original).resolve(), root, state, obligation, ref)
    _authorize_sources(descriptors, matches, path, root, state, charter["role"], obligation, ref)
    key = _input_id(ref)
    digests = {s["sha256"] for s in matches if s.get("sha256")}
    if bound_requirement:
        digests.add(bound_requirement["requirement_spec_digest"])
    if not sep:
        source = materialize_attempt_source_ref(state_dir=state, project_root=root, ref=str(path), source_id=key)
        if not source or (digests and digests != {source["sha256"]}):
            raise _unavailable(obligation, ref, "changed during materialization")
        return source
    body = path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    if digests and digests != {digest}:
        raise _unavailable(obligation, ref, "has changed since its source was bound")
    text = _markdown_section(body, fragment, obligation, ref)
    section = write_immutable_json_sidecar(state, {"source": ref, "document_sha256": digest,
        "text": text}, root="plan-team/sections", kind="plan_section", schema_version="plan-section.v1",
        created_by="kernel")
    return {**section, "artifact_id": ref, "source_id": key, "allowed_paths": ["$.text"]}


def bind_plan_team_inputs(runtime, child, stage_id: str, run: str, *, predecessor: str = "") -> None:
    payload = child.payload
    charter = payload.get("stage_obligation")
    if not charter or stage_id not in {"prd-scan", "prd-plan"}:
        return
    trigger = payload.get("trigger_payload") or {}
    reports = trigger.get("stage_member_results") or []
    author = stage_id == "prd-plan"
    assigned = list(charter["input_refs"])
    if author:
        assigned.extend(ref for m in payload.get("transferred_obligations") or [] for ref in m["input_refs"])
    expected = {r["obligation_id"] for r in reports} if author else set()
    refs = [ref for ref in dict.fromkeys(assigned) if ref not in expected]
    sources = (_retry_inputs(runtime, charter, refs, payload, predecessor) if predecessor else
               [_slice(runtime, charter, ref, trigger, run) for ref in refs])
    extension = payload.get("plan_extension_evidence") if not author else None
    if (author and reports) or extension:
        events = runtime.event_log.read_all()
        scan = payload.get("fanout_id") if extension else trigger.get("fanout_id")
        if extension:
            evidence = payload.get("plan_extension_evidence_event_id")
            if not evidence or not any(e.type == "fanout.plan.member.admitted" and e.origin == "kernel"
                    and e.payload.get("workflow_run_id") == run and e.payload.get("fanout_id") == scan
                    and e.payload.get("child_id") == child.child_id and e.payload.get("role_instance") == charter["role"]
                    and e.payload.get("evidence_event_id") == evidence for e in events):
                raise _unavailable(charter["obligation_id"], "extension evidence", "lacks the bound membership decision")
            reports = [extension]
        # The handoff only names kernel-admitted receipts. Consume the report
        # on that exact terminal child; a provider-supplied filename or a bare
        # event id cannot grant permission to read an unrelated artifact.
        admitted = {e.payload.get("result_event_id"): e for e in events
                    if e.type == "fanout.child.completed" and e.origin == "kernel"
                    and (e.payload.get("workflow_run_id") or e.correlation_id) == run
                    and e.payload.get("fanout_id") == scan}
        for row in reports:
            e = admitted.get(row["result_event_id"])
            if (not e or e.payload.get("child_id") != row["child_id"] or e.payload.get("run_id") != row["attempt"]
                    or e.payload.get("role_instance") != row["role_instance"]
                    or (not extension and row["recipient"] != charter["role"])
                    or (extension and e.id != evidence)
                    or not isinstance(e.payload.get("report"), dict) or not e.payload["report"]):
                raise _unavailable(charter["obligation_id"], row["obligation_id"], "lacks its admitted report")
            report = write_immutable_json_sidecar(runtime.state_dir, e.payload["report"], root="plan-team/reports",
                kind="plan_member_report", schema_version="plan-member-report.v1", created_by="kernel",
                source_event_id=e.id)
            sources.append({**report, "source_id": "plan-report-" + row["obligation_id"],
                            "artifact_id": row["obligation_id"] + ".json", "allowed_paths": ["$"]})
    payload["input_refs"] = sources
    payload["required_reads"] = [{"source_id": s["source_id"], "artifact_id": s["artifact_id"],
        "artifact_sha256": s["sha256"], "json_path": selector, "min_returned_bytes": 1}
        for s in sources for selector in s.get("allowed_paths", ["$"])]
    payload["durable_operation"] = True
    # The opt-in roster declares its inputs mandatory for each member. The
    # historical profile still retains its configured result protocol.
    payload["result_protocol_mode"] = "blocking"
