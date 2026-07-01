from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
PROD = ROOT / "examples" / "prod"


def _config_spec(path: Path) -> dict:
    docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    for doc in docs:
        if isinstance(doc, dict) and doc.get("kind") == "ZfConfig":
            spec = doc.get("spec")
            return spec if isinstance(spec, dict) else {}
    raise AssertionError(f"{path} has no ZfConfig spec")


def _rework_routing(path: Path) -> dict:
    workflow = _config_spec(path).get("workflow")
    assert isinstance(workflow, dict), f"{path} has no workflow"
    routing = workflow.get("rework_routing")
    assert isinstance(routing, dict), f"{path} has no rework_routing"
    return routing


def test_prod_codex_claude_examples_keep_integration_rework_route_parity():
    for stem, expected_target in (
        ("prd-fanout", "task-map-synth"),
        ("issue-fanout", "issue-triage"),
        ("refactor-flow", "refactor-plan-synth"),
    ):
        codex = _rework_routing(PROD / f"{stem}-codex.yaml")
        claude = _rework_routing(PROD / f"{stem}-claude.yaml")
        assert codex.get("integration.failed") == expected_target
        assert claude.get("integration.failed") == expected_target
