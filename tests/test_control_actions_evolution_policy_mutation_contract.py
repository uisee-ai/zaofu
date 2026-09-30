"""Mutation-resistant contracts for unattended evolution action policy."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import zf.runtime.control_actions_evolution as evolution_actions_module
from zf.runtime.control_actions_evolution import EvolutionActionsMixin


class _Capabilities:
    def __init__(self, registry: dict[str, Any]) -> None:
        self.registry = registry

    def load(self) -> dict[str, Any]:
        return self.registry


class _Coordinator:
    registry: dict[str, Any] = {"assets": {}}
    calls: list[Path] = []

    def __init__(self, state_dir: Path) -> None:
        self.calls.append(state_dir)
        self.capabilities = _Capabilities(self.registry)


def _service(
    tmp_path: Path,
    *,
    actor: str = "run-manager",
    source: str = "self-evolution",
    enabled: bool = True,
    mode: str = "auto_low_risk",
    auto_asset_kinds: list[str] | None = None,
) -> EvolutionActionsMixin:
    service = EvolutionActionsMixin()
    service.state_dir = tmp_path
    service.actor = actor
    service.source = source
    service.config = SimpleNamespace(runtime=SimpleNamespace(
        evolution=SimpleNamespace(
            enabled=enabled,
            mode=mode,
            auto_asset_kinds=(
                ["memory_entry"] if auto_asset_kinds is None else auto_asset_kinds
            ),
        )
    ))
    return service


def _asset(
    *,
    asset_kind: str = "memory_entry",
    policy_digest: str = "policy-v1",
) -> dict[str, Any]:
    return {
        "asset_kind": asset_kind,
        "activation": {"automation_policy_digest": policy_digest},
    }


def _payload(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "asset_id": "asset-1",
        "version": 2,
        "target_state": "approved",
        "policy_digest": "policy-v1",
    }
    payload.update(updates)
    return payload


@pytest.fixture(autouse=True)
def _fake_coordinator(monkeypatch: pytest.MonkeyPatch) -> None:
    _Coordinator.registry = {"assets": {"asset-1@2": _asset()}}
    _Coordinator.calls = []
    monkeypatch.setattr(evolution_actions_module, "EvolutionCoordinator", _Coordinator)


@pytest.mark.parametrize(
    ("actor", "source"),
    [
        ("web", "self-evolution"),
        ("run-manager", "web"),
        ("", ""),
    ],
)
def test_policy_requires_exact_run_manager_self_evolution_authority(
    tmp_path: Path,
    actor: str,
    source: str,
) -> None:
    service = _service(tmp_path, actor=actor, source=source)

    assert service._evolution_policy_error(_payload(), transition=True) == (
        "unattended evolution actions require the Run Manager authority"
    )
    assert _Coordinator.calls == []


@pytest.mark.parametrize("config_shape", [None, SimpleNamespace(), SimpleNamespace(runtime=None)])
def test_policy_rejects_missing_configuration(
    tmp_path: Path,
    config_shape: object,
) -> None:
    service = _service(tmp_path)
    service.config = config_shape

    assert service._evolution_policy_error(_payload(), transition=True) == (
        "runtime.evolution is disabled"
    )
    assert _Coordinator.calls == []


def test_policy_rejects_explicitly_disabled_configuration(tmp_path: Path) -> None:
    service = _service(tmp_path, enabled=False)

    assert service._evolution_policy_error(_payload(), transition=True) == (
        "runtime.evolution is disabled"
    )
    assert _Coordinator.calls == []


def test_policy_treats_missing_enabled_flag_as_disabled(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.config.runtime.evolution = SimpleNamespace(
        mode="auto_low_risk",
        auto_asset_kinds=["memory_entry"],
    )

    assert service._evolution_policy_error(_payload(), transition=True) == (
        "runtime.evolution is disabled"
    )
    assert _Coordinator.calls == []


@pytest.mark.parametrize(
    "stored_asset",
    [None, "not-a-mapping", [], 0],
)
def test_policy_rejects_missing_or_non_mapping_asset(
    tmp_path: Path,
    stored_asset: object,
) -> None:
    _Coordinator.registry = {"assets": {"asset-1@2": stored_asset}}
    service = _service(tmp_path)

    assert service._evolution_policy_error(_payload(), transition=True) == (
        "learning asset is unavailable"
    )
    assert _Coordinator.calls == [tmp_path]


def test_policy_uses_exact_asset_id_version_key(tmp_path: Path) -> None:
    _Coordinator.registry = {
        "assets": {
            "asset-1@1": _asset(),
            "asset-2@2": _asset(),
            "asset-1@2": _asset(),
        }
    }
    service = _service(tmp_path)

    assert service._evolution_policy_error(_payload(), transition=True) == ""


def test_missing_asset_identity_cannot_match_fallback_named_registry_entries(
    tmp_path: Path,
) -> None:
    _Coordinator.registry = {
        "assets": {
            "XXXX@2": _asset(),
            "asset-1@1": _asset(),
        }
    }
    service = _service(tmp_path)

    assert service._evolution_policy_error(
        _payload(asset_id=""), transition=True
    ) == "learning asset is unavailable"
    assert service._evolution_policy_error(
        _payload(version=None), transition=True
    ) == "learning asset is unavailable"


def test_non_revoke_requires_auto_low_risk_mode(tmp_path: Path) -> None:
    service = _service(tmp_path, mode="manual")

    assert service._evolution_policy_error(_payload(), transition=True) == (
        "runtime.evolution.mode does not permit automatic adoption"
    )


@pytest.mark.parametrize("transition", [True, False])
def test_non_skill_revoked_target_never_gains_skill_revoke_bypass(
    tmp_path: Path,
    transition: bool,
) -> None:
    service = _service(tmp_path, mode="manual", auto_asset_kinds=[])

    assert service._evolution_policy_error(
        _payload(target_state="revoked"), transition=transition
    ) == "runtime.evolution.mode does not permit automatic adoption"


def test_skill_prompt_revoke_bypasses_mode_and_auto_kind_but_not_digest(
    tmp_path: Path,
) -> None:
    _Coordinator.registry = {
        "assets": {"asset-1@2": _asset(asset_kind="skill_prompt")}
    }
    service = _service(tmp_path, mode="manual", auto_asset_kinds=[])

    assert service._evolution_policy_error(
        _payload(target_state="revoked"), transition=True
    ) == ""
    assert service._evolution_policy_error(
        _payload(target_state="revoked", policy_digest="wrong"), transition=True
    ) == "evolution policy digest drift"


def test_skill_prompt_retention_requires_owner_approved_source_patch(
    tmp_path: Path,
) -> None:
    _Coordinator.registry = {
        "assets": {"asset-1@2": _asset(asset_kind="skill_prompt")}
    }
    service = _service(tmp_path, auto_asset_kinds=["skill_prompt"])

    assert service._evolution_policy_error(
        _payload(target_state="active_retained"), transition=True
    ) == (
        "skill_prompt retention requires an owner-approved source patch; "
        "unattended evolution may only activate or revoke the scoped overlay"
    )


@pytest.mark.parametrize("target_state", ["candidate", "active_retained", "unknown"])
def test_skill_prompt_rejects_states_outside_scoped_overlay_lifecycle(
    tmp_path: Path,
    target_state: str,
) -> None:
    _Coordinator.registry = {
        "assets": {"asset-1@2": _asset(asset_kind="skill_prompt")}
    }
    service = _service(tmp_path, auto_asset_kinds=["skill_prompt"])

    reason = service._evolution_policy_error(
        _payload(target_state=target_state), transition=True
    )

    if target_state == "active_retained":
        assert "owner-approved source patch" in reason
    else:
        assert reason == "skill_prompt transition is outside the scoped overlay lifecycle"


@pytest.mark.parametrize("target_state", ["validated", "approved", "canary_active"])
def test_skill_prompt_accepts_scoped_overlay_states(
    tmp_path: Path,
    target_state: str,
) -> None:
    _Coordinator.registry = {
        "assets": {"asset-1@2": _asset(asset_kind="skill_prompt")}
    }
    service = _service(tmp_path, auto_asset_kinds=["skill_prompt"])

    assert service._evolution_policy_error(
        _payload(target_state=target_state), transition=True
    ) == ""


def test_policy_rejects_asset_kind_outside_explicit_allowlist(tmp_path: Path) -> None:
    service = _service(tmp_path, auto_asset_kinds=["workflow_pattern"])

    assert service._evolution_policy_error(_payload(), transition=True) == (
        "learning asset kind is not authorized for unattended adoption"
    )


def test_missing_asset_kind_cannot_match_fallback_named_allowlist(tmp_path: Path) -> None:
    _Coordinator.registry = {
        "assets": {"asset-1@2": {"activation": {
            "automation_policy_digest": "policy-v1"
        }}}
    }
    service = _service(tmp_path, auto_asset_kinds=["XXXX"])

    assert service._evolution_policy_error(_payload(), transition=True) == (
        "learning asset kind is not authorized for unattended adoption"
    )


@pytest.mark.parametrize("stored_digest", ["", None, "policy-v0"])
def test_policy_requires_exact_nonempty_automation_digest(
    tmp_path: Path,
    stored_digest: object,
) -> None:
    _Coordinator.registry = {
        "assets": {"asset-1@2": _asset(policy_digest=stored_digest)}  # type: ignore[arg-type]
    }
    service = _service(tmp_path)

    assert service._evolution_policy_error(_payload(), transition=True) == (
        "evolution policy digest drift"
    )


def test_missing_stored_digest_cannot_match_fallback_payload_digest(tmp_path: Path) -> None:
    _Coordinator.registry = {
        "assets": {"asset-1@2": _asset(policy_digest="")}
    }
    service = _service(tmp_path)

    assert service._evolution_policy_error(
        _payload(policy_digest="XXXX"), transition=True
    ) == "evolution policy digest drift"


def test_missing_payload_digest_cannot_match_fallback_stored_digest(tmp_path: Path) -> None:
    _Coordinator.registry = {
        "assets": {"asset-1@2": _asset(policy_digest="XXXX")}
    }
    service = _service(tmp_path)

    assert service._evolution_policy_error(
        _payload(policy_digest=""), transition=True
    ) == "evolution policy digest drift"


def test_policy_rejects_transition_target_outside_auto_states(tmp_path: Path) -> None:
    service = _service(tmp_path)

    assert service._evolution_policy_error(
        _payload(target_state="candidate"), transition=True
    ) == "evolution target state is not policy-authorized"


def test_outcome_policy_ignores_transition_target_but_keeps_mode_kind_and_digest(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    assert service._evolution_policy_error(
        _payload(target_state="anything"), transition=False
    ) == ""
    service.config.runtime.evolution.mode = "manual"
    assert service._evolution_policy_error(_payload(), transition=False) == (
        "runtime.evolution.mode does not permit automatic adoption"
    )


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-056: malformed evolution version escapes structured rejection",
)
def test_malformed_evolution_version_is_structurally_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    failed_calls: list[dict[str, Any]] = []
    service._failed = lambda **kwargs: failed_calls.append(kwargs) or {"failed": kwargs}

    result = service._evolution_asset_transition(
        requested=SimpleNamespace(id="evt-request"),  # type: ignore[arg-type]
        action="evolution-asset-transition",
        requested_action="operator-request",
        payload=_payload(version="not-an-integer"),
    )

    assert result["failed"]["status"] == "invalid_payload"
    assert failed_calls
