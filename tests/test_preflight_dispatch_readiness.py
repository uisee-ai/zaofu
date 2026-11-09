"""doc 78 W4: zf preflight static dispatch-readiness checks."""

from __future__ import annotations

from types import SimpleNamespace

from zf.runtime.preflight import (
    CheckResult,
    _check_dispatch_chain_imports,
    _check_dispatch_prompt_signature,
    _check_provider_auth_readiness,
    _configured_provider_backends,
    _probe_provider_auth,
    _check_role_backends,
    preflight_ok,
    run_preflight_checks,
)
from zf.runtime.workflow_preflight import _environment_readiness_diagnostics


def test_dispatch_prompt_signature_passes_on_current_code():
    # The flagship check: build_task_prompt accepts every prompt_kind the
    # dispatch sites pass. This is exactly what 0f7d623 broke (a call site
    # passed fanout_child the function didn't accept → all fanout dispatch died).
    res = _check_dispatch_prompt_signature()
    assert res.ok, res.detail


def test_dispatch_prompt_signature_detects_drift(monkeypatch):
    # Simulate the 0f7d623 regression: a build_task_prompt that no longer
    # accepts prompt_kind. Preflight must catch the TypeError.
    import zf.runtime.injection as injection

    def drifted(role_name, briefing_path):  # missing prompt_kind
        return "x"

    monkeypatch.setattr(injection, "build_task_prompt", drifted)
    res = _check_dispatch_prompt_signature()
    assert not res.ok
    assert "signature drift" in res.detail


def test_dispatch_chain_imports_clean():
    res = _check_dispatch_chain_imports()
    assert res.ok, res.detail


def test_role_backends_known_accepts_valid():
    config = SimpleNamespace(roles=[
        SimpleNamespace(name="dev", backend="claude-code", backends=[]),
        SimpleNamespace(name="rev", backend="codex", backends=["mock"]),
    ])
    res = _check_role_backends(config)
    assert res.ok, res.detail


def test_role_backends_known_flags_typo():
    config = SimpleNamespace(roles=[
        SimpleNamespace(name="dev", backend="claude-cod", backends=[]),  # typo
    ])
    res = _check_role_backends(config)
    assert not res.ok
    assert "dev:claude-cod" in res.detail


def test_run_preflight_checks_all_pass_on_minimal_config():
    config = SimpleNamespace(roles=[
        SimpleNamespace(name="dev", backend="mock", backends=[]),
    ])
    results = run_preflight_checks(config)
    assert {r.name for r in results} == {
        "dispatch_prompt_signature",
        "dispatch_chain_imports",
        "provider_session_configs",
        "role_backends_known",
    }
    assert preflight_ok(results)


def test_provider_auth_readiness_reports_logged_out_claude(monkeypatch):
    config = SimpleNamespace(
        roles=[SimpleNamespace(name="dev", backend="claude-code", backends=[])],
        runtime=SimpleNamespace(run_manager=SimpleNamespace(
            backend="",
            self_repair_backend="",
        )),
    )
    monkeypatch.setattr(
        "zf.runtime.preflight._probe_provider_auth",
        lambda backend: (False, "not authenticated; authenticate with `claude /login`"),
    )

    result = _check_provider_auth_readiness(config)

    assert result.ok is False
    assert "claude-code" in result.detail
    assert "/login" in result.detail


def test_provider_auth_readiness_skips_mock_without_probe(monkeypatch):
    config = SimpleNamespace(
        roles=[SimpleNamespace(name="dev", backend="mock", backends=[])],
        runtime=SimpleNamespace(run_manager=SimpleNamespace(
            backend="",
            self_repair_backend="",
        )),
    )
    monkeypatch.setattr(
        "zf.runtime.preflight._probe_provider_auth",
        lambda backend: (_ for _ in ()).throw(AssertionError(backend)),
    )

    result = _check_provider_auth_readiness(config)

    assert result.ok is True
    assert "no real provider" in result.detail


def test_provider_readiness_collects_recovery_and_autoresearch_backends():
    config = SimpleNamespace(
        roles=[SimpleNamespace(name="dev", backend="claude", backends=[])],
        runtime=SimpleNamespace(
            run_manager=SimpleNamespace(
                backend="codex",
                reflect=SimpleNamespace(backend="claude-headless"),
                source_repair=SimpleNamespace(backend="codex-headless"),
            ),
            autoresearch_resident=SimpleNamespace(
                self_repair_backend="claude-code",
            ),
        ),
        autoresearch=SimpleNamespace(
            trigger_policy=SimpleNamespace(self_repair_backend="codex"),
        ),
    )

    assert _configured_provider_backends(config) == {
        "claude-code",
        "claude-headless",
        "codex",
        "codex-headless",
    }


def test_headless_provider_uses_its_underlying_cli_auth_probe(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("zf.runtime.preflight.shutil.which", lambda command: command)
    monkeypatch.setattr(
        "zf.runtime.preflight.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='{"loggedIn": true}',
            stderr="",
        ),
    )

    assert _probe_provider_auth("claude-headless") == (True, "authenticated")


def test_claude_auth_probe_accepts_explicit_api_credential(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-secret")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("zf.runtime.preflight.shutil.which", lambda command: command)
    monkeypatch.setattr(
        "zf.runtime.preflight.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError(args)),
    )

    assert _probe_provider_auth("claude-code") == (
        True,
        "API credential configured via environment",
    )


def test_claude_auth_probe_summarizes_logged_out_json(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("zf.runtime.preflight.shutil.which", lambda command: command)
    monkeypatch.setattr(
        "zf.runtime.preflight.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=(
                '{\n  "loggedIn": false,\n  "authMethod": "none",\n'
                '  "apiProvider": "firstParty"\n}'
            ),
            stderr="",
        ),
    )

    ok, detail = _probe_provider_auth("claude-code")

    assert ok is False
    assert detail.startswith(
        "not authenticated (authMethod=none, apiProvider=firstParty)"
    )
    assert not detail.startswith("{")


def test_codex_headless_uses_codex_cli_auth_probe(monkeypatch):
    monkeypatch.setattr("zf.runtime.preflight.shutil.which", lambda command: command)
    monkeypatch.setattr(
        "zf.runtime.preflight.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="Logged in using ChatGPT",
            stderr="",
        ),
    )

    assert _probe_provider_auth("codex-headless") == (True, "authenticated")


def test_codex_auth_probe_accepts_configured_model_provider_command(
    tmp_path, monkeypatch,
):
    config_home = tmp_path / ".codex"
    config_home.mkdir()
    (config_home / "config.toml").write_text(
        """
model_provider = "cpa"

[model_providers.cpa]
name = "cpa"

[model_providers.cpa.auth]
command = "cpa"
args = ["credential"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(config_home))
    monkeypatch.setattr("zf.runtime.preflight.shutil.which", lambda command: command)

    def run(command, **kwargs):
        if command[:2] == ["codex", "login"]:
            return SimpleNamespace(returncode=1, stdout="not logged in", stderr="")
        assert command == ["cpa", "credential"]
        return SimpleNamespace(returncode=0, stdout="credential-present", stderr="")

    monkeypatch.setattr("zf.runtime.preflight.subprocess.run", run)

    assert _probe_provider_auth("codex-headless") == (
        True,
        "configured model-provider credentials (cpa)",
    )


def test_real_environment_does_not_require_api_key_after_provider_auth(
    monkeypatch,
):
    monkeypatch.setattr(
        "zf.runtime.workflow_preflight.shutil.which",
        lambda command: f"/usr/bin/{command}",
    )

    diagnostics = _environment_readiness_diagnostics(
        {"environment_policy": "real_env_required"},
        allow_missing_env=False,
    )

    assert diagnostics == []


def test_real_environment_still_requires_docker(monkeypatch):
    monkeypatch.setattr(
        "zf.runtime.workflow_preflight.shutil.which",
        lambda _command: None,
    )

    diagnostics = _environment_readiness_diagnostics(
        {"environment_policy": "real_env_required"},
        allow_missing_env=False,
    )

    assert len(diagnostics) == 1
    assert diagnostics[0]["severity"] == "STOP"
    assert diagnostics[0]["message"] == "缺少: docker"
