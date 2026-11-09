"""Editable, compiler-owned Workflow inputs for version-bound Preview chat."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml
from typing import Any

SCALAR_FIELDS = {"lanes", "strictness", "backend", "verifyBackend", "topology"}


def flow_input_roles(documents: list, index: int, family: str, *, config_path: Path | None = None) -> dict[str, list[str]]:
    """Use compiler inputs before multi-Flow role namespacing, including profiles."""
    from zf.core.config.kind_envelope import assemble_envelope_stream
    flow_kinds = {"IssueFlow", "PrdFlow", "RefactorFlow"}
    selected = [deepcopy(doc) for i, doc in enumerate(documents)
                if i == index or not isinstance(doc, dict) or doc.get("kind") not in flow_kinds]
    sources = None
    if config_path is not None:
        from zf.core.config.loader import (
            _config_env_map, _expand_env_vars, _load_profile_source_documents,
            _profile_source_refs_from_documents,
        )
        env = _config_env_map(config_path)
        selected = list(yaml.safe_load_all(_expand_env_vars(yaml.safe_dump_all(selected), env)))
        profiles, sources = _load_profile_source_documents(
            config_path, _profile_source_refs_from_documents(selected), env=env,
        )
        selected = profiles + selected
    body, _ = assemble_envelope_stream(selected, profile_source_files=sources)
    result = {role["name"]: list(role.get("skills", [])) for role in body.get("roles", [])
              if role.get("flow_kind") == family}
    spec = documents[index].get("spec", {})
    for pipeline in body.get("workflow", {}).get("pipelines", []):
        template = pipeline.get("lane_role_template") or {}
        for stage, skills in template.get("skills_by_stage", {}).items():
            alias = "fix" if family == "issue" and spec.get("topology", "light") == "fanout" and stage == "impl" else stage
            result.setdefault(alias, list(skills))
    return result


def config_review_fields(index: int, spec: dict, *, role_names: list[str], family: str = "", inherited_skills: dict | None = None) -> dict:
    result = {}
    reason = "修改将重新编译配置候选；需审核并应用新版 YAML"
    for key in sorted(SCALAR_FIELDS & spec.keys()):
        if type(spec[key]) in (str, int):
            result[f"config.{index}.{key}"] = {"value": spec[key], "editable": True, "reason": reason}
    if family in {"issue", "prd", "refactor"}:
        # A v4 controller requires the profile and pool policy together. Expose
        # absent inputs as empty values so one reviewed patch can enable it.
        profile_key = "flow_profile" if "flow_profile" in spec else "flowProfile"
        pipeline_key = "task_pipeline" if "task_pipeline" in spec else "taskPipeline"
        for key, default in ((profile_key, ""), (pipeline_key, {})):
            value = spec.get(key, default)
            if type(value) is type(default):
                result[f"config.{index}.{key}"] = {"value": value, "editable": True, "reason": reason}
        if family in {"issue", "prd"} and "topology" not in spec:
            result[f"config.{index}.topology"] = {
                "value": "light" if family == "issue" else "fanout", "editable": True, "reason": reason,
            }
        if family == "prd":
            for key in ("prdRef", "targetRoot"):
                if isinstance(spec.get(key, ""), str):
                    result[f"config.{index}.{key}"] = {"value": spec.get(key, ""), "editable": True, "reason": reason}
    defaults_key = "role_defaults" if "role_defaults" in spec else "roleDefaults"
    defaults = spec.get(defaults_key) or {}
    for key in ("model",):
        result[f"config.{index}.{defaults_key}.{key}"] = {
            "value": defaults.get(key, ""), "editable": True, "reason": reason,
        }
    bundles_key = "role_skill_bundles" if "role_skill_bundles" in spec else "roleSkillBundles"
    bundles = spec.get(bundles_key) or {}
    aliases = {"fix" if spec.get("topology", "light") == "fanout" else "impl", "verify"} if family == "issue" else {"impl", "verify", "scan"} if family == "prd" else {"impl", "verify"}
    for role in sorted(set(role_names) | aliases):
        if not role or "." in role:
            continue
        value = bundles.get(role, (inherited_skills or {}).get(role, []))
        if isinstance(value, list):
            result[f"config.{index}.{bundles_key}.{role}"] = {
                "value": value, "editable": True, "reason": reason,
            }
    return result


def apply_config_field(docs: list, field: str, value: Any, *, roles: dict | None = None) -> list:
    """Only called after the selected field has matched current server context."""
    result = deepcopy(docs)
    _, document, *keys = field.split(".")
    parent = result[int(document)]["spec"]
    if keys[0] == "roles":
        from zf.runtime.workflow_review_roles import apply_role_field
        apply_role_field(parent, keys, value, roles or {})
        return result
    for key in keys[:-1]:
        parent = parent.setdefault(key, {})
        if not isinstance(parent, dict):
            raise ValueError("Config field parent must be an object")
    parent[keys[-1]] = value
    return result
