"""Tests for the project-profile Web API (doc 102 B6)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from zf.web.server import create_app


@pytest.fixture(autouse=True)
def _isolated_workspace_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The init/scaffold endpoints register projects into the workspace registry.
    # Without this isolation they write to the REAL ~/.zaofu registry (leaked 9
    # ghost projects across 3 pytest runs; the web project picker showed
    # green/newproj/noted three times each).
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]")
    (sd / "events.jsonl").write_text("")
    return TestClient(create_app(sd))


@pytest.fixture
def py_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_x(): pass\n")
    return repo


def test_web_detect(client, py_repo):
    r = client.get("/api/profile/detect", params={"path": str(py_repo)})
    assert r.status_code == 200
    body = r.json()
    assert body["confidence"] == "high"
    assert "python" in body["languages"]


def test_web_detect_missing_path(client):
    r = client.get("/api/profile/detect", params={"path": "/no/such/dir/xyz"})
    assert r.status_code == 404


def test_web_validate_path_reports_state_dir_outside_root(client, tmp_path):
    root = tmp_path / "project"
    r = client.post(
        "/api/workspace/projects/validate-path",
        json={"root": str(root), "state_dir": "../outside"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["status"] == "invalid"
    assert any(item["kind"] == "state_dir_outside_root" for item in body["diagnostics"])


def test_web_validate_path_reports_non_empty_state_dir(client, tmp_path):
    root = tmp_path / "project"
    state = root / ".zf"
    state.mkdir(parents=True)
    (state / "events.jsonl").write_text("", encoding="utf-8")

    r = client.post(
        "/api/workspace/projects/validate-path",
        json={"root": str(root), "state_dir": ".zf"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["state_dir_non_empty"] is True
    assert any(item["kind"] == "state_dir_non_empty" for item in body["diagnostics"])


def test_web_recommend(client, py_repo):
    r = client.get("/api/profile/recommend", params={"path": str(py_repo), "intent": "build"})
    assert r.status_code == 200
    body = r.json()
    from zf.core.profile.flows import is_flow_id
    arch = body["recommendation"]["archetype"]
    assert is_flow_id(arch) or arch == "minimal"
    assert body["recommendation"]["required_checks"]


def test_web_catalog_lists_prod_flows_and_presets(client):
    r = client.get("/api/presets")
    assert r.status_code == 200
    by_name = {p["name"]: p for p in r.json()["presets"]}
    # validated prod flows are the main catalog + minimal preset fallback
    assert "prd-fanout-claude" in by_name and "minimal" in by_name
    prd = by_name["prd-fanout-claude"]
    assert prd["kind"] == "flow" and prd["backend"] == "claude" and prd["roleCount"] == 8
    assert prd["description"]
    assert by_name["minimal"]["kind"] == "preset"


def test_web_init_writes_catalog_flow_yaml(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "secret-token")
    target = tmp_path / "flowproj"
    r = client.post(
        "/api/workspace/projects/init",
        headers={"X-Zf-Web-Token": "secret-token"},
        json={
            "root": str(target),
            "preset": "prd-fanout-claude",
            "skip_instruction_docs": True,
        },
    )
    assert r.status_code == 201, r.text
    text = (target / "zf.yaml").read_text(encoding="utf-8")
    assert "kind: Workflow" in text
    assert "id: prd-fanout-claude" in text
    assert "preset: prod-prd-fanout-claude" in text


def test_web_init_with_kind_writes_typed_flow_yaml(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "secret-token")
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    r = client.post(
        "/api/workspace/projects/init",
        headers={"X-Zf-Web-Token": "secret-token"},
        json={
            "root": str(target),
            "kind": "refactor",
            "name": "cangjie-web-init",
            "source_root": str(source),
            "target_root": str(target),
            "backend": "mock",
            "lanes": 3,
            "state_dir": ".zf-cangjie",
            "skip_instruction_docs": True,
        },
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "refactor"
    assert body["config_generated"] == "typed_flow_spec"
    docs = list(yaml.safe_load_all((target / "zf.yaml").read_text(encoding="utf-8")))
    assert docs[0]["kind"] == "RefactorFlow"
    assert docs[0]["spec"]["sourceRoot"] == str(source)
    assert docs[0]["spec"]["targetRoot"] == str(target)
    assert docs[0]["spec"]["lanes"] == 3
    assert docs[1]["spec"]["project"]["state_dir"] == ".zf-cangjie"


def test_web_recommend_backend_codex_flow(client, py_repo):
    r = client.get("/api/profile/recommend",
                   params={"path": str(py_repo), "intent": "build", "backend": "codex"})
    # doc-125 controller flows: codex "build" intent now resolves to the v3
    # controller flow (flow_id_for_intent prefers controller/*.yaml). Matches
    # test_profile_catalog::test_codex_prefers_controller_v3_flows, which is the
    # authority on this mapping. The bare prd-fanout-codex expectation predated
    # the v3 controller catalog.
    assert r.json()["recommendation"]["archetype"] == "prd-fanout-v3-codex"


def test_web_init_writes_operator_notes(client, tmp_path, monkeypatch):
    """Operator free-text comments land in CLAUDE.md (= npm init description)."""
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "secret-token")
    target = tmp_path / "noted"
    r = client.post(
        "/api/workspace/projects/init",
        headers={"X-Zf-Web-Token": "secret-token"},
        json={"root": str(target), "preset": "minimal",
              "description": "支付网关,合规优先,勿动 legacy/billing"},
    )
    assert r.status_code == 201, r.text
    claude = (target / "CLAUDE.md").read_text(encoding="utf-8")
    assert "项目说明 (operator notes)" in claude
    assert "支付网关,合规优先" in claude
    assert r.json()["notes"] in {"created", "updated"}


def test_web_recommend_declared_stack(client):
    r = client.get("/api/profile/recommend", params={"stack": "node", "intent": "build"})
    assert r.status_code == 200
    assert r.json()["profile"]["confidence"] == "declared"


def test_web_recommend_unknown_stack(client):
    r = client.get("/api/profile/recommend", params={"stack": "cobol"})
    assert r.status_code == 400


def test_web_recommend_scale_overrides(client):
    r = client.get("/api/profile/recommend", params={"stack": "python", "scale": "launch"})
    assert r.json()["recommendation"]["harness_profile"] == "strict"
    r2 = client.get("/api/profile/recommend", params={"stack": "python", "scale": "hobby"})
    assert r2.json()["recommendation"]["harness_profile"] == "baseline"


def test_web_init_scaffold_greenfield(client, tmp_path, monkeypatch):
    """From-0 survey: declared stack + scaffold via Web init (token-gated)."""
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "secret-token")
    target = tmp_path / "green"
    r = client.post(
        "/api/workspace/projects/init",
        headers={"X-Zf-Web-Token": "secret-token"},
        json={"root": str(target), "preset": "minimal", "apply_profile": True,
              "stack": "python", "scaffold": True},
    )
    assert r.status_code == 201, r.text
    assert (target / "src").is_dir() and (target / "tests").is_dir()
    body = r.json()
    assert body["profile"]["scaffold"]  # scaffolded dirs reported back


def test_web_init_apply_profile(client, tmp_path, monkeypatch):
    """Token-gated init with apply_profile runs the overlay (CLI+Web parity)."""
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "secret-token")
    target = tmp_path / "newproj"
    r = client.post(
        "/api/workspace/projects/init",
        headers={"X-Zf-Web-Token": "secret-token"},
        json={"root": str(target), "preset": "minimal", "apply_profile": True,
              "skip_instruction_docs": False},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["profile"] is not None
    assert body["profile"]["archetype"] in {"minimal", "code-assist", "safe-team"}


def test_web_init_apply_profile_requires_token(client, tmp_path):
    target = tmp_path / "newproj2"
    r = client.post(
        "/api/workspace/projects/init",
        json={"root": str(target), "preset": "minimal", "apply_profile": True},
    )
    assert r.status_code == 403
