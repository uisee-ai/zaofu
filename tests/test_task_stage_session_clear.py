from zf.core.state.role_sessions import RoleSessionRegistry


def test_clear_retires_durable_task_stage_session_before_reactivation(tmp_path):
    registry = RoleSessionRegistry(tmp_path / "roles.yaml", project_root="/project")
    identity = dict(workflow_run_id="run-1", task_id="T1", stage="impl",
                    rework_affinity_id="map:impl")
    binding = registry.bind_task_stage_session(
        **identity, role_instance="impl-0", role_config_digest="digest",
        workspace_generation=1, placement_epoch=1, backend="codex",
    )
    registry.activate_task_stage_session(binding_key=binding["binding_key"], role_instance="impl-0")
    old = "57575757-5757-5757-5757-575757575757"
    registry.bind_codex_session("impl-0", old)
    registry.clear("impl-0")
    registry.clear("impl-0")
    registry = RoleSessionRegistry(tmp_path / "roles.yaml", project_root="/project")
    registry.activate_task_stage_session(binding_key=binding["binding_key"], role_instance="impl-0")
    assert registry.get("impl-0") is None
    current = registry.task_stage_binding(**identity)
    assert current["binding_key"] == binding["binding_key"]
    assert current["workspace_generation"] == 1
    assert current["rotation_counter"] == 1
    registry.bind_codex_session("impl-0", old)
    assert registry.get("impl-0") is None  # late departing hooks remain fenced
    new = "58585858-5858-5858-5858-585858585858"
    registry.bind_codex_session("impl-0", new)
    assert str(registry.get("impl-0")) == new
    assert registry.task_stage_binding(**identity)["session_id"] == new
