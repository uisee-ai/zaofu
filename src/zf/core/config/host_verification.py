"""Opt-in, bounded container verification policy loaded only from zf.yaml."""
from dataclasses import dataclass, field
import re


@dataclass
class HostVerificationConfig:
    enabled: bool = False
    network: str = 'none'
    protected_files: dict[str, str] = field(default_factory=dict)
    approval_ref: str = ''
    image: str = ''
    commands: list[str] = field(default_factory=list)
    max_executions_per_run: int = 0
    timeout_seconds: int = 180


def parse_host_verification(raw) -> HostVerificationConfig:
    if raw is None:
        return HostVerificationConfig()
    if not isinstance(raw, dict) or set(raw) - set(HostVerificationConfig.__dataclass_fields__):
        raise ValueError('runtime.host_verification contains unknown keys or is not a mapping')
    policy = HostVerificationConfig(**raw)
    if policy.network not in {'none', 'host'}:
        raise ValueError('host verification network must be none or explicitly authorized host')
    if not isinstance(policy.protected_files, dict) or any(
        not isinstance(name, str) or not name or name.startswith('/') or '..' in name.split('/')
        or not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value)
        for name, value in policy.protected_files.items()
    ):
        raise ValueError('host verification protected_files requires relative paths and sha256 digests')
    if not isinstance(policy.enabled, bool):
        raise ValueError('runtime.host_verification.enabled must be boolean')
    if not isinstance(policy.commands, list) or any(not isinstance(c, str) or not c.strip() for c in policy.commands):
        raise ValueError('host verification commands must be nonempty exact command strings')
    if (type(policy.max_executions_per_run) is not int or not 0 <= policy.max_executions_per_run <= 100
            or type(policy.timeout_seconds) is not int or not 1 <= policy.timeout_seconds <= 900):
        raise ValueError('host verification requires bounded executions (0..100) and timeout (1..900)')
    if policy.enabled and (not isinstance(policy.approval_ref, str) or not policy.approval_ref.strip()
            or not isinstance(policy.image, str) or not re.fullmatch(r'(?:[^\s]+@)?sha256:[a-f0-9]{64}', policy.image)
            or not policy.commands or not policy.max_executions_per_run):
        raise ValueError('enabled host verification requires approval_ref, immutable image, commands and execution budget')
    return policy
