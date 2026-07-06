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
