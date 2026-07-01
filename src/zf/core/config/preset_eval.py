"""Preset evaluator — static `zf.yaml` checks before real E2E (doc 68 S4).

Extends what `zf workflow audit`(runtime, per-task) does NOT: a pure static
validation of a loaded ``ZfConfig`` so config-layer mistakes surface before an
expensive real-provider run. Pure function over ZfConfig (no I/O), so it is
deterministic and testable.

Note: the loader already hard-validates some invariants at construction (e.g.
autoscale max_replicas<=6, and `RoleConfig.__post_init__` already raises on a
bad context-threshold order — so a "threshold order" check here would be dead).
This evaluator covers only the *soft* / cross-field checks the loader does NOT:
exception-trigger coverage, terminal `workflow.dag.stage_order`, stages
referencing valid roles, and worker reachability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Exception events an orchestrator should be able to react to; if the
# orchestrator declares an explicit triggers list, missing these is a WARN
# (empty triggers = subscribe-to-all, which is fine).
_EXCEPTION_TRIGGERS = ("dev.blocked", "clarification.needed", "worker.stuck")
_TERMINAL_STAGES = ("done", "ship", "shipped", "judge", "release")


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str   # PASS | WARN | FAIL
    detail: str = ""


def evaluate_preset(config: Any) -> dict[str, Any]:
    """Run static preset checks over a loaded ZfConfig; return a report dict."""
    checks: list[CheckResult] = []
    checks.append(_check_orchestrator_exception_triggers(config))
    checks.append(_check_stage_order_terminal(config))
    checks.extend(_check_stage_roles_exist(config))
    checks.extend(_check_worker_reachability(config))

    summary = {"pass": 0, "warn": 0, "fail": 0}
    for c in checks:
        key = c.status.lower()
        if key in summary:
            summary[key] += 1
    return {
        "schema_version": "preset-eval.v1",
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in checks],
        "summary": summary,
        "ok": summary["fail"] == 0,
    }


def _roles(config: Any) -> list[Any]:
    return list(getattr(config, "roles", []) or [])


def _check_orchestrator_exception_triggers(config: Any) -> CheckResult:
    orch = next((r for r in _roles(config) if getattr(r, "name", "") == "orchestrator"), None)
    if orch is None:
        orch = getattr(config, "orchestrator", None)
    triggers = list(getattr(orch, "triggers", []) or []) if orch is not None else []
    if not triggers:
        return CheckResult("orchestrator.exception_triggers", "PASS",
                           "no explicit triggers (subscribe-to-all)")
    missing = [t for t in _EXCEPTION_TRIGGERS if t not in triggers]
    if missing:
        return CheckResult("orchestrator.exception_triggers", "WARN",
                           f"orchestrator triggers miss exception events: {missing}")
    return CheckResult("orchestrator.exception_triggers", "PASS")


def _check_stage_order_terminal(config: Any) -> CheckResult:
    workflow = getattr(config, "workflow", None)
    dag = getattr(workflow, "dag", None) if workflow else None
    stage_order = list(getattr(dag, "stage_order", []) or []) if dag else []
    if not stage_order:
        return CheckResult("stage_order.terminal", "PASS",
                           "no workflow.dag.stage_order declared (not enforced)")
    last = str(stage_order[-1]).lower()
    if any(term in last for term in _TERMINAL_STAGES):
        return CheckResult("stage_order.terminal", "PASS", f"ends in {stage_order[-1]!r}")
    return CheckResult("stage_order.terminal", "WARN",
                       f"stage_order ends in {stage_order[-1]!r}, not a terminal stage")


def _check_stage_roles_exist(config: Any) -> list[CheckResult]:
    """Each workflow.stages[].roles entry must be a declared role."""
    role_names = {getattr(r, "name", "") for r in _roles(config)}
    workflow = getattr(config, "workflow", None)
    stages = list(getattr(workflow, "stages", []) or []) if workflow else []
    out: list[CheckResult] = []
    for stage in stages:
        sid = getattr(stage, "id", "?")
        for rname in getattr(stage, "roles", []) or []:
            if rname not in role_names:
                out.append(CheckResult(
                    f"stage_roles_exist[{sid}]", "FAIL",
                    f"stage {sid!r} references undeclared role {rname!r}",
                ))
    if not out:
        out.append(CheckResult("stage_roles_exist", "PASS"))
    return out


def _check_worker_reachability(config: Any) -> list[CheckResult]:
    out: list[CheckResult] = []
    for role in _roles(config):
        name = getattr(role, "name", "?")
        if name == "orchestrator":
            continue
        triggers = getattr(role, "triggers", []) or []
        publishes = getattr(role, "publishes", []) or []
        if not triggers and not publishes:
            out.append(CheckResult(
                f"worker_reachable[{name}]", "WARN",
                "role has neither triggers nor publishes — unreachable in the DAG",
            ))
    if not out:
        out.append(CheckResult("worker_reachable", "PASS"))
    return out
