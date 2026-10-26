"""Resolve and freeze role-scoped provider session configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore, ZfNotInitialized
from zf.runtime.backend import get_adapter, validate_provider_session_config
from zf.runtime.call_result_envelope import write_immutable_json_sidecar


EFFECTIVE_PROVIDER_SESSION_SCHEMA = "effective-provider-session-config.v1"
PROVIDER_SESSION_CAPABILITY_SCHEMA = "provider-session-capability-snapshot.v1"
PROVIDER_SESSION_CANONICALIZATION = "provider-session-canonical-json.v1"
MAX_PROVIDER_SESSION_PARALLEL_AGENTS = 6


@dataclass(frozen=True)
class ResolvedProviderSessionConfig:
    snapshot: dict[str, Any]
    descriptor: dict[str, Any]
    capability_descriptor: dict[str, Any]
    explicit: bool

    @property
    def digest(self) -> str:
        return str(self.descriptor["sha256"])

    @property
    def ref(self) -> str:
        return str(self.descriptor["ref"])


class ProviderSessionPreparationMixin:
    """Bind effective provider settings at the process-allocation boundary."""

    @staticmethod
    def provider_session_environment(role: RoleConfig) -> list[str]:
        """Return child env required to enforce an explicit session config."""

        session = role.provider_session
        if role.backend == "claude-code" and session is not None and session.effort:
            # Claude Code gives this variable precedence over --effort.
            return [f"CLAUDE_CODE_EFFORT_LEVEL={session.effort}"]
        return []

    def prepare_provider_session(
        self,
        role: RoleConfig,
    ) -> tuple[
        RoleConfig,
        ResolvedProviderSessionConfig,
        dict[str, object],
    ]:
        role = self._apply_runner_policy(role)
        provider_config, provider_binding = bind_effective_provider_session(
            state_dir=self.state_dir,
            registry=self.registry,
            event_log=self.event_log,
            role=role,
        )
        return role, provider_config, provider_binding


def bind_effective_provider_session(
    *,
    state_dir: Path,
    registry: RoleSessionRegistry,
    event_log: Any,
    role: RoleConfig,
) -> tuple[ResolvedProviderSessionConfig, dict[str, object]]:
    """Freeze, bind, and audit one role's current provider configuration."""
    resolved = resolve_effective_provider_session(
        state_dir=state_dir,
        role=role,
    )
    binding = registry.bind_provider_session_config(
        role.instance_id,
        digest=resolved.digest,
        ref=resolved.ref,
        explicit=resolved.explicit,
    )
    if event_log is not None:
        payload = {
            "instance_id": role.instance_id,
            "role": role.name,
            "backend": role.backend,
            "provider_session_config_ref": resolved.ref,
            "provider_session_config_digest": resolved.digest,
            "capability_snapshot_ref": str(
                resolved.capability_descriptor.get("ref") or ""
            ),
            "capability_snapshot_digest": str(
                resolved.capability_descriptor.get("sha256") or ""
            ),
            "explicit": resolved.explicit,
            "currentness": str(binding.get("status") or ""),
        }
        event_log.append(ZfEvent(
            type="provider.session.config.resolved",
            actor="zf-cli",
            payload=payload,
        ))
        if bool(binding.get("recycled")):
            event_log.append(ZfEvent(
                type="provider.session.config.recycled",
                actor="zf-cli",
                payload={
                    **payload,
                    "reason": str(binding.get("reason") or ""),
                    "previous_digest": str(
                        binding.get("previous_digest") or ""
                    ),
                    "previous_ref": str(binding.get("previous_ref") or ""),
                },
            ))
    return resolved, binding


def resolve_effective_provider_session(
    *,
    state_dir: Path,
    role: RoleConfig,
    workflow_run_id: str = "",
) -> ResolvedProviderSessionConfig:
    """Validate, canonicalize, and persist one role's effective settings."""

    adapter = get_adapter(role.backend)
    capabilities = adapter.capabilities
    validate_provider_session_config(role, capabilities=capabilities)
    session = role.provider_session
    explicit = session is not None
    run_id = workflow_run_id or _workflow_run_id(state_dir)
    capability_snapshot = {
        "schema_version": PROVIDER_SESSION_CAPABILITY_SCHEMA,
        "backend": role.backend,
        "provider_session": {
            "efforts": list(capabilities.provider_session_efforts),
            "agent": capabilities.provider_session_agent,
            "parallelism": capabilities.provider_session_parallelism,
            "max_parallel_agents": min(
                MAX_PROVIDER_SESSION_PARALLEL_AGENTS,
                capabilities.max_provider_session_parallel_agents
                or MAX_PROVIDER_SESSION_PARALLEL_AGENTS,
            )
            if capabilities.provider_session_parallelism
            else 0,
        },
        "native_resume": capabilities.native_resume,
        "stream_json": capabilities.stream_json,
    }
    capability_descriptor = write_immutable_json_sidecar(
        state_dir,
        capability_snapshot,
        root="provider-sessions/capabilities",
        kind="provider-session-capability-snapshot",
        schema_version=PROVIDER_SESSION_CAPABILITY_SCHEMA,
        created_by="zf-spawn",
    )
    effort = session.effort if session is not None else ""
    agent = session.agent if session is not None else ""
    max_parallel = (
        session.max_parallel_agents if session is not None else None
    )
    snapshot = {
        "schema_version": EFFECTIVE_PROVIDER_SESSION_SCHEMA,
        "canonicalization": PROVIDER_SESSION_CANONICALIZATION,
        "workflow_run_id": run_id,
        "role": {
            "name": role.name,
            "instance_id": role.instance_id,
        },
        "provider": {
            "backend": role.backend,
            "model": role.model or "provider_default",
        },
        "explicit": explicit,
        "resolved": {
            "native_delegation": {
                "value": session.native_delegation if session is not None else None,
                "source": "role.provider_session" if session is not None and session.native_delegation is not None else "provider_default",
            },
            "effort": {
                "value": effort or None,
                "source": "role.provider_session" if effort else "provider_default",
            },
            "agent": {
                "value": agent or role.agent or None,
                "source": (
                    "role.provider_session"
                    if agent
                    else "role.agent"
                    if role.agent
                    else "provider_default"
                ),
            },
            "max_parallel_agents": {
                "value": max_parallel,
                "source": (
                    "role.provider_session"
                    if max_parallel is not None
                    else "provider_default_bounded_by_harness"
                ),
            },
        },
        "limits": {
            "harness_max_parallel_agents": MAX_PROVIDER_SESSION_PARALLEL_AGENTS,
        },
        "capability_snapshot": _ref_projection(capability_descriptor),
    }
    descriptor = write_immutable_json_sidecar(
        state_dir,
        snapshot,
        root="provider-sessions/effective",
        kind="effective-provider-session-config",
        schema_version=EFFECTIVE_PROVIDER_SESSION_SCHEMA,
        created_by="zf-spawn",
    )
    return ResolvedProviderSessionConfig(
        snapshot=snapshot,
        descriptor=descriptor,
        capability_descriptor=capability_descriptor,
        explicit=explicit,
    )


def _workflow_run_id(state_dir: Path) -> str:
    try:
        return SessionStore(state_dir / "session.yaml").load().session_id
    except ZfNotInitialized:
        return "uninitialized"


def _ref_projection(descriptor: dict[str, Any]) -> dict[str, Any]:
    return {
        "ref": str(descriptor.get("ref") or ""),
        "sha256": str(descriptor.get("sha256") or ""),
        "kind": str(descriptor.get("kind") or ""),
        "schema_version": str(descriptor.get("schema_version") or ""),
    }


__all__ = [
    "EFFECTIVE_PROVIDER_SESSION_SCHEMA",
    "MAX_PROVIDER_SESSION_PARALLEL_AGENTS",
    "PROVIDER_SESSION_CAPABILITY_SCHEMA",
    "PROVIDER_SESSION_CANONICALIZATION",
    "ProviderSessionPreparationMixin",
    "ResolvedProviderSessionConfig",
    "bind_effective_provider_session",
    "resolve_effective_provider_session",
]
