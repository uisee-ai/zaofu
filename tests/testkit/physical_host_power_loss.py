"""Safety/evidence contract for the physical-host-power-loss model.

No power action is performed here.  The real model is an explicit host tier
that requires a dedicated disposable machine and an out-of-band controller;
process signals, QMP reset, guest panic, and normal shutdown are rejected as
different fault models.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "zf-testkit-physical-host-power-loss-method.v1"
FAULT_MODEL = "physical-host-power-loss"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_CONTROLLERS = frozenset({"dedicated-pdu", "dedicated-ipmi", "dedicated-redfish"})
_FORBIDDEN_SUBSTITUTES = frozenset({
    "owned-qemu-sigkill", "guest-kernel-panic", "qmp-system-reset",
    "guest-shutdown", "qemu-sigterm",
})


@dataclass(frozen=True)
class PhysicalPowerLossPlan:
    schema_version: str
    fault_model: str
    run_root: str
    host_identity: str
    controller_kind: str
    approval_ref: str
    overlay_path: str
    base_image_path: str
    base_image_readonly: bool
    guest_network_disabled: bool
    dedicated_host: bool
    disposable_overlay: bool
    coverage_status: str = "open"


def _absolute(path: str, *, name: str) -> Path:
    value = Path(path)
    if not value.is_absolute():
        raise ValueError(f"{name} must be absolute")
    return value.resolve()


def build_physical_host_power_loss_plan(
    *,
    run_root: str,
    host_identity: str,
    controller_kind: str,
    approval_ref: str,
    overlay_path: str,
    base_image_path: str,
    base_image_readonly: bool = True,
    guest_network_disabled: bool = True,
    dedicated_host: bool = True,
    disposable_overlay: bool = True,
) -> dict[str, Any]:
    """Build a non-executable plan for an explicitly isolated host tier."""

    root = _absolute(run_root, name="run_root")
    overlay = _absolute(overlay_path, name="overlay_path")
    base = _absolute(base_image_path, name="base_image_path")
    if not overlay.is_relative_to(root):
        raise ValueError("overlay_path must stay inside run_root")
    if overlay.suffix != ".qcow2":
        raise ValueError("overlay_path must be a qcow2 disposable overlay")
    if base == overlay:
        raise ValueError("base image and writable overlay must be distinct")
    if not base_image_readonly:
        raise ValueError("base image must be explicitly read-only")
    if not host_identity.strip():
        raise ValueError("dedicated host identity is required")
    if controller_kind not in _ALLOWED_CONTROLLERS:
        raise ValueError("physical power loss requires an approved out-of-band controller")
    if not approval_ref.strip():
        raise ValueError("manual destructive-test approval reference is required")
    if not guest_network_disabled:
        raise ValueError("guest network must be disabled")
    if not dedicated_host:
        raise ValueError("physical power loss requires a dedicated host")
    if not disposable_overlay:
        raise ValueError("physical power loss requires a disposable overlay")
    plan = PhysicalPowerLossPlan(
        schema_version=SCHEMA_VERSION,
        fault_model=FAULT_MODEL,
        run_root=str(root),
        host_identity=host_identity,
        controller_kind=controller_kind,
        approval_ref=approval_ref,
        overlay_path=str(overlay),
        base_image_path=str(base),
        base_image_readonly=True,
        guest_network_disabled=True,
        dedicated_host=True,
        disposable_overlay=True,
    )
    payload = asdict(plan)
    return {
        **payload,
        "status": "destructive_model_open",
        "execution_authorized": False,
        "scope": "method contract only; no power controller call performed",
    }


def _digest(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


def validate_physical_host_power_loss_evidence(
    plan: Mapping[str, Any], evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate an exact power-loss observation while keeping the model open."""

    if plan.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unknown physical power-loss plan schema")
    if plan.get("fault_model") != FAULT_MODEL:
        raise ValueError("plan fault model mismatch")
    if plan.get("status") != "destructive_model_open":
        raise ValueError("plan must remain destructive_model_open")
    if evidence.get("fault_model") != FAULT_MODEL:
        raise ValueError("evidence fault model mismatch")
    if evidence.get("host_identity") != plan.get("host_identity"):
        raise ValueError("host identity mismatch")
    if evidence.get("controller_kind") != plan.get("controller_kind"):
        raise ValueError("controller identity mismatch")
    if evidence.get("approval_ref") != plan.get("approval_ref"):
        raise ValueError("destructive-test approval mismatch")
    if evidence.get("overlay_path") != plan.get("overlay_path"):
        raise ValueError("overlay identity mismatch")
    if evidence.get("base_image_path") != plan.get("base_image_path"):
        raise ValueError("base image identity mismatch")
    if evidence.get("base_image_readonly") is not True:
        raise ValueError("evidence must prove a read-only base image")
    if evidence.get("power_event_confirmed") is not True:
        raise ValueError("out-of-band power event is not confirmed")
    if evidence.get("substitute_fault_model") in _FORBIDDEN_SUBSTITUTES:
        raise ValueError("substitute fault model cannot prove physical power loss")
    if evidence.get("dedicated_host") is not True or evidence.get("guest_network_disabled") is not True:
        raise ValueError("host isolation evidence is incomplete")
    if evidence.get("same_overlay_reused") is not True:
        raise ValueError("recovery must reuse the same overlay")
    if evidence.get("cleanup_ok") is not True or evidence.get("source_verified") is not True:
        raise ValueError("cleanup/source evidence is incomplete")
    for field in (
        "base_image_sha256", "overlay_before_sha256", "overlay_after_sha256",
        "durable_prefix_sha256", "source_sha256",
    ):
        _digest(evidence.get(field), field=field)
    for field in ("machine_boot_id_before", "recovery_boot_id", "power_event_id"):
        value = evidence.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
    if evidence["machine_boot_id_before"] == evidence["recovery_boot_id"]:
        raise ValueError("recovery boot identity must change")
    event = evidence.get("host_power_event")
    if not isinstance(event, Mapping) or event.get("controller_kind") != plan.get("controller_kind"):
        raise ValueError("host power event receipt is incomplete")
    if event.get("event_id") != evidence.get("power_event_id"):
        raise ValueError("host power event identity mismatch")
    if not event.get("requested_at_ns") or not event.get("observed_at_ns"):
        raise ValueError("host power event timestamps are required")
    return {
        "status": "evidence_complete_model_open",
        "fault_model": FAULT_MODEL,
        "coverage_status": "open",
        "execution_authorized": False,
        "scope": "exact physical host power-loss model only; no cross-model inference",
    }
