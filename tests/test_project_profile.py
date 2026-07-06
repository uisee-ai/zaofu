"""Tests for the deterministic project profile detector + recommender + apply.

Covers doc 102 B1-B4: type registry, detection (incl. monorepo/polyglot/cangjie),
three-axis recommendation, no-clobber required_checks, AGENTS.md stack section.
"""

from __future__ import annotations

import json

import yaml

from zf.core.config.presets import list_presets
from zf.core.profile import PROJECT_TYPES
from zf.core.profile.apply import (
    apply_agents_md_stack,
    fill_required_checks,
    materialize_zf_yaml,
)
from zf.core.profile.detector import detect
from zf.core.profile.recommender import recommend


# ----------------------------------------------------------------- fixtures


def _py_repo(root, with_tests=True):
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "src").mkdir(exist_ok=True)
    if with_tests:
        (root / "tests").mkdir(exist_ok=True)
        (root / "tests" / "test_x.py").write_text("def test_x(): pass\n")


def _node_pkg(root, deps=None, scripts=None, configs=()):
    pkg = {"name": root.name, "dependencies": deps or {}, "scripts": scripts or {}}
    (root / "package.json").write_text(json.dumps(pkg))
    for c in configs:
        (root / c).write_text("")


# ------------------------------------------------------------- B1 registry


def test_registry_has_core_languages():
    ids = {pt.type_id for pt in PROJECT_TYPES}
    assert {"python", "node", "go", "rust"} <= ids
    for pt in PROJECT_TYPES:
        assert pt.key_files and pt.gate_cmds and pt.language


# ------------------------------------------------------------- B2 detector


def test_detect_python_backend(tmp_path):
    _py_repo(tmp_path)
    p = detect(tmp_path)
    assert p.confidence == "high"
    assert p.units[0].language == "python"
    assert p.units[0].surface == "backend"
    assert p.units[0].has_tests is True


def test_detect_node_frontend(tmp_path):
    _node_pkg(tmp_path, deps={"react": "^18"}, scripts={"test": "vitest"},
              configs=("vite.config.ts",))
    p = detect(tmp_path)
    u = p.units[0]
    assert u.language == "node"
    assert u.surface == "frontend"
    assert "react" in u.frameworks
    assert u.test_cmd == "npm test"


def test_detect_node_backend(tmp_path):
    _node_pkg(tmp_path, deps={"express": "^4"}, scripts={"lint": "eslint ."})
    p = detect(tmp_path)
    assert p.units[0].surface == "backend"
    assert "npm run lint" in p.units[0].gate_cmds


def test_detect_node_prefers_actual_scripts(tmp_path):
    _node_pkg(tmp_path, deps={"react": "^18"},
              scripts={"typecheck": "tsc", "test": "vitest"})
    p = detect(tmp_path)
    assert "npm run typecheck" in p.units[0].gate_cmds


def test_detect_polyglot_fullstack(tmp_path):
    _py_repo(tmp_path)
    web = tmp_path / "web"
    web.mkdir()
    _node_pkg(web, deps={"react": "^18"}, scripts={"test": "vitest"})
    p = detect(tmp_path)
    langs = {u.language for u in p.units}
    assert langs == {"python", "node"}
    assert p.is_fullstack is True
    assert "polyglot:companion-dirs" in p.source_signals


def test_detect_monorepo_pnpm(tmp_path):
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n")
    a = tmp_path / "packages" / "a"
    b = tmp_path / "packages" / "b"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    _node_pkg(a, deps={"express": "^4"})
    _py_repo(b, with_tests=False)
    p = detect(tmp_path)
    assert p.layout == "monorepo"
    assert len(p.units) == 2


def test_detect_manifestless_monorepo_subdir_scan(tmp_path):
    # cangjie-style: root has NO manifest; code lives in subdirs (PB4)
    svc = tmp_path / "services"
    svc.mkdir()
    (svc / "go.mod").write_text("module x\n")
    p = detect(tmp_path)
    assert p.layout == "monorepo"
    assert any(u.language == "go" for u in p.units)
    assert "monorepo:subdir-scan" in p.source_signals


def test_detect_empty_low_confidence(tmp_path):
    p = detect(tmp_path)
    assert p.confidence == "low"
    assert p.units[0].language == "unknown"


# ----------------------------------------------------------- B3 recommender


def test_recommend_build_maps_to_prd_flow(tmp_path):
    _py_repo(tmp_path)
    web = tmp_path / "web"
    web.mkdir()
    _node_pkg(web, deps={"react": "^18"}, scripts={"test": "vitest"})
    r = recommend(detect(tmp_path), "build")
    assert r.archetype == "prd-fanout-claude"  # validated prod flow, not synthetic
    assert r.catalog == "flow" and r.backend == "claude"
    assert r.harness_profile == "strict"
    assert "ruff check ." in r.required_checks and "npm test" in r.required_checks


def test_recommend_backend_codex_flow(tmp_path):
    _py_repo(tmp_path)
    assert recommend(detect(tmp_path), "build", backend="codex").archetype == "prd-fanout-v3-codex"


def test_recommend_empty_minimal(tmp_path):
    r = recommend(detect(tmp_path), "build")
    assert r.archetype == "minimal" and r.catalog == "preset"


def test_recommend_hobby_lightweight_minimal(tmp_path):
    # hobby → lightweight preset fallback even for real code (long tail)
    _py_repo(tmp_path)
    r = recommend(detect(tmp_path), "build", scale="hobby")
    assert r.archetype == "minimal" and r.catalog == "preset"


def test_recommend_intent_review_issue_flow(tmp_path):
    _py_repo(tmp_path)
    assert recommend(detect(tmp_path), "review").archetype == "issue-fanout-claude"


def test_recommend_intent_refactor_flow_strict(tmp_path):
    _py_repo(tmp_path)
    r = recommend(detect(tmp_path), "refactor")
    assert r.archetype == "refactor-flow-claude"
    assert r.catalog == "flow"
    assert r.harness_profile == "strict"


def test_recommend_declared_refactor_codex_uses_controller_flow():
    from zf.core.profile.detector import declared_profile
    r = recommend(
        declared_profile("node"),
        "refactor",
        declared=True,
        backend="codex",
        scale="internal",
    )
    assert r.archetype == "refactor-lane-v3-codex"
    assert r.catalog == "flow"
    assert r.backend == "codex"


def test_recommend_misroute_on_build_existing(tmp_path):
    _py_repo(tmp_path)
    r = recommend(detect(tmp_path), "build")
    assert r.misroute  # build intent on existing code → suggest refactor/maintain


def test_recommend_no_misroute_when_declared(tmp_path):
    _py_repo(tmp_path)
    r = recommend(detect(tmp_path), "build", declared=True)
    assert not r.misroute


def test_recommend_scale_overrides_strictness(tmp_path):
    # fullstack auto-recommends strict; scale (survey) overrides it both ways
    _py_repo(tmp_path)
    web = tmp_path / "web"
    web.mkdir()
    _node_pkg(web, deps={"react": "^18"}, scripts={"test": "vitest"})
    p = detect(tmp_path)
    assert recommend(p, "build").harness_profile == "strict"
    assert recommend(p, "build", scale="hobby").harness_profile == "baseline"
    assert recommend(p, "build", scale="launch").harness_profile == "strict"


def test_declared_profile_surface_override():
    from zf.core.profile.detector import declared_profile
    assert declared_profile("python", "fullstack").is_fullstack is True


def test_recommend_archetype_always_valid(tmp_path):
    from zf.core.profile.flows import is_flow_id
    _py_repo(tmp_path)
    for intent in ("build", "refactor", "review", "maintain"):
        r = recommend(detect(tmp_path), intent)
        assert is_flow_id(r.archetype) or r.archetype in list_presets()


def test_prod_flow_catalog_scans_yaml_metadata(tmp_path, monkeypatch):
    examples = tmp_path / "examples"
    prod = examples / "prod"
    prod.mkdir(parents=True)
    (prod / "custom-review-claude.yaml").write_text(
        """
apiVersion: zaofu.dev/v1
kind: Workflow
metadata:
  name: custom-review
  zaofu:
    catalog:
      id: custom-review-claude
      label: Custom Review Claude
      description: Custom review flow
      roles: 4
      intent: review
      backend: claude
      recommended_for: [review, maintain]
      order: 5
spec: {}
---
apiVersion: zaofu.dev/v1
kind: ZfConfig
metadata: {name: custom-review-claude}
spec:
  version: "1.0"
  preset: custom-review-claude
  project: {name: custom-review, state_dir: .zf-custom-review}
""".lstrip(),
        encoding="utf-8",
    )
    (prod / "not-registered.yaml").write_text(
        "apiVersion: zaofu.dev/v1\nkind: Workflow\nmetadata: {name: ignored}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_EXAMPLES_DIR", str(examples))

    from zf.core.profile.flows import (
        flow_id_for_intent,
        flow_roles,
        is_flow_id,
        list_flows_detailed,
        read_flow_yaml,
    )

    catalog = list_flows_detailed()
    assert [item["id"] for item in catalog] == ["custom-review-claude"]
    assert catalog[0]["kind"] == "flow"
    assert catalog[0]["backend"] == "claude"
    assert catalog[0]["intent"] == "review"
    assert catalog[0]["roles"] == 4
    assert is_flow_id("custom-review-claude")
    assert not is_flow_id("not-registered")
    assert flow_id_for_intent("maintain", "claude") == "custom-review-claude"
    assert flow_roles("custom-review-claude") == 4
    assert "preset: custom-review-claude" in (read_flow_yaml("custom-review-claude") or "")


# --------------------------------------------------------------- B4 apply


def test_materialize_flow_copies_prod_yaml(tmp_path):
    # build → prd flow → materialize copies the validated prod yaml verbatim
    _py_repo(tmp_path)
    r = recommend(detect(tmp_path), "build")
    assert r.catalog == "flow"
    text = materialize_zf_yaml(r.archetype, "demo", r)
    assert "kind:" in text  # k8s-style prod flow, not a flat preset


def test_materialize_preset_injects_checks(tmp_path):
    # lightweight preset path still injects required_checks + harness_profile
    from zf.core.profile.detector import declared_profile
    r = recommend(declared_profile("python"), "build", scale="hobby")
    assert r.catalog == "preset"
    cfg = yaml.safe_load(materialize_zf_yaml(r.archetype, "demo", r))
    assert cfg["project"]["name"] == "demo"
    assert cfg["quality_gates"]["static"]["required_checks"] == ["ruff check .", "pytest"]


def test_fill_required_checks_no_clobber(tmp_path):
    cfg = tmp_path / "zf.yaml"
    cfg.write_text(yaml.dump({"quality_gates": {"static": {"required_checks": []}}}))
    res = fill_required_checks(cfg, ["pytest"], write=True)
    assert res["action"] == "filled"
    assert yaml.safe_load(cfg.read_text())["quality_gates"]["static"]["required_checks"] == ["pytest"]
    # second run must NOT clobber the operator's value
    res2 = fill_required_checks(cfg, ["mypy"], write=True)
    assert res2["action"] == "kept"
    assert yaml.safe_load(cfg.read_text())["quality_gates"]["static"]["required_checks"] == ["pytest"]


def test_fill_required_checks_skips_multidoc_flow(tmp_path):
    # a prod-flow config is multi-document — must skip, not crash (E6 regression)
    cfg = tmp_path / "zf.yaml"
    cfg.write_text("apiVersion: zaofu.dev/v1\nkind: Workflow\n---\nmetadata: {name: x}\n")
    res = fill_required_checks(cfg, ["pytest"], write=True)
    assert res["action"] == "skipped"
    assert "kind: Workflow" in cfg.read_text()  # untouched


def test_apply_project_notes_idempotent(tmp_path):
    from zf.core.profile.apply import apply_project_notes
    claude = tmp_path / "CLAUDE.md"
    claude.write_text("# CLAUDE\n\nhand rules\n")
    r1 = apply_project_notes(claude, "my project notes", write=True)
    assert r1["action"] == "created"
    assert "hand rules" in claude.read_text()  # non-destructive
    assert "my project notes" in claude.read_text()
    r2 = apply_project_notes(claude, "updated notes", write=True)
    assert r2["action"] == "updated"
    assert claude.read_text().count("ZF:NOTES:START") == 1  # idempotent
    assert "updated notes" in claude.read_text()
    assert apply_project_notes(claude, "   ", write=True)["action"] == "noop"


def test_apply_agents_md_stack_idempotent(tmp_path):
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# AGENTS\n\nhand-written rules\n")
    _py_repo(tmp_path)
    profile = detect(tmp_path)
    r1 = apply_agents_md_stack(agents, profile, write=True)
    assert r1["action"] == "created"
    assert "hand-written rules" in agents.read_text()  # PB6: preserve human content
    assert "探测到的项目栈" in agents.read_text()
    r2 = apply_agents_md_stack(agents, profile, write=True)
    assert r2["action"] == "updated"
    # idempotent: exactly one managed block
    assert agents.read_text().count("ZF:PROFILE:START") == 1
