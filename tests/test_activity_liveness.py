from pathlib import Path
from unittest.mock import patch

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.orchestrator import Orchestrator


def test_activity_burst_reuses_registry_and_observes_external_idle(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    runtime = Orchestrator(state, ZfConfig(project=ProjectConfig(name="liveness", workspace=str(tmp_path))), transport=None)
    path = state / "role_sessions.yaml"
    registry = RoleSessionRegistry(path, str(tmp_path))
    registry.record_heartbeat("dev-1", {"state": "busy", "current_task_id": "T1"})
    with patch("zf.runtime.activity_liveness.RoleSessionRegistry", wraps=RoleSessionRegistry) as constructor:
        for _ in range(100):
            runtime._apply_housekeeping(ZfEvent(type="codex.hook.post_tool_use", actor="dev-1"))
        assert constructor.call_count == 1
        # A separate writer's idle transition invalidates the snapshot.
        registry.record_heartbeat("dev-1", {"state": "idle", "current_task_id": ""})
        runtime._apply_housekeeping(ZfEvent(type="codex.hook.post_tool_use", actor="dev-1"))
        assert constructor.call_count == 2
    fresh = RoleSessionRegistry(path, str(tmp_path))
    assert fresh.get_last_heartbeat("dev-1")[1]["state"] == "idle"


def test_activity_refresh_retains_other_writer_fields_and_reloads_after_replace(tmp_path: Path):
    from types import SimpleNamespace
    from zf.runtime.activity_liveness import apply_observed_activity
    import yaml

    path = tmp_path / "role_sessions.yaml"
    runtime = SimpleNamespace(state_dir=tmp_path, project_root=tmp_path)
    registry = RoleSessionRegistry(path, str(tmp_path))
    registry.record_heartbeat("dev-1", {"state": "busy", "current_task_id": "T1"})
    event = ZfEvent(type="agent.tool.use", actor="dev-1")
    apply_observed_activity(runtime, event)
    registry.record_heartbeat("dev-2", {"state": "busy", "current_task_id": "T2"})
    data = yaml.safe_load(path.read_text())
    data["instance_meta"]["dev-1"]["last_heartbeat_at"] = "2020-01-01T00:00:00+00:00"
    replacement = path.with_suffix(".tmp")
    replacement.write_text(yaml.safe_dump(data))
    replacement.replace(path)
    apply_observed_activity(runtime, event)
    fresh = RoleSessionRegistry(path, str(tmp_path))
    assert fresh.get_last_heartbeat("dev-1")[1]["source"] == "activity_liveness"
    assert fresh.get_last_heartbeat("dev-2")[1]["current_task_id"] == "T2"


def test_usage_burst_preserves_accounting_without_rewriting_same_liveness(tmp_path):
    from zf.core.config.schema import RoleConfig

    state = tmp_path / 'state'
    state.mkdir()
    config = ZfConfig(project=ProjectConfig(name='usage', workspace=str(tmp_path)), roles=[RoleConfig(name='dev', instance_id='dev-1', backend='mock')])
    runtime = Orchestrator(state, config, transport=None)
    original = RoleSessionRegistry.record_heartbeat
    writes = []
    def record(registry, *args, **kwargs):
        writes.append(args)
        return original(registry, *args, **kwargs)
    with patch.object(RoleSessionRegistry, 'record_heartbeat', record):
        for _ in range(20):
            runtime._apply_housekeeping(ZfEvent(type='agent.usage', actor='dev-1', payload={'usage': {'input_tokens': 1}}))
    assert len(writes) == 1
    assert runtime.cost_tracker.per_role_totals()['dev'].entries == 20
    # A changed context ratio must be persisted even inside the liveness gap.
    runtime._apply_housekeeping(ZfEvent(type='agent.usage', actor='dev-1', payload={'context_usage_ratio': .9}))
    assert RoleSessionRegistry(state/'role_sessions.yaml', str(tmp_path)).get_last_heartbeat('dev-1')[1]['context_usage_ratio'] == .9
