"""Opt-in Controller observation settings; never a replacement for managed access policy."""

from dataclasses import dataclass, fields
import re
from urllib.parse import urlsplit


@dataclass
class ControllerObservationConfig:
    enabled: bool = False
    endpoint: str = ""
    issuer: str = ""
    workspace_id: str = ""
    deployment_id: str = ""
    project_id: str = ""
    client_id: str = ""
    client_secret_env: str = "ZF_CONTROLLER_CLIENT_SECRET"
    mode: str = "unknown"
    development: bool = False
    interval_seconds: int = 15
    batch_size: int = 16
    max_pending_records: int = 100000
    max_pending_bytes: int = 67108864


def parse_controller_observation(raw: object) -> ControllerObservationConfig:
    if raw is None:
        return ControllerObservationConfig()
    if not isinstance(raw, dict) or set(raw) - {f.name for f in fields(ControllerObservationConfig)}:
        raise ValueError("observability.controller contains unknown settings")
    config = ControllerObservationConfig(**raw)
    for field in fields(ControllerObservationConfig):
        if isinstance(field.default, str) and not isinstance(getattr(config, field.name), str):
            raise ValueError(f'observability.controller.{field.name} must be a string')
    for name in ("enabled", "development"):
        if type(getattr(config, name)) is not bool:
            raise ValueError(f"observability.controller.{name} must be boolean")
    for name, low, high in (("interval_seconds", 1, 3600), ("batch_size", 1, 32),
                            ("max_pending_records", 32, 1000000), ("max_pending_bytes", 65536, 1073741824)):
        value = getattr(config, name)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"observability.controller.{name} outside supported bounds")
    if config.mode not in {"managed_cpa", "self_managed_api", "self_managed_cli", "unknown"}:
        raise ValueError("invalid Controller model access mode")
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", config.client_secret_env):
        raise ValueError("client_secret_env must name an environment variable")
    if config.enabled:
        for name in ("workspace_id", "deployment_id", "project_id", "client_id"):
            value = getattr(config, name)
            if not isinstance(value, str) or not re.fullmatch(r"[\w.:/@+-]{1,160}", value):
                raise ValueError(f"invalid observability.controller.{name}")
        for name in ("endpoint", "issuer"):
            value = getattr(config, name)
            parsed = urlsplit(value)
            if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or value.endswith("/") or (name == "endpoint" and parsed.path)):
                raise ValueError(f"invalid observability.controller.{name}")
            if parsed.scheme != "https" and not (config.development and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
                raise ValueError("Controller observation requires HTTPS; development HTTP is loopback-only")
    return config
