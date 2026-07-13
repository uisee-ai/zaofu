"""P1-5 (2026-07-09): the report-projection promotion set derives top-level
contract fields from the EventSchemaRegistry, so a new contract field flows into
children/*/report.json automatically — matching the briefing education side —
instead of needing a hardcoded whitelist entry (which was patched N times and
silently dropped fields whenever a new one was missed, burning a judge round).
"""

from __future__ import annotations

from types import SimpleNamespace

from zf.runtime.fanout import REPORT_AUDIT_FIELD_KEYS, report_audit_field_keys


def _config_with_schema(event_type: str, *, required=(), non_empty=()):
    return SimpleNamespace(
        workflow=SimpleNamespace(
            dag=SimpleNamespace(
                event_schemas={
                    event_type: {
                        "required": list(required),
                        "non_empty": list(non_empty),
                    }
                }
            )
        )
    )


def test_static_superset_always_included_even_without_schema():
    # No schema configured (rule is None) → the static superset is still
    # returned in full, preserving pre-P1-5 behavior.
    keys = report_audit_field_keys(SimpleNamespace(), "reader.child.completed")
    assert set(REPORT_AUDIT_FIELD_KEYS).issubset(set(keys))


def test_new_schema_contract_field_flows_in_without_hardcoding():
    # A brand-new top-level contract field the static whitelist never heard of.
    cfg = _config_with_schema(
        "reader.child.completed",
        required=["summary"],
        non_empty=["coverage_matrix_v2"],  # novel field, not in the static set
    )
    keys = report_audit_field_keys(cfg, "reader.child.completed")
    assert "coverage_matrix_v2" in keys  # derived from schema, no hardcoding
    assert "summary" in keys             # required flows too
    assert set(REPORT_AUDIT_FIELD_KEYS).issubset(set(keys))  # superset preserved


def test_report_container_not_promoted_as_toplevel_key():
    # `report` is the nested container, not a top-level promoted field.
    cfg = _config_with_schema("reader.child.completed", required=["report"])
    keys = report_audit_field_keys(cfg, "reader.child.completed")
    assert "report" not in keys


def test_no_duplicate_when_schema_repeats_static_key():
    cfg = _config_with_schema("e", non_empty=["evidence_refs"])  # already static
    keys = report_audit_field_keys(cfg, "e")
    assert keys.count("evidence_refs") == 1
