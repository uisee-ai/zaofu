"""doc 90 B2:kind: Workflow 编译 + 三族等价 graph 快照。

依名依赖只是语法面:事件边由约定铸造,compile_workflow_graph 的产物
必须与等价手写 stages 逐字段相同——新语法是另一条进同一 graph 的路。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.workflow.graph import compile_workflow_graph
from zf.core.workflow.workflow_kind import (
    WorkflowKindError,
    translate_workflow_kind,
)

_REPO = Path(__file__).resolve().parent.parent


def _roles(*names):
    return [
        {"name": n, "backend": "mock", "instance_id": n,
         "role_kind": "reader"} for n in names
    ]


def _load_yaml(tmp_path, name, data_or_text):
    p = tmp_path / name
    if isinstance(data_or_text, str):
        p.write_text(data_or_text)
    else:
        p.write_text(yaml.dump(data_or_text))
    return load_config(p)


def _graph_dict(cfg):
    return compile_workflow_graph(cfg).to_dict()


class TestEquivalenceSnapshots:
    """三 family:DAG 链 / star reader / star writer。"""

    def _envelope(self, workflow_spec, roles):
        return (
            "apiVersion: zaofu.dev/v1\nkind: Workflow\n"
            "metadata: {name: wf}\n"
            + yaml.dump({"spec": workflow_spec})
            + "---\napiVersion: zaofu.dev/v1\nkind: ZfConfig\n"
            "metadata: {name: t}\n"
            + yaml.dump({"spec": {
                "version": "1.0",
                "project": {"name": "t"},
                "roles": roles,
            }})
        )

    def test_dag_chain_family(self, tmp_path):
        roles = _roles("planner", "builder")
        wf = {
            "entry": "plan",
            "tasks": [
                {"name": "plan", "trigger": "work.requested",
                 "role": "planner"},
                {"name": "build", "dependencies": ["plan"],
                 "role": "builder"},
            ],
        }
        hand_stages = [
            {"id": "plan", "trigger": "work.requested",
             "topology": "fanout_reader", "roles": ["planner"],
             "aggregate": {
                 "mode": "wait_for_all",
                 "success_event": "plan.completed",
                 "failure_event": "plan.failed",
                 "child_success_event": "plan.child.completed",
                 "child_failure_event": "plan.child.failed"}},
            {"id": "build", "trigger": "plan.completed",
             "topology": "fanout_reader", "roles": ["builder"],
             "aggregate": {
                 "mode": "wait_for_all",
                 "success_event": "build.completed",
                 "failure_event": "build.failed",
                 "child_success_event": "build.child.completed",
                 "child_failure_event": "build.child.failed"}},
        ]
        cfg_new = _load_yaml(tmp_path, "new.yaml", self._envelope(wf, roles))
        cfg_old = _load_yaml(tmp_path, "old.yaml", {
            "version": "1.0", "project": {"name": "t"}, "roles": roles,
            "workflow": {"stages": hand_stages},
        })
        assert _graph_dict(cfg_new) == _graph_dict(cfg_old)

    def test_star_reader_family(self, tmp_path):
        roles = _roles("scan-a", "scan-b", "scan-c", "synth")
        wf = {
            "tasks": [{
                "name": "scan", "trigger": "scan.requested",
                "fanout": {"roles": ["scan-a", "scan-b", "scan-c"]},
                "aggregate": {"mode": "wait_for_all", "synthRole": "synth"},
                "target": "${target_ref}",
                "deadlineSeconds": 1800,
            }],
        }
        hand_stages = [{
            "id": "scan", "trigger": "scan.requested",
            "topology": "fanout_reader",
            "roles": ["scan-a", "scan-b", "scan-c"],
            "target_ref": "${target_ref}",
            "timeout_seconds": 1800,
            "aggregate": {
                "mode": "wait_for_all",
                "synth_role": "synth",
                "success_event": "scan.completed",
                "failure_event": "scan.failed",
                "child_success_event": "scan.child.completed",
                "child_failure_event": "scan.child.failed"},
        }]
        cfg_new = _load_yaml(tmp_path, "new.yaml", self._envelope(wf, roles))
        cfg_old = _load_yaml(tmp_path, "old.yaml", {
            "version": "1.0", "project": {"name": "t"}, "roles": roles,
            "workflow": {"stages": hand_stages},
        })
        assert _graph_dict(cfg_new) == _graph_dict(cfg_old)

    def test_star_writer_family(self, tmp_path):
        roles = [
            dict(r, role_kind="writer") for r in _roles("dev-0", "dev-1")
        ]
        wf = {
            "tasks": [{
                "name": "impl", "trigger": "task_map.ready",
                "fanout": {"fromTaskMap": "${task_map_ref}",
                           "roles": ["dev-0", "dev-1"]},
                "aggregate": {
                    "mode": "candidate_integration",
                    "successEvent": "candidate.ready",
                    "failureEvent": "integration.failed",
                    "maxRetries": 1,
                },
            }],
        }
        hand_stages = [{
            "id": "impl", "trigger": "task_map.ready",
            "topology": "fanout_writer_scoped",
            "roles": ["dev-0", "dev-1"],
            "source": {"task_map": "${task_map_ref}"},
            "aggregate": {
                "mode": "candidate_integration",
                "success_event": "candidate.ready",
                "failure_event": "integration.failed",
                "max_retries": 1},
        }]
        cfg_new = _load_yaml(tmp_path, "new.yaml", self._envelope(wf, roles))
        cfg_old = _load_yaml(tmp_path, "old.yaml", {
            "version": "1.0", "project": {"name": "t"}, "roles": roles,
            "workflow": {"stages": hand_stages},
        })
        assert _graph_dict(cfg_new) == _graph_dict(cfg_old)

    def test_task_source_maps_to_canonical_stage(self):
        stages = translate_workflow_kind({"tasks": [{
            "name": "plan",
            "trigger": "idea.submitted",
            "role": "arch",
            "source": {"documents": ["docs/research.md"]},
        }]})

        assert stages[0]["source"] == {"documents": ["docs/research.md"]}


class TestFailClosed:
    def test_unsupported_task_field_stops(self):
        with pytest.raises(WorkflowKindError, match="unsupported field"):
            translate_workflow_kind({
                "tasks": [{"name": "a", "trigger": "t", "role": "r",
                           "retryPolicy": {}}],
            })

    def test_multiple_dependencies_fail_closed(self):
        with pytest.raises(WorkflowKindError, match="barrier"):
            translate_workflow_kind({"tasks": [
                {"name": "a", "trigger": "t", "role": "r"},
                {"name": "b", "trigger": "t2", "role": "r"},
                {"name": "c", "dependencies": ["a", "b"], "role": "r"},
            ]})

    def test_unknown_dependency_fail_closed(self):
        with pytest.raises(WorkflowKindError, match="not a task"):
            translate_workflow_kind({"tasks": [
                {"name": "a", "dependencies": ["ghost"], "role": "r"},
            ]})

    def test_entry_without_trigger_fail_closed(self):
        with pytest.raises(WorkflowKindError, match="explicit trigger"):
            translate_workflow_kind({"tasks": [{"name": "a", "role": "r"}]})

    def test_unsupported_aggregate_field_stops(self):
        with pytest.raises(WorkflowKindError, match="aggregate: unsupported"):
            translate_workflow_kind({"tasks": [{
                "name": "a", "trigger": "t",
                "fanout": {"roles": ["r"]},
                "aggregate": {"mode": "wait_for_all", "magic": 1},
            }]})

    def test_source_must_be_mapping(self):
        with pytest.raises(WorkflowKindError, match="source must be a mapping"):
            translate_workflow_kind({"tasks": [{
                "name": "a",
                "trigger": "t",
                "role": "r",
                "source": "docs/research.md",
            }]})

    def test_source_conflict_with_from_task_map_stops(self):
        with pytest.raises(WorkflowKindError, match="source conflicts"):
            translate_workflow_kind({"tasks": [{
                "name": "impl",
                "trigger": "task_map.ready",
                "fanout": {"fromTaskMap": "${task_map_ref}", "roles": ["dev"]},
                "source": {"task_map": "${other_task_map_ref}"},
            }]})

    def test_envelope_wraps_error_as_config_error(self, tmp_path):
        text = (
            "apiVersion: zaofu.dev/v1\nkind: Workflow\nmetadata: {name: w}\n"
            "spec:\n  tasks:\n  - {name: a, role: r}\n"
            "---\napiVersion: zaofu.dev/v1\nkind: ZfConfig\n"
            "spec: {version: '1.0', project: {name: t}}\n"
        )
        p = tmp_path / "zf.yaml"
        p.write_text(text)
        with pytest.raises(ConfigError, match="explicit trigger"):
            load_config(p)


class TestInspectOnlyBoundary:
    def test_runtime_does_not_import_workflow_kind(self):
        pattern = re.compile(r"workflow_kind")
        offenders = [
            path.name
            for path in (_REPO / "src/zf/runtime").glob("*.py")
            if pattern.search(path.read_text(encoding="utf-8", errors="replace"))
        ]
        assert offenders == [], (
            f"workflow_kind referenced by runtime modules: {offenders} "
            f"(compiler-side only; stages flow through canonical loader)"
        )
