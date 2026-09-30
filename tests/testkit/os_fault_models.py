"""Explicit OS-fault coverage matrix and fail-closed result admission."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


@dataclass(frozen=True)
class OsFaultModel:
    model_id: str
    coverage_status: str
    destructive: bool
    required_evidence: tuple[str, ...]


OS_FAULT_MODELS = {
    "owned-qemu-sigkill": OsFaultModel(
        "owned-qemu-sigkill", "covered", False,
        ("fault_model", "qemu_pid", "before_disk_sha256", "after_disk_sha256", "cleanup_ok"),
    ),
    "guest-kernel-panic": OsFaultModel(
        "guest-kernel-panic", "covered", False,
        ("fault_model", "guest_nonce", "serial_panic_marker", "boot_id_before", "boot_id_after", "cleanup_ok"),
    ),
    "physical-host-power-loss": OsFaultModel(
        "physical-host-power-loss", "open", True,
        ("fault_model", "host_power_event", "durable_prefix_sha256", "recovery_boot_id", "cleanup_ok"),
    ),
    "guest-filesystem-corruption": OsFaultModel(
        "guest-filesystem-corruption", "open", True,
        ("fault_model", "corruption_injection", "durable_prefix_sha256", "recovery_boot_id", "cleanup_ok"),
    ),
}


def admit_os_fault_result(model_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """Admit only evidence for the exact model; never infer another model."""
    try:
        model = OS_FAULT_MODELS[model_id]
    except KeyError as exc:
        raise ValueError("unknown OS fault model") from exc
    if result.get("fault_model") != model_id:
        raise ValueError("fault model identity mismatch")
    if model.coverage_status != "covered":
        raise ValueError("OS fault model remains open and is not admitted")
    missing = [field for field in model.required_evidence if not result.get(field)]
    if missing:
        raise ValueError(f"OS fault evidence missing: {','.join(missing)}")
    for field in ("before_disk_sha256", "after_disk_sha256"):
        if field in model.required_evidence and not re.fullmatch(r"[0-9a-f]{64}", str(result[field])):
            raise ValueError(f"invalid {field}")
    return {
        "status": "admitted",
        "model_id": model_id,
        "coverage_status": model.coverage_status,
        "destructive": model.destructive,
        "scope": "exact OS fault model only; no cross-model inference",
    }
