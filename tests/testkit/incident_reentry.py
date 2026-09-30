"""Fail-closed admission for reproducing an open incident signature.

This is test infrastructure only.  It prevents a normal green control run or
an old report from being used as evidence for a historical root-cause item.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


@dataclass(frozen=True)
class IncidentProfile:
    issue_id: str
    signature: str
    required_fields: tuple[str, ...]


@dataclass(frozen=True)
class IncidentReentryEvidence:
    issue_id: str
    signature: str
    source_ref: str
    source_sha256: str
    observed_at_ns: int
    fields: Mapping[str, Any]
    new_observation: bool = True
    failure_observed: bool = True
    normal_control_only: bool = False


K530_WATCHDOG_PROFILE = IncidentProfile(
    issue_id="K530-watchdog-root-cause",
    signature="worker.first_exit.no_liveness_evidence",
    required_fields=(
        "event_id", "worker_role", "failure_reason", "pid", "started_at_ns", "snapshot_ref",
    ),
)

K584_HANDSHAKE_PROFILE = IncidentProfile(
    issue_id="K584-R",
    signature="provider_restart.shell_handshake_timeout",
    required_fields=(
        "failure_class", "current_command", "nonce", "capture_ref", "diagnostics_ref",
    ),
)


def admit_incident_reentry(
    profile: IncidentProfile,
    evidence: IncidentReentryEvidence,
) -> dict[str, Any]:
    """Admit only a new, same-signature failure with complete diagnostics."""
    if evidence.issue_id != profile.issue_id:
        raise ValueError("incident evidence targets a different issue")
    if evidence.signature != profile.signature:
        raise ValueError("incident signature does not match the re-entry profile")
    if not evidence.new_observation:
        raise ValueError("historical evidence cannot reopen an incident")
    if not evidence.failure_observed or evidence.normal_control_only:
        raise ValueError("a normal control run cannot reopen an incident")
    if not evidence.source_ref.strip():
        raise ValueError("incident source reference is required")
    if not re.fullmatch(r"[0-9a-f]{64}", evidence.source_sha256):
        raise ValueError("incident source SHA-256 is invalid")
    if type(evidence.observed_at_ns) is not int or evidence.observed_at_ns <= 0:
        raise ValueError("incident observation timestamp is invalid")
    missing = [name for name in profile.required_fields if not evidence.fields.get(name)]
    if missing:
        raise ValueError(f"incident diagnostics missing: {','.join(missing)}")
    return {
        "status": "admitted",
        "issue_id": profile.issue_id,
        "signature": profile.signature,
        "source_ref": evidence.source_ref,
        "source_sha256": evidence.source_sha256,
        "required_fields": list(profile.required_fields),
        "scope": "admission only; caller owns the bounded reproduction and final verdict",
    }
