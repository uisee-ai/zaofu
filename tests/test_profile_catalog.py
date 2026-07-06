"""Prod flow catalog includes controller examples."""

from __future__ import annotations

from zf.core.profile.flows import flow_id_for_intent, list_flows_detailed


def test_controller_examples_are_catalog_entries():
    ids = {entry["id"] for entry in list_flows_detailed()}
    assert "issue-fanout-v3-codex" in ids
    assert "prd-fanout-v3-codex" in ids
    assert "refactor-lane-v3-codex" in ids


def test_prod_new_lkg_examples_are_not_catalog_entries():
    ids = {entry["id"] for entry in list_flows_detailed()}
    assert "issue-fanout-v2" not in ids
    assert "prd-fanout-v2" not in ids
    assert "refactor-lane-v2" not in ids


def test_codex_prefers_controller_v3_flows():
    assert flow_id_for_intent("build", "codex") == "prd-fanout-v3-codex"
    assert flow_id_for_intent("refactor", "codex") == "refactor-lane-v3-codex"
    assert flow_id_for_intent("maintain", "codex") == "issue-fanout-v3-codex"
    assert flow_id_for_intent("review", "codex") == "issue-fanout-v3-codex"
    assert flow_id_for_intent("refactor", "claude") == "refactor-flow-claude"
