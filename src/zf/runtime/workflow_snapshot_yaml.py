"""Verified Workflow YAML shared by read projections and snapshot discussion."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from zf.core.config.render import redact_config
from zf.core.security.redaction import redact_obj
from zf.runtime.sidecar_refs import DEFAULT_HYDRATE_LIMIT_BYTES
from zf.runtime.workflow_config_apply import WorkflowConfigApplyService


def project_workflow_yaml(
    snapshot: dict[str, Any],
    proposal: dict[str, Any],
    *,
    state_dir: Path,
    project_root: Path,
) -> dict[str, Any]:
    """Enrich a hydrated snapshot without changing its immutable artifact."""
    if isinstance(snapshot.get("yaml"), str) and snapshot["yaml"].strip():
        return snapshot
    config = snapshot.get("config")
    if not isinstance(config, dict) or not config:
        return snapshot

    source = snapshot.get("source_config")
    target = proposal.get("target_config")
    digest = source.get("sha256") if isinstance(source, dict) else None
    if digest and isinstance(target, dict) and digest == target.get("sha256"):
        try:
            candidate = WorkflowConfigApplyService(
                state_dir=state_dir, project_root=project_root,
            )._private_candidate(proposal)
            with candidate.open("rb") as stream:
                content = stream.read(DEFAULT_HYDRATE_LIMIT_BYTES + 1)
            if (
                len(content) <= DEFAULT_HYDRATE_LIMIT_BYTES
                and hashlib.sha256(content).hexdigest() == digest
            ):
                documents = list(yaml.safe_load_all(content.decode("utf-8")))
                if documents and all(isinstance(doc, dict) for doc in documents):
                    return {
                        **snapshot,
                        "yaml": _render(documents),
                        "yaml_source": "bound_candidate",
                    }
        except (OSError, ValueError, yaml.YAMLError):
            pass

    # The mutable source_config.path is never a historical configuration source.
    return {
        **snapshot,
        "yaml": _render([config]),
        "yaml_source": "compiled_snapshot",
    }


def _render(documents: list[dict[str, Any]]) -> str:
    return yaml.safe_dump_all(
        [redact_obj(redact_config(doc)) for doc in documents],
        sort_keys=False,
        allow_unicode=True,
    )
