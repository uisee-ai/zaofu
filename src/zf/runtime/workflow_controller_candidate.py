"""Materialize bounded controller changes for the existing proposal/apply path."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

import yaml

from zf.core.config.loader import load_config
from zf.core.state.atomic_io import atomic_write_text


def materialize_controller_candidate(
    *, base_path: Path, request_id: str, short_spec: Mapping[str, Any],
) -> Path:
    """Materialize bounded Flow inputs; never write canonical config.

    Strictness remains checked against the compiled delivery contract, and
    pattern_id remains a registered entry selection. Neither authorizes gate
    changes or arbitrary YAML from an agent. PRD v4 requires explicit typed
    profile, mode and independent pool capacities.
    """
    family = str(short_spec.get("flow_family") or "")
    parameters = short_spec.get("parameters") or {}
    if family not in {"IssueFlow", "PrdFlow", "RefactorFlow"} or "lanes" not in parameters:
        return base_path
    lanes = parameters["lanes"]
    if isinstance(lanes, bool) or not isinstance(lanes, int) or not 1 <= lanes <= 64:
        raise ValueError("controller lanes must be an integer between 1 and 64")
    documents = list(yaml.safe_load_all(base_path.read_text(encoding="utf-8")))
    matches = [d for d in documents if isinstance(d, dict) and d.get("kind") == family]
    if len(matches) != 1 or not isinstance(matches[0].get("spec"), dict):
        raise ValueError("controller synthesis requires exactly one existing selected Flow")
    spec = matches[0]["spec"]
    pipeline = parameters.get("task_pipeline")
    if pipeline is not None:
        from zf.runtime.workflow_task_pipeline_inputs import validate_prd_task_pipeline
        validate_prd_task_pipeline(pipeline, family=family, lanes=lanes)
        if spec.get("taskPipeline") or spec.get("task_pipeline") or spec.get("flowProfile") or spec.get("flow_profile"):
            raise ValueError("existing profile requires explicit config revision")
    if family in {"IssueFlow", "PrdFlow"}:
        topology = spec.get("topology", "light" if family == "IssueFlow" else "fanout")
        if topology == "light" and lanes != 1 and pipeline is None:
            raise ValueError("light topology cannot resize lanes; explicit topology revision is required")
    lane_keys = [key for key in ("lanes", "laneCount", "lane_count") if key in spec]
    if len(lane_keys) > 1:
        raise ValueError("controller Flow has ambiguous lane aliases")
    key = lane_keys[0] if lane_keys else "lanes"
    if spec.get(key) == lanes and pipeline is None:
        return base_path
    if spec.get("taskPipeline") or spec.get("task_pipeline"):
        raise ValueError("Task Pipeline resizing requires an explicit pool/config revision, not lanes alone")
    spec[key] = lanes
    if pipeline is not None:
        spec["topology"] = "fanout"
        spec["flowProfile"] = pipeline["profile"]
        spec["taskPipeline"] = {
            "mode": pipeline["mode"],
            "maxActiveTaskPipelines": pipeline["max_active_task_pipelines"],
            "pools": {
                "impl": {"capacity": pipeline["impl_capacity"]},
                "verify": {"capacity": pipeline["verify_capacity"]},
            },
            "backpressure": {
                "maxUnverifiedTasks": pipeline["max_active_task_pipelines"],
                "maxIntegrationQueue": 1,
            },
            "workerLifecycle": {},
            "affinity": {},
            "integrationAdmission": {},
            "candidate": {},
        }
    content = yaml.safe_dump_all(documents, sort_keys=False, allow_unicode=True)
    digest = hashlib.sha256(content.encode()).hexdigest()
    request_digest = hashlib.sha256(request_id.encode()).hexdigest()[:12]
    candidate = base_path.parent / f".{base_path.name}.workflow-candidate-{request_digest}-{digest[:16]}.tmp"
    if candidate.is_symlink():
        raise ValueError("controller candidate must not be a symlink")
    existed = candidate.exists()
    if existed and candidate.read_text(encoding="utf-8") != content:
        raise ValueError("controller candidate digest collision")
    if not existed:
        atomic_write_text(candidate, content)
        candidate.chmod(0o600)
    try:
        # Resolve profiles relative to the canonical config directory and keep
        # all normal schema/topology gates before a proposal can be approved.
        load_config(candidate)
    except Exception:
        if not existed:
            candidate.unlink()
        raise
    return candidate
