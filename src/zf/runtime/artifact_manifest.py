"""Agent-skills artifact manifest contract.

Manifests are runtime artifacts: useful as structured handoff input, but not
business truth by themselves. Layer 1 indexes eligible manifest refs so
downstream roles can consume them deterministically; orchestrator still owns
final task-contract acceptance.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SEMVER_LABEL_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z][0-9A-Za-z.-]*)?$"
)
_ARTIFACT_STATUSES = {
    "",
    "draft",
    "proposed",
    "accepted",
    "superseded",
    "rejected",
}
_ARTIFACT_STATUS_ALIASES = {
    "approve": "accepted",
    "approved": "accepted",
}
_CONTRACT_REF_STATUSES = {"", "accepted"}

ARTIFACT_KIND_ALIASES: dict[str, str] = {
    "sdd": "spec",
    "spec_ref": "spec",
    "specification": "spec",
    "product_spec": "spec",
    "capability_baseline": "spec",
    "ga_capability_baseline": "spec",
    "plan": "implementation_plan",
    "design_plan": "implementation_plan",
    "full_stage_plan": "implementation_plan",
    "full_stage_implementation_plan": "implementation_plan",
    "data_agent_plan": "implementation_plan",
    "p3_plan": "implementation_plan",
    "p3_implementation_plan": "implementation_plan",
    "phase_3_plan": "implementation_plan",
    "todo": "backlog_plan",
    "backlog": "backlog_plan",
    "full_stage_backlog": "backlog_plan",
    "data_agent_backlog": "backlog_plan",
    "p3_backlog": "backlog_plan",
    "phase_3_backlog": "backlog_plan",
    "work_unit_map": "task_map",
    "work-unit-map": "task_map",
    "task-map": "task_map",
    "source-index": "source_index",
    "source_index_ref": "source_index",
    "coverage-report": "coverage_report",
    "coverage_report_ref": "coverage_report",
    "review": "critic_gate",
    "critic_review": "critic_gate",
    "acceptance_criteria": "critic_gate",
}

TASKLESS_WORKFLOW_MANIFEST_SOURCES: set[str] = {
    "refactor_task_map",
    "product_task_map",
    "task_map",
}

CONTRACT_KIND_FIELDS: dict[str, str] = {
    "spec": "spec_ref",
    "implementation_plan": "plan_ref",
    "process_plan": "plan_ref",
    "backlog_plan": "plan_ref",
    "tdd": "tdd_ref",
    "test_plan": "tdd_ref",
    "critic_gate": "critic_gate_ref",
}

EVIDENCE_KIND_FIELDS: dict[str, str] = {
    "process_plan": "process_plan_ref",
    "backlog_plan": "backlog_plan_ref",
    "backlog_map": "backlog_map_ref",
    "backlog": "backlog_map_ref",
    "task_map": "task_map_ref",
    "work_unit_map": "task_map_ref",
    "source_index": "source_index_ref",
    "coverage_report": "coverage_report_ref",
}


@dataclass(frozen=True)
class ArtifactRef:
    kind: str
    path: str
    sha256: str
    summary: str
    workdir_path: str = ""
    commit: str = ""
    artifact_id: str = ""
    version: int = 0
    supersedes: str = ""
    status: str = ""
    source_event_id: str = ""
    accepted_event_id: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactManifest:
    task_id: str
    role: str
    skills_used: list[str] = field(default_factory=list)
    artifact_refs: list[ArtifactRef] = field(default_factory=list)
    handoff_contract: dict[str, Any] = field(default_factory=dict)
    feature_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "role": self.role,
            "skills_used": list(self.skills_used),
            "artifact_refs": [ref.to_dict() for ref in self.artifact_refs],
            "handoff_contract": dict(self.handoff_contract),
            "feature_id": self.feature_id,
        }


@dataclass(frozen=True)
class ManifestValidationResult:
    ok: bool
    manifest: ArtifactManifest | None = None
    errors: list[str] = field(default_factory=list)


def load_manifest_from_payload(
    payload: dict[str, Any],
    *,
    project_root: Path,
    state_dir: Path,
    default_role: str = "",
    default_task_id: str = "",
) -> ManifestValidationResult:
    """Load a manifest from an event payload.

    Accepted payloads:
      - the manifest object directly,
      - ``{"manifest": {...}}``,
      - ``{"manifest_path": "..."}`` where the path is under project root or
        the runtime state directory.
    """
    raw: Any = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else None
    if raw is None and _looks_like_manifest(payload):
        raw = payload
    if raw is None and isinstance(payload.get("manifest_path"), str):
        path_result = _load_manifest_path(
            str(payload.get("manifest_path") or ""),
            project_root=project_root,
            state_dir=state_dir,
            default_role=default_role,
            default_task_id=default_task_id,
        )
        if not path_result.ok:
            return path_result
        raw = path_result.manifest.to_dict() if path_result.manifest else {}
    if not isinstance(raw, dict):
        return ManifestValidationResult(False, errors=["manifest payload is required"])
    return validate_artifact_manifest(
        raw,
        project_root=project_root,
        state_dir=state_dir,
        default_role=default_role,
        default_task_id=default_task_id,
    )


def validate_artifact_manifest(
    raw: dict[str, Any],
    *,
    project_root: Path,
    state_dir: Path,
    default_role: str = "",
    default_task_id: str = "",
) -> ManifestValidationResult:
    errors: list[str] = []
    task_id = str(raw.get("task_id") or default_task_id or "").strip()
    role = str(raw.get("role") or default_role or "").strip()
    feature_id = str(raw.get("feature_id") or "").strip()
    if not task_id:
        errors.append("task_id is required")
    if not role:
        errors.append("role is required")

    skills_used = _string_list(raw.get("skills_used"))
    handoff_contract = raw.get("handoff_contract") or {}
    if not isinstance(handoff_contract, dict):
        errors.append("handoff_contract must be a mapping")
        handoff_contract = {}

    artifact_refs_raw = raw.get("artifact_refs")
    if not isinstance(artifact_refs_raw, list) or not artifact_refs_raw:
        errors.append("artifact_refs must be a non-empty list")
        artifact_refs_raw = []

    refs: list[ArtifactRef] = []
    for idx, item in enumerate(artifact_refs_raw):
        if not isinstance(item, dict):
            errors.append(f"artifact_refs[{idx}] must be a mapping")
            continue
        ref = _validate_ref(
            item,
            idx=idx,
            errors=errors,
            project_root=project_root,
            state_dir=state_dir,
        )
        if ref is not None:
            refs.append(ref)

    if errors:
        return ManifestValidationResult(False, errors=errors)
    return ManifestValidationResult(
        True,
        manifest=ArtifactManifest(
            task_id=task_id,
            role=role,
            skills_used=skills_used,
            artifact_refs=refs,
            handoff_contract=handoff_contract,
            feature_id=feature_id,
        ),
    )


def artifact_refs_by_kind(manifest: ArtifactManifest) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for ref in manifest.artifact_refs:
        out.setdefault(ref.kind, []).append(ref.to_dict())
    return out


def normalize_artifact_kind(kind: str) -> str:
    normalized = str(kind or "").strip().lower().replace("-", "_")
    return ARTIFACT_KIND_ALIASES.get(normalized, normalized)


def is_taskless_workflow_manifest_payload(payload: dict[str, Any]) -> bool:
    """Return true for workflow-level manifests that are not task handoff refs."""
    raw: Any = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else None
    if raw is None and _looks_like_manifest(payload):
        raw = payload
    if not isinstance(raw, dict):
        return False
    if str(raw.get("task_id") or "").strip():
        return False
    handoff = raw.get("handoff_contract")
    handoff = handoff if isinstance(handoff, dict) else {}
    if str(handoff.get("source") or "").strip() in TASKLESS_WORKFLOW_MANIFEST_SOURCES:
        return True
    refs = raw.get("artifact_refs")
    if not isinstance(refs, list):
        return False
    return any(
        isinstance(ref, dict)
        and normalize_artifact_kind(str(ref.get("kind") or "")) == "task_map"
        for ref in refs
    )


def contract_refs_from_manifest(
    manifest: ArtifactManifest,
    *,
    event_id: str,
) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    evidence_refs: dict[str, str] = {}
    for ref in manifest.artifact_refs:
        if ref.status not in _CONTRACT_REF_STATUSES:
            continue
        kind = normalize_artifact_kind(ref.kind)
        field = CONTRACT_KIND_FIELDS.get(kind)
        if field and not refs.get(field):
            refs[field] = ref.path
        evidence_field = EVIDENCE_KIND_FIELDS.get(kind)
        if evidence_field and not evidence_refs.get(evidence_field):
            evidence_refs[evidence_field] = ref.path
    if refs.get("critic_gate_ref"):
        refs.setdefault("critic_event_id", event_id)
    if manifest.handoff_contract or evidence_refs:
        evidence_contract: dict[str, Any] = {
            "artifact_manifest_event_id": event_id,
        }
        if manifest.handoff_contract:
            evidence_contract["handoff_contract"] = dict(manifest.handoff_contract)
        if evidence_refs:
            evidence_contract["artifact_refs"] = evidence_refs
        refs["evidence_contract"] = evidence_contract
    return refs


def _validate_ref(
    raw: dict[str, Any],
    *,
    idx: int,
    errors: list[str],
    project_root: Path,
    state_dir: Path,
) -> ArtifactRef | None:
    kind = str(raw.get("kind") or "").strip()
    path = str(raw.get("path") or "").strip()
    sha256 = str(raw.get("sha256") or "").strip()
    summary = str(raw.get("summary") or "").strip()
    prefix = f"artifact_refs[{idx}]"
    workdir_path = str(raw.get("workdir_path") or "").strip()
    commit = str(raw.get("commit") or "").strip()
    artifact_id = str(raw.get("artifact_id") or "").strip()
    supersedes = str(raw.get("supersedes") or "").strip()
    status = _normalize_artifact_status(raw.get("status"))
    source_event_id = str(raw.get("source_event_id") or "").strip()
    accepted_event_id = str(raw.get("accepted_event_id") or "").strip()
    version = _parse_version(raw.get("version"), errors=errors, prefix=prefix)

    if not kind:
        errors.append(f"{prefix}.kind is required")
    if not path:
        errors.append(f"{prefix}.path is required")
    elif _invalid_artifact_path(path, project_root=project_root, state_dir=state_dir):
        errors.append(f"{prefix}.path is outside allowed artifact roots")
    if not sha256:
        errors.append(f"{prefix}.sha256 is required")
    elif not _SHA256_RE.match(sha256):
        errors.append(f"{prefix}.sha256 must be 64 hex chars")
    if not summary:
        errors.append(f"{prefix}.summary is required")
    if workdir_path and _invalid_workdir_path(workdir_path, state_dir=state_dir):
        errors.append(f"{prefix}.workdir_path is outside runtime workdirs")
    if status not in _ARTIFACT_STATUSES:
        errors.append(
            f"{prefix}.status must be one of "
            "draft/proposed/accepted/superseded/rejected"
        )
    if not kind or not path or not sha256 or not summary:
        return None
    if _invalid_artifact_path(path, project_root=project_root, state_dir=state_dir):
        return None
    if workdir_path and _invalid_workdir_path(workdir_path, state_dir=state_dir):
        return None
    if not _SHA256_RE.match(sha256):
        return None
    if status not in _ARTIFACT_STATUSES:
        return None
    if version < 0:
        return None
    return ArtifactRef(
        kind=kind,
        path=Path(path).as_posix(),
        sha256=sha256.lower(),
        summary=summary,
        workdir_path=workdir_path,
        commit=commit,
        artifact_id=artifact_id,
        version=version,
        supersedes=supersedes,
        status=status,
        source_event_id=source_event_id,
        accepted_event_id=accepted_event_id,
    )


def _parse_version(value: Any, *, errors: list[str], prefix: str) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, str) and _SEMVER_LABEL_RE.match(value.strip()):
        # External skills commonly use semantic artifact/package labels such as
        # "0.1.0". The runtime ledger owns monotonic integer versions, so keep
        # semver labels from rejecting the manifest and let TaskRefManager assign
        # the durable ledger version during indexing.
        return 0
    try:
        version = int(value)
    except (TypeError, ValueError):
        errors.append(f"{prefix}.version must be a positive integer")
        return -1
    if version < 1:
        errors.append(f"{prefix}.version must be a positive integer")
        return -1
    return version


def _normalize_artifact_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    return _ARTIFACT_STATUS_ALIASES.get(status, status)


def _load_manifest_path(
    raw_path: str,
    *,
    project_root: Path,
    state_dir: Path,
    default_role: str = "",
    default_task_id: str = "",
) -> ManifestValidationResult:
    if not raw_path.strip():
        return ManifestValidationResult(False, errors=["manifest_path is empty"])
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    try:
        resolved = path.resolve(strict=False)
        project_root_resolved = project_root.resolve(strict=False)
        state_dir_resolved = state_dir.resolve(strict=False)
        if not (
            _is_relative_to(resolved, project_root_resolved)
            or _is_relative_to(resolved, state_dir_resolved)
        ):
            return ManifestValidationResult(
                False,
                errors=["manifest_path is outside project root and state_dir"],
            )
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ManifestValidationResult(False, errors=[f"manifest_path unreadable: {exc}"])
    if not isinstance(data, dict):
        return ManifestValidationResult(False, errors=["manifest file must contain a JSON object"])
    return validate_artifact_manifest(
        data,
        project_root=project_root,
        state_dir=state_dir,
        default_role=default_role,
        default_task_id=default_task_id,
    )


def _invalid_artifact_path(path: str, *, project_root: Path, state_dir: Path) -> bool:
    candidate = Path(path)
    if any(part == ".." for part in candidate.parts):
        return True
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=False)
        return not (
            _is_relative_to(resolved, project_root.resolve(strict=False))
            or _is_relative_to(resolved, state_dir.resolve(strict=False) / "artifacts")
        )
    if candidate.parts and candidate.parts[0] == ".git":
        return True
    if candidate.parts and candidate.parts[0] == ".zf":
        return len(candidate.parts) < 2 or candidate.parts[1] != "artifacts"
    return False


def _invalid_workdir_path(path: str, *, state_dir: Path) -> bool:
    candidate = Path(path)
    if any(part == ".." for part in candidate.parts):
        return True
    workdirs_root = (state_dir / "workdirs").resolve(strict=False)
    candidates = [candidate] if candidate.is_absolute() else [
        state_dir / candidate,
        state_dir / "workdirs" / candidate,
    ]
    return all(
        not _is_relative_to(path.resolve(strict=False), workdirs_root)
        for path in candidates
    )


def _looks_like_manifest(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in ("artifact_refs", "handoff_contract", "skills_used"))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
