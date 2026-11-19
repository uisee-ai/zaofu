from pathlib import Path
import hashlib

from tests.eight_by_eight_prd_fixture import FEATURES, prepare
from zf.core.config.loader import load_config


def test_prepared_project_compiles_real_eight_by_eight_policy(tmp_path):
    root = tmp_path / "product"
    harness = Path(__file__).resolve().parents[1]
    receipt = prepare(root, harness=harness, image="sha256:" + "a" * 64)
    config = load_config(root / "zf.yaml")
    policy = config.workflow.flow_metadata["task_pipeline"]
    assert config.workflow.plan_approval_enabled
    assert config.workflow.task_attempt.mode == "enforce"
    assert policy["max_active_task_pipelines"] == 16
    assert policy["backpressure"]["max_unverified_tasks"] == 16
    assert policy["backpressure"]["max_integration_queue"] == 16
    for stage, prefix in (("impl", "dev"), ("verify", "verify")):
        assert policy["pools"][stage]["capacity"] == 8
        assert policy["pools"][stage]["role_instances"] == [f"{prefix}-lane-{i}" for i in range(8)]
    roles = {r.instance_id: r for r in config.roles}
    assert roles["planner"].model == "gpt-6-astra"
    assert roles["plan-critic"].model == "gpt-6-astra"
    assert roles["dev-lane-7"].model == "gpt-6.1-terra"
    assert roles["verify-lane-7"].provider_session.sandbox == "workspace-write"
    assert config.runtime.run_manager.resident_agent.enabled
    assert config.runtime.autoresearch_resident.enabled
    assert len(set(receipt["owned_paths"])) == 32
    assert len(FEATURES) == 16
    assert not (root / ".zf").exists()  # preparation does not start/admit a Run
    assert config.runtime.host_verification.protected_files["tests/browser.cjs"] == hashlib.sha256((root / "tests/browser.cjs").read_bytes()).hexdigest()
    commands = set(config.runtime.host_verification.commands)
    assert len(commands) == 19
    assert {f"npm run build && node --test app/tests/{key}.test.mjs" for key, *_ in FEATURES} <= commands
    assert config.workflow.run_limits.timeout_seconds == 0
    assert config.workflow.run_limits.token_budget == 0
    assert all(p.limits.timeout_seconds == 0 and p.limits.token_budget == 0
               for p in config.workflow.execution_profiles.values())


def test_prepared_unattended_policy_routes_expiry_to_run_manager(tmp_path, monkeypatch):
    from tests.test_task_pipeline_fault_matrix import _runtime, _running_operation_v2_attempt
    from zf.core.state.task_attempts import TaskAttemptStore
    from zf.runtime.task_attempt_runtime import reconcile_task_attempts
    from zf.runtime.task_attempt_recovery import pending_task_attempt_recovery_actions

    root = tmp_path / "product"
    prepare(root, harness=Path(__file__).resolve().parents[1], image="sha256:" + "a" * 64)
    runtime = _runtime(root)
    runtime.config = load_config(root / "zf.yaml")
    attempt = _running_operation_v2_attempt(runtime)
    monkeypatch.setattr("zf.runtime.task_attempt_runtime._expired", lambda _: True)
    assert reconcile_task_attempts(runtime) >= 1
    store = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
    assert store.get(attempt["attempt_id"])["status"] == "expired"
    actions = pending_task_attempt_recovery_actions(runtime.state_dir / "projections",
                                                   canonical_store_path=store.path)
    assert len(actions) == 1
    assert actions[0]["action"] == "worker-lifecycle-recover"
    assert actions[0]["operation_id"] == attempt["operation_id"]
    assert actions[0]["attempt_id"] == attempt["attempt_id"]
    assert actions[0]["recovery_decision_owner"] == "run_manager"
