from zf.runtime.provider_permissions import codex_security_config_for_profile
from zf.runtime.channel_contracts import permission_profile_write_policy


def test_investigation_profile_allows_execution_without_file_write_policy() -> None:
    security = codex_security_config_for_profile("issue_investigation")
    policy = permission_profile_write_policy("issue_investigation")
    assert security["sandbox"] == "danger-full-access"
    assert policy["investigation_only"] is True
    assert policy["allowed_write_paths"] == []
    assert policy["dangerous"] is False
