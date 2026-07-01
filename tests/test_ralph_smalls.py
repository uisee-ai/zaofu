"""V4:Ralph 借鉴三小物(on_fail 文案/预算直白化/guardrails 轻档)。"""

from __future__ import annotations

from pathlib import Path

from zf.runtime.injection import render_guardrails_block, render_on_fail_hint

_REPO = Path(__file__).resolve().parent.parent


class TestOnFailHint:
    def test_hint_rendered_when_payload_carries_it(self):
        out = render_on_fail_hint({"on_fail": "Run cargo fmt --all and retry"})
        assert "修复提示" in out and "cargo fmt" in out

    def test_silent_when_absent(self):
        assert render_on_fail_hint({}) == ""
        assert render_on_fail_hint(None) == ""

    def test_gate_config_carries_on_fail(self, tmp_path):
        from zf.core.config.loader import load_config
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\nproject: {name: t}\n'
            "quality_gates:\n"
            "  static: {enabled: true, on_fail: '先跑 zf check 再重交'}\n"
        )
        cfg = load_config(p)
        assert cfg.quality_gates["static"].on_fail == "先跑 zf check 再重交"


class TestGuardrailsLite:
    def test_rendered_as_hints_block(self, tmp_path):
        from zf.core.config.loader import load_config
        p = tmp_path / "zf.yaml"
        p.write_text(
            'version: "1.0"\nproject: {name: t}\n'
            "roles:\n- name: dev\n  backend: mock\n  instance_id: dev\n"
            "  guardrails:\n  - 禁止跨 slice 改根配置\n  - 每步后跑目标包测试\n"
        )
        cfg = load_config(p)
        block = render_guardrails_block(cfg.roles[0])
        assert "提示,非门" in block and "禁止跨 slice" in block

    def test_empty_renders_nothing(self):
        class R: guardrails = []
        assert render_guardrails_block(R()) == ""

    def test_guardrails_never_enter_gate_modules(self):
        # rg 证明:guardrails 不参与任何门判定(doc 90 rev2.1 边界)
        offenders = []
        for sub in ("core/verification", "core/safety"):
            base = _REPO / "src/zf" / sub
            if not base.exists():
                continue
            for path in base.rglob("*.py"):
                if "guardrails" in path.read_text(encoding="utf-8", errors="replace"):
                    offenders.append(str(path))
        assert offenders == []


class TestBudgetPlain:
    def test_lane_contract_speaks_plainly(self):
        import sys
        sys.path.insert(0, str(_REPO / "tests"))
        from test_lane_pipeline_compiler import _hermes_raw, _hermes_roles
        from zf.core.workflow.lane_pipeline import (
            compile_lane_pipeline,
            parse_lane_pipeline,
        )
        contract, _ = compile_lane_pipeline(
            parse_lane_pipeline(_hermes_raw()), _hermes_roles(),
        )
        assert "次返工" in contract["budget_plain"]
        assert "quarantine" in contract["budget_plain"]
