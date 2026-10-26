"""Immutable Workflow Proposal artifacts compiled from confirmed requests."""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from zf.core.config.loader import load_config
from zf.core.config.render import redact_config, renderable_config_to_primitive
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text
from zf.core.workflow.generic_workflow import (
    generic_workflow_catalog_projection,
)
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.workflow_proposal_projection import (
    approval_policy as _approval_policy,
    completion_profile as _completion_profile,
    estimate as _estimate,
    flow_purpose as _flow_purpose,
    rendered_flow_metadata as _rendered_flow_metadata,
    role_skill_profile_closure as _role_skill_profile_closure,
    selected_flow_spec as _selected_flow_spec,
    stage_graph as _stage_graph,
)


WORKFLOW_PROPOSAL_SCHEMA = "workflow-proposal.v1"
FLOW_SPEC_SNAPSHOT_SCHEMA = "workflow-flow-spec-snapshot.v1"
EFFECTIVE_CONFIG_SNAPSHOT_SCHEMA = "effective-config-snapshot.v1"
CONFIG_DIFF_SCHEMA = "workflow-config-diff.v1"
PREFLIGHT_SNAPSHOT_SCHEMA = "workflow-preflight-snapshot.v1"
WORKFLOW_PROPOSAL_COMPILER_VERSION = "workflow-proposal-compiler.v1"
_FLOW_KINDS = frozenset({"IssueFlow", "PrdFlow", "RefactorFlow", "Workflow"})
_WORKFLOW_PROPOSAL_FIELDS = frozenset({
    "schema_version",
    "compiler_version",
    "compiler_inputs",
    "project_identity",
    "request_id",
    "request_revision",
    "requirement_spec_ref",
    "requirement_spec_digest",
    "flow_family",
    "flow_purpose",
    "short_flow_spec_ref",
    "synthesis_result_ref",
    "synthesis_result_digest",
    "decision_rationale",
    "assumptions",
    "risk_hints",
    "open_questions",
    "run_parameters",
    "requested_closure",
    "requested_completion_profile",
    "effective_config_ref",
    "config_diff_ref",
    "private_config_candidate_ref",
    "base_config",
    "target_config",
    "change_mode",
    "stage_graph",
    "closure",
    "completion_profile",
    "estimated",
    "preflight",
    "validation_result_ref",
    "blockers",
    "approval_policy",
    "risk_class",
    "approval_status",
    "proposal_id",
    "proposal_digest",
})


class WorkflowProposalError(ValueError):
    pass


def build_workflow_proposal(
    state_dir: Path,
    *,
    request: Mapping[str, Any],
    base_config_path: Path,
    candidate_config_path: Path | None = None,
    synthesis_result_ref: Mapping[str, Any] | None = None,
    preflight: Mapping[str, Any] | None = None,
    flow_kind: str = "",
    actor: str = "zf-cli",
    writer: EventWriter | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compile and persist one immutable, non-executable proposal."""

    request_id = str(request.get("request_id") or "").strip()
    request_revision = int(request.get("revision") or 0)
    requirement_ref = str(request.get("requirement_spec_ref") or "").strip()
    requirement_digest = str(
        request.get("requirement_spec_digest") or ""
    ).strip()
    if not request_id or request_revision <= 0:
        raise WorkflowProposalError("workflow proposal requires request identity")
    if not requirement_ref or not requirement_digest:
        raise WorkflowProposalError(
            "workflow proposal requires an immutable requirement spec"
        )
    from zf.runtime.workflow_requests import (
        WorkflowRequestError,
        hydrate_workflow_requirement,
    )

    try:
        hydrate_workflow_requirement(Path(state_dir), dict(request))
    except WorkflowRequestError as exc:
        raise WorkflowProposalError(str(exc)) from exc
    current_ref = request.get("proposal_ref")
    current_digest = str(request.get("proposal_digest") or "")
    if (
        str(request.get("status") or "")
        in {"proposed", "approved", "submitted", "running"}
        and isinstance(current_ref, Mapping)
        and current_digest
    ):
        current = load_workflow_proposal(state_dir, current_ref)
        if (
            str(current.get("proposal_digest") or "") != current_digest
            or str(current.get("request_id") or "") != request_id
            or int(current.get("request_revision") or 0) != request_revision
        ):
            raise WorkflowProposalError(
                "current workflow proposal binding is invalid"
            )
        return current, dict(current_ref)
    base_path = Path(base_config_path).expanduser().resolve()
    candidate_path = Path(candidate_config_path or base_path).expanduser().resolve()
    if not base_path.is_file():
        raise WorkflowProposalError("workflow proposal config input is missing")

    synthesis, short_spec, short_spec_ref = _synthesis_input(
        state_dir,
        request=request,
        descriptor=synthesis_result_ref,
        flow_kind=flow_kind,
    )
    if str(short_spec.get("flow_family") or "") == "Workflow":
        generated_path = _materialize_generic_candidate(
            Path(state_dir),
            base_path=base_path,
            request_id=request_id,
            short_spec=short_spec,
        )
        if (
            candidate_config_path is not None
            and candidate_path != base_path
            and (
                not candidate_path.is_file()
                or candidate_path.read_bytes() != generated_path.read_bytes()
            )
        ):
            raise WorkflowProposalError(
                "explicit config candidate does not match the admitted "
                "registered template"
            )
        candidate_path = generated_path
    if not candidate_path.is_file():
        raise WorkflowProposalError("workflow proposal config input is missing")
    config = load_config(candidate_path)
    rendered = redact_config(renderable_config_to_primitive(config))
    source_docs = _flow_documents(candidate_path)
    if not short_spec_ref:
        short_spec = {
            "schema_version": FLOW_SPEC_SNAPSHOT_SCHEMA,
            "request_id": request_id,
            "request_revision": request_revision,
            "flow_kind": str(flow_kind or request.get("kind") or ""),
            "documents": source_docs,
        }
        short_spec_ref = write_immutable_json_sidecar(
            state_dir,
            short_spec,
            root=f"workflow/proposals/{_safe_component(request_id)}/flow-specs",
            kind="workflow_flow_spec",
            schema_version=FLOW_SPEC_SNAPSHOT_SCHEMA,
            created_by="workflow-proposal-compiler",
        )
    base_digest = _file_digest(base_path)
    candidate_digest = _file_digest(candidate_path)
    effective_snapshot = {
        "schema_version": EFFECTIVE_CONFIG_SNAPSHOT_SCHEMA,
        "compiler_version": WORKFLOW_PROPOSAL_COMPILER_VERSION,
        "request_id": request_id,
        "request_revision": request_revision,
        "source_config": {
            "path": str(candidate_path),
            "sha256": candidate_digest,
        },
        "config": rendered,
        "yaml": yaml.safe_dump_all(
            [redact_obj(redact_config(doc)) for doc in yaml.safe_load_all(candidate_path.read_text(encoding="utf-8"))],
            sort_keys=False, allow_unicode=True,
        ),
    }
    effective_ref = write_immutable_json_sidecar(
        state_dir,
        effective_snapshot,
        root=f"workflow/proposals/{_safe_component(request_id)}/effective-configs",
        kind="effective_config_snapshot",
        schema_version=EFFECTIVE_CONFIG_SNAPSHOT_SCHEMA,
        created_by="workflow-proposal-compiler",
    )
    private_candidate_ref = _write_private_candidate(
        state_dir,
        request_id=request_id,
        digest=candidate_digest,
        content=candidate_path.read_text(encoding="utf-8"),
    )
    diff_body = {
        "schema_version": CONFIG_DIFF_SCHEMA,
        "request_id": request_id,
        "base_config_sha256": base_digest,
        "target_config_sha256": candidate_digest,
        "changed": base_digest != candidate_digest,
        "unified_diff": _redacted_config_diff(base_path, candidate_path),
    }
    diff_ref = write_immutable_json_sidecar(
        state_dir,
        diff_body,
        root=f"workflow/proposals/{_safe_component(request_id)}/config-diffs",
        kind="workflow_config_diff",
        schema_version=CONFIG_DIFF_SCHEMA,
        created_by="workflow-proposal-compiler",
    )
    preflight_body = _stable_preflight(preflight or {})
    validation_result_ref = write_immutable_json_sidecar(
        state_dir,
        preflight_body,
        root=f"workflow/proposals/{_safe_component(request_id)}/preflights",
        kind="workflow_preflight_snapshot",
        schema_version=PREFLIGHT_SNAPSHOT_SCHEMA,
        created_by="workflow-proposal-compiler",
    )
    graph = _stage_graph(rendered)
    closure = _role_skill_profile_closure(rendered)
    completion_profile = _completion_profile(rendered, preflight_body)
    blockers = [
        *_blocking_diagnostics(request, preflight_body),
        *_synthesis_compatibility_blockers(
            synthesis=synthesis,
            short_spec=short_spec,
            source_docs=source_docs,
            preflight=preflight_body,
            closure=closure,
            completion_profile=completion_profile,
        ),
    ]
    selected_family = str(
        short_spec.get("flow_family")
        or flow_kind
        or request.get("kind")
        or ""
    )
    selected_purpose = str(
        short_spec.get("purpose")
        or _flow_purpose(flow_kind or str(request.get("kind") or ""))
    )
    compiler_inputs = {
        "controller_catalog_digest": stable_json_digest(
            {"flow_documents": source_docs}
        ),
        "closure_digest": stable_json_digest(closure),
        "profile_catalog_digest": stable_json_digest(
            {
                "execution_profiles": closure.get(
                    "execution_profiles",
                    {},
                )
            }
        ),
        "generic_workflow_catalog_digest": stable_json_digest(
            generic_workflow_catalog_projection()
        ),
        "generic_workflow_contract_digest": str(
            _rendered_flow_metadata(rendered).get(
                "generic_workflow_contract_digest"
            )
            or ""
        ),
    }
    stable_body = {
        "schema_version": WORKFLOW_PROPOSAL_SCHEMA,
        "compiler_version": WORKFLOW_PROPOSAL_COMPILER_VERSION,
        "compiler_inputs": compiler_inputs,
        "project_identity": {
            "name": str(
                (
                    rendered.get("project")
                    if isinstance(rendered.get("project"), Mapping)
                    else {}
                ).get("name")
                or ""
            ),
            "root": str(base_path.parent),
            "config_ref": str(base_path),
        },
        "request_id": request_id,
        "request_revision": request_revision,
        "requirement_spec_ref": requirement_ref,
        "requirement_spec_digest": requirement_digest,
        "flow_family": selected_family,
        "flow_purpose": selected_purpose,
        "short_flow_spec_ref": short_spec_ref,
        "synthesis_result_ref": (
            dict(synthesis_result_ref)
            if synthesis_result_ref is not None
            else {}
        ),
        "synthesis_result_digest": str(
            (synthesis_result_ref or {}).get("sha256") or ""
        ),
        "decision_rationale": str(
            synthesis.get("decision_rationale") or ""
        ),
        "assumptions": list(synthesis.get("assumptions") or []),
        "risk_hints": list(synthesis.get("risk_hints") or []),
        "open_questions": list(
            synthesis.get("open_questions")
            or request.get("open_questions")
            or []
        ),
        "run_parameters": dict(short_spec.get("parameters") or {}),
        "requested_closure": {
            "roles": list(synthesis.get("requested_roles") or []),
            "skills": list(synthesis.get("requested_skills") or []),
            "profiles": list(synthesis.get("requested_profiles") or []),
        },
        "requested_completion_profile": dict(
            synthesis.get("completion_profile") or {}
        ),
        "effective_config_ref": effective_ref,
        "config_diff_ref": diff_ref,
        "private_config_candidate_ref": private_candidate_ref,
        "base_config": {"path": str(base_path), "sha256": base_digest},
        "target_config": {
            "source_path": str(candidate_path),
            "sha256": candidate_digest,
        },
        "change_mode": (
            "config_change" if base_digest != candidate_digest
            else "run_parameters_only"
        ),
        "stage_graph": graph,
        "closure": closure,
        "completion_profile": completion_profile,
        "estimated": _estimate(rendered, source_docs, selected_family),
        "preflight": preflight_body,
        "validation_result_ref": validation_result_ref,
        "blockers": blockers,
        "approval_policy": _approval_policy(rendered),
        "risk_class": (
            "topology_or_config_change"
            if base_digest != candidate_digest
            else "parameter_only"
        ),
        "approval_status": "blocked" if blockers else "approvable",
    }
    proposal_digest = stable_json_digest(stable_body)
    proposal = {
        **stable_body,
        "proposal_id": f"workflow-proposal:{proposal_digest[:24]}",
        "proposal_digest": proposal_digest,
    }
    descriptor = write_immutable_json_sidecar(
        state_dir,
        proposal,
        root=f"workflow/proposals/{_safe_component(request_id)}/proposals",
        kind="workflow_proposal",
        schema_version=WORKFLOW_PROPOSAL_SCHEMA,
        created_by="workflow-proposal-compiler",
    )
    if not blockers:
        from zf.runtime.workflow_requests import bind_workflow_proposal

        bind_workflow_proposal(
            state_dir,
            request_id=request_id,
            request_revision=request_revision,
            proposal_ref=descriptor,
            proposal_digest=proposal_digest,
            actor=actor,
            writer=writer,
        )
    _cleanup_generated_candidate(candidate_path, base_path=base_path)
    return proposal, descriptor


def load_workflow_proposal(
    state_dir: Path,
    descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    hydrated = hydrate_sidecar_ref(Path(state_dir), dict(descriptor)).payload
    if not isinstance(hydrated, Mapping):
        raise WorkflowProposalError("workflow proposal body is invalid")
    proposal = dict(hydrated)
    _validate_workflow_proposal_schema(proposal)
    expected = str(proposal.get("proposal_digest") or "")
    body = {
        key: value
        for key, value in proposal.items()
        if key not in {"proposal_id", "proposal_digest"}
    }
    if not expected or stable_json_digest(body) != expected:
        raise WorkflowProposalError("workflow proposal digest mismatch")
    if proposal["proposal_id"] != f"workflow-proposal:{expected[:24]}":
        raise WorkflowProposalError("workflow proposal id does not match its digest")
    return proposal


def _validate_workflow_proposal_schema(proposal: Mapping[str, Any]) -> None:
    if proposal.get("schema_version") != WORKFLOW_PROPOSAL_SCHEMA:
        raise WorkflowProposalError("workflow proposal schema is unsupported")
    missing = sorted(_WORKFLOW_PROPOSAL_FIELDS - set(proposal))
    if missing:
        raise WorkflowProposalError(
            "workflow proposal missing required fields: " + ", ".join(missing)
        )
    unknown = sorted(set(proposal) - _WORKFLOW_PROPOSAL_FIELDS)
    if unknown:
        raise WorkflowProposalError(
            "workflow proposal has unknown fields: " + ", ".join(unknown)
        )
    if proposal.get("compiler_version") != WORKFLOW_PROPOSAL_COMPILER_VERSION:
        raise WorkflowProposalError("workflow proposal compiler is unsupported")
    if not str(proposal.get("request_id") or "").strip():
        raise WorkflowProposalError("workflow proposal request id is missing")
    if int(proposal.get("request_revision") or 0) <= 0:
        raise WorkflowProposalError("workflow proposal request revision is invalid")
    for field in (
        "short_flow_spec_ref",
        "effective_config_ref",
        "config_diff_ref",
        "validation_result_ref",
    ):
        if not isinstance(proposal.get(field), Mapping):
            raise WorkflowProposalError(
                f"workflow proposal {field} must be an artifact ref"
            )
    if proposal.get("change_mode") not in {
        "run_parameters_only",
        "config_change",
    }:
        raise WorkflowProposalError("workflow proposal change mode is invalid")
    if proposal.get("approval_status") not in {"approvable", "blocked"}:
        raise WorkflowProposalError("workflow proposal approval status is invalid")
    blockers = proposal.get("blockers")
    if not isinstance(blockers, list):
        raise WorkflowProposalError("workflow proposal blockers must be a list")
    if bool(blockers) != (proposal.get("approval_status") == "blocked"):
        raise WorkflowProposalError(
            "workflow proposal blockers and approval status disagree"
        )


def stable_json_digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _stable_preflight(value: Any) -> Any:
    """Remove observation timestamps that cannot affect an approval decision."""

    if isinstance(value, Mapping):
        return {
            str(key): _stable_preflight(item)
            for key, item in value.items()
            if str(key) not in {"created_at", "generated_at", "updated_at"}
        }
    if isinstance(value, list):
        return [_stable_preflight(item) for item in value]
    return value


def _flow_documents(path: Path) -> list[dict[str, Any]]:
    try:
        values = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        raise WorkflowProposalError(f"workflow FlowSpec cannot be read: {exc}") from exc
    return [
        dict(value)
        for value in values
        if isinstance(value, Mapping) and str(value.get("kind") or "") in _FLOW_KINDS
    ]


def _write_private_candidate(
    state_dir: Path,
    *,
    request_id: str,
    digest: str,
    content: str,
) -> str:
    path = (
        Path(state_dir)
        / "private"
        / "workflow-config-candidates"
        / _safe_component(request_id)
        / f"{digest}.yaml"
    )
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise WorkflowProposalError("private config candidate digest collision")
    if not path.exists():
        atomic_write_text(path, content)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return str(path)


def _redacted_config_diff(base_path: Path, candidate_path: Path) -> str:
    base = yaml.safe_dump(
        redact_config(renderable_config_to_primitive(load_config(base_path))),
        sort_keys=False,
        allow_unicode=True,
    ).splitlines()
    target = yaml.safe_dump(
        redact_config(renderable_config_to_primitive(load_config(candidate_path))),
        sort_keys=False,
        allow_unicode=True,
    ).splitlines()
    return "\n".join(
        difflib.unified_diff(
            base,
            target,
            fromfile="zf.yaml@base",
            tofile="zf.yaml@proposal",
            lineterm="",
        )
    )


def _blocking_diagnostics(
    request: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if str(request.get("status") or "") not in {"draft", "ready", "proposed"}:
        blockers.append({
            "kind": "workflow_request_not_ready",
            "severity": "STOP",
            "message": "workflow request must be confirmed and ready",
        })
    if request.get("open_questions"):
        blockers.append({
            "kind": "workflow_request_open_questions",
            "severity": "STOP",
            "message": "workflow request still has open questions",
        })
    for item in preflight.get("blockers", []):
        if isinstance(item, Mapping) and str(item.get("severity") or "").upper() == "STOP":
            blockers.append(dict(item))
    return blockers


def _synthesis_input(
    state_dir: Path,
    *,
    request: Mapping[str, Any],
    descriptor: Mapping[str, Any] | None,
    flow_kind: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if descriptor is None:
        return {}, {}, {}
    from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref
    from zf.runtime.workflow_synthesis import WORKFLOW_SYNTHESIS_RESULT_SCHEMA

    current_ref = request.get("synthesis_ref")
    if not isinstance(current_ref, Mapping) or not _same_descriptor(
        current_ref,
        descriptor,
    ):
        raise WorkflowProposalError(
            "workflow synthesis result is not current for this request"
        )
    try:
        payload = hydrate_sidecar_ref(
            Path(state_dir),
            dict(descriptor),
        ).payload
    except SidecarRefError as exc:
        raise WorkflowProposalError(
            f"workflow synthesis result cannot be verified: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise WorkflowProposalError(
            "workflow synthesis result body must be an object"
        )
    synthesis = dict(payload)
    if (
        synthesis.get("schema_version") != WORKFLOW_SYNTHESIS_RESULT_SCHEMA
        or str(synthesis.get("request_id") or "")
        != str(request.get("request_id") or "")
        or int(synthesis.get("request_revision") or 0)
        != int(request.get("revision") or 0)
        or str(synthesis.get("requirement_digest") or "")
        != str(request.get("requirement_spec_digest") or "")
    ):
        raise WorkflowProposalError(
            "workflow synthesis result does not match the current requirement"
        )
    short_ref = synthesis.get("short_flow_spec_ref")
    if not isinstance(short_ref, Mapping):
        raise WorkflowProposalError(
            "workflow synthesis result has no short FlowSpec ref"
        )
    expected_short_digest = str(
        synthesis.get("short_flow_spec_digest") or ""
    )
    if (
        not expected_short_digest
        or str(short_ref.get("sha256") or "") != expected_short_digest
    ):
        raise WorkflowProposalError(
            "workflow synthesis short FlowSpec binding is invalid"
        )
    try:
        short_payload = hydrate_sidecar_ref(
            Path(state_dir),
            dict(short_ref),
        ).payload
    except SidecarRefError as exc:
        raise WorkflowProposalError(
            f"workflow synthesis short FlowSpec cannot be verified: {exc}"
        ) from exc
    if not isinstance(short_payload, Mapping):
        raise WorkflowProposalError(
            "workflow synthesis short FlowSpec must be an object"
        )
    short_spec = dict(short_payload)
    if (
        short_spec.get("schema_version") != "workflow-short-flow-spec.v1"
        or str(short_spec.get("request_id") or "")
        != str(request.get("request_id") or "")
        or int(short_spec.get("request_revision") or 0)
        != int(request.get("revision") or 0)
    ):
        raise WorkflowProposalError(
            "workflow synthesis short FlowSpec is stale"
        )
    family = str(short_spec.get("flow_family") or "")
    expected_family = {
        "issue": "IssueFlow",
        "prd": "PrdFlow",
        "feat": "PrdFlow",
        "refactor": "RefactorFlow",
        "workflow": "Workflow",
    }.get(str(flow_kind or request.get("kind") or "").lower(), "")
    if (
        family != str(synthesis.get("selected_flow_family") or "")
        or (expected_family and family != expected_family)
    ):
        raise WorkflowProposalError(
            "workflow synthesis Flow family does not match the selected route"
        )
    return synthesis, short_spec, dict(short_ref)


def _same_descriptor(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    return (
        str(left.get("ref") or "") == str(right.get("ref") or "")
        and str(left.get("sha256") or "") == str(right.get("sha256") or "")
    )


def _synthesis_compatibility_blockers(
    *,
    synthesis: Mapping[str, Any],
    short_spec: Mapping[str, Any],
    source_docs: list[dict[str, Any]],
    preflight: Mapping[str, Any],
    closure: Mapping[str, Any],
    completion_profile: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not synthesis:
        return []
    mismatches: list[str] = []
    parameters = (
        short_spec.get("parameters")
        if isinstance(short_spec.get("parameters"), Mapping)
        else {}
    )
    family = str(short_spec.get("flow_family") or "")
    source_spec = _selected_flow_spec(source_docs, family)
    requested_lanes = int(parameters.get("lanes") or 0)
    actual_lanes = int(source_spec.get("lanes", source_spec.get("laneCount", source_spec.get("lane_count", 0))) or 0)
    if requested_lanes and requested_lanes != actual_lanes:
        mismatches.append(
            f"lanes requested {requested_lanes}, compiled {actual_lanes or 'none'}"
        )
    delivery_contract = (
        preflight.get("delivery_contract")
        if isinstance(preflight.get("delivery_contract"), Mapping)
        else {}
    )
    requested_strictness = str(parameters.get("strictness") or "")
    actual_strictness = str(delivery_contract.get("strictness") or "")
    if (
        requested_strictness
        and requested_strictness != actual_strictness
    ):
        mismatches.append(
            "strictness requested "
            f"{requested_strictness!r}, compiled {actual_strictness!r}"
        )
    requested_pattern = str(parameters.get("pattern_id") or "")
    actual_pattern = str(preflight.get("resolved_pattern_id") or "")
    if requested_pattern and requested_pattern != actual_pattern:
        mismatches.append(
            f"pattern_id requested {requested_pattern!r}, "
            f"compiled {actual_pattern!r}"
        )

    roles = {
        str(item.get("name") or item.get("instance_id") or "")
        for item in closure.get("roles", [])
        if isinstance(item, Mapping)
    }
    skills = {
        str(skill)
        for item in closure.get("roles", [])
        if isinstance(item, Mapping)
        for skill in item.get("skills", [])
        if str(skill)
    }
    profiles = set(
        str(item)
        for item in (
            closure.get("execution_profiles", {})
            if isinstance(
                closure.get("execution_profiles"),
                Mapping,
            )
            else {}
        )
    )
    for label, requested, available in (
        ("role", synthesis.get("requested_roles"), roles),
        ("skill", synthesis.get("requested_skills"), skills),
        ("profile", synthesis.get("requested_profiles"), profiles),
    ):
        missing = sorted(
            set(str(item) for item in requested or []) - available
        )
        if missing:
            mismatches.append(
                f"{label} closure missing {', '.join(missing)}"
            )

    requested_completion = (
        synthesis.get("completion_profile")
        if isinstance(synthesis.get("completion_profile"), Mapping)
        else {}
    )
    completion_aliases = {
        "id": "id",
        "delivery_policy": "delivery_policy",
        "completion_threshold": "completion_threshold",
        "required_artifacts": "required_delivery_artifacts",
    }
    for requested_key, compiled_key in completion_aliases.items():
        requested_value = requested_completion.get(requested_key)
        if requested_value in (None, "", []):
            continue
        compiled_value = completion_profile.get(compiled_key)
        if requested_key == "required_artifacts":
            compiled_value = [
                str(item.get("source_ref") or "")
                for item in compiled_value or []
                if isinstance(item, Mapping)
            ]
        if requested_value != compiled_value:
            mismatches.append(
                f"{requested_key} requested {requested_value!r}, "
                f"compiled {compiled_value!r}"
            )
    if not mismatches:
        return []
    return [{
        "kind": "workflow_synthesis_compile_mismatch",
        "severity": "STOP",
        "title": "Synthesis result is not represented by compiled config",
        "message": "; ".join(mismatches),
        "why_it_matters": (
            "A Proposal cannot silently ignore Agent-selected parameters or "
            "capability requirements."
        ),
        "fix_it": (
            "Revise the Requirement/FlowSpec or submit an explicit "
            "config-changing candidate for deterministic compilation."
        ),
        "safe_auto_fix": False,
    }]


def materialize_synthesized_workflow_candidate(
    state_dir: Path,
    *,
    request: Mapping[str, Any],
    base_config_path: Path,
    synthesis_result_ref: Mapping[str, Any] | None,
    flow_kind: str,
) -> Path:
    """Return the exact config input represented by admitted synthesis."""

    _synthesis, short_spec, _short_ref = _synthesis_input(
        Path(state_dir),
        request=request,
        descriptor=synthesis_result_ref,
        flow_kind=flow_kind,
    )
    base_path = Path(base_config_path).expanduser().resolve()
    if str(short_spec.get("flow_family") or "") != "Workflow":
        from zf.core.config.loader import ConfigError
        from zf.runtime.workflow_controller_candidate import materialize_controller_candidate

        try:
            return materialize_controller_candidate(
                base_path=base_path, request_id=str(request.get("request_id") or ""),
                short_spec=short_spec,
            )
        except (OSError, ValueError, ConfigError, yaml.YAMLError) as exc:
            raise WorkflowProposalError(str(exc)) from exc
    return _materialize_generic_candidate(
        Path(state_dir),
        base_path=base_path,
        request_id=str(request.get("request_id") or ""),
        short_spec=short_spec,
    )


def _materialize_generic_candidate(
    state_dir: Path,
    *,
    base_path: Path,
    request_id: str,
    short_spec: Mapping[str, Any],
) -> Path:
    generic_spec = short_spec.get("generic_workflow_spec")
    if not isinstance(generic_spec, Mapping):
        raise WorkflowProposalError(
            "admitted Generic Workflow short spec has no expanded template"
        )
    try:
        documents = list(
            yaml.safe_load_all(base_path.read_text(encoding="utf-8"))
        )
    except (OSError, yaml.YAMLError) as exc:
        raise WorkflowProposalError(
            f"base config cannot be read for Generic Workflow: {exc}"
        ) from exc
    workflows = [
        item
        for item in documents
        if isinstance(item, Mapping)
        and str(item.get("kind") or "") == "Workflow"
    ]
    generated_doc = {
        "apiVersion": "zaofu.dev/v1",
        "kind": "Workflow",
        "metadata": {
            "name": f"generated-{_safe_component(request_id)}",
        },
        "spec": dict(generic_spec),
    }
    if workflows:
        if len(workflows) != 1 or dict(workflows[0].get("spec") or {}) != dict(
            generic_spec
        ):
            raise WorkflowProposalError(
                "base config contains a different Generic Workflow; explicit "
                "operator reconciliation is required"
            )
        return base_path
    generated_yaml = yaml.safe_dump(
        generated_doc,
        sort_keys=False,
        allow_unicode=True,
    )
    content = (
        base_path.read_text(encoding="utf-8").rstrip()
        + "\n---\n"
        + generated_yaml
    )
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    path = (
        base_path.parent
        / (
            f".{base_path.name}.workflow-candidate-"
            f"{_safe_component(request_id)}-{digest[:16]}.tmp"
        )
    )
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise WorkflowProposalError(
            "generated Generic Workflow candidate digest collision"
        )
    if not path.exists():
        atomic_write_text(path, content)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return path


def cleanup_synthesized_workflow_candidate(
    path: Path,
    *,
    base_config_path: Path,
) -> None:
    _cleanup_generated_candidate(
        Path(path),
        base_path=Path(base_config_path).expanduser().resolve(),
    )


def _cleanup_generated_candidate(path: Path, *, base_path: Path) -> None:
    if path == base_path:
        return
    if (
        path.parent == base_path.parent
        and path.name.startswith(f".{base_path.name}.workflow-candidate-")
        and path.suffix == ".tmp"
    ):
        path.unlink(missing_ok=True)


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_component(value: str) -> str:
    return "".join(
        ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value)
    ).strip("-.") or "item"


__all__ = [
    "CONFIG_DIFF_SCHEMA",
    "EFFECTIVE_CONFIG_SNAPSHOT_SCHEMA",
    "FLOW_SPEC_SNAPSHOT_SCHEMA",
    "WORKFLOW_PROPOSAL_COMPILER_VERSION",
    "WORKFLOW_PROPOSAL_SCHEMA",
    "WorkflowProposalError",
    "build_workflow_proposal",
    "cleanup_synthesized_workflow_candidate",
    "load_workflow_proposal",
    "materialize_synthesized_workflow_candidate",
    "stable_json_digest",
]
