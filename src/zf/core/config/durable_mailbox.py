"""Project opt-in for durable agent handoffs."""

from dataclasses import dataclass


@dataclass
class DurableMailboxConfig:
    enabled: bool = False


def parse_durable_mailbox(value: object) -> DurableMailboxConfig:
    if value is None:
        return DurableMailboxConfig()
    if (
        not isinstance(value, dict)
        or value.keys() - {"enabled"}
        or type(value.get("enabled", False)) is not bool
    ):
        raise ValueError("runtime.durable_mailbox supports a boolean enabled field")
    return DurableMailboxConfig(enabled=value.get("enabled", False))
