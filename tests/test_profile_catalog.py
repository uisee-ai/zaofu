"""Prod flow catalog includes controller examples."""

from __future__ import annotations

from zf.core.profile.flows import flow_id_for_intent, list_flows_detailed


def test_controller_examples_are_catalog_entries():
    entries = {entry["id"]: entry for entry in list_flows_detailed()}
    ids = set(entries)
    assert "issue-fanout-v3-codex" in ids
    assert "prd-fanout-v3-codex" in ids
    assert "refactor-lane-v3-codex" in ids
    assert entries["issue-fanout-v3-codex"]["roles"] == 5
    assert entries["issue-fanout-v3-claude"]["roles"] == 5


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
    # claude now has symmetric controller v3 variants (was flat-flow fallback).
    assert flow_id_for_intent("build", "claude") == "prd-fanout-v3-claude"
    assert flow_id_for_intent("refactor", "claude") == "refactor-lane-v3-claude"
    assert flow_id_for_intent("maintain", "claude") == "issue-fanout-v3-claude"
    assert flow_id_for_intent("review", "claude") == "issue-fanout-v3-claude"
