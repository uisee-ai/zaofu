"""doc 90 A4:cj-min 重写实证 — 精简形与全量手写形三层逐字段等价。

精简形(54 行,三件套:laneRoleTemplate + schemaProfile + 约定铸造)
必须与全量手写形(381 行)产出完全相同的 roles / effective schemas /
compiled lane contract——证明语法层只是"另一条进同一配置的路"。
"""

from __future__ import annotations

from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.workflow.lane_pipeline import compile_lane_pipeline

_FIX = Path(__file__).resolve().parent / "fixtures" / "lane_pipeline"
_CONCISE = _FIX / "cjmin-concise.yaml"
_FULL = _FIX / "cjmin-full.yaml"


def _role_signature(role) -> tuple:
    return (
        role.name, role.instance_id, role.backend, role.role_kind,
        tuple(role.skills), tuple(role.stages), tuple(role.publishes),
        role.stuck_threshold_seconds,
    )


def _load_pair():
    return load_config(_CONCISE), load_config(_FULL)


class TestParity:
    def test_roles_identical(self):
        concise, full = _load_pair()
        c = sorted(_role_signature(r) for r in concise.roles)
        f = sorted(_role_signature(r) for r in full.roles)
        assert c == f
        assert len(c) == 16  # 15 lane roles + judge

    def test_effective_event_schemas_identical(self):
        concise, full = _load_pair()
        assert concise.workflow.dag.event_schemas == full.workflow.dag.event_schemas
        assert len(concise.workflow.dag.event_schemas) == 22

    def test_compiled_lane_contract_identical(self):
        concise, full = _load_pair()
        cc, cd = compile_lane_pipeline(
            concise.workflow.pipelines[0], concise.roles,
        )
        fc, fd = compile_lane_pipeline(
            full.workflow.pipelines[0], full.roles,
        )
        assert [d for d in cd if d["severity"] == "STOP"] == []
        assert [d for d in fd if d["severity"] == "STOP"] == []
        # schema_profile 是精简形的来源标注,其余合同逐字段相等
        cc.pop("schema_profile"), fc.pop("schema_profile")
        assert cc == fc

    def test_inspect_zero_stop_both(self, tmp_path):
        from zf.core.workflow.inspection import (
            build_workflow_inspection_report,
        )
        for cfg_path in (_CONCISE, _FULL):
            cfg = load_config(cfg_path)
            report = build_workflow_inspection_report(
                cfg, project_root=tmp_path, state_dir=tmp_path / ".zf",
            )
            stops = [
                d for d in report["diagnostics"]
                if d.get("severity") == "STOP"
                and "lane_pipeline" in str(d.get("kind"))
            ]
            assert stops == [], (cfg_path.name, stops)

    def test_line_budget_ledger(self):
        concise_lines = len(_CONCISE.read_text().splitlines())
        full_lines = len(_FULL.read_text().splitlines())
        assert concise_lines <= 60, "精简形管线本体须 ≤60 行(doc 90 A4)"
        assert full_lines >= concise_lines * 5, (
            f"账本:全量 {full_lines} 行 vs 精简 {concise_lines} 行 —— "
            f"派生事实(15 role + 22 schema + lanes/routes)由三件套铸造"
        )
