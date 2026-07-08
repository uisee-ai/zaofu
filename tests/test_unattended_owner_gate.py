"""P0-7(审计 D5):授权自主 + owner 通道未配 = validate FAIL。"""

from __future__ import annotations

from types import SimpleNamespace

from zf.cli.validate import _unattended_autonomy_enabled


def _config(mode: str = "supervised", repair: bool = False):
    return SimpleNamespace(
        autoresearch=SimpleNamespace(trigger_policy=SimpleNamespace(mode=mode)),
        runtime=SimpleNamespace(run_manager=SimpleNamespace(
            source_repair=SimpleNamespace(enabled=repair),
        )),
    )


def test_continuous_autoresearch_counts_as_unattended() -> None:
    assert _unattended_autonomy_enabled(_config(mode="continuous")) is True


def test_source_repair_counts_as_unattended() -> None:
    assert _unattended_autonomy_enabled(_config(repair=True)) is True


def test_supervised_defaults_are_attended() -> None:
    assert _unattended_autonomy_enabled(_config()) is False


def test_declared_budget_without_enforcement_warns(tmp_path, capsys, monkeypatch):
    """FIX-5③(bizsim r4):声明 global_budget_usd 但关闭 enforcement → WARN。"""
    import sys as _sys
    from zf.cli.main import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n  name: budget-demo\n"
        "global_budget_usd: 700.0\n"
        "budget_enforcement_enabled: false\n",
        encoding="utf-8",
    )
    rc = main(["validate"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "budget_enforcement_enabled=false" in err
    assert "700.0" in err


def test_enforced_budget_does_not_warn(tmp_path, capsys, monkeypatch):
    from zf.cli.main import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n  name: budget-demo\n"
        "global_budget_usd: 700.0\n",
        encoding="utf-8",
    )
    rc = main(["validate"])
    assert rc == 0
    assert "budget_enforcement_enabled=false" not in capsys.readouterr().err


def test_fanout_writer_without_quality_gates_warns(tmp_path, capsys, monkeypatch):
    """FIX-10(bizsim r4 F10):写入型 fanout workflow 未配 quality_gates →
    candidate 合成树零验证,validate 必须 WARN。"""
    from zf.cli.main import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n  name: gates-demo\n"
        "roles:\n"
        "  - name: dev\n    backend: mock\n    role_kind: writer\n"
        "workflow:\n"
        "  stages:\n"
        "    - id: impl\n"
        "      trigger: task_map.ready\n"
        "      topology: fanout_writer_scoped\n"
        "      roles: [dev]\n"
        "      task_map: artifacts/task_map.json\n"
        "      aggregate:\n"
        "        mode: wait_for_all\n"
        "        success_event: impl.done\n"
        "        failure_event: impl.failed\n",
        encoding="utf-8",
    )
    rc = main(["validate"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "quality_gates" in err
    assert "cross-lane" in err


def test_fanout_writer_with_quality_gates_no_warning(tmp_path, capsys, monkeypatch):
    from zf.cli.main import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n  name: gates-demo\n"
        "roles:\n"
        "  - name: dev\n    backend: mock\n    role_kind: writer\n"
        "workflow:\n"
        "  stages:\n"
        "    - id: impl\n"
        "      trigger: task_map.ready\n"
        "      topology: fanout_writer_scoped\n"
        "      roles: [dev]\n"
        "      task_map: artifacts/task_map.json\n"
        "      aggregate:\n"
        "        mode: wait_for_all\n"
        "        success_event: impl.done\n"
        "        failure_event: impl.failed\n"
        "quality_gates:\n"
        "  build:\n"
        "    required_checks: ['true']\n",
        encoding="utf-8",
    )
    rc = main(["validate"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "cross-lane" not in err
