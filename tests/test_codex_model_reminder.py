"""Automated launches retain configured models without a model-switch modal."""
import pytest

from zf.core.config.schema import RoleConfig, ProviderSessionConfig
from zf.runtime.backend import CodexAdapter


@pytest.mark.parametrize("resume", [False, True])
def test_launch_disables_only_model_switch_reminder(resume):
    role = RoleConfig(name="dev", backend="codex", model="gpt-6.1-terra",
                      model_reasoning_effort="high", provider_session=ProviderSessionConfig(sandbox="workspace-write"))
    cmd = CodexAdapter().build_command(role, session_id="test-session", is_resume=resume)
    assert "notice.hide_rate_limit_model_nudge=true" in cmd
    assert cmd[cmd.index("--model") + 1] == "gpt-6.1-terra"
    assert 'model_reasoning_effort="high"' in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
    assert not any(x.startswith("notice.hide_full_access") for x in cmd)
    if resume:
        assert cmd.index("notice.hide_rate_limit_model_nudge=true") < cmd.index("resume")
