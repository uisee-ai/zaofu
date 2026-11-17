import threading

import pytest

from tests.test_controller_managed import managed as managed, workspace as workspace
from zf.core.config.schema import RoleConfig
from zf.integrations.controller.binding import ManagedDenied
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import DispatchContext, TmuxTransport


def test_managed_runtime_generated_role_uses_spawned_contract_and_accounting(managed, monkeypatch):
    from zf.integrations.controller import tmux as accounting
    from zf.integrations.controller.workspace import synchronize_workspace

    config, peer, project, _ = managed
    monkeypatch.chdir(project.project_root)
    role = RoleConfig(name="run-manager", instance_id="run-manager", backend="codex", model="fixture")
    assert all(item.instance_id != role.instance_id for item in project.config.roles)
    tmux = TmuxSession("isolated")
    transport = TmuxTransport(tmux)
    sent = []
    monkeypatch.setattr(tmux.layout, "create_slot", lambda *args: None)
    monkeypatch.setattr(tmux, "send_keys", lambda *args, **kwargs: None)
    monkeypatch.setattr(tmux, "clear_input", lambda *args: None)
    monkeypatch.setattr(tmux, "send_paste", lambda name, prompt: sent.append((name, prompt)))
    monkeypatch.setattr(tmux, "capture_pane", lambda *args, **kwargs: "")
    monkeypatch.setattr(transport, "_wait_for_shell_handshake", lambda *args: None)
    monkeypatch.setattr(transport, "_assert_expected_cwd", lambda *args: None)
    monkeypatch.setattr(transport, "_agent_process_liveness", lambda *args: (True, ""))
    monkeypatch.setattr(accounting, "role_snapshot", lambda *args, **kwargs: {"status": "unavailable"})
    completed = threading.Event()

    def receipt(transport, name, registry, role, before, execution):
        execution.finish({"input_tokens": 3, "output_tokens": 2}, status="completed")
        completed.set()

    monkeypatch.setattr(accounting, "observe", receipt)
    transport.spawn(role, ["codex"], cwd=project.project_root)
    transport.send_task(role.instance_id, project.state_dir / "briefing.md", "fixture turn",
                        context=DispatchContext(backend="codex"))
    assert completed.wait(3)
    synchronize_workspace(config)
    assert sent == [("run-manager", "fixture turn")]
    assert len(peer.budgets) == len(peer.settlements) == 1
    assert peer.settlements[0]["tokens"]["input_tokens"] == 3
    with pytest.raises(ManagedDenied, match="role"):
        transport.send_task("invented-role", project.state_dir / "briefing.md", "must not run")
    assert len(sent) == 1


def test_pre_input_cwd_failure_never_reserves_provider_budget(managed, monkeypatch):
    from zf.runtime.tmux import TmuxError
    config, peer, project, _ = managed
    monkeypatch.chdir(project.project_root)
    tmux = TmuxSession('isolated')
    transport = TmuxTransport(tmux)
    transport.register_role(RoleConfig(name='resident', backend='codex'))
    transport._expected_cwds['resident'] = project.project_root
    monkeypatch.setattr(transport, '_agent_process_liveness', lambda _: (True, ''))
    monkeypatch.setattr(tmux, 'pane_current_path', lambda _: '')
    for call in [lambda: transport.send_task('resident', project.state_dir / 'brief.md', 'fixture'),
                 lambda: transport.compact_context('resident', '/compact')]:
        with pytest.raises(TmuxError, match='unable to verify pane cwd'):
            call()
    assert not peer.budgets and not peer.leases
