"""Phase 4 integration test — polish features."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.cli.main import main
from zf.core.verification.architecture_rules import parse_rules, rules_to_gates
from zf.core.verification.promoted_rules import PromotedRulesStore
from zf.core.metrics.vcr import calculate_vcr
from zf.core.config.presets import get_preset, list_presets, generate_preset_yaml
from zf.core.task.schema import Task, TaskEvidence
from zf.core.task.store import TaskStore


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "phase4-test", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    main(["init"])
    return tmp_path


class TestArchitectureRules:
    def test_parse_rules(self, tmp_path: Path):
        rules_file = tmp_path / "ARCHITECTURE_RULES.md"
        rules_file.write_text(
            "# Architecture Rules\n\n"
            "## Rule: no-console-log\n"
            '- check: `grep -r console.log src/ && exit 1 || exit 0`\n'
            '- fix: "Remove console.log statements"\n'
            '- why: "Production code should not have console.log"\n\n'
        )
        rules = parse_rules(rules_file)
        assert len(rules) == 1
        assert rules[0].name == "no-console-log"

    def test_rules_to_gates(self, tmp_path: Path):
        rules_file = tmp_path / "ARCHITECTURE_RULES.md"
        rules_file.write_text(
            "## Rule: test-rule\n"
            "- check: `true`\n"
        )
        rules = parse_rules(rules_file)
        gates = rules_to_gates(rules)
        assert len(gates) == 1
        assert gates[0].name == "arch:test-rule"

    def test_empty_file(self, tmp_path: Path):
        rules = parse_rules(tmp_path / "nonexistent.md")
        assert rules == []


class TestPromotedRules:
    def test_add_and_list(self, project: Path):
        store = PromotedRulesStore(project / ".zf" / "promoted_rules.jsonl")
        store.add("architecture_violation", "grep -r TODO src/", "Remove TODOs")
        rules = store.list()
        assert len(rules) == 1
        assert rules[0].category == "architecture_violation"

    def test_to_gates(self, project: Path):
        store = PromotedRulesStore(project / ".zf" / "promoted_rules.jsonl")
        store.add("test", "true")
        gates = store.to_gates()
        assert len(gates) == 1
        assert "promoted:" in gates[0].name

    def test_empty_store(self, project: Path):
        store = PromotedRulesStore(project / ".zf" / "promoted_rules.jsonl")
        assert store.list() == []
        assert store.to_gates() == []


class TestVCR:
    def test_empty_store(self, project: Path):
        store = TaskStore(project / ".zf" / "kanban.json")
        report = calculate_vcr(store)
        assert report.rate == 0.0
        assert report.attempted == 0

    def test_vcr_with_tasks(self, project: Path):
        store = TaskStore(project / ".zf" / "kanban.json")
        t1 = Task(title="A", id="T1", status="done", assigned_to="dev",
                   evidence=TaskEvidence(commit="abc", output_summary="ok"))
        t2 = Task(title="B", id="T2", status="done", assigned_to="dev")
        t3 = Task(title="C", id="T3", status="review", assigned_to="dev")
        store.add(t1)
        store.add(t2)
        store.add(t3)

        report = calculate_vcr(store)
        assert report.attempted == 3  # done + review
        assert report.verified == 1  # only T1 has evidence
        assert 0.3 < report.rate < 0.4


class TestPresets:
    def test_list_presets(self):
        presets = list_presets()
        assert "minimal" in presets
        assert "code-assist" in presets
        assert "design-first" in presets

    def test_get_preset(self):
        config = get_preset("minimal")
        assert config["preset"] == "minimal"
        assert len(config["roles"]) == 1

    def test_unknown_preset_raises(self):
        with pytest.raises(ValueError, match="Unknown preset"):
            get_preset("nonexistent")

    def test_generate_yaml(self):
        yaml_str = generate_preset_yaml("minimal", "my-project")
        assert "my-project" in yaml_str
        parsed = yaml.safe_load(yaml_str)
        assert parsed["project"]["name"] == "my-project"

    def test_design_first_has_5_roles(self):
        config = get_preset("design-first")
        assert len(config["roles"]) == 5
