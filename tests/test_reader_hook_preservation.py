"""Candidate pinning must not delete hooks installed for a live Codex reader."""

from pathlib import Path

import pytest

from tests.test_workdirs import _init_repo
from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    WorkdirConfig,
    ZfConfig,
)
from zf.runtime.codex_hooks import write_codex_hook_settings
from zf.runtime.workdirs import WorkdirManager


@pytest.mark.parametrize("operation", ["pin", "checkout", "dirty_reset"])
def test_reader_target_preserves_runtime_hooks(tmp_path: Path, operation: str) -> None:
    head = _init_repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    role = RoleConfig(name="verify", backend="codex", role_kind="reader")
    config = ZfConfig(
        project=ProjectConfig(name="hook-fixture"),
        roles=[role],
        runtime=RuntimeConfig(workdirs=WorkdirConfig(enabled=True, mode="worktree")),
    )
    manager = WorkdirManager(state_dir=state, project_root=tmp_path, config=config)
    manager.prepare(role)
    project = Path(manager.plan(role).project_path)
    write_codex_hook_settings(state, project_root=project)
    hooks = project / ".codex/hooks.json"
    expected = hooks.read_bytes()
    (project / "junk.tmp").write_text("unowned product residue")
    (project / ".codex/junk.tmp").write_text("not an authorized runtime hook")
    if operation == "pin":
        assert manager.pin_reader_target(role, head) == head
    elif operation == "checkout":
        manager.checkout_reader_ref(role, "HEAD")
    else:
        manager.reset_reader_if_dirty(role)
    assert hooks.exists(), "reader checkout deleted active provider telemetry hooks"
    assert hooks.read_bytes() == expected
    assert not (project / "junk.tmp").exists()
    assert not (project / ".codex/junk.tmp").exists()
