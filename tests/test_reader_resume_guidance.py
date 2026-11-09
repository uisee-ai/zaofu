"""Resume guidance must expose a safe same-session cursor path to readers."""

from argparse import Namespace

import pytest

from tests.test_synth_budget_recovery import _blocked_synth
from zf.cli.goal import _run_set
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.runtime.workflow_resume_apply import apply_workflow_resume


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
def test_budget_resumed_synth_gets_provider_specific_read_guidance(tmp_path, monkeypatch, backend):
    monkeypatch.setenv("ZF_CLI_CMD", "/current/zf")
    state, log, runtime, dispatch = _blocked_synth(tmp_path, monkeypatch)
    next(role for role in runtime.config.roles if role.instance_id == "task-map-synth").backend = backend
    op_id = dispatch.payload["operation_id"]
    assert _run_set(Namespace(objective="", status="active", reason="resume test", operation=op_id,
                              operation_max_usage_samples=120)) == 0
    cp = next(c for c in build_workflow_resume_projection(state, runtime.config)["operation_checkpoints"]
              if c["operation_id"] == op_id)
    assert apply_workflow_resume(state, runtime.config, checkpoint_id=cp["checkpoint_id"])["applied"] == 1
    assert runtime._recover_lost_fanout_synth_dispatches(log.read_all())
    prompt = runtime.transport.sent[-1][2]
    assert "cannot still use" in prompt
    if backend == "codex":
        assert f'/current/zf artifact read --attempt {dispatch.payload["attempt_id"]} --required --resume' in prompt
        assert "only if" in prompt
    else:
        assert "--required --resume" not in prompt
