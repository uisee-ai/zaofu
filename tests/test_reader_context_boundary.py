"""Reader context changes must fence the old process before replacing identity."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from zf.core.config.loader import ConfigError, load_config
from tests.test_role_lifecycle_runtime import _runtime, _seed_reader_fanout_context


def test_sibling_preregistration_does_not_rotate_one_reader_for_queued_tasks(
    tmp_path, monkeypatch,
):
    runtime, role, transport = _runtime(tmp_path)
    role.role_kind = "reader"
    role.lifecycle = replace(role.lifecycle, reader_context_scope="dispatch")
    children = [SimpleNamespace(
        child_id=task, role_instance=role.instance_id, payload={"task_id": task},
    ) for task in ["API", "CLI", "PRODUCT"]]
    context = SimpleNamespace(
        fanout_id="verify", stage_id="verify", trace_id="same-run",
        expected_children=children,
    )
    registered = []

    def prepare(**kwargs):
        registered.append(kwargs["child"].child_id)
        return {"skip": False}

    monkeypatch.setattr(runtime, "_prepare_reader_fanout_child_operation", prepare)
    prepared = runtime._preregister_reader_fanout_operations(
        context=context, roles_by_instance={role.instance_id: role},
        causation_id="evt-candidate",
    )
    assert registered == ["API", "CLI", "PRODUCT"]
    assert set(prepared) == set(registered)
    assert len(transport.spawned) == 1
    assert not transport.terminated

    args = dict(role=role, fanout_id="verify", stage_id="verify",
                trace_id="same-run", child_id="API", task_id="API",
                run_id="run-verify-API")
    assert runtime._ensure_fanout_role_dispatchable(**args)
    assert len(transport.spawned) == 1
    runtime._set_worker_state(role.instance_id, "busy", force=True)
    args.update(child_id="CLI", task_id="CLI", run_id="run-verify-CLI")
    assert not runtime._ensure_fanout_role_dispatchable(**args)
    assert not transport.terminated
    runtime._set_worker_state(role.instance_id, "idle", force=True)
    assert runtime._ensure_fanout_role_dispatchable(**args)
    assert transport.terminated == [role.instance_id]


@pytest.mark.parametrize('failure', ['alive', 'unknown', 'terminate'])
def test_context_switch_keeps_identity_when_stop_is_unconfirmed(tmp_path, monkeypatch, failure):
    runtime, role, transport = _runtime(tmp_path)
    _seed_reader_fanout_context(runtime, role, transport, task_id='OLD', trace_id='old-run')
    registry = runtime._role_lifecycle_registry()
    old_session = registry.get(role.instance_id)
    old_meta = registry.instance_meta()[role.instance_id]
    stopped = False

    def terminate(_):
        nonlocal stopped
        stopped = True
        if failure == 'terminate':
            raise RuntimeError('stop failed')

    def alive(_):
        if stopped and failure == 'unknown':
            raise RuntimeError('process observation unavailable')
        return True

    monkeypatch.setattr(transport, 'terminate', terminate)
    monkeypatch.setattr(transport, 'is_alive', alive)
    assert not runtime._ensure_fanout_role_dispatchable(
        role=role, fanout_id='new', stage_id='plan', child_id=role.instance_id,
        run_id='new-attempt', trace_id='new-run', task_id='NEW')
    assert registry.get(role.instance_id) == old_session
    assert registry.instance_meta()[role.instance_id] == old_meta
    assert not transport.spawned
    assert not any(e.type == 'worker.recycled' for e in runtime.event_log.read_all())
    assert any(e.type == 'worker.recycle.failed' for e in runtime.event_log.read_all())


@pytest.mark.parametrize("task_id", ["SAME", ""])
def test_dispatch_scope_rotates_once_and_preserves_workdir(tmp_path, task_id):
    runtime, role, transport = _runtime(tmp_path)
    _seed_reader_fanout_context(runtime, role, transport, task_id=task_id, trace_id='same-run')
    role.lifecycle = replace(role.lifecycle, reader_context_scope='dispatch')
    product = runtime.state_dir / 'workdirs' / role.instance_id / 'project'
    product.mkdir(parents=True, exist_ok=True)
    artifact = product / 'prior-plan.json'
    artifact.write_text('{"preserve":true}')
    args = dict(role=role, fanout_id='rework', stage_id='plan', child_id=role.instance_id,
                run_id='rework-attempt', trace_id='same-run', task_id=task_id)
    assert runtime._ensure_fanout_role_dispatchable(**args)
    assert transport.terminated == [role.instance_id]
    assert len(transport.spawned) == 1
    assert artifact.read_text() == '{"preserve":true}'
    assert runtime._ensure_fanout_role_dispatchable(**args, skip_send_window=True)
    assert len(transport.terminated) == 1
    assert len(transport.spawned) == 1
    events = [e for e in runtime.event_log.read_all() if e.type == 'worker.recycled']
    assert len(events) == 1
    assert events[0].payload['reason'] == 'reader_dispatch_context_changed'
    assert events[0].payload['dispatch_id'] == 'rework-attempt'


def test_dispatch_scope_waits_for_prior_child_settlement(tmp_path):
    runtime, role, transport = _runtime(tmp_path)
    _seed_reader_fanout_context(runtime, role, transport, task_id='SAME', trace_id='same-run', terminal=False)
    role.lifecycle = replace(role.lifecycle, reader_context_scope='dispatch')
    assert not runtime._ensure_fanout_role_dispatchable(
        role=role, fanout_id='rework', stage_id='plan', child_id=role.instance_id,
        run_id='rework-attempt', trace_id='same-run', task_id='SAME')
    assert not transport.terminated
    assert not transport.spawned


@pytest.mark.parametrize("scope, mode, kind", [
    ("invalid", "on_demand", "reader"),
    (None, "on_demand", "reader"),
    ([], "on_demand", "reader"),
    ("dispatch", "resident", "reader"),
    ("dispatch", "on_demand", "writer"),
])
def test_loader_rejects_unsupported_context_scope(tmp_path, scope, mode, kind):
    import yaml
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump({
        "version": "1.0", "project": {"name": "context-boundary"},
        "roles": [{"name": "worker", "backend": "mock", "role_kind": kind,
                   "lifecycle": {"mode": mode, "reader_context_scope": scope}}],
    }))
    with pytest.raises((ConfigError, ValueError), match="reader_context_scope"):
        load_config(path)


@pytest.mark.parametrize("with_defaults", [False, True])
def test_profile_stage_lifecycle_reaches_actual_reader(tmp_path, with_defaults):
    path = tmp_path / "zf.yaml"
    path.write_text("""apiVersion: zaofu.dev/v1
kind: RefactorFlow
metadata: {name: demo-flow}
spec:
  flowProfile: refactor-flow/v3
  lanes: 1
  assembly: {task: DEMO-ASM-001}
  roleDefaults:
    backend: mock
    lifecycleByStage:
      verify:
        mode: on_demand
        reader_context_scope: dispatch
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: demo}
spec:
  version: "1.0"
  project: {name: demo}
""")
    if with_defaults:
        path.write_text(path.read_text() + "  flow_defaults:\n    refactor:\n      roleDefaults:\n        permission_mode: bypass\n")
    config = load_config(path)
    scan = next(r for r in config.roles if r.name == "verify-lane-0")
    assert scan.role_kind == "reader"
    assert scan.lifecycle.reader_context_scope == "dispatch"
    assert all(r.lifecycle.reader_context_scope == "workflow"
               for r in config.roles if r.role_kind == "writer")


def test_dispatch_scope_requires_dispatch_identity(tmp_path):
    runtime, role, transport = _runtime(tmp_path)
    _seed_reader_fanout_context(runtime, role, transport, task_id='SAME', trace_id='same-run')
    role.lifecycle = replace(role.lifecycle, reader_context_scope='dispatch')
    assert not runtime._ensure_fanout_role_dispatchable(
        role=role, fanout_id='rework', stage_id='plan', child_id=role.instance_id,
        run_id='', trace_id='same-run', task_id='SAME')
    assert not transport.terminated
    assert not transport.spawned


def test_dispatch_scope_waits_for_provider_turn(tmp_path, monkeypatch):
    runtime, role, transport = _runtime(tmp_path)
    _seed_reader_fanout_context(runtime, role, transport, task_id="SAME", trace_id="same-run")
    role.lifecycle = replace(role.lifecycle, reader_context_scope="dispatch")
    monkeypatch.setattr(runtime, "_fanout_role_has_active_provider_turn", lambda _: True)
    assert not runtime._ensure_fanout_role_dispatchable(
        role=role, fanout_id="rework", stage_id="plan", child_id=role.instance_id,
        run_id="next-attempt", trace_id="same-run", task_id="SAME")
    assert not transport.terminated
    assert not transport.spawned
