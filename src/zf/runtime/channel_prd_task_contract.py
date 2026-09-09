"""Compile a canonical Channel PRD into a workflow-parent Task contract."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_readiness import (
    channel_prd_planning_authorized,
    owner_readiness_risk_accepted,
)
from zf.runtime.channel_workflow_authority import (
    channel_authority_context_from_submit_payload,
    channel_workflow_authority_error,
)
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref
from zf.runtime.task_map import _verification_command_errors
from zf.runtime.verification_commands import (
    VerificationCommandError,
    normalize_verification_commands,
    validation_with_commands,
)
from zf.runtime.workflow_intake import _extract_verification_commands


class ChannelPrdTaskContractError(ValueError):
    """The selected Channel PRD cannot authorize an executable Task."""


def compile_channel_prd_task_payload(
    state_dir: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Return a Task payload bound to one ready, immutable Channel PRD.

    The compiler preserves product facts already present in the PRD. It does
    not invent implementation file ownership; the delivery planner owns that
    decomposition in its task map.
    """

    authority = channel_authority_context_from_submit_payload(payload)
    if not authority:
        return dict(payload)
    authority_error = channel_workflow_authority_error(state_dir, authority)
    if authority_error:
        raise ChannelPrdTaskContractError(authority_error)

    channel_id = str(authority["channel_id"])
    thread_id = str(authority.get("thread_id") or "main")
    channel = project_channel(Path(state_dir), channel_id) or {}
    consensus = _thread_consensus(channel, thread_id)
    synthesis = _matching_synthesis(channel, thread_id, consensus)
    if synthesis is None:
        raise ChannelPrdTaskContractError(
            "Channel PRD has no synthesis matching the confirmed artifact"
        )

    readiness_verdict = str(
        synthesis.get("readiness_verdict")
        or consensus.get("readiness_verdict")
        or "unassessed"
    ).strip()
    implementation_start = synthesis.get(
        "implementation_start",
        consensus.get("implementation_start"),
    )
    readiness_ref = str(synthesis.get("readiness_ref") or "").strip()
    readiness_digest = _bare_digest(synthesis.get("readiness_digest"))
    risk_accepted = owner_readiness_risk_accepted(
        consensus,
        readiness_ref=readiness_ref,
        readiness_digest=readiness_digest,
    )
    if not channel_prd_planning_authorized(
        readiness_verdict=readiness_verdict,
        risk_accepted=risk_accepted,
    ):
        raise ChannelPrdTaskContractError(
            "Channel PRD is not ready for Task planning: "
            f"readiness_verdict={readiness_verdict!r}"
        )
    if _string_items(synthesis.get("open_questions")):
        raise ChannelPrdTaskContractError(
            "Channel PRD still has unresolved open questions"
        )

    source_ref = str(authority.get("source_ref") or "").strip()
    source_digest = _bare_digest(authority.get("source_digest"))
    if not readiness_ref or not readiness_digest:
        raise ChannelPrdTaskContractError(
            "Channel PRD readiness artifact ref/digest is required"
        )

    prd_payload = _hydrate_json(
        state_dir,
        ref=source_ref,
        digest=source_digest,
        label="Channel PRD",
    )
    readiness_payload = _hydrate_json(
        state_dir,
        ref=readiness_ref,
        digest=readiness_digest,
        label="Channel PRD readiness",
    )
    _require_sidecar_identity(
        prd_payload,
        authority=authority,
        label="Channel PRD",
    )
    _require_sidecar_identity(
        readiness_payload,
        authority=authority,
        label="Channel PRD readiness",
    )
    if not channel_prd_planning_authorized(
        readiness_verdict=readiness_payload.get("verdict"),
        risk_accepted=risk_accepted,
    ):
        raise ChannelPrdTaskContractError(
            "Channel PRD readiness artifact does not authorize Task planning"
        )
    if _string_items(readiness_payload.get("gaps")) and not risk_accepted:
        raise ChannelPrdTaskContractError(
            "Channel PRD readiness artifact still contains blocking gaps"
        )

    prd_body = (
        prd_payload.get("body")
        if isinstance(prd_payload.get("body"), dict)
        else {}
    )
    semantic = (
        prd_body.get("synthesis")
        if isinstance(prd_body.get("synthesis"), dict)
        else {}
    )
    acceptance_criteria = _canonical_acceptance_criteria(
        semantic.get("acceptance_criteria")
    )
    if not acceptance_criteria:
        raise ChannelPrdTaskContractError(
            "Channel PRD must declare at least one acceptance criterion"
        )

    spec_ref = str(
        synthesis.get("spec_path") or prd_body.get("spec_path") or ""
    ).strip()
    spec_digest = _bare_digest(
        synthesis.get("spec_digest") or prd_body.get("spec_digest")
    )
    if not spec_ref or not spec_digest:
        raise ChannelPrdTaskContractError(
            "Channel PRD must bind a canonical product spec ref/digest"
        )
    _verify_state_artifact(
        state_dir,
        ref=spec_ref,
        digest=spec_digest,
        label="Channel product spec",
    )

    raw_contract = (
        dict(payload.get("contract"))
        if isinstance(payload.get("contract"), dict)
        else {}
    )
    commands = _verification_commands(raw_contract, semantic, prd_body)
    if not commands:
        raise ChannelPrdTaskContractError(
            "Channel PRD must declare at least one executable verification command"
        )
    for command in commands:
        errors = _verification_command_errors(str(command.get("command") or ""))
        if errors:
            raise ChannelPrdTaskContractError(
                "Channel PRD verification command is invalid: " + "; ".join(errors)
            )

    exclusions = _dedupe_strings([
        *_string_items(semantic.get("out_of_scope")),
        *_string_items(raw_contract.get("exclusions")),
        *_string_items(raw_contract.get("explicit_non_goals")),
    ])
    handoff_artifacts = _dedupe_strings([
        source_ref,
        spec_ref,
        readiness_ref,
        str(synthesis.get("conclusion_ref") or ""),
        str(synthesis.get("contract_ref") or ""),
        *_string_items(raw_contract.get("handoff_artifacts")),
    ])
    evidence = (
        dict(raw_contract.get("evidence_contract"))
        if isinstance(raw_contract.get("evidence_contract"), dict)
        else {}
    )
    evidence.update({
        "execution_owner": "workflow",
        "channel_id": channel_id,
        "thread_id": thread_id,
        "channel_member_id": str(authority.get("channel_member_id") or ""),
        "leader_revision": int(authority.get("leader_revision") or 0),
        "prd_revision": int(authority.get("prd_revision") or 0),
        "source_digest": source_digest,
        "channel_prd_digest": source_digest,
        "readiness_ref": readiness_ref,
        "readiness_digest": readiness_digest,
        "readiness_verdict": readiness_verdict,
        "implementation_start": implementation_start is True,
        "declared_implementation_start": implementation_start is True,
        "readiness_risk_accepted": risk_accepted,
        "readiness_risk_confirmed_by": str(
            consensus.get("human_confirmed_by") or ""
        ),
        "spec_digest": spec_digest,
        "conclusion_digest": _bare_digest(synthesis.get("conclusion_digest")),
        "contract_digest": _bare_digest(synthesis.get("contract_digest")),
    })
    verification_tiers = _verification_tiers(raw_contract, commands)
    compiled_contract = {
        **raw_contract,
        "schema_version": "task-contract.v1",
        "behavior": str(
            prd_body.get("summary")
            or synthesis.get("summary")
            or raw_contract.get("behavior")
            or payload.get("title")
            or ""
        ).strip(),
        "acceptance": str(
            raw_contract.get("acceptance")
            or "All canonical Channel PRD acceptance criteria pass."
        ).strip(),
        "acceptance_criteria": acceptance_criteria,
        "verification": str(commands[0]["command"]),
        "verification_tiers": verification_tiers,
        "validation": validation_with_commands(
            raw_contract.get("validation")
            if isinstance(raw_contract.get("validation"), dict)
            else {},
            commands,
        ),
        "spec_ref": spec_ref,
        "product_contract_ref": source_ref,
        "source_ref": source_ref,
        "source_revision": str(authority.get("prd_revision") or ""),
        "source_mode": "channel_prd",
        "source_title": str(
            prd_body.get("title") or payload.get("title") or ""
        ).strip(),
        "handoff_artifacts": handoff_artifacts,
        "exclusions": exclusions,
        "explicit_non_goals": exclusions,
        "scope": _semantic_paths(raw_contract, semantic, "scope"),
        "affected_files": _semantic_paths(raw_contract, semantic, "affected_files"),
        "shared_files": _semantic_paths(raw_contract, semantic, "shared_files"),
        "exclusive_files": _semantic_paths(raw_contract, semantic, "exclusive_files"),
        "evidence_contract": evidence,
    }
    return {
        **payload,
        "execution_mode": "workflow",
        "contract": compiled_contract,
        "source_artifact": {
            "kind": "channel_prd",
            "ref": source_ref,
            "digest": source_digest,
            "revision": int(authority.get("prd_revision") or 0),
        },
    }


def _thread_consensus(
    channel: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    consensus = channel.get("consensus")
    value = consensus.get(thread_id) if isinstance(consensus, dict) else None
    return dict(value) if isinstance(value, dict) else {}


def _matching_synthesis(
    channel: dict[str, Any],
    thread_id: str,
    consensus: dict[str, Any],
) -> dict[str, Any] | None:
    artifact_ref = str(
        consensus.get("prd_ref") or consensus.get("artifact_ref") or ""
    )
    artifact_digest = _bare_digest(
        consensus.get("prd_digest") or consensus.get("artifact_digest")
    )
    return next(
        (
            dict(item)
            for item in reversed(channel.get("syntheses") or [])
            if isinstance(item, dict)
            and str(item.get("thread_id") or "main") == thread_id
            and str(item.get("prd_ref") or item.get("artifact_ref") or "")
            == artifact_ref
            and _bare_digest(
                item.get("prd_digest") or item.get("artifact_digest")
            ) == artifact_digest
        ),
        None,
    )


def _hydrate_json(
    state_dir: Path,
    *,
    ref: str,
    digest: str,
    label: str,
) -> dict[str, Any]:
    try:
        hydrated = hydrate_sidecar_ref(
            Path(state_dir),
            {"ref": ref, "sha256": digest, "content_type": "application/json"},
            purpose="channel-prd-task-compile",
            actor="kernel",
        )
    except SidecarRefError as exc:
        raise ChannelPrdTaskContractError(f"{label} is invalid: {exc}") from exc
    if not isinstance(hydrated.payload, dict):
        raise ChannelPrdTaskContractError(f"{label} must contain a JSON object")
    return dict(hydrated.payload)


def _require_sidecar_identity(
    payload: dict[str, Any],
    *,
    authority: dict[str, Any],
    label: str,
) -> None:
    expected = (
        str(authority.get("channel_id") or ""),
        str(authority.get("thread_id") or "main"),
        int(authority.get("prd_revision") or 0),
    )
    observed = (
        str(payload.get("channel_id") or ""),
        str(payload.get("thread_id") or "main"),
        int(payload.get("revision") or 0),
    )
    if observed != expected:
        raise ChannelPrdTaskContractError(
            f"{label} identity is stale: expected {expected!r}, got {observed!r}"
        )


def _verify_state_artifact(
    state_dir: Path,
    *,
    ref: str,
    digest: str,
    label: str,
) -> None:
    rel = PurePosixPath(str(ref or "").strip())
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise ChannelPrdTaskContractError(f"{label} ref is not a safe relative path")
    root = Path(state_dir).resolve()
    path = (root / Path(rel.as_posix())).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ChannelPrdTaskContractError(f"{label} ref escapes state dir") from exc
    if not path.is_file():
        raise ChannelPrdTaskContractError(f"{label} ref is missing: {ref}")
    observed = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != digest:
        raise ChannelPrdTaskContractError(
            f"{label} digest mismatch: expected {digest}, got {observed}"
        )


def _verification_commands(
    contract: dict[str, Any],
    semantic: dict[str, Any],
    prd_body: dict[str, Any],
) -> list[dict[str, Any]]:
    validation = (
        contract.get("validation")
        if isinstance(contract.get("validation"), dict)
        else {}
    )
    semantic_raw = _canonical_semantic_verification_commands(
        semantic.get("verification_commands")
    )
    raw: Any = semantic_raw
    command_validation = {} if semantic_raw else validation
    if not raw:
        raw = contract.get("verification")
    if not raw:
        raw = _extract_verification_commands(str(prd_body.get("markdown") or ""))
    try:
        return normalize_verification_commands(raw, validation=command_validation)
    except VerificationCommandError as exc:
        raise ChannelPrdTaskContractError(str(exc)) from exc


def _canonical_acceptance_criteria(value: object) -> list[Any]:
    source = value if isinstance(value, list) else []
    criteria: list[Any] = []
    for item in source:
        if not isinstance(item, dict):
            criteria.append(item)
            continue
        row = dict(item)
        command_ids = _string_items(
            row.get("verification_command_ids")
            or row.get("verification_command_refs")
        )
        if command_ids:
            row["verification_command_ids"] = command_ids
        criteria.append(row)
    return criteria


def _canonical_semantic_verification_commands(value: object) -> list[Any]:
    source = value if isinstance(value, list) else []
    commands: list[Any] = []
    for item in source:
        if not isinstance(item, dict):
            commands.append(item)
            continue
        row = dict(item)
        acceptance_ids = _string_items(
            row.get("acceptance_ids")
            or row.get("acceptance_id")
            or row.get("covers")
        )
        if acceptance_ids:
            row["acceptance_ids"] = acceptance_ids
        commands.append(row)
    return commands


def _verification_tiers(
    contract: dict[str, Any],
    commands: list[dict[str, Any]],
) -> list[str]:
    valid = {"static", "runtime", "e2e", "manual_evidence"}
    declared = [
        value
        for value in _string_items(contract.get("verification_tiers"))
        if value in valid
    ]
    if declared:
        return _dedupe_strings(declared)
    inferred = [
        "e2e" if str(item.get("tier") or "") in {"e2e", "real_e2e"}
        else "runtime"
        for item in commands
    ]
    return _dedupe_strings(inferred) or ["runtime"]


def _semantic_paths(
    contract: dict[str, Any],
    semantic: dict[str, Any],
    key: str,
) -> list[str]:
    return _dedupe_strings([
        *_string_items(semantic.get(key)),
        *_string_items(contract.get(key)),
    ])


def _string_items(value: object) -> list[str]:
    source = value if isinstance(value, list) else []
    return [str(item).strip() for item in source if str(item).strip()]


def _dedupe_strings(values: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in values
            if str(value).strip()
        )
    )


def _bare_digest(value: object) -> str:
    return str(value or "").strip().removeprefix("sha256:")


__all__ = [
    "ChannelPrdTaskContractError",
    "compile_channel_prd_task_payload",
]
