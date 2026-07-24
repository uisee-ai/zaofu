from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_zf_run_teardown_uses_stop_fast_without_direct_event_append():
    script = ROOT / "tests" / "e2e" / "scripts" / "zf_run_teardown.sh"
    text = script.read_text(encoding="utf-8")

    assert "stop --fast" in text
    assert ">> \"$STATE_ABS/events.jsonl\"" not in text
    assert "fallback-append" not in text


def test_scoped_fast_teardown_runbook_bans_process_name_kill():
    runbook = ROOT / "docs" / "runbooks" / "scoped-fast-teardown.md"
    text = runbook.read_text(encoding="utf-8")

    assert "zf stop --fast" in text
    assert "Do not use `pkill`" in text
    assert "No script should append directly to `events.jsonl`" in text


def test_three_workflow_e2e_defaults_to_controller_v3_with_explicit_legacy_mode():
    script = ROOT / "tests" / "e2e" / "scripts" / "run_prod_new_three_workflow_e2e.sh"
    text = script.read_text(encoding="utf-8")

    assert 'ZF_E2E_TEMPLATE_FAMILY:-controller-v3' in text
    assert 'controller/prd-fanout-v3.yaml' in text
    assert 'controller/issue-fanout-v3.yaml' in text
    assert 'controller/refactor-lane-v3.yaml' in text
    assert "legacy-v2)" in text


def test_product_fanout_manual_uses_parseable_start_and_stop_commands():
    for name in (
        "18-product-fanout-real-e2e.md",
        "18-product-fanout-real-e2e.en.md",
    ):
        text = (ROOT / "docs" / "manual" / name).read_text(encoding="utf-8")
        assert "zf start --path" not in text
        assert "zf stop --path" not in text
