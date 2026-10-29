from types import SimpleNamespace

from zf.core.config.schema import (
    RoleConfig,
    WorkflowConfig,
    WorkflowOrchestrationConfig,
    ZfConfig,
)
from zf.runtime.orchestrator_agent_role import (
    configured_orchestrator_role,
    configured_orchestrator_role_from_config,
    orchestrator_instance_id,
    is_configured_orchestrator_role,
)


def _runtime(*, profile: str = "") -> SimpleNamespace:
    config = ZfConfig(
        workflow=WorkflowConfig(
            orchestration=WorkflowOrchestrationConfig(oa_profile=profile),
        ),
        roles=[RoleConfig(name="oa-primary", instance_id="oa-primary-1")],
    )
    roles = config.roles
    return SimpleNamespace(
        config=config,
        _find_role_by_name=lambda name: next(
            (role for role in roles if role.name == name), None
        ),
        _find_role_by_instance=lambda instance: next(
            (role for role in roles if role.instance_id == instance), None
        ),
    )


def test_explicit_oa_profile_resolves_role_and_instance() -> None:
    runtime = _runtime(profile="oa-primary")

    assert configured_orchestrator_role(runtime).name == "oa-primary"
    assert orchestrator_instance_id(runtime) == "oa-primary-1"


def test_missing_explicit_profile_fails_closed() -> None:
    runtime = _runtime(profile="missing-oa")

    assert configured_orchestrator_role(runtime) is None
    assert orchestrator_instance_id(runtime) == "missing-oa"


def test_config_only_resolver_matches_profile_name_and_instance() -> None:
    runtime = _runtime(profile="oa-primary-1")

    assert configured_orchestrator_role_from_config(runtime.config).name == "oa-primary"


def test_config_only_resolver_keeps_legacy_fallback_fail_closed() -> None:
    runtime = _runtime()

    assert configured_orchestrator_role_from_config(runtime.config) is None


def test_explicit_profile_excludes_all_logical_role_replicas() -> None:
    runtime = _runtime(profile="oa-primary")
    replica = RoleConfig(name="oa-primary", instance_id="oa-primary-2")

    assert is_configured_orchestrator_role(runtime.config, runtime.config.roles[0])
    assert is_configured_orchestrator_role(runtime.config, replica)
