"""Optional, explicit evidence requirement for newly frozen operation inputs."""
from __future__ import annotations

from typing import Any


def input_delivery_parameter(params: dict[str, Any]) -> dict[str, str]:
    from zf.core.config.workflow_profiles import WorkflowProfileError

    names = ("inputDeliveryMode", "input_delivery_mode")
    values = [params[name] for name in names if name in params]
    if not values:
        return {}  # Keep legacy expanded metadata and operation hashes stable.
    if any(value not in ("produced", "observed") for value in values):
        raise WorkflowProfileError("inputDeliveryMode must be produced or observed")
    if len(values) == 2 and values[0] != values[1]:
        raise WorkflowProfileError("inputDeliveryMode aliases disagree")
    return {"input_delivery_mode": values[0]}
