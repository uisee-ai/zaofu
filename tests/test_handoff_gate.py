"""L0 handoff gate (2026-06-19 handoff-prevention-framework).

Cold-start in the strict harness profile must FAIL on the unambiguously-fatal
stage-handoff classes (dead-end roles, silent route breaks). Locks the prod
yamls clean and proves the detection fires on a broken handoff.
"""

from __future__ import annotations

import glob
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.workflow.topology import WorkflowTopology
from zf.runtime.wake_patterns import WAKE_PATTERNS, reactor_handler_events


def _check(config):
    return WorkflowTopology.from_config(config).check(
        reactor_handlers=reactor_handler_events(),
        wake_patterns=set(WAKE_PATTERNS),
    )


@pytest.mark.parametrize("path", sorted(glob.glob("examples/prod/*-claude.yaml")))
def test_prod_claude_yaml_has_no_fatal_handoff(path, monkeypatch):
    # New prod yamls must not introduce a role waiting on an unpublished event
    # or a handler the watcher never wakes — the failure classes the prod E2E
    # spent four real-LLM rounds finding.
    monkeypatch.setenv("ZF_AGENT_SKILLS_DIR", "/tmp")
    monkeypatch.setenv("ZF_ZAOFU_SKILLS_DIR", "/tmp")
    monkeypatch.setenv("ZF_STATE_DIR", ".zf-x")
    monkeypatch.setenv("ZF_PROJECT_NAME", "x")
    report = _check(load_config(Path(path)))
    assert not report.dead_end_roles, f"{path}: dead-end roles {report.dead_end_roles}"
    assert not report.unwoken_events, f"{path}: silent route break {report.unwoken_events}"


def test_external_trigger_without_producer_lint(tmp_path):
    # P0-2 class: an external_trigger driving a writer stage with no stage
    # producer and not a known kernel-produced event must be flagged.
    from types import SimpleNamespace

    from zf.core.workflow.inspection import _external_trigger_producer_diagnostics

    broken = SimpleNamespace(workflow=SimpleNamespace(
        dag=SimpleNamespace(external_triggers=["user.message", "impl.go"]),
        stages=[SimpleNamespace(
            trigger="impl.go", topology="fanout_writer_scoped",
            aggregate=SimpleNamespace(success_event="candidate.ready", failure_event=""),
        )],
        pipelines=[],
    ))
    diags = _external_trigger_producer_diagnostics(broken)
    assert [d["event"] for d in diags] == ["impl.go"]
    assert diags[0]["kind"] == "external_trigger_without_producer"


@pytest.mark.parametrize("path", sorted(glob.glob("examples/prod/*-claude.yaml")))
def test_prod_yaml_external_triggers_have_producers(path, monkeypatch):
    # task_map.ready in refactor is kernel-produced (the bridge) → allowlisted;
    # entry triggers drive reader stages → not flagged. Prod yamls stay clean.
    monkeypatch.setenv("ZF_AGENT_SKILLS_DIR", "/tmp")
    monkeypatch.setenv("ZF_ZAOFU_SKILLS_DIR", "/tmp")
    monkeypatch.setenv("ZF_STATE_DIR", ".zf-x")
    monkeypatch.setenv("ZF_PROJECT_NAME", "x")
    from zf.core.workflow.inspection import _external_trigger_producer_diagnostics
    assert _external_trigger_producer_diagnostics(load_config(Path(path))) == []


def test_gate_detects_dead_end_role(tmp_path):
    # A role triggering on an event nothing publishes is a broken consumer-end
    # handoff; the strict gate keys on exactly this.
    yaml = tmp_path / "zf.yaml"
    yaml.write_text(
        "version: '1.0'\n"
        "project: {name: t, state_dir: .zf-t}\n"
        "workflow:\n"
        "  harness_profile: strict\n"
        "roles:\n"
        "  - {name: orchestrator, backend: mock, triggers: [user.message], publishes: [task.dispatched]}\n"
        "  - {name: dev, backend: mock, triggers: [never.published.event], publishes: [dev.build.done]}\n"
    )
    report = _check(load_config(yaml))
    assert "dev" in report.dead_end_roles
