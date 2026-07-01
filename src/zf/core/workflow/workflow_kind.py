"""kind: Workflow — Argo 形依名依赖语法 → canonical workflow.stages(doc 90 §3.5)。

关键转译决策(§2.2):ZaoFu 的事件类型边承重(I1/P4/discriminator/
expected_next),**依名依赖只是语法面**——`dependencies: [impl]` 由本层
铸造为事件边(trigger = 上游 success 事件,约定 `{name}.completed`)。
产物是普通 canonical stage dict,进既有 `_build_workflow_stages` /
doc 74 compiler:**不建第二 scheduler,本模块不被 runtime 导入**。

§3.5.1 operator coverage matrix:不能表达的字段 fail-closed
(workflow_kind_unsupported_field),禁止笼统"全覆盖"。
"""

from __future__ import annotations

from typing import Any


class WorkflowKindError(ValueError):
    """spec 翻译失败——envelope/loader 包装为 ConfigError。"""


_KNOWN_TASK_KEYS = frozenset({
    "name", "trigger", "dependencies", "role", "fanout", "aggregate",
    "target", "source", "criteria", "gateProfile", "gate_profile",
    "onFailure", "on_failure", "onFail", "on_fail",
    "onReject", "on_reject",
    "deadlineSeconds", "timeout_seconds", "synthesizeCanonicalTasks",
    "synthesize_canonical_tasks",
})
_KNOWN_FANOUT_KEYS = frozenset({
    "roles", "fromTaskMap", "from_task_map", "assignment", "children",
})
_KNOWN_AGG_KEYS = frozenset({
    "mode", "synthRole", "synth_role", "reviewStrategy", "review_strategy",
    "quorum", "pendingEvent", "pending_event",
    "childSuccessEvent", "child_success_event",
    "childFailureEvent", "child_failure_event",
    "successEvent", "success_event", "failureEvent", "failure_event",
    "maxRetries", "max_retries",
})


def _pick(raw: dict, *names: str, default: Any = None) -> Any:
    for name in names:
        if name in raw:
            return raw[name]
    return default


def success_event_of(task_name: str) -> str:
    return f"{task_name}.completed"


def failure_event_of(task_name: str) -> str:
    return f"{task_name}.failed"


def translate_workflow_kind(spec: dict, *, context: str = "Workflow") -> list[dict]:
    """Workflow spec → canonical stage dict 列表。

    约定铸造:task 终态 `{name}.completed/failed`、fanout child 终态
    `{name}.child.completed/failed`;显式 aggregate 事件名为逃生门。
    依名依赖:`dependencies: [d]` → trigger = d 的 success 事件;
    多依赖暂不支持(需要 barrier 语义)→ fail-closed。
    """
    if not isinstance(spec, dict):
        raise WorkflowKindError(f"{context}: spec must be a mapping")
    unknown_top = sorted(
        str(k) for k in spec if str(k) not in ("entry", "tasks")
    )
    if unknown_top:
        raise WorkflowKindError(
            f"{context}: unsupported spec key(s) {unknown_top} "
            f"(workflow_kind_unsupported_field; doc 90 §3.5.1)"
        )
    tasks = spec.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise WorkflowKindError(f"{context}: tasks must be a non-empty list")

    names = []
    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise WorkflowKindError(f"{context}.tasks[{i}] must be a mapping")
        name = str(task.get("name") or "").strip()
        if not name:
            raise WorkflowKindError(f"{context}.tasks[{i}]: name is required")
        names.append(name)
    known = set(names)

    stages: list[dict] = []
    for i, task in enumerate(tasks):
        name = names[i]
        ctx = f"{context}.tasks[{name}]"
        unknown = sorted(
            str(k) for k in task if str(k) not in _KNOWN_TASK_KEYS
        )
        if unknown:
            raise WorkflowKindError(
                f"{ctx}: unsupported field(s) {unknown} "
                f"(workflow_kind_unsupported_field)"
            )
        deps = task.get("dependencies") or []
        if not isinstance(deps, list):
            raise WorkflowKindError(f"{ctx}: dependencies must be a list")
        trigger = str(task.get("trigger") or "").strip()
        if deps:
            if len(deps) > 1:
                raise WorkflowKindError(
                    f"{ctx}: multiple dependencies need barrier semantics — "
                    f"not supported by kind: Workflow yet (fail-closed)"
                )
            dep = str(deps[0])
            if dep not in known:
                raise WorkflowKindError(
                    f"{ctx}: dependency {dep!r} is not a task in this workflow"
                )
            if trigger:
                raise WorkflowKindError(
                    f"{ctx}: specify either trigger or dependencies, not both"
                )
            trigger = success_event_of(dep)
        elif not trigger:
            raise WorkflowKindError(
                f"{ctx}: entry task requires an explicit trigger event"
            )

        stage: dict[str, Any] = {"id": name, "trigger": trigger}
        role = str(task.get("role") or "").strip()
        fanout = task.get("fanout")
        if role and fanout:
            raise WorkflowKindError(f"{ctx}: role and fanout are exclusive")
        agg_raw = task.get("aggregate")
        if isinstance(agg_raw, str):
            agg_raw = {"mode": agg_raw}
        if agg_raw is not None and not isinstance(agg_raw, dict):
            raise WorkflowKindError(f"{ctx}: aggregate must be a mapping/mode")
        if isinstance(agg_raw, dict):
            unknown_agg = sorted(
                str(k) for k in agg_raw if str(k) not in _KNOWN_AGG_KEYS
            )
            if unknown_agg:
                raise WorkflowKindError(
                    f"{ctx}.aggregate: unsupported field(s) {unknown_agg}"
                )

        if fanout is not None:
            if not isinstance(fanout, dict):
                raise WorkflowKindError(f"{ctx}: fanout must be a mapping")
            unknown_f = sorted(
                str(k) for k in fanout if str(k) not in _KNOWN_FANOUT_KEYS
            )
            if unknown_f:
                raise WorkflowKindError(
                    f"{ctx}.fanout: unsupported field(s) {unknown_f}"
                )
            from_map = _pick(fanout, "fromTaskMap", "from_task_map")
            roles = fanout.get("roles")
            if from_map:
                stage["topology"] = "fanout_writer_scoped"
                stage["source"] = {"task_map": str(from_map)}
                if roles:
                    stage["roles"] = [str(r) for r in roles]
                default_mode = "candidate_integration"
            elif roles:
                stage["topology"] = "fanout_reader"
                stage["roles"] = [str(r) for r in roles]
                default_mode = "wait_for_all"
            else:
                raise WorkflowKindError(
                    f"{ctx}.fanout: requires roles or fromTaskMap"
                )
            if fanout.get("assignment") is not None:
                stage["fanout"] = {"assignment": fanout["assignment"]}
            if fanout.get("children") is not None:
                stage.setdefault("fanout", {})["children"] = fanout["children"]
            agg = dict(agg_raw or {})
            mode = str(agg.get("mode") or default_mode)
            stage["aggregate"] = {
                "mode": mode,
                "success_event": str(
                    _pick(agg, "successEvent", "success_event")
                    or success_event_of(name)
                ),
                "failure_event": str(
                    _pick(agg, "failureEvent", "failure_event")
                    or failure_event_of(name)
                ),
            }
            if mode != "candidate_integration":
                stage["aggregate"]["child_success_event"] = str(
                    _pick(agg, "childSuccessEvent", "child_success_event")
                    or f"{name}.child.completed"
                )
                stage["aggregate"]["child_failure_event"] = str(
                    _pick(agg, "childFailureEvent", "child_failure_event")
                    or f"{name}.child.failed"
                )
            for src_keys, dst in (
                (("synthRole", "synth_role"), "synth_role"),
                (("reviewStrategy", "review_strategy"), "review_strategy"),
                (("quorum",), "quorum"),
                (("pendingEvent", "pending_event"), "pending_event"),
                (("maxRetries", "max_retries"), "max_retries"),
            ):
                value = _pick(agg, *src_keys)
                if value is not None:
                    stage["aggregate"][dst] = value
        elif role:
            # 单 role task:fanout_reader 单子(与手写单 role stage 同构),
            # 终态走约定事件。
            stage["topology"] = "fanout_reader"
            stage["roles"] = [role]
            agg = dict(agg_raw or {})
            stage["aggregate"] = {
                "mode": str(agg.get("mode") or "wait_for_all"),
                "success_event": str(
                    _pick(agg, "successEvent", "success_event")
                    or success_event_of(name)
                ),
                "failure_event": str(
                    _pick(agg, "failureEvent", "failure_event")
                    or failure_event_of(name)
                ),
                "child_success_event": str(
                    _pick(agg, "childSuccessEvent", "child_success_event")
                    or f"{name}.child.completed"
                ),
                "child_failure_event": str(
                    _pick(agg, "childFailureEvent", "child_failure_event")
                    or f"{name}.child.failed"
                ),
            }
        else:
            raise WorkflowKindError(f"{ctx}: requires role or fanout")

        source = task.get("source")
        if source is not None:
            if not isinstance(source, dict):
                raise WorkflowKindError(f"{ctx}: source must be a mapping")
            if "source" in stage and stage["source"] != source:
                raise WorkflowKindError(
                    f"{ctx}: source conflicts with fanout.fromTaskMap"
                )
            stage["source"] = source

        target = task.get("target")
        if target is not None:
            stage["target_ref"] = str(target)
        deadline = _pick(task, "deadlineSeconds", "timeout_seconds")
        if deadline is not None:
            stage["timeout_seconds"] = int(deadline)
        for src_key, dst in (
            ("criteria", "criteria"),
            ("gateProfile", "gate_profile"), ("gate_profile", "gate_profile"),
            ("onFailure", "on_fail"), ("on_failure", "on_fail"),
            ("onFail", "on_fail"), ("on_fail", "on_fail"),
            ("onReject", "on_reject"), ("on_reject", "on_reject"),
            ("synthesizeCanonicalTasks", "synthesize_canonical_tasks"),
            ("synthesize_canonical_tasks", "synthesize_canonical_tasks"),
        ):
            if src_key in task:
                stage[dst] = task[src_key]
        stages.append(stage)
    return stages
