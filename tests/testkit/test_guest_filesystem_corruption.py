"""Contracts for the guest-filesystem-corruption method boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.testkit.guest_filesystem_corruption import (
    FAULT_MODEL,
    SCHEMA_VERSION,
    build_guest_filesystem_corruption_plan,
    validate_guest_filesystem_corruption_evidence,
)


def _plan(tmp_path: Path) -> dict:
    return build_guest_filesystem_corruption_plan(
        run_root=str(tmp_path / "run"),
        overlay_path=str(tmp_path / "run" / "overlay.qcow2"),
        base_image_path=str(tmp_path / "base.img"),
        guest_target_path="/var/lib/zaofu/events.jsonl",
    )


def _evidence(plan: dict) -> dict:
    digest_a = "a" * 64
    digest_b = "b" * 64
    return {
        "fault_model": FAULT_MODEL,
        "injection_method": plan["injection_method"],
        "overlay_path": plan["overlay_path"],
        "base_image_path": plan["base_image_path"],
        "base_image_readonly": True,
        "disposable_overlay": True,
        "network_disabled": True,
        "host_shared_writable_paths": [],
        "same_overlay_reused": True,
        "cleanup_ok": True,
        "source_verified": True,
        "base_image_sha256": digest_a,
        "overlay_before_sha256": digest_a,
        "overlay_after_sha256": digest_b,
        "durable_prefix_sha256": digest_a,
        "source_sha256": digest_b,
        "guest_nonce": "nonce-1",
        "boot_id_before": "boot-1",
        "recovery_boot_id": "boot-2",
        "corruption_injection": {
            "confirmed": True,
            "guest_target_path": plan["guest_target_path"],
            "target_before_sha256": digest_a,
            "target_after_sha256": digest_b,
        },
    }


def test_plan_is_isolated_and_remains_open(tmp_path: Path):
    plan = _plan(tmp_path)
    assert plan["schema_version"] == SCHEMA_VERSION
    assert plan["status"] == "method_ready_open"
    assert plan["execution_authorized"] is False
    assert plan["disposable_overlay"] is True
    assert plan["network_disabled"] is True
    assert plan["host_shared_writable_paths"] == []


@pytest.mark.parametrize("mutation", [
    "shared_write", "network", "non_disposable", "non_readonly", "overlay_escape", "bad_method",
])
def test_plan_rejects_unsafe_execution_shape(tmp_path: Path, mutation: str):
    kwargs = {
        "run_root": str(tmp_path / "run"),
        "overlay_path": str(tmp_path / "run" / "overlay.qcow2"),
        "base_image_path": str(tmp_path / "base.img"),
        "guest_target_path": "/var/lib/zaofu/events.jsonl",
    }
    if mutation == "shared_write":
        kwargs["host_shared_writable_paths"] = ("/tmp/shared",)
    elif mutation == "network":
        kwargs["network_disabled"] = False
    elif mutation == "non_disposable":
        kwargs["disposable_overlay"] = False
    elif mutation == "non_readonly":
        kwargs["base_image_readonly"] = False
    elif mutation == "overlay_escape":
        kwargs["overlay_path"] = str(tmp_path / "outside.qcow2")
    elif mutation == "bad_method":
        kwargs["injection_method"] = "qemu-sigkill"
    with pytest.raises(ValueError):
        build_guest_filesystem_corruption_plan(**kwargs)


def test_complete_observation_is_still_open_until_host_model_is_promoted(tmp_path: Path):
    plan = _plan(tmp_path)
    result = validate_guest_filesystem_corruption_evidence(plan, _evidence(plan))
    assert result == {
        "status": "evidence_complete_model_open",
        "fault_model": FAULT_MODEL,
        "coverage_status": "open",
        "execution_authorized": False,
        "scope": "exact guest filesystem corruption model only; no cross-model inference",
    }


@pytest.mark.parametrize("mutation", [
    "wrong_model", "same_boot", "not_confirmed", "duplicate_target", "shared_write",
])
def test_evidence_rejects_shortcuts(tmp_path: Path, mutation: str):
    plan = _plan(tmp_path)
    evidence = _evidence(plan)
    if mutation == "wrong_model":
        evidence["fault_model"] = "owned-qemu-sigkill"
    elif mutation == "same_boot":
        evidence["recovery_boot_id"] = evidence["boot_id_before"]
    elif mutation == "not_confirmed":
        evidence["corruption_injection"]["confirmed"] = False
    elif mutation == "duplicate_target":
        evidence["corruption_injection"]["target_after_sha256"] = evidence["corruption_injection"]["target_before_sha256"]
    else:
        evidence["host_shared_writable_paths"] = ["/tmp/shared"]
    with pytest.raises(ValueError):
        validate_guest_filesystem_corruption_evidence(plan, evidence)
