from pathlib import Path

import yaml
import pytest

from tests.continuity_prd_fixture import configuration
from zf.core.config.loader import ConfigError, load_config


def test_real_qualification_preserves_v5_and_delivers_reader_policy(tmp_path):
    harness = Path(__file__).resolve().parents[1]
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump_all(configuration(tmp_path, harness=harness)))
    config = load_config(path)
    assert config.version == "5.0.1"
    assert config.workflow.plan_approval_enabled
    assert config.project.setup_script.endswith("node --version && node_modules/.bin/tsc --version")
    assert config.runtime.workdirs.provision_paths == []
    assert config.runtime.workdirs.required_paths == ["node_modules"]
    assert config.workflow.orchestration.normal_frontier_owner == "wrc"
    pipeline = config.workflow.flow_metadata["task_pipeline"]
    assert pipeline["mode"] == "blocking"
    assert pipeline["candidate"]["final_verify_target"] == "frozen_exact_commit"
    for stage in ("impl", "verify"):
        assert pipeline["pools"][stage]["capacity"] == 2
        assert len(pipeline["pools"][stage]["role_instances"]) == 2
    limits = [config.workflow.run_limits, *[
        p.limits for p in config.workflow.execution_profiles.values()
    ]]
    assert all(item.token_budget == item.timeout_seconds == 0 for item in limits)
    assert config.workflow.run_limits.cost_budget_usd == 40
    for role in config.roles:
        assert role.model == "gpt-6.1-terra"
        assert role.model_reasoning_effort == "high"
        assert role.provider_session.sandbox == "workspace-write", role.instance_id
        assert role.provider_session.native_delegation is False
        if role.flow_kind == "prd" and role.role_kind == "reader":
            assert role.lifecycle.reader_context_scope == "dispatch", role.instance_id
        else:
            assert role.lifecycle.reader_context_scope == "workflow", role.instance_id


@pytest.mark.parametrize("sandbox", ["danger-full-access", None, []])
def test_lane_template_rejects_unsupported_sandbox(tmp_path, sandbox):
    documents = configuration(tmp_path, harness=Path(__file__).resolve().parents[1])
    documents[0]["spec"]["roleDefaults"]["provider_session"]["sandbox"] = sandbox
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump_all(documents))
    with pytest.raises(ConfigError, match="sandbox"):
        load_config(path)


def test_unattended_qualification_policy_is_bounded_and_keeps_approval(tmp_path, monkeypatch):
    from tests.unattended_prd_fixture import prepare
    monkeypatch.setattr('tests.unattended_prd_fixture.subprocess.check_output', lambda *args, **kwargs: 'sha256:'+'a'*64)
    root=tmp_path/'product'
    prepare(root,harness=Path(__file__).resolve().parents[1],compiler_root=Path('/installed/compiler'))
    config=load_config(root/'zf.yaml')
    assert config.workflow.plan_approval_enabled
    assert config.runtime.host_verification.enabled
    assert config.runtime.host_verification.max_executions_per_run == 12
    assert config.runtime.host_verification.protected_files['tests/browser.cjs']
    assert Path(config.runtime.autoresearch_resident.worktree_root).is_relative_to(root / '.zf')
    assert config.runtime.task_publication.enabled
    assert config.runtime.task_publication.max_publications_per_run == 4
    assert set(config.runtime.task_publication.paths) == {
        'app/src/normalize.ts', 'app/tests/normalize.test.mjs',
        'app/src/numbers.ts', 'app/tests/numbers.test.mjs',
    }
    assert config.runtime.run_manager.source_repair.model == 'gpt-6.1-terra'
    assert config.runtime.run_manager.source_repair.publish_candidate
    assert config.runtime.run_manager.source_repair.restart_policy == 'operator_approved'
    assert config.runtime.run_manager.source_repair.allow_paths == ['src/zf/runtime/host_verification.py','tests/test_host_verification.py']
    from tests.unattended_prd_fixture import request_payload
    from zf.runtime.workflow_request_identity import request_source_ref
    assert request_source_ref(root, root / '.zf', request_payload(root)) == str(root / 'docs/prd/TODO.md')
