"""Fail-closed contracts for physical-host-power-loss."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.testkit.physical_host_power_loss import (
    FAULT_MODEL,
    SCHEMA_VERSION,
    build_physical_host_power_loss_plan,
    validate_physical_host_power_loss_evidence,
)


def _plan(tmp_path: Path) -> dict:
    return build_physical_host_power_loss_plan(
        run_root=str(tmp_path / "run"),
        host_identity="dedicated-host-01",
        controller_kind="dedicated-ipmi",
        approval_ref="approval/2026-09-29-power-loss",
        overlay_path=str(tmp_path / "run" / "overlay.qcow2"),
        base_image_path=str(tmp_path / "base.img"),
    )


def _evidence(plan: dict) -> dict:
    a, b = "a" * 64, "b" * 64
    return {
        "fault_model": FAULT_MODEL,
        "host_identity": plan["host_identity"],
        "controller_kind": plan["controller_kind"],
        "approval_ref": plan["approval_ref"],
        "overlay_path": plan["overlay_path"],
        "base_image_path": plan["base_image_path"],
        "base_image_readonly": True,
        "power_event_confirmed": True,
        "substitute_fault_model": None,
        "dedicated_host": True,
        "guest_network_disabled": True,
        "same_overlay_reused": True,
        "cleanup_ok": True,
        "source_verified": True,
        "base_image_sha256": a,
        "overlay_before_sha256": a,
        "overlay_after_sha256": b,
        "durable_prefix_sha256": a,
        "source_sha256": b,
        "machine_boot_id_before": "machine-boot-1",
        "recovery_boot_id": "machine-boot-2",
        "power_event_id": "event-1",
        "host_power_event": {
            "event_id": "event-1",
            "controller_kind": plan["controller_kind"],
            "requested_at_ns": 100,
            "observed_at_ns": 200,
        },
    }


def test_plan_is_explicitly_isolated_but_not_executable(tmp_path: Path):
    plan = _plan(tmp_path)
    assert plan["schema_version"] == SCHEMA_VERSION
    assert plan["status"] == "destructive_model_open"
    assert plan["execution_authorized"] is False
    assert plan["dedicated_host"] and plan["guest_network_disabled"]
    assert plan["disposable_overlay"]


@pytest.mark.parametrize("mutation", [
    "shared_host", "network", "non_disposable", "non_readonly", "bad_controller", "missing_approval",
])
def test_plan_rejects_unsafe_or_unauthorized_shape(tmp_path: Path, mutation: str):
    kwargs = {
        "run_root": str(tmp_path / "run"),
        "host_identity": "dedicated-host-01",
        "controller_kind": "dedicated-ipmi",
        "approval_ref": "approval/ref",
        "overlay_path": str(tmp_path / "run" / "overlay.qcow2"),
        "base_image_path": str(tmp_path / "base.img"),
    }
    if mutation == "shared_host":
        kwargs["dedicated_host"] = False
    elif mutation == "network":
        kwargs["guest_network_disabled"] = False
    elif mutation == "non_disposable":
        kwargs["disposable_overlay"] = False
    elif mutation == "non_readonly":
        kwargs["base_image_readonly"] = False
    elif mutation == "bad_controller":
        kwargs["controller_kind"] = "local-shell"
    else:
        kwargs["approval_ref"] = " "
    with pytest.raises(ValueError):
        build_physical_host_power_loss_plan(**kwargs)


def test_complete_receipt_remains_open_until_dedicated_host_tier_is_promoted(tmp_path: Path):
    plan = _plan(tmp_path)
    assert validate_physical_host_power_loss_evidence(plan, _evidence(plan)) == {
        "status": "evidence_complete_model_open",
        "fault_model": FAULT_MODEL,
        "coverage_status": "open",
        "execution_authorized": False,
        "scope": "exact physical host power-loss model only; no cross-model inference",
    }


@pytest.mark.parametrize("mutation", [
    "substitute", "wrong_host", "unconfirmed", "same_boot", "bad_event",
])
def test_evidence_rejects_cross_model_shortcuts(tmp_path: Path, mutation: str):
    plan = _plan(tmp_path)
    evidence = _evidence(plan)
    if mutation == "substitute":
        evidence["substitute_fault_model"] = "owned-qemu-sigkill"
    elif mutation == "wrong_host":
        evidence["host_identity"] = "other-host"
    elif mutation == "unconfirmed":
        evidence["power_event_confirmed"] = False
    elif mutation == "same_boot":
        evidence["recovery_boot_id"] = evidence["machine_boot_id_before"]
    else:
        evidence["host_power_event"]["controller_kind"] = "dedicated-pdu"
    with pytest.raises(ValueError):
        validate_physical_host_power_loss_evidence(plan, evidence)
