"""Direct contract test for the isolated browser app factory."""

from pathlib import Path

from fastapi.testclient import TestClient

from tests.testkit.browser_app import create_app


def test_browser_app_factory_uses_declared_fixture_root_and_dist(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "zf.yaml").write_text(
        "project:\n  name: browser-factory\n  state_dir: runtime-state\nroles: []\n",
        encoding="utf-8",
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>fixture</title>", encoding="utf-8")
    monkeypatch.setenv("ZF_TESTKIT_BROWSER_ROOT", str(root))
    monkeypatch.setenv("ZF_TESTKIT_BROWSER_DIST", str(dist))

    response = TestClient(create_app()).get("/")

    assert response.status_code == 200
    assert "fixture" in response.text
    assert not (root / ".zf").exists()
