"""B10: examples 出厂不得带 dev-route 类 inspect STOP。

R25 当日实锤(1132 record §0.5 配置债 → product-autobuild 首启 STOP):
fanout 系列 example 普遍缺 dev.blocked/dev.failed rework route,用户
copy 后 `zf workflow inspect` 直接 STOP 无法启动。本测试把"无 dev-route
类 STOP"钉为 examples 回归;其余 STOP 类(terminal_event_without_producer
设计性 / skill_resolution_failed 环境性)是已知债,显式 allowlist,
随修随收紧 —— 不许新增。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.workflow.inspection import build_workflow_inspection_report

EXAMPLES = sorted(
    (Path(__file__).resolve().parent.parent / "examples").glob("*.yaml")
)

# dev-route 类 STOP:B10 已清零,永不回归。
_DEV_ROUTE_KINDS = {"missing_rework_route", "explicit_rework_route_missing"}
_DEV_EVENTS = {"dev.blocked", "dev.failed"}


def _stop_diagnostics(config) -> list[dict]:
    report = build_workflow_inspection_report(config)
    out: list[dict] = []
    for diag in report.get("diagnostics", []) or []:
        if str(diag.get("severity") or "").upper() == "STOP":
            out.append(diag)
    return out


@pytest.mark.parametrize(
    "example", EXAMPLES, ids=[p.name for p in EXAMPLES],
)
def test_example_has_no_dev_route_stop(example: Path):
    config = load_config(example)
    offenders = [
        diag for diag in _stop_diagnostics(config)
        if str(diag.get("kind") or "") in _DEV_ROUTE_KINDS
        and str(diag.get("event") or "") in _DEV_EVENTS
    ]
    assert not offenders, (
        f"{example.name}: dev.blocked/dev.failed 缺 rework route 会让用户"
        f"首启 STOP — 补 workflow.rework_routing 两行(B10): {offenders}"
    )
