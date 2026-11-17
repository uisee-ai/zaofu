"""The real fanout caller must pin before starting a workspace holder."""
from pathlib import Path

from tests.test_reader_target_pinning import _state, _trigger
from tests.test_role_lifecycle_runtime import RecordingTransport
from zf.core.config.schema import RoleLifecycleConfig
from zf.runtime.environment_usage import environment_in_use
from zf.runtime.orchestrator import Orchestrator


class HoldingTransport(RecordingTransport):
    def __init__(self):
        super().__init__()
        self.holders = {}

    def spawn(self, role, argv, *, cwd=None):
        super().spawn(role, argv, cwd=cwd)
        holder = environment_in_use(Path(cwd))
        holder.__enter__()
        self.holders[role.instance_id] = holder

    def terminate(self, role_name):
        holder = self.holders.pop(role_name, None)
        if holder:
            holder.__exit__(None, None, None)
        super().terminate(role_name)

    def close(self):
        for role in list(self.holders):
            self.terminate(role)


def test_actual_reader_caller_pins_before_provider_acquires_workspace(tmp_path):
    head, state, log, _, runtime = _state(tmp_path, workdirs=True)
    runtime.config.roles[0].lifecycle = RoleLifecycleConfig(mode="on_demand")
    transport = HoldingTransport()
    runtime.transport = transport
    try:
        _trigger(runtime, log, target_ref=head)
        assert len(transport.sent) == 1
        assert len(transport.spawned) == 1
        assert not transport.terminated
        runtime.run_once(events=[])
        assert len(transport.sent) == 1
    finally:
        transport.close()


def test_external_hold_does_not_start_provider_and_restart_resumes(tmp_path):
    from zf.runtime.workdirs import WorkdirManager
    head, state, log, _, runtime = _state(tmp_path, workdirs=True)
    role = runtime.config.roles[0]
    role.lifecycle = RoleLifecycleConfig(mode="on_demand")
    transport = HoldingTransport()
    runtime.transport = transport
    workspace = WorkdirManager(state_dir=state, project_root=tmp_path, config=runtime.config).prepare(role)
    try:
        with environment_in_use(Path(workspace.project_path)):
            _trigger(runtime, log, target_ref=head)
            runtime = Orchestrator(state, runtime.config, transport, project_root=tmp_path)
            runtime.run_once(events=[])
            assert not transport.spawned
            assert not transport.sent
        runtime.run_once(events=[])
        assert len(transport.sent) == 1
        assert len([e for e in log.read_all() if e.type == "fanout.started"]) == 1
    finally:
        transport.close()


def test_fresh_reader_retry_prepares_target_before_activation(tmp_path, monkeypatch):
    from zf.core.events import ZfEvent
    head, state, log, _, runtime = _state(tmp_path, workdirs=True)
    role = runtime.config.roles[0]
    calls = []
    def prepare(*args, **kwargs):
        calls.append('prepare')
        return None
    def activate(**kwargs):
        calls.append('activate')
        return False
    monkeypatch.setattr('zf.runtime.reader_fanout_retry.prepare_fresh_reader_retry', prepare)
    monkeypatch.setattr(runtime, '_ensure_fanout_role_dispatchable', activate)
    runtime._retry_fanout_child(
        manifest={'fanout_id':'f', 'stage_id':'final-judge', 'trace_id':'r', 'topology':'fanout_reader'},
        child={'child_id':'judge','role_instance':'judge'},
        attempt=1, previous_dispatch=ZfEvent(type='fanout.child.dispatched'), fresh_operation=True)
    assert calls == ['prepare', 'activate']
