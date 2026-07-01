"""Project context resolver uses zf.yaml as the state-dir source."""

from __future__ import annotations

from pathlib import Path

from zf.core.config.project_context import resolve_project_context, resolve_state_dir
from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.runtime.orchestrator import Orchestrator


def test_resolve_state_dir_uses_project_config(tmp_path: Path):
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n',
        encoding="utf-8",
    )

    state_dir = resolve_state_dir(cwd=tmp_path)

    assert state_dir == (tmp_path / "runtime-state").resolve()


def test_explicit_state_dir_overrides_config(tmp_path: Path):
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n',
        encoding="utf-8",
    )

    context = resolve_project_context(
        cwd=tmp_path,
        explicit_state_dir="override-state",
    )

    assert context.state_dir == (tmp_path / "override-state").resolve()


def test_missing_config_defaults_to_dot_zf(tmp_path: Path):
    state_dir = resolve_state_dir(cwd=tmp_path)

    assert state_dir == (tmp_path / ".zf").resolve()


def test_resolve_project_root_from_subdirectory(tmp_path: Path):
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n',
        encoding="utf-8",
    )
    subdir = tmp_path / "src" / "pkg"
    subdir.mkdir(parents=True)

    context = resolve_project_context(cwd=subdir)

    assert context.project_root == tmp_path.resolve()
    assert context.state_dir == (tmp_path / "runtime-state").resolve()


def test_env_runtime_context_overrides_detached_workdir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    detached = tmp_path / "runtime" / "workdirs" / "dev" / "project"
    project_root.mkdir(parents=True)
    detached.mkdir(parents=True)
    yaml = 'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n'
    (project_root / "zf.yaml").write_text(yaml, encoding="utf-8")
    (detached / "zf.yaml").write_text(yaml, encoding="utf-8")
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("ZF_STATE_DIR", "runtime-state")

    context = resolve_project_context(cwd=detached)

    assert context.project_root == project_root.resolve()
    assert context.state_dir == (project_root / "runtime-state").resolve()


def test_explicit_state_dir_overrides_env_runtime_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("ZF_STATE_DIR", "runtime-state")

    context = resolve_project_context(
        cwd=project_root,
        explicit_state_dir="override-state",
    )

    assert context.state_dir == (project_root / "override-state").resolve()


def test_explicit_state_dir_keeps_env_project_root_from_detached_workdir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    detached = tmp_path / "project" / ".zf" / "workdirs" / "arch" / "project"
    state_dir = project_root / ".zf"
    project_root.mkdir(parents=True)
    detached.mkdir(parents=True)
    yaml = 'version: "1.0"\nproject:\n  name: test\n  state_dir: .zf\n'
    (project_root / "zf.yaml").write_text(yaml, encoding="utf-8")
    (detached / "zf.yaml").write_text(yaml, encoding="utf-8")
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(project_root))

    context = resolve_project_context(
        cwd=detached,
        explicit_state_dir=state_dir,
        load_config_with_explicit=True,
    )

    assert context.project_root == project_root.resolve()
    assert context.config_path == (project_root / "zf.yaml").resolve()
    assert context.state_dir == state_dir.resolve()


class _NoopTransport:
    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def test_orchestrator_uses_explicit_project_root_for_workspace(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    state_dir = tmp_path / "runtime" / "zf-state"
    project_root.mkdir()
    state_dir.mkdir(parents=True)
    config = ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[RoleConfig(name="dev", backend="mock")],
    )

    orch = Orchestrator(
        state_dir,
        config,
        _NoopTransport(),  # type: ignore[arg-type]
        project_root=project_root,
    )

    assert orch.project_root == project_root.resolve()
    assert orch._scope_ratchet.workspace == project_root.resolve()
