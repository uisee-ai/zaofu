"""Mechanical admission for explicitly selected PRD v4 pool inputs."""

from collections.abc import Mapping
from typing import Any


def validate_prd_task_pipeline(value: Any, *, family: str, lanes: Any) -> None:
    keys = {"profile", "mode", "impl_capacity", "verify_capacity", "max_active_task_pipelines"}
    if family != "PrdFlow" or not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError("task_pipeline requires the bounded PRD v4 profile and pool parameters")
    if value["profile"] != "prd-flow-v4-task-pipeline" or value["mode"] not in ("shadow", "blocking"):
        raise ValueError("task_pipeline profile or mode is invalid")
    for key, limit in (("impl_capacity", 16), ("verify_capacity", 16), ("max_active_task_pipelines", 32)):
        item = value[key]
        if type(item) is not int or not 1 <= item <= limit:
            raise ValueError(f"task_pipeline {key} must be an integer between 1 and {limit}")
    if type(lanes) is not int or lanes < max(value["impl_capacity"], value["verify_capacity"]):
        raise ValueError("task_pipeline pools require enough declared lanes")
