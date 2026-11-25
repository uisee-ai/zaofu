"""Validate host-owned, per-role mount scope declarations at config load."""

import re
from pathlib import PurePosixPath
from typing import Any


def artifact_worker_cgroups(
    config: Any, *, validate_roles: bool = False
) -> dict[str, str]:
    """Return physical instance bindings across the active workflow families."""
    workflow = getattr(config, "workflow", None)
    scoped = getattr(workflow, "flow_metadata_by_kind", {}) or {}
    layers = [("", getattr(workflow, "flow_metadata", {}) or {}), *scoped.items()]
    roles = {r.instance_id: r.flow_kind for r in config.roles} if validate_roles else {}
    groups: dict[str, str] = {}
    for kind, metadata in layers:
        declared = metadata.get("artifact_worker_cgroups", {})
        if not isinstance(declared, dict):
            raise ValueError(
                "artifactWorkerCgroups must be a mapping from worker instance to dedicated cgroup"
            )
        for actor, value in declared.items():
            if validate_roles and (
                actor not in roles or (kind and roles[actor] != kind)
            ):
                raise ValueError(
                    f"artifactWorkerCgroups: unknown worker instance {actor!r} for {kind or 'project'}"
                )
            if (
                not isinstance(value, str)
                or not re.fullmatch(r"(?:/[\w.@:-]+)+", value)
                or any(part in {".", ".."} for part in value.split("/"))
            ):
                raise ValueError(
                    f"artifactWorkerCgroups: expected an absolute dedicated cgroup path for {actor!r}"
                )
            if actor in groups and value != groups[actor]:
                raise ValueError(
                    f"artifactWorkerCgroups: conflicting host bindings for {actor!r}"
                )
            groups[actor] = value
    # A parent grant contains every child of that cgroup. Compare across
    # Flow families too, since the kernel places all roles in one host tree.
    if validate_roles:
        paths: set[PurePosixPath] = set()
        for value in sorted(groups.values()):
            path = PurePosixPath(value)
            if path in paths or paths.intersection(path.parents):
                raise ValueError(
                    f"artifactWorkerCgroups: overlapping worker scopes at {value!r}"
                )
            paths.add(path)
    return groups
