"""Registered, digest-bound controller assembly for reviewed config proposals.

The agent selects a catalog entry and project checks. Code binds project facts;
neither discovery nor assembly applies config, installs skills, or starts a Run.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

import yaml

from zf.core.config.loader import (
    _config_env_map,
    _expand_env_vars,
    _load_profile_source_documents,
    _profile_source_refs_from_documents,
    load_config,
)
from zf.core.config.render import renderable_config_to_primitive
from zf.core.profile.flows import _catalog_entries, flow_path
from zf.runtime.execution_profiles import execution_profile_catalog


_FAMILIES = {"IssueFlow": "issueRef", "PrdFlow": "prdRef", "RefactorFlow": "objectiveRef"}
CONTROLLER_PARAMETERS = frozenset({"controller_id", "controller_digest", "verification_commands"})


def _documents(path: Path) -> list[dict[str, Any]]:
    env = _config_env_map(path)
    documents = list(yaml.load_all(_expand_env_vars(path.read_text(), env), Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)))
    profiles, _ = _load_profile_source_documents(
        path, _profile_source_refs_from_documents(documents), env=env,
    )
    return [d for d in [*profiles, *documents] if isinstance(d, dict)]


def _digest(documents: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(documents, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def controller_catalog() -> dict[str, dict[str, Any]]:
    """Expose only explicitly opted-in, loadable controller packages."""
    result = {}
    for entry in _catalog_entries():
        path = entry.path
        # Avoid reparsing/expanding non-assembly examples on the HTTP enqueue path.
        if entry.assembly != "project-bound-v1":
            continue
        documents = _documents(path)
        flows = [d for d in documents if d.get("kind") in _FAMILIES]
        if len(flows) != 1:
            raise ValueError("registered controller requires exactly one Flow")
        config = load_config(path)
        flow = flows[0]
        from zf.runtime.workflow_proposal_projection import completion_profile
        completion = completion_profile({}, {"effective_flow_metadata": config.workflow.flow_metadata})
        result[entry.id] = {
            "id": entry.id, "digest": _digest(documents),
            "label": entry.label, "description": entry.description,
            "flow_family": flow["kind"], "version": flow.get("metadata", {}).get("version", ""),
            "lanes": flow["spec"].get("lanes"),
            "task_pipeline": flow["spec"].get("taskPipeline", {}),
            "roles": sorted({str(r.instance_id or r.name) for r in config.roles}),
            "skills": sorted({str(s) for r in config.roles for s in r.skills}),
            "profiles": sorted(execution_profile_catalog(config)),
            "completion_profile": {
                "id": completion["id"], "delivery_policy": completion["delivery_policy"],
                "completion_threshold": completion["completion_threshold"],
            },
            "assembly": "Replaces the controller configuration after explicit config approval. "
                        "Preserves project/session/local skill sources, snapshots only missing controller skills; "
                        "binds Requirement paths, current Git "
                        "branch and project verification_commands. Keeps controller gates and limits.",
        }
    return result


def selected_controller(
    parameters: Mapping[str, Any], *, family: str, catalog: Mapping[str, Any],
) -> dict[str, Any] | None:
    """No capability expansion without an exact registered package selection."""
    if not (set(parameters) & CONTROLLER_PARAMETERS):
        return None
    identity = parameters.get("controller_id")
    entry = catalog.get(identity) if isinstance(identity, str) else None
    if not entry or parameters.get("controller_digest") != entry["digest"]:
        raise ValueError("registered controller identity/digest is missing, unknown or stale")
    if family != entry["flow_family"]:
        raise ValueError("registered controller Flow family mismatch")
    if "task_pipeline" in parameters or parameters.get("lanes", entry["lanes"]) != entry["lanes"]:
        raise ValueError("registered controller selection preserves its lanes and pipeline; use a separate config revision")
    commands = parameters.get("verification_commands")
    if (not isinstance(commands, list) or not 1 <= len(commands) <= 16
            or any(not isinstance(c, str) or not c.strip() or len(c) > 4096 or "\x00" in c for c in commands)):
        raise ValueError("registered controller requires 1..16 project verification_commands")
    return dict(entry)


def admit_capabilities(
    parameters: Mapping[str, Any], family: str, catalog: Mapping[str, Any],
    roles: list[str], skills: list[str], profiles: list[str],
) -> None:
    allowed = selected_controller(parameters, family=family, catalog=catalog.get("registered_controllers", {})) or catalog
    for label, requested in (("role", roles), ("skill", skills), ("profile", profiles)):
        unknown = sorted(set(requested) - set(allowed[label + "s"]))
        if unknown:
            raise ValueError(f"workflow synthesis requested unknown {label}(s): " + ", ".join(unknown))


def bound_controller_candidate(
    state_dir: Path, request: Mapping[str, Any], base_path: Path, candidate_path: Path,
    synthesis_result_ref: Mapping[str, Any] | None, flow_kind: str,
    *, preview_review_ref: Mapping[str, Any] | None = None,
) -> Path:
    from zf.runtime.workflow_proposal import (
        WorkflowProposalError, cleanup_synthesized_workflow_candidate, materialize_synthesized_workflow_candidate,
    )
    generated = materialize_synthesized_workflow_candidate(
        state_dir, request=request, base_config_path=base_path,
        synthesis_result_ref=synthesis_result_ref, flow_kind=flow_kind,
    )
    if preview_review_ref is not None:
        from zf.runtime.workflow_review_controller import bound_reviewed_controller_candidate
        try:
            return bound_reviewed_controller_candidate(
                state_dir, request, base_path, candidate_path, synthesis_result_ref, preview_review_ref,
            )
        finally:
            cleanup_synthesized_workflow_candidate(generated, base_config_path=base_path)
    if candidate_path != base_path and candidate_path.read_bytes() != generated.read_bytes():
        cleanup_synthesized_workflow_candidate(generated, base_config_path=base_path)
        raise WorkflowProposalError("config candidate does not match the admitted registered controller")
    return generated


def _project_path(root: Path, value: Any, *, field: str, file: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"registered controller requires Requirement {field}")
    path = Path(raw)
    path = path if path.is_absolute() else root / path
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"controller {field} must stay inside the project") from exc
    if file and not path.is_file():
        raise ValueError(f"controller {field} must be an existing project file")
    return str(relative)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)
    if result.returncode or not result.stdout.strip():
        raise ValueError("controller assembly requires a committed project on a named Git branch")
    return result.stdout.strip()


def assemble_controller(
    *, base_path: Path, short_spec: Mapping[str, Any], requirement: Mapping[str, Any],
) -> list[dict[str, Any]]:
    parameters = short_spec["parameters"]
    entry = selected_controller(parameters, family=short_spec["flow_family"], catalog=controller_catalog())
    if entry is None:
        raise ValueError("registered controller selection is required")
    path = flow_path(entry["id"])
    if path is None:
        raise ValueError("registered controller is no longer available")
    documents = copy.deepcopy(_documents(path))
    if _digest(documents) != entry["digest"]:
        raise ValueError("registered controller changed during assembly")
    root = base_path.parent
    from zf.runtime.workflow_git_readiness import require_git_baseline
    require_git_baseline(root)
    from zf.runtime.workflow_controller_skills import SOURCE_NAME, controller_skill_source
    base_config = load_config(base_path)
    package = controller_skill_source(root=root, base=base_config, template=load_config(path))
    base = renderable_config_to_primitive(base_config)
    flow = next(d for d in documents if d.get("kind") == entry["flow_family"])
    config_docs = [d for d in documents if d.get("kind") == "ZfConfig"]
    if len(config_docs) != 1:
        raise ValueError("registered controller requires exactly one ZfConfig")
    spec = config_docs[0]["spec"]
    # Freeze the profile closure in the candidate so private validation and
    # later apply cannot depend on a checkout or mutable profile file.
    spec.pop("profile_sources", None)
    spec["project"] = copy.deepcopy(base["project"])
    spec["session"] = copy.deepcopy(base["session"])
    sources = copy.deepcopy(base.get("skill_sources") or [])
    sources = [s for s in sources if s["name"] != SOURCE_NAME]
    if package:
        sources.append(package)
    for document in documents:
        body = document.get("spec") or {}
        if "skill_sources" in body:
            body["skill_sources"] = copy.deepcopy(sources)
    spec["skill_sources"] = sources
    flow["spec"][_FAMILIES[entry["flow_family"]]] = _project_path(
        root, requirement.get("source_ref"), field="source_ref", file=True,
    )
    flow["spec"]["targetRoot"] = _project_path(root, requirement.get("target_root"), field="target_root")
    if entry["flow_family"] == "RefactorFlow":
        flow["spec"]["sourceRoot"] = _project_path(root, requirement.get("source_root"), field="source_root")
    branch = _git(root, "symbolic-ref", "--short", "HEAD")
    _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    git = spec.setdefault("runtime", {}).setdefault("git", {})
    git.update(candidate_base_ref=branch, ship_target_branch=branch)
    provision = base.get("runtime", {}).get("workdirs", {}).get("provision_paths", [])
    spec["runtime"].setdefault("workdirs", {})["provision_paths"] = copy.deepcopy(provision)
    static = spec.setdefault("quality_gates", {}).setdefault("static", {})
    static.update(enabled=True, required_checks=list(parameters["verification_commands"]))
    return documents
