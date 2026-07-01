"""doc 78 O-7 fix: canonical project .env loader used by zf start/web/feishu."""

from __future__ import annotations

import os

from zf.core.config.project_context import load_env_file, load_project_env


def test_loads_keys_into_environ(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "FOO=bar\n"
        "export BAZ='qux'\n"
        "WITH_COMMENT=val # trailing note\n"
        "# a comment line\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAZ", raising=False)
    monkeypatch.delenv("WITH_COMMENT", raising=False)

    loaded = load_project_env(tmp_path)

    assert os.environ["FOO"] == "bar"
    assert os.environ["BAZ"] == "qux"          # quotes stripped
    assert os.environ["WITH_COMMENT"] == "val"  # inline comment stripped
    assert set(loaded) == {"FOO", "BAZ", "WITH_COMMENT"}


def test_does_not_override_existing_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("FEISHU_APP_ID=from_dotenv\n", encoding="utf-8")
    monkeypatch.setenv("FEISHU_APP_ID", "from_shell")
    load_project_env(tmp_path)
    assert os.environ["FEISHU_APP_ID"] == "from_shell"  # shell wins


def test_missing_env_is_noop(tmp_path):
    assert load_env_file(tmp_path / ".env") == {}


def test_zf_start_loads_project_env():
    # wire-up proof: zf start calls load_project_env so the watcher (and the
    # O-7 owner.visible auto-delivery) get FEISHU creds + ZF_OWNER_VISIBLE_CHAT.
    import inspect

    from zf.cli import start

    src = inspect.getsource(start.run)
    assert "load_project_env" in src
