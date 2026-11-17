"""Consumer-side planning for the authorized self-repair loop (backlog 0820, block B last mile).

The autoresearch reactor emits ``autoresearch.repair.dispatch_requested`` (gated
by ``ZF_AUTORESEARCH_AUTO_REPAIR=authorized`` + a per-fingerprint cap). The
repair targets the HARNESS's own code (``src/zf``), so it must run in the ZAOFU
repo — not the project worktree the orchestrator is driving. This module + the
``zf self-repair`` CLI are that zaofu-side consumer: prepare an isolated zaofu
worktree + a briefing pointing the agent at the ``zf-self-repair`` skill; the
skill-equipped agent then runs the tracked playbook (backlog → fix → verify →
done). Pure functions here; the CLI does the git worktree + agent spawn.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref

DISPATCH_REQUESTED = "autoresearch.repair.dispatch_requested"
DISPATCHED = "autoresearch.repair.dispatched"
RUN_MANAGER_ACCEPTED = "run.manager.repair.accepted"
REPAIR_CONTRACT_SCHEMA = "self-repair.contract.v1"


class RepairContractError(ValueError):
    """An immutable self-repair contract is missing or no longer matches."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RepairRequest:
    fingerprint: str
    attempt: int
    candidate_id: str
    candidate_path: str
    repair_task_payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = ""


def _key(payload: dict[str, Any]) -> tuple[str, int]:
    try:
        attempt = int(payload.get("attempt") or 0)
    except (TypeError, ValueError):
        attempt = 0
    return str(payload.get("fingerprint") or ""), attempt


def pending_repair_dispatches(
    events,
    *,
    request_types: tuple[str, ...] = (DISPATCH_REQUESTED,),
) -> list[RepairRequest]:
    """Repair request events that don't yet have a matching dispatched."""
    accepted_request_types = set(request_types)
    dispatched: set[tuple[str, int]] = set()
    requests: dict[tuple[str, int], RepairRequest] = {}
    for event in events:
        etype = getattr(event, "type", "")
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        key = _key(payload)
        if etype == DISPATCHED:
            dispatched.add(key)
        elif etype in accepted_request_types:
            repair = payload.get("repair_task_payload")
            requests[key] = RepairRequest(
                fingerprint=key[0],
                attempt=key[1],
                candidate_id=str(payload.get("candidate_id") or ""),
                candidate_path=str(payload.get("candidate_path") or ""),
                repair_task_payload=repair if isinstance(repair, dict) else {},
                event_id=str(getattr(event, "id", "")),
            )
    return [req for key, req in requests.items() if key not in dispatched]


def repair_branch_name(req: RepairRequest) -> str:
    short = (req.fingerprint.replace(":", "-").replace("/", "-") or "unknown")[:48]
    return f"self-repair/{short}-a{req.attempt}"


def build_repair_contract(
    req: RepairRequest,
    *,
    base_commit: str,
) -> dict[str, Any]:
    """Freeze the diagnosis-to-closeout contract for one repair attempt."""

    task_payload = req.repair_task_payload
    contract = task_payload.get("contract")
    contract = contract if isinstance(contract, Mapping) else {}
    evidence = contract.get("evidence_contract")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    scope = _strings(contract.get("scope")) or ["src/zf/**", "tests/**"]
    continuation = task_payload.get("continuation")
    continuation = continuation if isinstance(continuation, Mapping) else {}
    reproduction = contract.get("reproduction") or task_payload.get(
        "reproduction"
    )
    if reproduction in (None, "", [], {}):
        reproduction = {
            "candidate_path": req.candidate_path,
            "diagnosis_evidence_paths": _strings(
                evidence.get("diagnosis_evidence_paths")
            ),
            "evidence_paths": _strings(evidence.get("evidence_paths")),
            "source_event_id": str(evidence.get("source_event_id") or ""),
        }
    return {
        "schema_version": REPAIR_CONTRACT_SCHEMA,
        "fingerprint": req.fingerprint,
        "attempt": req.attempt,
        "candidate_id": req.candidate_id,
        "candidate_path": req.candidate_path,
        "source_event_id": req.event_id,
        "repair_task_id": str(task_payload.get("task_id") or ""),
        "title": str(task_payload.get("title") or ""),
        "base_commit": str(base_commit or ""),
        "behavior": str(contract.get("behavior") or task_payload.get("title") or ""),
        "scope": scope,
        "governance_scope": ["tasks/**", "backlogs/**"],
        "reproduction": reproduction,
        "verification_instruction": str(contract.get("verification") or ""),
        "verification_commands": _verification_commands(contract, evidence),
        "acceptance": contract.get("acceptance") or "",
        "continuation": dict(continuation),
    }


def write_repair_contract(
    state_dir: Path,
    req: RepairRequest,
    *,
    base_commit: str,
    created_by: str,
    source_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    contract = build_repair_contract(req, base_commit=base_commit)
    if source_root is not None:
        from zf.runtime.repair_checkout_contract import bind_checkout_contract

        contract = bind_checkout_contract(contract, source_root)
    descriptor = write_immutable_json_sidecar(
        Path(state_dir),
        contract,
        root="self-repair/contracts",
        kind="self_repair_contract",
        schema_version=REPAIR_CONTRACT_SCHEMA,
        created_by=created_by,
        source_event_id=req.event_id,
    )
    return contract, descriptor


def hydrate_repair_contract(
    state_dir: Path,
    descriptor: Mapping[str, Any] | None,
    *,
    expected_digest: str = "",
) -> dict[str, Any]:
    if not isinstance(descriptor, Mapping) or not descriptor:
        raise RepairContractError(
            "repair_contract_missing",
            "self-repair contract descriptor is required",
        )
    digest = str(descriptor.get("sha256") or "")
    if expected_digest and digest != expected_digest:
        raise RepairContractError(
            "repair_contract_digest_mismatch",
            "self-repair contract event digest does not match its descriptor",
        )
    try:
        hydrated = hydrate_sidecar_ref(
            Path(state_dir),
            dict(descriptor),
            purpose="self_repair_contract",
            actor="run-manager",
        )
    except SidecarRefError as exc:
        raise RepairContractError(
            f"repair_contract_{exc.code}",
            str(exc),
        ) from exc
    payload = hydrated.payload
    if not isinstance(payload, dict) or payload.get("schema_version") != REPAIR_CONTRACT_SCHEMA:
        raise RepairContractError(
            "repair_contract_schema_invalid",
            "self-repair contract sidecar has an unsupported schema",
        )
    return payload


def assert_repair_contract_binding(
    state_dir: Path,
    event_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Hydrate a contract and prove its immutable identity matches the event."""

    descriptor = event_payload.get("repair_contract_ref")
    digest = str(event_payload.get("repair_contract_digest") or "")
    contract = hydrate_repair_contract(
        state_dir,
        descriptor if isinstance(descriptor, Mapping) else None,
        expected_digest=digest,
    )
    expected = {
        "fingerprint": str(event_payload.get("fingerprint") or ""),
        "candidate_id": str(event_payload.get("candidate_id") or ""),
        "base_commit": str(event_payload.get("base_commit") or ""),
    }
    try:
        expected_attempt = int(event_payload.get("attempt") or 0)
    except (TypeError, ValueError):
        expected_attempt = 0
    if int(contract.get("attempt") or 0) != expected_attempt:
        raise RepairContractError(
            "repair_contract_identity_mismatch",
            "self-repair contract attempt does not match the event",
        )
    for field, value in expected.items():
        if str(contract.get(field) or "") != value:
            raise RepairContractError(
                "repair_contract_identity_mismatch",
                f"self-repair contract {field} does not match the event",
            )
    return contract


def validate_repair_contract_action(
    state_dir: Path,
    action_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove a validation/apply action still implements the frozen contract."""

    contract = assert_repair_contract_binding(state_dir, action_payload)
    plan = action_payload.get("verification_plan")
    plan = plan if isinstance(plan, list) else []
    plan_commands = {
        str(row.get("command") or "").strip()
        for row in plan
        if isinstance(row, Mapping) and str(row.get("command") or "").strip()
    }
    missing_commands = [
        command
        for command in _strings(contract.get("verification_commands"))
        if command not in plan_commands
    ]
    if missing_commands:
        raise RepairContractError(
            "repair_contract_verification_drift",
            "repair verification plan dropped immutable contract commands",
        )
    base_commit = str(contract.get("base_commit") or "").strip()
    impact_commands = {
        f"python scripts/dev-verify.py plan --base {base_commit}",
        f"python scripts/dev-verify.py run --base {base_commit}",
    }
    if not base_commit or not impact_commands.issubset(plan_commands):
        raise RepairContractError(
            "repair_contract_impact_closure_missing",
            "repair verification plan lacks dev-verify impact closure",
        )
    changed_files = _strings(action_payload.get("changed_files"))
    allowed_patterns = [
        *_strings(contract.get("scope")),
        *_strings(contract.get("governance_scope")),
    ]
    outside_scope = [
        path for path in changed_files
        if not any(fnmatch.fnmatchcase(path, pattern) for pattern in allowed_patterns)
    ]
    if outside_scope:
        raise RepairContractError(
            "repair_contract_scope_drift",
            "repair changed files outside immutable contract scope: "
            + ", ".join(outside_scope[:5]),
        )
    requested_continuation = contract.get("continuation")
    requested_continuation = (
        requested_continuation
        if isinstance(requested_continuation, Mapping)
        else {}
    )
    actual_continuation = action_payload.get("continuation")
    actual_continuation = (
        actual_continuation
        if isinstance(actual_continuation, Mapping)
        else {}
    )
    if any(
        actual_continuation.get(key) != value
        for key, value in requested_continuation.items()
    ):
        raise RepairContractError(
            "repair_contract_continuation_drift",
            "repair continuation no longer matches the immutable contract",
        )
    return contract


def build_repair_briefing(
    req: RepairRequest,
    *,
    repair_contract: Mapping[str, Any] | None = None,
    repair_contract_ref: Mapping[str, Any] | None = None,
    state_dir: Path | None = None,
) -> str:
    contract = (
        dict(repair_contract)
        if isinstance(repair_contract, Mapping)
        else build_repair_contract(req, base_commit="")
    )
    scope = _strings(contract.get("scope"))
    commands = _strings(contract.get("verification_commands"))
    verification = " && ".join(commands) or str(
        contract.get("verification_instruction")
        or "run the focused pytest target + relevant regression"
    )
    hypothesis = str(contract.get("behavior") or "")
    contract_digest = str((repair_contract_ref or {}).get("sha256") or "")
    contract_ref = str((repair_contract_ref or {}).get("ref") or "")
    if state_dir is not None and contract_ref:
        contract_ref = str(state_dir / contract_ref)
    return (
        f"# Authorized self-repair — {req.candidate_id}\n\n"
        "Follow the **zf-self-repair** skill exactly: write backlog → fix → "
        "verify → done. Read skills/zf-self-repair/SKILL.md in this isolated checkout.\n"
        "Scope paths and verification cwd refer to THIS checkout, never the source harness.\n"
        "The bound contract's source_event_id identifies the kernel dispatch authorization; "
        "verify that grant in the parent ZF_STATE_DIR ledger. Do not infer authorization "
        "from this checkout's copied zf.yaml or enable global bypass flags.\n\n"
        f"- fingerprint: {req.fingerprint}  (attempt {req.attempt})\n"
        f"- hypothesis: {hypothesis}\n"
        f"- scope (do NOT exceed): {', '.join(str(s) for s in scope)}\n"
        f"- verification (HARD gate — never merge on red): {verification}\n"
        f"- candidate artifact: {req.candidate_path}\n\n"
        f"- immutable repair contract: {contract_ref} ({contract_digest})\n\n"
        "Steps: ① write the backlog FIRST (`> 状态: active`) ② make the surgical "
        "fix within scope ③ run verification ④ on GREEN commit + mark the backlog "
        "`done` with the commit hash. If sandbox denies Git metadata, leave the verified patch "
        "uncommitted and report the exact denial; the separately authorized runtime publisher "
        "may commit it after exit. Never disable sandbox or request broader filesystem access. "
        "On RED or over the attempt cap leave it "
        "un-merged, mark `blocked`, escalate. Never touch runtime truth "
        "(events.jsonl/kanban.json/...) or credentials. Do not push to any remote.\n"
    )


def dispatched_event_payload(
    req: RepairRequest,
    *,
    branch: str,
    worktree: str,
    briefing_path: str,
    base_commit: str = "",
    repair_contract: Mapping[str, Any] | None = None,
    repair_contract_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    continuation = (
        repair_contract.get("continuation")
        if isinstance(repair_contract, Mapping)
        else req.repair_task_payload.get("continuation")
    )
    return {
        "fingerprint": req.fingerprint,
        "attempt": req.attempt,
        "candidate_id": req.candidate_id,
        "branch": branch,
        "worktree": worktree,
        "briefing_path": briefing_path,
        "base_commit": str(base_commit or ""),
        "continuation": continuation if isinstance(continuation, dict) else {},
        "repair_contract_ref": dict(repair_contract_ref or {}),
        "repair_contract_digest": str(
            (repair_contract_ref or {}).get("sha256") or ""
        ),
        "skill": "zf-self-repair",
    }


def _verification_commands(
    contract: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> list[str]:
    values: list[Any] = []
    values.extend(_items(contract.get("verification_commands")))
    validation = contract.get("validation")
    if isinstance(validation, Mapping):
        for row in _items(validation.get("commands")):
            values.append(row.get("command") if isinstance(row, Mapping) else row)
    for row in _items(evidence.get("success_criteria")):
        if isinstance(row, Mapping) and str(row.get("kind") or "") == "command_passed":
            values.append(row.get("command"))
    commands: list[str] = []
    for value in values:
        command = str(value or "").strip()
        if command and command not in commands:
            commands.append(command)
    return commands


def _items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _strings(value: Any) -> list[str]:
    return [str(item) for item in _items(value) if str(item).strip()]
