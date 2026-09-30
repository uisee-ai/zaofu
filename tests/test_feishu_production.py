from __future__ import annotations

import builtins
from types import SimpleNamespace

import yaml

from zf.cli.main import main
from zf.integrations.feishu.production import (
    production_config_issues,
    production_context_issues,
)


def _config(*, require: bool, users: dict[str, object] | None = None):
    identity = SimpleNamespace(
        enabled=True,
        require_signed_actions=require,
        action_token_secret_env="ACTION_SECRET",
        verification_token_env="VERIFY_SECRET",
        users=users or {"ou_real": object()},
    )
    return SimpleNamespace(integrations=SimpleNamespace(
        feishu_identity=identity,
        feishu_routing={"oc_project": object()},
    ))


def test_disabled_identity_has_no_production_preflight_errors() -> None:
    cfg = SimpleNamespace(
        integrations=SimpleNamespace(feishu_identity=SimpleNamespace(enabled=False)),
    )
    assert production_config_issues(cfg, environ={}) == ()
    assert production_context_issues(
        [SimpleNamespace(config=cfg)], environ={}, require_enabled=True,
    )


def test_real_identity_requires_signed_tokens_secrets_and_real_principals() -> None:
    issues = production_config_issues(
        _config(require=False, users={"__zf_feishu_openid_unset__": object()}),
        environ={},
        require_callback_signature=True,
    )
    assert any("require_signed_actions" in issue for issue in issues)
    assert any("ACTION_SECRET" in issue for issue in issues)
    assert any("VERIFY_SECRET" in issue for issue in issues)
    assert any("placeholder" in issue for issue in issues)


def test_ws_preflight_does_not_require_webhook_encrypt_key() -> None:
    issues = production_config_issues(
        _config(require=True),
        environ={"ACTION_SECRET": "a"},
    )
    assert not any("VERIFY_SECRET" in issue for issue in issues)


def test_webhook_preflight_requires_signature_encrypt_key() -> None:
    issues = production_config_issues(
        _config(require=True),
        environ={"ACTION_SECRET": "a"},
        require_callback_signature=True,
    )
    assert any("signature Encrypt Key" in issue for issue in issues)


def test_real_identity_rejects_wildcard_only_write_route() -> None:
    cfg = _config(require=True)
    cfg.integrations.feishu_routing = {"*": object()}
    issues = production_config_issues(
        cfg,
        environ={"ACTION_SECRET": "a", "VERIFY_SECRET": "v"},
    )
    assert any("exact project chat" in issue for issue in issues)


def test_complete_production_identity_passes() -> None:
    cfg = _config(require=True)
    assert production_config_issues(
        cfg, environ={"ACTION_SECRET": "a", "VERIFY_SECRET": "v"},
    ) == ()


def test_shared_ws_checks_each_project_context() -> None:
    complete = SimpleNamespace(
        config=_config(require=True),
    )
    incomplete = SimpleNamespace(
        config=SimpleNamespace(
            integrations=SimpleNamespace(
                feishu_identity=SimpleNamespace(enabled=False),
            ),
        ),
    )
    issues = production_context_issues(
        [complete, incomplete],
        environ={"ACTION_SECRET": "a", "VERIFY_SECRET": "v"},
        require_enabled=True,
    )
    assert issues == (
        "integrations.feishu_identity.enabled must be true for real transport",
    )


def test_real_push_runs_production_preflight_before_transport(
    tmp_path, monkeypatch, capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.safe_dump({
        "version": "1.0",
        "project": {"name": "feishu-preflight"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_identity": {"enabled": True}},
    }))
    assert main(["init"]) == 0
    result = main(["feishu", "push", "--transport", "real", "--to", "oc_project"])
    assert result == 2
    assert "production preflight failed" in capsys.readouterr().err


def test_real_consume_runs_production_preflight_before_subprocess(
    tmp_path, monkeypatch, capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.safe_dump({
        "version": "1.0",
        "project": {"name": "feishu-consume-preflight"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_identity": {"enabled": True}},
    }))
    assert main(["init"]) == 0
    result = main(["feishu", "consume", "im.message.receive_v1"])
    assert result == 2
    assert "consume production preflight failed" in capsys.readouterr().err


def test_webhook_server_runs_production_preflight_before_bind(
    tmp_path, monkeypatch, capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.safe_dump({
        "version": "1.0",
        "project": {"name": "feishu-webhook-preflight"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_identity": {"enabled": True}},
    }))
    assert main(["init"]) == 0
    result = main(["feishu", "serve", "--port", "0"])
    assert result == 2
    assert "webhook production preflight failed" in capsys.readouterr().err


def test_bridge_watch_runs_production_preflight_before_sdk_import(
    tmp_path, monkeypatch, capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.safe_dump({
        "version": "1.0",
        "project": {"name": "feishu-bridge-preflight"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_identity": {"enabled": True}},
    }))
    assert main(["init"]) == 0
    monkeypatch.setenv("FEISHU_APP_ID", "cli_preflight")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret_preflight")
    original_import = builtins.__import__

    def reject_sdk_import(name, *args, **kwargs):
        if name == "lark_oapi":
            raise ImportError("SDK import must happen after production preflight")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_sdk_import)
    result = main(["feishu", "bridge", "--watch"])
    assert result == 2
    error = capsys.readouterr().err
    assert "bridge production preflight failed" in error
    assert "lark-oapi is not installed" not in error


def test_bridge_watch_loads_project_dotenv_before_resolving_credentials(
    tmp_path, monkeypatch, capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "ACTION_SECRET",
    ):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    (tmp_path / "zf.yaml").write_text(yaml.safe_dump({
        "version": "1.0",
        "project": {"name": "feishu-bridge-dotenv", "state_dir": "runtime"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {
            "feishu_identity": {
                "enabled": True,
                "require_signed_actions": True,
                "action_token_secret_env": "ACTION_SECRET",
                "users": {"ou_real": {"operator": "min", "level": "operator"}},
            },
            "feishu_routing": {"oc_project": {"target": "kanban_agent"}},
        },
    }))
    (tmp_path / ".env").write_text(
        "FEISHU_APP_ID=cli_from_dotenv\n"
        "FEISHU_APP_SECRET=secret_from_dotenv\n"
        "ACTION_SECRET=action-secret\n",
        encoding="utf-8",
    )
    assert main(["init"]) == 0
    original_import = builtins.__import__

    def reject_sdk_import(name, *args, **kwargs):
        if name == "lark_oapi":
            raise ImportError("SDK import must happen after dotenv load")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_sdk_import)
    result = main(["feishu", "bridge", "--watch"])
    assert result == 1
    error = capsys.readouterr().err
    assert "lark-oapi is not installed" in error
    assert "FEISHU_APP_ID / FEISHU_APP_SECRET" not in error

