"""doc 78 W4: zf preflight static dispatch-readiness checks."""

from __future__ import annotations

from types import SimpleNamespace

from zf.runtime.preflight import (
    CheckResult,
    _check_dispatch_chain_imports,
    _check_dispatch_prompt_signature,
    _check_role_backends,
    preflight_ok,
    run_preflight_checks,
)


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
        "role_backends_known",
    }
    assert preflight_ok(results)
