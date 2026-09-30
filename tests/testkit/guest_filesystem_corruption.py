"""Fail-closed method contract for the guest-filesystem-corruption model.

This module deliberately does not inject corruption or start QEMU.  It fixes
the safety boundary and the evidence shape for a future explicit host tier:
one disposable qcow2 overlay, no guest network, no host writable share, and a
same-overlay recovery boot.  A complete observation remains ``open`` until
the OS model is explicitly promoted by an audited host result.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "zf-testkit-guest-filesystem-corruption-method.v1"
FAULT_MODEL = "guest-filesystem-corruption"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INJECTION_METHODS = frozenset({
    "guest-private-corruption-fixture",
    "private-overlay-block-corruption",
})


@dataclass(frozen=True)
class GuestFilesystemCorruptionPlan:
    """A bounded, disposable plan; it is not an execution receipt."""

    schema_version: str
    fault_model: str
    run_root: str
    overlay_path: str
    base_image_path: str
    base_image_readonly: bool
    injection_method: str
    guest_target_path: str
    disposable_overlay: bool
    network_disabled: bool
    host_shared_writable_paths: tuple[str, ...]
    coverage_status: str = "open"


def _absolute(path: str, *, name: str) -> Path:
    value = Path(path)
    if not value.is_absolute():
        raise ValueError(f"{name} must be absolute")
    return value.resolve()


def build_guest_filesystem_corruption_plan(
    *,
    run_root: str,
    overlay_path: str,
    base_image_path: str,
    base_image_readonly: bool = True,
    guest_target_path: str,
    injection_method: str = "guest-private-corruption-fixture",
    network_disabled: bool = True,
    disposable_overlay: bool = True,
    host_shared_writable_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Construct and validate the only plan shape allowed by this contract."""

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
    if not guest_target_path.startswith("/"):
        raise ValueError("guest_target_path must be an absolute guest path")
    if injection_method not in _INJECTION_METHODS:
        raise ValueError("unsupported corruption injection method")
    if not disposable_overlay:
        raise ValueError("guest filesystem corruption requires a disposable overlay")
    if not network_disabled:
        raise ValueError("guest filesystem corruption requires network isolation")
    if host_shared_writable_paths:
        raise ValueError("host writable shares are forbidden")
    plan = GuestFilesystemCorruptionPlan(
        schema_version=SCHEMA_VERSION,
        fault_model=FAULT_MODEL,
        run_root=str(root),
        overlay_path=str(overlay),
        base_image_path=str(base),
        base_image_readonly=True,
        injection_method=injection_method,
        guest_target_path=guest_target_path,
        disposable_overlay=True,
        network_disabled=True,
        host_shared_writable_paths=(),
    )
    payload = asdict(plan)
    # Evidence is JSON-facing; keep the dataclass tuple internal but expose a
    # stable array so the plan and its later receipt use the same shape.
    payload["host_shared_writable_paths"] = list(plan.host_shared_writable_paths)
    return {
        **payload,
        "status": "method_ready_open",
        "execution_authorized": False,
        "scope": "method contract only; no QEMU or corruption injection performed",
    }


def _digest(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


def validate_guest_filesystem_corruption_evidence(
    plan: Mapping[str, Any], evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a complete observation without turning the open model green."""

    if plan.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unknown guest filesystem corruption plan schema")
    if plan.get("fault_model") != FAULT_MODEL:
        raise ValueError("plan fault model mismatch")
    if plan.get("status") != "method_ready_open":
        raise ValueError("plan must remain method_ready_open")
    if evidence.get("fault_model") != FAULT_MODEL:
        raise ValueError("evidence fault model mismatch")
    if evidence.get("injection_method") != plan.get("injection_method"):
        raise ValueError("evidence injection method mismatch")
    if evidence.get("overlay_path") != plan.get("overlay_path"):
        raise ValueError("evidence overlay identity mismatch")
    if evidence.get("base_image_path") != plan.get("base_image_path"):
        raise ValueError("base image identity mismatch")
    if evidence.get("base_image_readonly") is not True:
        raise ValueError("evidence must prove a read-only base image")
    if evidence.get("disposable_overlay") is not True:
        raise ValueError("evidence must prove a disposable overlay")
    if evidence.get("network_disabled") is not True:
        raise ValueError("evidence must prove network isolation")
    if evidence.get("host_shared_writable_paths") != []:
        raise ValueError("evidence contains a host writable share")
    if evidence.get("same_overlay_reused") is not True:
        raise ValueError("recovery must reuse the same overlay")
    if evidence.get("cleanup_ok") is not True:
        raise ValueError("cleanup evidence is incomplete")
    if evidence.get("source_verified") is not True:
        raise ValueError("source verification is required")
    for field in (
        "base_image_sha256", "overlay_before_sha256", "overlay_after_sha256",
        "durable_prefix_sha256", "source_sha256",
    ):
        _digest(evidence.get(field), field=field)
    for field in ("guest_nonce", "boot_id_before", "recovery_boot_id"):
        value = evidence.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required")
    if evidence["boot_id_before"] == evidence["recovery_boot_id"]:
        raise ValueError("recovery boot identity must change")
    injection = evidence.get("corruption_injection")
    if not isinstance(injection, Mapping):
        raise ValueError("corruption_injection evidence is required")
    if injection.get("confirmed") is not True:
        raise ValueError("corruption injection was not confirmed")
    if injection.get("guest_target_path") != plan.get("guest_target_path"):
        raise ValueError("corruption target path mismatch")
    _digest(injection.get("target_before_sha256"), field="target_before_sha256")
    _digest(injection.get("target_after_sha256"), field="target_after_sha256")
    if injection["target_before_sha256"] == injection["target_after_sha256"]:
        raise ValueError("corruption injection did not change the target")
    return {
        "status": "evidence_complete_model_open",
        "fault_model": FAULT_MODEL,
        "coverage_status": "open",
        "execution_authorized": False,
        "scope": "exact guest filesystem corruption model only; no cross-model inference",
    }
