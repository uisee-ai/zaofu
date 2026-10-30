"""TaskRef rework must retain the full task delta for real v4 integration."""

from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.task_refs import TaskRefManager

from tests.test_candidate_incremental import _git, _integrate, _setup


@pytest.mark.parametrize("base_field", ["base_git_head", "base_commit"])
@pytest.mark.parametrize("cleanup_only", [False, True])
def test_rework_retains_bootstrap_and_smoke_in_candidate(
    tmp_path: Path, base_field: str, cleanup_only: bool,
) -> None:
    state, config, log, writer, base, _ = _setup(tmp_path)
    store = TaskStore(state / "kanban.json")
    task = store.get("TASK-A")
    task.contract.scope = ["feature.txt", "required.txt", "smoke.sh"]
    task.contract.validation = {"commands": [{
        "id": "rolling-bootstrap", "command": "bash smoke.sh",
        "tier": "runtime", "rolling_smoke": True,
    }]}
    store.update(task.id, contract=task.contract)
    (tmp_path / "required.txt").write_text("bootstrap\n")
    (tmp_path / "smoke.sh").write_text(
        "set -eu\ntest -f required.txt\ntest -f feature.txt\n"
    )
    _git(tmp_path, "add", "--", "required.txt", "smoke.sh")
    _git(tmp_path, "commit", "-qm", "feat: bootstrap and smoke")
    manager = TaskRefManager(state_dir=state, project_root=tmp_path, config=config)

    def admit(paths: list[str], field: str) -> None:
        event = writer.append(ZfEvent(
            type="dev.build.done", actor="impl-1", task_id=task.id,
            payload={
                "source_commit": _git(tmp_path, "rev-parse", "HEAD"),
                "source_branch": "worker/task-a", field: base,
                "feature_id": "F-11111111", "changed_files": paths,
            },
        ))
        result = manager.process_dev_build_done(event)
        assert result is not None and result.status == "updated"

    admit(["feature.txt", "required.txt", "smoke.sh"], "base_git_head")
    (tmp_path / "feature.txt").write_text("repaired\n")
    _git(tmp_path, "add", "--", "feature.txt")
    _git(tmp_path, "commit", "-qm", "fix: repair feature")
    verified_head = _git(tmp_path, "rev-parse", "HEAD")
    admit(["feature.txt"], base_field)
    if cleanup_only:
        # A new typed result repairs only untracked build output. Its source
        # commit is unchanged; an empty per-attempt report must not erase scope.
        admit([], base_field)

    result = _integrate(tmp_path, state, config, log, writer, base)

    assert result.status == "integrated", result.payload
    candidate = "refs/heads/candidate/F-11111111"
    for name in ("feature.txt", "required.txt", "smoke.sh"):
        assert _git(tmp_path, "show", f"{candidate}:{name}") == _git(
            tmp_path, "show", f"{verified_head}:{name}",
        )
