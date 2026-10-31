"""Deterministic consumer for approved Workflow config proposals."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import yaml

from zf.core.config.loader import load_config
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.workflow_proposal import (
    WorkflowProposalError,
    load_workflow_proposal,
)
from zf.runtime.workflow_requests import load_workflow_request


WORKFLOW_CONFIG_APPLY_RECEIPT_SCHEMA = "workflow-config-apply-receipt.v1"
_APPLY_PAYLOAD_FIELDS = frozenset({
    "actor",
    "approval_id",
    "approval_ref",
    "config_ref",
    "decision_token",
    "establish_delivery_baseline",
    "idempotency_key",
    "project_id",
    "proposal_digest",
    "proposal_id",
    "proposal_ref",
    "request_id",
    "task_id",
    "validation_result_ref",
})
_SENSITIVE_CONFIG_KEY = re.compile(
    r"(?:api[_-]?key|token|secret|password|passwd|credential)",
    re.IGNORECASE,
)
_TOKEN_VALUE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"gh[pousr]_[A-Za-z0-9_]{12,})"
)


@dataclass(frozen=True)
class WorkflowConfigApplyOutcome:
    status: str
    reason: str
    proposal_id: str
    proposal_digest: str
    base_config_digest: str
    target_config_digest: str
    config_ref: str
    receipt_ref: dict[str, Any]
    replayed: bool = False
    delivery_baseline_ref: dict[str, Any] = field(default_factory=dict)


class WorkflowConfigApplyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorkflowConfigApplyService:
    def __init__(self, *, state_dir: Path, project_root: Path) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.project_root = Path(project_root).expanduser().resolve()

    def apply(
        self,
        payload: Mapping[str, Any],
        *,
        source_event_id: str = "",
        actor: str = "operator",
    ) -> WorkflowConfigApplyOutcome:
        self._validate_payload(payload)
        proposal_ref = payload.get("proposal_ref")
        if not isinstance(proposal_ref, Mapping):
            raise WorkflowConfigApplyError(
                "proposal_ref_missing",
                "workflow config apply requires an immutable proposal_ref",
            )
        try:
            proposal = load_workflow_proposal(self.state_dir, proposal_ref)
        except WorkflowProposalError as exc:
            raise WorkflowConfigApplyError(
                "proposal_invalid",
                str(exc),
            ) from exc
        if str(proposal.get("approval_status") or "") != "approvable":
            raise WorkflowConfigApplyError(
                "proposal_blocked",
                "workflow proposal is not approvable",
            )
        if str(proposal.get("change_mode") or "") != "config_change":
            raise WorkflowConfigApplyError(
                "config_change_not_required",
                "parameter-only workflow proposals do not apply zf.yaml",
            )
        self._require_current_proposal(proposal)
        expected_proposal_digest = str(
            payload.get("proposal_digest")
            or proposal.get("proposal_digest")
            or ""
        )
        if expected_proposal_digest != str(proposal.get("proposal_digest") or ""):
            raise WorkflowConfigApplyError(
                "proposal_digest_mismatch",
                "workflow proposal digest does not match approval payload",
            )
        self._validate_approval(payload)
        validation_result_ref = self._exact_validation_result_ref(
            proposal,
            payload.get("validation_result_ref"),
        )
        self._validate_result(validation_result_ref)
        target = self._target_config(payload)
        self._require_project_binding(proposal, payload, target)
        base_digest = str(
            (proposal.get("base_config") or {}).get("sha256")
            if isinstance(proposal.get("base_config"), Mapping)
            else ""
        )
        target_digest = str(
            (proposal.get("target_config") or {}).get("sha256")
            if isinstance(proposal.get("target_config"), Mapping)
            else ""
        )
        if not base_digest or not target_digest:
            raise WorkflowConfigApplyError(
                "config_digest_missing",
                "workflow proposal is missing config digests",
            )
        candidate_path = self._private_candidate(proposal)
        content = candidate_path.read_text(encoding="utf-8")
        if _digest_bytes(content.encode("utf-8")) != target_digest:
            raise WorkflowConfigApplyError(
                "candidate_digest_mismatch",
                "private config candidate digest does not match the proposal",
            )
        _validate_no_inline_secrets(content)
        try:
            candidate_config = load_config(candidate_path)
            from zf.runtime.workflow_controller_skills import validate_controller_skill_packages
            validate_controller_skill_packages(candidate_config, self.project_root)
            if candidate_config.runtime.skills.strict:
                from zf.core.skills import validate_skill_sources
                problems = validate_skill_sources(config=candidate_config, project_root=self.project_root)
                if problems:
                    raise ValueError("skill validation failed: " + "; ".join(problems))
        except Exception as exc:
            raise WorkflowConfigApplyError(
                "candidate_config_invalid",
                f"workflow config candidate failed validation: {exc}",
            ) from exc
        idempotency_key = str(
            payload.get("idempotency_key")
            or f"workflow-config-apply:{expected_proposal_digest}"
        )
        with locked_path(target):
            self._require_current_proposal(proposal)
            target = self._target_config(payload)
            try:
                outcome = self._apply_locked(
                    payload=payload,
                    proposal=proposal,
                    target=target,
                    content=content,
                    base_digest=base_digest,
                    target_digest=target_digest,
                    expected_proposal_digest=expected_proposal_digest,
                    validation_result_ref=validation_result_ref,
                    idempotency_key=idempotency_key,
                    source_event_id=source_event_id,
                    actor=actor,
                )
            except WorkflowConfigApplyError as exc:
                if payload.get("establish_delivery_baseline") and exc.code == "config_checkpoint_required":
                    raise WorkflowConfigApplyError("config_delivery_baseline_blocked", str(exc)) from exc
                raise
            if payload.get("establish_delivery_baseline"):
                from zf.runtime.workflow_config_baseline import (
                    ConfigBaselineError, establish_config_baseline,
                )
                try:
                    baseline = establish_config_baseline(
                        target, base_digest=base_digest, target_digest=target_digest,
                        proposal_digest=expected_proposal_digest,
                    )
                except ConfigBaselineError as exc:
                    raise WorkflowConfigApplyError("config_delivery_baseline_blocked", str(exc)) from exc
                baseline_ref = write_immutable_json_sidecar(
                    self.state_dir,
                    {**baseline, "config_apply_receipt_ref": outcome.receipt_ref,
                     "target_config_digest": target_digest},
                    root="workflow/config-baseline-receipts",
                    kind="workflow_config_baseline_receipt",
                    schema_version="workflow-config-baseline-receipt.v1",
                    created_by="workflow-config-apply", source_event_id=source_event_id,
                )
                outcome = replace(outcome, delivery_baseline_ref=baseline_ref)
            return outcome

    def _validate_payload(self, payload: Mapping[str, Any]) -> None:
        if "establish_delivery_baseline" in payload and not isinstance(payload["establish_delivery_baseline"], bool):
            raise WorkflowConfigApplyError("apply_payload_invalid", "establish_delivery_baseline must be boolean")
        unknown = sorted(set(payload) - _APPLY_PAYLOAD_FIELDS)
        if unknown:
            raise WorkflowConfigApplyError(
                "apply_payload_unknown_field",
                "workflow config apply contains unsupported fields: "
                + ", ".join(unknown),
            )

    def _require_project_binding(
        self,
        proposal: Mapping[str, Any],
        payload: Mapping[str, Any],
        target: Path,
    ) -> None:
        identity = proposal.get("project_identity")
        if not isinstance(identity, Mapping):
            raise WorkflowConfigApplyError(
                "project_identity_missing",
                "workflow proposal has no project identity",
            )
        root = Path(str(identity.get("root") or "")).expanduser()
        config_ref = Path(str(identity.get("config_ref") or "")).expanduser()
        if (
            root.resolve(strict=False) != self.project_root
            or config_ref.resolve(strict=False) != target.resolve(strict=False)
        ):
            raise WorkflowConfigApplyError(
                "project_identity_mismatch",
                "workflow proposal belongs to a different project",
            )
        request_id = str(payload.get("request_id") or "").strip()
        if request_id and request_id != str(proposal.get("request_id") or ""):
            raise WorkflowConfigApplyError(
                "project_identity_mismatch",
                "workflow config apply request identity does not match",
            )
    def _apply_locked(
        self,
        *,
        payload: Mapping[str, Any],
        proposal: Mapping[str, Any],
        target: Path,
        content: str,
        base_digest: str,
        target_digest: str,
        expected_proposal_digest: str,
        validation_result_ref: Mapping[str, Any],
        idempotency_key: str,
        source_event_id: str,
        actor: str,
    ) -> WorkflowConfigApplyOutcome:
        replay = self._idempotency_record(idempotency_key)
        resume_pending = bool(replay and replay.get("status") == "checkpoint_pending")
        if replay:
            if (
                replay.get("proposal_digest") != expected_proposal_digest
                or replay.get("target_config_digest") != target_digest
            ):
                raise WorkflowConfigApplyError(
                    "idempotency_conflict",
                    "workflow config apply idempotency key was reused",
                )
        if replay and not resume_pending:
            current_digest = _file_digest(target)
            if current_digest != target_digest:
                raise WorkflowConfigApplyError(
                    "applied_config_drift",
                    "previously applied workflow config has drifted",
                )
            config_commit = self._checkpoint(base_digest, target_digest, expected_proposal_digest)
            if config_commit and not replay.get("config_commit"):
                from zf.runtime.sidecar_refs import hydrate_sidecar_ref
                receipt = dict(hydrate_sidecar_ref(self.state_dir, replay["receipt_ref"]).payload)
                receipt.update(config_commit=config_commit, prior_receipt_ref=replay["receipt_ref"])
                replay["receipt_ref"] = write_immutable_json_sidecar(
                    self.state_dir, receipt, root="workflow/config-apply-receipts",
                    kind="workflow_config_apply_receipt", schema_version=WORKFLOW_CONFIG_APPLY_RECEIPT_SCHEMA,
                    created_by="workflow-config-apply", source_event_id=source_event_id,
                )
                replay["config_commit"] = config_commit
                self._write_idempotency_record(idempotency_key, replay)
            return WorkflowConfigApplyOutcome(
                status="applied",
                reason="workflow config apply replayed",
                proposal_id=str(proposal.get("proposal_id") or ""),
                proposal_digest=expected_proposal_digest,
                base_config_digest=base_digest,
                target_config_digest=target_digest,
                config_ref=str(target),
                receipt_ref=dict(replay.get("receipt_ref") or {}),
                replayed=True,
            )
        current_digest = _file_digest(target)
        if current_digest != base_digest and not (resume_pending and current_digest == target_digest):
            raise WorkflowConfigApplyError(
                "base_config_stale",
                "project zf.yaml no longer matches the approved proposal base",
            )
        self._write_idempotency_record(idempotency_key, {
            "status": "checkpoint_pending", "proposal_digest": expected_proposal_digest,
            "target_config_digest": target_digest,
        })
        atomic_write_text(target, content)
        if _file_digest(target) != target_digest:
            raise WorkflowConfigApplyError(
                "config_write_verification_failed",
                "applied workflow config digest verification failed",
            )
        config_commit = self._checkpoint(base_digest, target_digest, expected_proposal_digest)
        receipt = {
            "schema_version": WORKFLOW_CONFIG_APPLY_RECEIPT_SCHEMA,
            "proposal_id": str(proposal.get("proposal_id") or ""),
            "proposal_digest": expected_proposal_digest,
            "request_id": str(proposal.get("request_id") or ""),
            "request_revision": int(proposal.get("request_revision") or 0),
            "base_config_digest": base_digest,
            "target_config_digest": target_digest,
            "config_ref": str(target),
            "config_commit": config_commit,
            "approval_ref": str(
                payload.get("approval_ref")
                or payload.get("approval_id")
                or payload.get("decision_token")
                or ""
            ),
            "validation_result_ref": dict(validation_result_ref),
            "idempotency_key": idempotency_key,
            "actor": str(actor),
        }
        receipt_ref = write_immutable_json_sidecar(
            self.state_dir,
            receipt,
            root="workflow/config-apply-receipts",
            kind="workflow_config_apply_receipt",
            schema_version=WORKFLOW_CONFIG_APPLY_RECEIPT_SCHEMA,
            created_by="workflow-config-apply",
            source_event_id=source_event_id,
        )
        self._write_idempotency_record(
            idempotency_key,
            {
                "proposal_digest": expected_proposal_digest,
                "target_config_digest": target_digest,
                "receipt_ref": receipt_ref,
                "config_commit": config_commit,
            },
        )
        return WorkflowConfigApplyOutcome(
            status="applied",
            reason="workflow config applied",
            proposal_id=str(proposal.get("proposal_id") or ""),
            proposal_digest=expected_proposal_digest,
            base_config_digest=base_digest,
            target_config_digest=target_digest,
            config_ref=str(target),
            receipt_ref=receipt_ref,
        )

    def _checkpoint(self, base_digest: str, target_digest: str, proposal_digest: str) -> str:
        from zf.runtime.workflow_config_checkpoint import checkpoint_config
        try:
            return checkpoint_config(self.project_root, base_digest=base_digest,
                                     target_digest=target_digest, proposal_digest=proposal_digest)
        except (OSError, ValueError) as exc:
            raise WorkflowConfigApplyError("config_checkpoint_required", str(exc)) from exc

    def _require_current_proposal(
        self,
        proposal: Mapping[str, Any],
    ) -> None:
        request = load_workflow_request(
            self.state_dir,
            str(proposal.get("request_id") or ""),
        )
        if (
            not request
            or int(request.get("revision") or 0)
            != int(proposal.get("request_revision") or 0)
            or str(request.get("proposal_digest") or "")
            != str(proposal.get("proposal_digest") or "")
            or str(request.get("status") or "") not in {"proposed", "approved"}
        ):
            raise WorkflowConfigApplyError(
                "proposal_superseded",
                "workflow proposal is not current for its request",
            )

    def _exact_validation_result_ref(
        self,
        proposal: Mapping[str, Any],
        provided: object,
    ) -> dict[str, Any]:
        expected = proposal.get("validation_result_ref")
        if not isinstance(expected, Mapping) or not isinstance(
            provided,
            Mapping,
        ):
            raise WorkflowConfigApplyError(
                "validation_result_mismatch",
                "config apply requires the Proposal validation descriptor",
            )
        if any(
            str(provided.get(key) or "") != str(expected.get(key) or "")
            for key in ("ref", "sha256")
        ):
            raise WorkflowConfigApplyError(
                "validation_result_mismatch",
                "validation result does not match the approved Proposal",
            )
        return dict(expected)

    def _target_config(self, payload: Mapping[str, Any]) -> Path:
        raw = str(payload.get("config_ref") or self.project_root / "zf.yaml")
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = self.project_root / target
        target = Path(os.path.abspath(target))
        expected = Path(os.path.abspath(self.project_root / "zf.yaml"))
        if target != expected:
            raise WorkflowConfigApplyError(
                "config_path_outside_control_plane",
                "workflow config apply may only update project zf.yaml",
            )
        try:
            mode = target.lstat().st_mode
        except OSError as exc:
            raise WorkflowConfigApplyError(
                "config_unreadable",
                "project zf.yaml is unavailable",
            ) from exc
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise WorkflowConfigApplyError(
                "config_path_unsafe",
                "project zf.yaml must be a regular non-symlink file",
            )
        return target

    def _private_candidate(self, proposal: Mapping[str, Any]) -> Path:
        raw = str(proposal.get("private_config_candidate_ref") or "")
        candidate = Path(raw).expanduser()
        try:
            raw_mode = candidate.lstat().st_mode
            if stat.S_ISLNK(raw_mode):
                raise ValueError("candidate is a symlink")
            candidate = candidate.resolve(strict=True)
            candidate.relative_to(
                self.state_dir / "private" / "workflow-config-candidates"
            )
            mode = candidate.lstat().st_mode
        except (OSError, ValueError) as exc:
            raise WorkflowConfigApplyError(
                "candidate_path_unsafe",
                "workflow config candidate is outside private state",
            ) from exc
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise WorkflowConfigApplyError(
                "candidate_path_unsafe",
                "workflow config candidate must be a regular file",
            )
        return candidate

    def _validate_approval(self, payload: Mapping[str, Any]) -> None:
        value = str(
            payload.get("approval_ref")
            or payload.get("approval_id")
            or payload.get("decision_token")
            or ""
        ).strip()
        if not value:
            raise WorkflowConfigApplyError(
                "approval_required",
                "owner approval is required before config apply",
            )

    def _validate_result(self, value: object) -> None:
        path = _bounded_ref_path(
            value,
            project_root=self.project_root,
            state_dir=self.state_dir,
        )
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowConfigApplyError(
                "validation_result_invalid",
                "workflow config validation result is unreadable",
            ) from exc
        status = str(
            report.get("status") if isinstance(report, Mapping) else ""
        ).upper()
        if status not in {"GO", "PASS", "WARN"}:
            raise WorkflowConfigApplyError(
                "validation_failed",
                "workflow config validation did not pass",
            )

    def _idempotency_record(self, key: str) -> dict[str, Any]:
        return _read_json(self._idempotency_path(key))

    def _write_idempotency_record(
        self,
        key: str,
        value: Mapping[str, Any],
    ) -> None:
        path = self._idempotency_path(key)
        atomic_write_text(path, json.dumps(dict(value), sort_keys=True, indent=2) + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _idempotency_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return (
            self.state_dir
            / "private"
            / "workflow-config-apply"
            / f"{digest}.json"
        )


def _bounded_ref_path(
    value: object,
    *,
    project_root: Path,
    state_dir: Path,
) -> Path:
    raw = ""
    if isinstance(value, Mapping):
        raw = str(value.get("ref") or "")
    else:
        raw = str(value or "")
    if not raw:
        raise WorkflowConfigApplyError(
            "validation_result_missing",
            "validation_result_ref is required",
        )
    path = Path(raw).expanduser()
    candidates = [path] if path.is_absolute() else [
        state_dir / path,
        project_root / path,
    ]
    for candidate in candidates:
        try:
            if candidate.is_symlink():
                continue
            resolved = candidate.resolve(strict=True)
            if not (
                resolved == project_root
                or project_root in resolved.parents
                or resolved == state_dir
                or state_dir in resolved.parents
            ):
                continue
            if resolved.is_file() and not resolved.is_symlink():
                if isinstance(value, Mapping):
                    expected = str(value.get("sha256") or "")
                    if expected and _file_digest(resolved) != expected:
                        raise WorkflowConfigApplyError(
                            "validation_result_digest_mismatch",
                            "validation_result_ref digest does not match",
                        )
                return resolved
        except WorkflowConfigApplyError:
            raise
        except OSError:
            continue
    raise WorkflowConfigApplyError(
        "validation_result_unsafe",
        "validation_result_ref is outside project/state scope",
    )


def _file_digest(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _validate_no_inline_secrets(content: str) -> None:
    try:
        documents = list(yaml.safe_load_all(content))
    except yaml.YAMLError as exc:
        raise WorkflowConfigApplyError(
            "candidate_config_invalid",
            f"workflow config candidate failed YAML parsing: {exc}",
        ) from exc

    def walk(value: Any, *, key: str = "") -> None:
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                walk(child, key=str(child_key))
            return
        if isinstance(value, list):
            for child in value:
                walk(child, key=key)
            return
        if not isinstance(value, str) or not value:
            return
        normalized_key = key.lower()
        is_reference_key = normalized_key.endswith(
            ("_env", "env", "_ref", "ref", "_path", "path", "_name", "name")
        )
        is_env_reference = value.startswith("${") and value.endswith("}")
        if (
            _TOKEN_VALUE.search(value)
            or (
                _SENSITIVE_CONFIG_KEY.search(normalized_key)
                and not is_reference_key
                and not is_env_reference
                and value != "<redacted>"
            )
        ):
            raise WorkflowConfigApplyError(
                "candidate_inline_secret",
                "workflow config candidate contains an inline secret",
            )

    for document in documents:
        walk(document)


__all__ = [
    "WORKFLOW_CONFIG_APPLY_RECEIPT_SCHEMA",
    "WorkflowConfigApplyError",
    "WorkflowConfigApplyOutcome",
    "WorkflowConfigApplyService",
]
