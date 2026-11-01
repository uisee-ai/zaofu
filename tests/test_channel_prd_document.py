"""Full author Markdown reaches the canonical PRD and same-version export."""
import hashlib
import json
from copy import deepcopy

import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_contract import emit_structured_reply_events
from zf.runtime.channel_prd_document import prd_view_index, render_prd_document, validate_prd_document


def document():
    return {
        "summary": "Short overview, intentionally different from the full document.",
        "prd_markdown": "# Inventory PRD\n\n## Atomic reservation\n" + "Detailed failure behavior. " * 1000 + "END-OF-FULL-PRD\n",
        "requirement_coverage": [{"id": "REQ-1", "source_refs": ["source:PRD#reservation"],
                                  "section": "Atomic reservation", "acceptance_ids": ["AC-1"], "status": "covered"}],
        "acceptance_criteria": [{"id": "AC-1", "criterion": "Concurrent reservations cannot oversell."}],
        "decision_log": [{"decision": "Atomic reservation", "source_refs": ["source:PRD#reservation"], "rationale": "Source requirement"}],
        "assumptions": [{"id": "A-01", "statement": "Reservations expire after 15 minutes.", "risk": "Time source drift", "source_refs": ["source:PRD#expiry"]}],
        "risks": [{"id": "R-01", "risk": "Concurrent expiration", "mitigation": "Serialize expiration with reservation", "source_refs": ["source:PRD#reservation"]}],
        "dissent": [{"id": "D-01", "statement": "Expiry duration remains disputed.", "custom_evidence": {"minutes": [10, 15]}}],
        "open_questions": [],
    }


def spec_document():
    body = deepcopy(document())
    body.update({
        "spec_format": "prd-spec.v1",
        "document_control": {
            "revision": "r1", "status": "draft", "owner": "owner", "profile": "full",
        },
        "user_stories": [{
            "id": "US-1", "title": "Reserve inventory", "priority": "P1",
            "persona": "operator", "value": "Reserve without overselling.",
            "independent_test": "Run AC-1 against concurrent requests.",
            "acceptance_ids": ["AC-1"],
        }],
        "edge_cases": [{
            "id": "EC-1", "trigger": "No inventory remains",
            "expected_behavior": "The request is rejected without changing stock.",
            "acceptance_ids": ["AC-1"],
        }],
        "key_entities": [{
            "id": "ENT-1", "name": "Inventory reservation",
            "purpose": "Represents one held unit.", "lifecycle": "requested -> active -> expired",
            "constraints": ["cannot oversell"],
        }],
        "prd_markdown": """# Inventory PRD

## 00. Document control
Revision r1; owner owner.

## 01. Overview
Reserve inventory without overselling.

## 02. Background and problem
Concurrent reservations currently race.

## 03. Goals, non-goals, and scope
Goals: reserve safely. Non-goal: replace inventory accounting.

## 04. User personas and prioritized scenarios
P1: an operator reserves stock and verifies the result.

## 05. User stories and independent tests
US-1: The operator reserves inventory; independent test: run AC-1 concurrently.

## 06. User flow and interaction
The operator submits a reservation and receives a result.

## 07. Functional requirements
### FR-001: Reserve atomically
The system SHALL reserve available inventory once.

#### Scenario: Concurrent reservation
- GIVEN one unit remains
- WHEN two clients reserve it concurrently
- THEN exactly one reservation succeeds

## 08. Business rules, permissions, and state
Only authorized operators may reserve; expiration returns inventory.

## 09. Edge cases and error scenarios
When no inventory remains, the request is rejected without changing stock.

## 10. Key entities and contracts
Inventory reservation moves from requested to active to expired.

## 11. Non-functional requirements
Reservation writes are auditable and deterministic.

## 12. Success metrics
Operators can complete a reservation without an oversell.

## 13. Dependencies, assumptions, risks, and open questions
The inventory source is authoritative; no open questions remain.

## 14. Acceptance Criteria
AC-1: Concurrent reservations cannot oversell.

## 15. Implementation Companion / Handoff
The control plane, data plane, and event-driven boundary are separate. Long-running work is asynchronous. Web/API exposes controlled actions and read-only projections. Canonical business state is stored separately from the append-only event ledger and immutable evidence artifacts/sidecars. Every state transition records actor, reason, request ID, and correlation ID. Direct writes are rejected, projections remain read-only, events and artifacts are immutable, and recovery evidence is required.
""",
        "requirement_coverage": [{
            "id": "REQ-1", "source_refs": ["source:PRD#reservation"],
            "section": "Functional requirements", "acceptance_ids": ["AC-1"], "status": "covered",
        }],
        "acceptance_criteria": [{"id": "AC-1", "criterion": "Concurrent reservations cannot oversell."}],
        "non_functional_requirements": [{"id": "NFR-1", "statement": "Reservation writes are auditable."}],
        "success_metrics": [{
            "id": "SC-1", "statement": "Operators complete reservations without oversell.",
            "baseline": "TBD (owner decision required)", "target": "TBD (owner decision required)",
            "measurement_method": "Run AC-1 and inspect oversell count.", "owner": "verify",
            "measurement_timing": "At each verification run.", "status": "NOT_RUN",
        }],
        "open_questions": [],
    })
    return body


@pytest.mark.parametrize("missing", [False, True])
def test_full_document_reaches_canonical_artifact_or_requires_repair(tmp_path, missing):
    state = tmp_path / "state"
    writer = EventWriter(EventLog(state / "events.jsonl"))
    writer.emit("channel.created", actor="test", payload={"channel_id": "ch-document"})
    body = document()
    if missing:
        del body["prd_markdown"]
    emit_structured_reply_events(
        state_dir=state, writer=writer, channel=project_channel(state, "ch-document"),
        request={"thread_id": "main", "target_member_id": "synthesizer"},
        message={"refs": {"synthesis_request_id": "synth-1", "prd_document_contract": "full.v1"}},
        reply=json.dumps({"channel_synthesis": body}), reply_event_id="reply-1", actor="test", source="test",
    )
    events = writer.event_log.read_all()
    syntheses = [e.payload for e in events if e.type == "channel.synthesis.proposed"]
    if missing:
        assert not syntheses
        assert any(e.type == "channel.synthesis.repair.requested" and "prd_markdown" in e.payload.get("contract_error", "") for e in events)
        return
    assert len(syntheses) == 1
    synthesis = syntheses[0]
    canonical = (state / synthesis["artifact_ref"]).read_bytes()
    assert hashlib.sha256(canonical).hexdigest() == synthesis["artifact_digest"]
    prd = json.loads(canonical)["body"]
    markdown = (state / synthesis["spec_path"]).read_text()
    assert markdown == prd["markdown"]
    assert markdown.startswith(body["prd_markdown"].rstrip())
    assert "END-OF-FULL-PRD" in markdown
    assert "REQ-1" in markdown and "AC-1" in markdown
    assert "Reservations expire after 15 minutes." in markdown
    assert "Serialize expiration with reservation" in markdown
    assert "Expiry duration remains disputed." in markdown
    assert "source:PRD#expiry" in markdown and "custom_evidence" in markdown
    assert prd["synthesis"]["assumptions"] == body["assumptions"]
    assert prd["summary"] == body["summary"]
    assert hashlib.sha256(markdown.encode()).hexdigest() == synthesis["spec_digest"]
    consensus = next(e for e in events if e.type == "channel.consensus.proposed")
    assert consensus.payload["artifact_digest"] == synthesis["artifact_digest"]
    assert consensus.payload["prd_revision"] == synthesis["prd_revision"]


@pytest.mark.parametrize("fault", ["unknown_acceptance", "no_source", "empty_body", "duplicate_id", "unexplained_gap"])
def test_full_document_reference_contract_rejects_incomplete_mapping(fault):
    body = document()
    row = body["requirement_coverage"][0]
    if fault == "unknown_acceptance": row["acceptance_ids"] = ["invented"]
    if fault == "no_source": row["source_refs"] = []
    if fault == "empty_body": body["prd_markdown"] = " "
    if fault == "duplicate_id": body["requirement_coverage"].append(dict(row))
    if fault == "unexplained_gap": row["status"] = "gap"
    assert validate_prd_document(body)


def test_prd_spec_v1_accepts_standard_body_and_legacy_body_remains_valid():
    assert validate_prd_document(spec_document()) == ""
    assert validate_prd_document(document()) == ""


def test_prd_spec_v1_accepts_standalone_goals_heading():
    body = spec_document()
    body["prd_markdown"] = body["prd_markdown"].replace(
        "## 03. Goals, non-goals, and scope", "## Goals"
    )
    assert validate_prd_document(body) == ""


@pytest.mark.parametrize("fault", ["format", "control", "section", "scenario", "metric", "duplicate"])
def test_prd_spec_v1_rejects_structural_contract_defects(fault):
    body = spec_document()
    if fault == "format":
        body["spec_format"] = "prd-spec.v0"
    elif fault == "control":
        body["document_control"].pop("owner")
    elif fault == "section":
        body["prd_markdown"] = body["prd_markdown"].replace("## 11. Non-functional requirements", "## 11. Quality")
    elif fault == "scenario":
        body["prd_markdown"] = body["prd_markdown"].replace("#### Scenario: Concurrent reservation", "#### Example: Concurrent reservation")
    elif fault == "metric":
        body["success_metrics"] = []
    elif fault == "duplicate":
        body["success_metrics"].append(dict(body["success_metrics"][0]))
    error = validate_prd_document(body)
    assert error
    assert {
        "format": "unsupported spec_format",
        "control": "document_control.owner",
        "section": "section: nfr",
        "scenario": "Scenario heading",
        "metric": "success_metrics",
        "duplicate": "success_metrics id",
    }[fault] in error


@pytest.mark.parametrize("field", ["user_stories", "edge_cases", "key_entities"])
def test_full_prd_requires_audience_contract_sections(field):
    body = spec_document()
    body.pop(field)
    error = validate_prd_document(body)
    assert f"requires non-empty {field}" in error


def test_full_prd_requires_normative_requirements_and_measured_metrics():
    body = spec_document()
    body["prd_markdown"] = body["prd_markdown"].replace("The system SHALL", "The system should")
    assert "MUST or SHALL" in validate_prd_document(body)

    body = spec_document()
    body["success_metrics"][0].pop("measurement_method")
    assert "requires measurement_method" in validate_prd_document(body)


def test_full_prd_requires_executable_architecture_boundary():
    body = spec_document()
    body["prd_markdown"] = body["prd_markdown"].replace("append-only event ledger", "event history")
    error = validate_prd_document(body)
    assert "append-only event ledger" in error


def test_full_prd_accepts_chinese_control_and_data_plane_terms():
    body = spec_document()
    body["prd_markdown"] = body["prd_markdown"].replace(
        "The control plane, data plane, and event-driven boundary",
        "控制面、数据面和事件驱动边界",
    )
    assert validate_prd_document(body) == ""


def test_export_catalog_preserves_commands_unknown_fields_and_author_body():
    body = document()
    body["acceptance_criteria"][0]["risk_note"] = "Keep this uncommon acceptance evidence."
    body["verification_commands"] = [{
        "id": "VC-1", "command": "python -m pytest tests/test_inventory.py -q",
        "acceptance_ids": ["AC-1"], "owner": "verify", "tier": "runtime",
        "deterministic": True, "reusable": False, "timeout_seconds": 60,
        "producer_paths": ["evidence/inventory.json"], "custom_proof": "exit + ledger",
    }]
    original = json.loads(json.dumps(body))
    markdown = render_prd_document(body, channel_id="ch", thread_id="main", source_refs=["source:PRD"])
    assert markdown.startswith(body["prd_markdown"].rstrip())
    assert markdown.count("Concurrent reservations cannot oversell.") == 1
    assert markdown.count("python -m pytest tests/test_inventory.py -q") == 1
    assert "Keep this uncommon acceptance evidence." in markdown
    for field in body["verification_commands"][0]:
        assert field in markdown
    assert "exit + ledger" in markdown and "evidence/inventory.json" in markdown
    assert "REQ-1" in markdown and "source:PRD#reservation" in markdown
    assert "Atomic reservation" in markdown and "Source requirement" in markdown
    assert body == original
    assert "```json" not in markdown
    for field in ("assumptions", "risks", "dissent"):
        for key, value in body[field][0].items():
            assert key in markdown
            if isinstance(value, str):
                assert value in markdown


def test_export_catalog_includes_success_metrics_and_non_functional_requirements():
    body = spec_document()
    markdown = render_prd_document(body, channel_id="ch", thread_id="main", source_refs=[])
    assert "成功指标" in markdown and "SC-1" in markdown
    assert "非功能需求" in markdown and "NFR-1" in markdown


def test_prd_view_index_keeps_one_revision_and_assigns_audience_sections():
    views = prd_view_index(spec_document()["prd_markdown"])
    assert "01. Overview" in views["product"]["sections"]
    assert "08. Business rules, permissions, and state" in views["architecture"]["sections"]
    assert "07. Functional requirements" in views["developer"]["sections"]


def test_export_catalog_keeps_multiline_and_pipe_values_in_one_table_row():
    body = document()
    body["verification_commands"] = [{"id": "VC-1", "command": "printf '<ok>' | cat\ntrue"}]
    markdown = render_prd_document(body, channel_id="ch", thread_id="main", source_refs=[])
    line = next(line for line in markdown.splitlines() if "printf" in line)
    assert "&lt;ok&gt;" in line and "\\| cat<br>true" in line
    assert "channel_prd_scope=approved_task" in markdown


def test_export_omits_empty_review_catalogs():
    body = document()
    for field in ("assumptions", "risks", "dissent"):
        body[field] = []
    markdown = render_prd_document(body, channel_id="ch", thread_id="main", source_refs=[])
    assert "## 假设、风险与分歧" not in markdown
