from __future__ import annotations

import json
import subprocess
from pathlib import Path

from zf.core.config.schema import (
    GitIsolationConfig,
    ProjectConfig,
    QualityGateConfig,
    RoleConfig,
    RuntimeConfig,
    WorkdirConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    WorkflowStageCriteriaConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.candidates import CandidateRebuilder, CandidateTask
from zf.runtime.task_refs import TaskRefManager


class _StubTransport:
    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        pass

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "branch", "-M", "main")
    return _git(root, "rev-parse", "HEAD")


def _config(
    state_dir: Path,
    *,
    quality_gates: dict[str, QualityGateConfig] | None = None,
    setup_script: str = "",
) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(
            name="test",
            state_dir=str(state_dir),
            setup_script=setup_script,
        ),
        roles=[
            RoleConfig(name="dev", backend="mock", role_kind="writer"),
            RoleConfig(
                name="review",
                backend="mock",
                role_kind="reader",
                publishes=["review.approved", "review.rejected"],
            ),
        ],
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
            git=GitIsolationConfig(candidate_base_ref="main"),
        ),
        quality_gates=quality_gates or {},
    )


def _state(tmp_path: Path) -> tuple[Path, ZfConfig, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = _config(state_dir)
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, config, log


def _task_commit(
    root: Path,
    *,
    branch: str,
    file_name: str,
    content: str,
    message: str,
) -> str:
    _git(root, "checkout", "-q", "-B", branch, "main")
    (root / file_name).write_text(content, encoding="utf-8")
    _git(root, "add", file_name)
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _record_task_ref(
    root: Path,
    state_dir: Path,
    config: ZfConfig,
    *,
    task_id: str,
    commit: str,
    branch: str,
    feature_id: str = "F-11111111",
    changed_files: list[str] | None = None,
) -> None:
    result = TaskRefManager(
        state_dir=state_dir,
        project_root=root,
        config=config,
    ).process_dev_build_done(ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id=task_id,
        payload={
            "source_commit": commit,
            "source_branch": branch,
            "feature_id": feature_id,
            "changed_files": changed_files or [],
        },
    ))
    assert result is not None
    assert result.status == "updated"


def _add_task(
    state_dir: Path,
    log: EventLog,
    *,
    task_id: str,
    feature_id: str = "F-11111111",
    verification: str = "",
) -> None:
    task = Task(
        id=task_id,
        title=task_id,
        key=f"{feature_id}:{task_id}",
    )
    task.contract.verification = verification
    TaskStore(state_dir / "kanban.json").add(task)
    log.append(ZfEvent(
        type="task.created",
        actor="zf-cli",
        task_id=task_id,
        payload={"feature_id": feature_id},
    ))


def _approve(log: EventLog, task_id: str, feature_id: str = "F-11111111") -> None:
    log.append(ZfEvent(
        type="review.approved",
        actor="review",
        task_id=task_id,
        payload={"feature_id": feature_id},
    ))


def _rebuilder(root: Path, state_dir: Path, config: ZfConfig, log: EventLog):
    return CandidateRebuilder(
        state_dir=state_dir,
        project_root=root,
        config=config,
        event_log=log,
    )


def test_candidate_quality_prefers_run_scoped_task_contract_commands(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    state_dir, _, log = _state(root)
    config = _config(
        state_dir,
        quality_gates={
            "static": QualityGateConfig(
                required_checks=["npm run missing-config-script"],
            ),
        },
    )
    _add_task(
        state_dir,
        log,
        task_id="TASK-RUST",
        verification="cargo test --workspace",
    )
    candidate_task = CandidateTask(
        task_id="TASK-RUST",
        task_ref="refs/heads/task-rust",
        source_commit="source-rust",
        approval_event_id="approved-rust",
        approval_event_type="review.approved",
    )

    checks, source = _rebuilder(root, state_dir, config, log)._quality_checks(
        [candidate_task],
    )

    assert source == "task_contract"
    assert checks == [("task_contract:TASK-RUST", "cargo test --workspace")]


def test_candidate_quality_uses_config_only_as_legacy_fallback(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    state_dir, _, log = _state(root)
    config = _config(
        state_dir,
        quality_gates={
            "static": QualityGateConfig(required_checks=["make verify"]),
        },
    )

    checks, source = _rebuilder(root, state_dir, config, log)._quality_checks([])

    assert source == "zf_config_fallback"
    assert checks == [("static", "make verify")]


def test_candidate_quality_required_never_uses_config_fallback(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    state_dir, _, log = _state(root)
    config = _config(
        state_dir,
        quality_gates={
            "static": QualityGateConfig(required_checks=["make verify"]),
        },
    )
    config.workflow.candidate_quality_source = "task_contract_required"

    checks, source = _rebuilder(root, state_dir, config, log)._quality_checks([])

    assert checks == []
    assert source == "task_contract_missing"


def test_candidate_quality_required_rejects_partially_missing_contracts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    state_dir, _, log = _state(root)
    config = _config(state_dir)
    config.workflow.candidate_quality_source = "task_contract_required"
    _add_task(
        state_dir,
        log,
        task_id="TASK-WITH-CHECK",
        verification="make verify",
    )
    _add_task(state_dir, log, task_id="TASK-WITHOUT-CHECK")
    tasks = [
        CandidateTask(
            task_id=task_id,
            task_ref=f"refs/heads/{task_id.lower()}",
            source_commit=f"source-{task_id.lower()}",
            approval_event_id=f"approved-{task_id.lower()}",
            approval_event_type="review.approved",
        )
        for task_id in ("TASK-WITH-CHECK", "TASK-WITHOUT-CHECK")
    ]

    checks, source = _rebuilder(root, state_dir, config, log)._quality_checks(tasks)

    assert checks == []
    assert source == "task_contract_missing"


def test_candidate_order_uses_task_map_when_present(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    for task_id in ("TASK-1", "TASK-2"):
        commit = _task_commit(
            tmp_path,
            branch=f"worker/{task_id}",
            file_name=f"{task_id}.txt",
            content=f"{task_id}\n",
            message=task_id,
        )
        _record_task_ref(
            tmp_path,
            state_dir,
            config,
            task_id=task_id,
            commit=commit,
            branch=f"worker/{task_id}",
        )
        _add_task(state_dir, log, task_id=task_id)
        _approve(log, task_id)
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.parent.mkdir(parents=True)
    task_map.write_text(
        json.dumps({"tasks": [{"task_id": "TASK-2"}, {"task_id": "TASK-1"}]}),
        encoding="utf-8",
    )

    tasks = _rebuilder(tmp_path, state_dir, config, log).approved_tasks("F-11111111")

    assert [task.task_id for task in tasks] == ["TASK-2", "TASK-1"]


def test_candidate_excludes_unapproved_task_refs(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    for task_id in ("TASK-1", "TASK-2"):
        commit = _task_commit(
            tmp_path,
            branch=f"worker/{task_id}",
            file_name=f"{task_id}.txt",
            content=f"{task_id}\n",
            message=task_id,
        )
        _record_task_ref(
            tmp_path,
            state_dir,
            config,
            task_id=task_id,
            commit=commit,
            branch=f"worker/{task_id}",
        )
        _add_task(state_dir, log, task_id=task_id)
    _approve(log, "TASK-1")

    tasks = _rebuilder(tmp_path, state_dir, config, log).approved_tasks("F-11111111")

    assert [task.task_id for task in tasks] == ["TASK-1"]


def test_candidate_rebuild_restores_accepted_dependency_refs_after_ledger_rotation(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)

    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-SCAFFOLD", "main")
    package = tmp_path / "app" / "package.json"
    package.parent.mkdir()
    package.write_text('{"name":"demo"}\n', encoding="utf-8")
    _git(tmp_path, "add", "app/package.json")
    _git(tmp_path, "commit", "-q", "-m", "TASK-SCAFFOLD")
    scaffold_commit = _git(tmp_path, "rev-parse", "HEAD")

    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-CORE")
    core = tmp_path / "app" / "src" / "core.ts"
    core.parent.mkdir()
    core.write_text("export const core = false;\n", encoding="utf-8")
    core_types = tmp_path / "app" / "src" / "core-types.ts"
    core_types.write_text("export type Core = boolean;\n", encoding="utf-8")
    _git(tmp_path, "add", "app/src/core.ts", "app/src/core-types.ts")
    _git(tmp_path, "commit", "-q", "-m", "TASK-CORE initial package files")
    core.write_text("export const core = true;\n", encoding="utf-8")
    _git(tmp_path, "add", "app/src/core.ts")
    _git(tmp_path, "commit", "-q", "-m", "TASK-CORE rework")
    core_commit = _git(tmp_path, "rev-parse", "HEAD")

    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-LEAF")
    leaf = tmp_path / "app" / "src" / "leaf.ts"
    leaf.write_text("export const leaf = true;\n", encoding="utf-8")
    _git(tmp_path, "add", "app/src/leaf.ts")
    _git(tmp_path, "commit", "-q", "-m", "TASK-LEAF")
    leaf_commit = _git(tmp_path, "rev-parse", "HEAD")

    unrelated_commit = _task_commit(
        tmp_path,
        branch="worker/TASK-UNRELATED",
        file_name="unrelated.txt",
        content="unrelated\n",
        message="TASK-UNRELATED",
    )
    refs = (
        ("TASK-SCAFFOLD", scaffold_commit, "worker/TASK-SCAFFOLD", ["app/package.json"]),
        ("TASK-CORE", core_commit, "worker/TASK-CORE", ["app/src/core.ts"]),
        ("TASK-LEAF", leaf_commit, "worker/TASK-LEAF", ["app/src/leaf.ts"]),
        ("TASK-UNRELATED", unrelated_commit, "worker/TASK-UNRELATED", ["unrelated.txt"]),
    )
    for task_id, commit, branch, changed_files in refs:
        _record_task_ref(
            tmp_path,
            state_dir,
            config,
            task_id=task_id,
            commit=commit,
            branch=branch,
            changed_files=changed_files,
        )
        _add_task(state_dir, log, task_id=task_id)

    # Simulate a resumed generation whose active ledger contains only the leaf
    # approval while the canonical task-ref index still owns prior results.
    _approve(log, "TASK-LEAF")
    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=EventWriter(log),
    )

    assert result is not None
    assert result.status == "updated", result.payload.get("error")
    assert _git(tmp_path, "show", "candidate/F-11111111:app/package.json") == (
        '{"name":"demo"}'
    )
    assert _git(tmp_path, "show", "candidate/F-11111111:app/src/core.ts") == (
        "export const core = true;"
    )
    assert _git(
        tmp_path,
        "show",
        "candidate/F-11111111:app/src/core-types.ts",
    ) == "export type Core = boolean;"
    assert _git(tmp_path, "show", "candidate/F-11111111:app/src/leaf.ts") == (
        "export const leaf = true;"
    )
    candidate_files = _git(
        tmp_path,
        "ls-tree",
        "-r",
        "--name-only",
        "candidate/F-11111111",
    ).splitlines()
    assert "unrelated.txt" not in candidate_files
    manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text()
    )
    assert [task["task_id"] for task in manifest["requested_tasks"]] == [
        "TASK-LEAF",
    ]
    assert [task["task_id"] for task in manifest["dependency_tasks"]] == [
        "TASK-SCAFFOLD",
        "TASK-CORE",
    ]


def test_candidate_rebuild_rejects_stale_task_index_entry(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    first_commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1",
        file_name="a.txt",
        content="first\n",
        message="TASK-1 first",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=first_commit,
        branch="worker/TASK-1",
        changed_files=["a.txt"],
    )
    stale_task = CandidateTask(
        task_id="TASK-1",
        task_ref="task/TASK-1",
        source_commit=first_commit,
        approval_event_id="approved-1",
        approval_event_type="review.approved",
    )
    second_commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1-v2",
        file_name="a.txt",
        content="second\n",
        message="TASK-1 second",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=second_commit,
        branch="worker/TASK-1-v2",
        changed_files=["a.txt"],
    )

    result = _rebuilder(tmp_path, state_dir, config, log)._rebuild_locked(  # type: ignore[attr-defined]
        pdd_id="F-11111111",
        branch="candidate/F-11111111",
        base_ref="main",
        strategy="cherry-pick",
        tasks=[stale_task],
        manifest_path=state_dir / "candidates" / "F-11111111" / "manifest.json",
    )

    assert result.status == "stale"
    assert result.event_type == "candidate.stale"
    assert result.payload["reason"] == "stale_task_ref"
    assert result.payload["stale_tasks"][0]["current_source_commit"] == second_commit


def test_candidate_rebuild_cherry_picks_approved_task_refs(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    for task_id, file_name in (("TASK-1", "a.txt"), ("TASK-2", "b.txt")):
        branch = f"worker/{task_id}"
        commit = _task_commit(
            tmp_path,
            branch=branch,
            file_name=file_name,
            content=f"{task_id}\n",
            message=task_id,
        )
        _record_task_ref(
            tmp_path,
            state_dir,
            config,
            task_id=task_id,
            commit=commit,
            branch=branch,
        )
        _add_task(state_dir, log, task_id=task_id)
        _approve(log, task_id)
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "updated"
    assert _git(tmp_path, "show", "candidate/F-11111111:a.txt") == "TASK-1"
    assert _git(tmp_path, "show", "candidate/F-11111111:b.txt") == "TASK-2"
    manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text()
    )
    assert manifest["status"] == "updated"
    assert [task["task_id"] for task in manifest["included_tasks"]] == [
        "TASK-1",
        "TASK-2",
    ]
    event_types = [event.type for event in log.read_all()]
    assert "candidate.started" in event_types
    assert "candidate.integration.started" in event_types
    assert "candidate.task_ref.applied" in event_types
    assert "candidate.updated" in event_types
    assert "candidate.integration.completed" in event_types


def test_candidate_subset_rebuild_preserves_existing_candidate_base(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    for task_id, file_name, content in (
        ("TASK-ASSEMBLY", "package.json", '{"name":"app"}\n'),
        ("TASK-CORE", "core.txt", "core\n"),
    ):
        branch = f"worker/{task_id}"
        commit = _task_commit(
            tmp_path,
            branch=branch,
            file_name=file_name,
            content=content,
            message=task_id,
        )
        _record_task_ref(
            tmp_path,
            state_dir,
            config,
            task_id=task_id,
            commit=commit,
            branch=branch,
            changed_files=[file_name],
        )
        _add_task(state_dir, log, task_id=task_id)
        _approve(log, task_id)

    writer = EventWriter(log)
    full = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )
    assert full is not None
    assert full.status == "updated"
    full_commit = _git(tmp_path, "rev-parse", "candidate/F-11111111")

    gap_commit = _task_commit(
        tmp_path,
        branch="worker/TASK-GAP",
        file_name="gap.txt",
        content="gap\n",
        message="TASK-GAP",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-GAP",
        commit=gap_commit,
        branch="worker/TASK-GAP",
        changed_files=["gap.txt"],
    )
    _add_task(state_dir, log, task_id="TASK-GAP")
    _approve(log, "TASK-GAP")

    gap = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
        task_ids=["TASK-GAP"],
    )

    assert gap is not None
    assert gap.status == "updated"
    assert _git(tmp_path, "show", "candidate/F-11111111:package.json") == (
        '{"name":"app"}'
    )
    assert _git(tmp_path, "show", "candidate/F-11111111:core.txt") == "core"
    assert _git(tmp_path, "show", "candidate/F-11111111:gap.txt") == "gap"
    manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text()
    )
    assert manifest["base_ref"] == "refs/heads/candidate/F-11111111"
    assert manifest["base_commit"] == full_commit
    assert [task["task_id"] for task in manifest["included_tasks"]] == ["TASK-GAP"]


def test_candidate_subset_rebuild_rejects_failed_candidate_as_incremental_base(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    _git(tmp_path, "checkout", "-q", "-B", "candidate/F-11111111", "main")
    (tmp_path / "partial.txt").write_text("failed partial\n", encoding="utf-8")
    _git(tmp_path, "add", "partial.txt")
    _git(tmp_path, "commit", "-q", "-m", "failed partial candidate")
    failed_commit = _git(tmp_path, "rev-parse", "HEAD")
    manifest = state_dir / "candidates" / "F-11111111" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"status": "quality_failed", "commit": failed_commit}),
        encoding="utf-8",
    )

    base_ref = _rebuilder(
        tmp_path,
        state_dir,
        config,
        log,
    )._resolve_candidate_base_ref(  # type: ignore[attr-defined]
        "main",
        pdd_id="F-11111111",
        branch="candidate/F-11111111",
        task_ids=["TASK-GAP"],
    )

    assert base_ref == "main"


def test_candidate_rebuild_skips_base_equivalent_assembly_task_ref(tmp_path: Path):
    base_commit = _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    _git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()

    task_specs = (
        ("TASK-CORE", "src/discounts.py", "def discount():\n    return 1\n"),
        ("TASK-TEST", "tests/test_discounts.py", "def test_discount():\n    assert True\n"),
    )
    for task_id, file_name, content in task_specs:
        branch = f"worker/{task_id}"
        commit = _task_commit(
            tmp_path,
            branch=branch,
            file_name=file_name,
            content=content,
            message=task_id,
        )
        _record_task_ref(
            tmp_path,
            state_dir,
            config,
            task_id=task_id,
            commit=commit,
            branch=branch,
            changed_files=[file_name],
        )
        _add_task(state_dir, log, task_id=task_id)
        _approve(log, task_id)

    _git(tmp_path, "branch", "-f", "worker/ASSEMBLY", base_commit)
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-ASSEMBLY",
        commit=base_commit,
        branch="worker/ASSEMBLY",
        changed_files=[],
    )
    _add_task(state_dir, log, task_id="TASK-ASSEMBLY")
    _approve(log, "TASK-ASSEMBLY")

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=EventWriter(log),
    )

    assert result is not None
    assert result.status == "updated", result.payload.get("error")
    assert _git(tmp_path, "show", "candidate/F-11111111:src/discounts.py") == (
        "def discount():\n    return 1"
    )
    assert _git(
        tmp_path,
        "show",
        "candidate/F-11111111:tests/test_discounts.py",
    ) == "def test_discount():\n    assert True"
    manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text()
    )
    assert [task["task_id"] for task in manifest["included_tasks"]] == [
        "TASK-CORE",
        "TASK-TEST",
    ]
    assert manifest["skipped_tasks"] == [{
        "task_id": "TASK-ASSEMBLY",
        "task_ref": "task/TASK-ASSEMBLY",
        "source_commit": base_commit,
        "approval_event_id": manifest["skipped_tasks"][0]["approval_event_id"],
        "approval_event_type": "review.approved",
        "reason": "base_equivalent_task_ref",
        "metadata_only": True,
        "skipped_commits": [base_commit],
    }]
    applied_task_ids = [
        event.task_id for event in log.read_all()
        if event.type == "candidate.task_ref.applied"
    ]
    assert applied_task_ids == ["TASK-CORE", "TASK-TEST"]


def test_candidate_rebuild_uses_harness_identity_without_configured_git_user(
    tmp_path: Path,
):
    # Regression (cj-mono / calc full-flow): candidate.integration aborted —
    # mislabeled candidate.conflict — because the kernel's `git commit` in the
    # candidate worktree failed with "Author identity unknown" on a repo/host
    # with no git user configured (fresh CI box / isolated E2E repo). The kernel
    # must supply a deterministic harness identity for its own integration
    # commits regardless of ambient git config.
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1",
        file_name="a.txt",
        content="TASK-1\n",
        message="TASK-1",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
        changed_files=["a.txt"],
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")
    # Strip the repo identity so no user.name/user.email is resolvable —
    # exactly the state in which the kernel's candidate commit failed.
    _git(tmp_path, "config", "--unset", "user.name", check=False)
    _git(tmp_path, "config", "--unset", "user.email", check=False)
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result.status == "updated", result.payload.get("error")
    # The candidate commit is committed by the deterministic harness identity
    # (env always wins), so this holds on any host regardless of git config and
    # for both the scoped-commit and cherry-pick apply paths (cherry-pick keeps
    # the original author, but the committer is the harness).
    committer = _git(tmp_path, "show", "-s", "--format=%cn", "candidate/F-11111111")
    assert committer == "ZaoFu Harness"


def test_candidate_rebuild_allows_validated_large_scoped_commit(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    paths = [f"src/generated_{index:02d}.txt" for index in range(26)]
    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-1", "main")
    for path in paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{path}\n", encoding="utf-8")
    _git(tmp_path, "add", "--", *paths)
    _git(tmp_path, "commit", "-q", "-m", "large validated task")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
        changed_files=paths,
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.write_text("""#!/bin/sh
count=$(git diff --cached --name-only | wc -l)
if [ "$count" -gt 25 ] && [ -z "$ZF_ALLOW_LARGE_COMMIT" ]; then
  echo "large commit blocked" >&2
  exit 1
fi
""", encoding="utf-8")
    hook.chmod(0o755)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=EventWriter(log),
    )

    assert result.status == "updated", result.payload.get("error")
    assert _git(
        tmp_path,
        "show",
        "candidate/F-11111111:src/generated_25.txt",
    ) == "src/generated_25.txt"


def test_candidate_rebuild_applies_full_task_ranges_and_skips_duplicates(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-1", "main")
    (tmp_path / "a.txt").write_text("TASK-1\n", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "TASK-1")
    task1_commit = _git(tmp_path, "rev-parse", "HEAD")
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=task1_commit,
        branch="worker/TASK-1",
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")

    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-2", "worker/TASK-1")
    (tmp_path / "b.txt").write_text("TASK-2\n", encoding="utf-8")
    _git(tmp_path, "add", "b.txt")
    _git(tmp_path, "commit", "-q", "-m", "TASK-2")
    task2_commit = _git(tmp_path, "rev-parse", "HEAD")
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-2",
        commit=task2_commit,
        branch="worker/TASK-2",
    )
    _add_task(state_dir, log, task_id="TASK-2")
    _approve(log, "TASK-2")
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "updated"
    assert _git(tmp_path, "show", "candidate/F-11111111:a.txt") == "TASK-1"
    assert _git(tmp_path, "show", "candidate/F-11111111:b.txt") == "TASK-2"
    task2_event = [
        event
        for event in log.read_all()
        if event.type == "candidate.task_ref.applied" and event.task_id == "TASK-2"
    ][0]
    assert task2_event.payload["commit_count"] == 2
    assert task1_commit in task2_event.payload["skipped_commits"]
    assert task2_commit in task2_event.payload["applied_commits"]


def test_candidate_rebuild_skips_stacked_commits_outside_task_scope(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-1", "main")
    (tmp_path / "a.txt").write_text("TASK-1\n", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "TASK-1")
    task1_commit = _git(tmp_path, "rev-parse", "HEAD")
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=task1_commit,
        branch="worker/TASK-1",
        changed_files=["a.txt"],
    )
    log.append(ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"changed_files": ["a.txt"]},
    ))
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")

    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-2", "main")
    (tmp_path / "a.txt").write_text("duplicate prerequisite\n", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "duplicate prerequisite")
    duplicate_commit = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "b.txt").write_text("TASK-2\n", encoding="utf-8")
    _git(tmp_path, "add", "b.txt")
    _git(tmp_path, "commit", "-q", "-m", "TASK-2")
    task2_commit = _git(tmp_path, "rev-parse", "HEAD")
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-2",
        commit=task2_commit,
        branch="worker/TASK-2",
        changed_files=["b.txt"],
    )
    log.append(ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-2",
        payload={"changed_files": ["b.txt"]},
    ))
    _add_task(state_dir, log, task_id="TASK-2")
    _approve(log, "TASK-2")
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "updated"
    assert _git(tmp_path, "show", "candidate/F-11111111:a.txt") == "TASK-1"
    assert _git(tmp_path, "show", "candidate/F-11111111:b.txt") == "TASK-2"
    task2_event = [
        event
        for event in log.read_all()
        if event.type == "candidate.task_ref.applied" and event.task_id == "TASK-2"
    ][0]
    assert task2_event.payload["scope_skipped_commits"] == [duplicate_commit]
    assert task2_event.payload["task_commits"] == [task2_commit]


def test_candidate_rebuild_scopes_mixed_stacked_commit_to_declared_files(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    (tmp_path / "core.txt").write_text("base\n", encoding="utf-8")
    (tmp_path / "tool.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "core.txt", "tool.txt")
    _git(tmp_path, "commit", "-q", "-m", "base files")

    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-1", "main")
    (tmp_path / "core.txt").write_text("task-1\n", encoding="utf-8")
    _git(tmp_path, "add", "core.txt")
    _git(tmp_path, "commit", "-q", "-m", "TASK-1")
    task1_commit = _git(tmp_path, "rev-parse", "HEAD")
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=task1_commit,
        branch="worker/TASK-1",
        changed_files=["core.txt"],
    )
    log.append(ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"changed_files": ["core.txt"]},
    ))
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")

    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-2", "main")
    (tmp_path / "core.txt").write_text("stale stacked baseline\n", encoding="utf-8")
    (tmp_path / "tool.txt").write_text("prep\n", encoding="utf-8")
    _git(tmp_path, "add", "core.txt", "tool.txt")
    _git(tmp_path, "commit", "-q", "-m", "mixed stacked prerequisite")
    mixed_commit = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "tool.txt").write_text("task-2\n", encoding="utf-8")
    _git(tmp_path, "add", "tool.txt")
    _git(tmp_path, "commit", "-q", "-m", "TASK-2")
    task2_commit = _git(tmp_path, "rev-parse", "HEAD")
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-2",
        commit=task2_commit,
        branch="worker/TASK-2",
        changed_files=["tool.txt"],
    )
    log.append(ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-2",
        payload={"changed_files": ["tool.txt"]},
    ))
    _add_task(state_dir, log, task_id="TASK-2")
    _approve(log, "TASK-2")
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "updated"
    assert _git(tmp_path, "show", "candidate/F-11111111:core.txt") == "task-1"
    assert _git(tmp_path, "show", "candidate/F-11111111:tool.txt") == "task-2"
    task2_event = [
        event
        for event in log.read_all()
        if event.type == "candidate.task_ref.applied" and event.task_id == "TASK-2"
    ][0]
    assert task2_event.payload["task_commits"] == [mixed_commit, task2_commit]
    assert task2_event.payload["applied_commits"] == [mixed_commit, task2_commit]


def test_candidate_rebuild_preserves_workspace_package_closure_for_reworked_task(
    tmp_path: Path,
):
    base = _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)

    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-CONTRACTS", "main")
    files = {
        "packages/contracts/package.json": '{"name":"@demo/contracts","version":"1.0.0"}\n',
        "packages/contracts/src/index.ts": "export * from './tools';\n",
        "packages/contracts/src/tools.ts": "export const tool = 'v1';\n",
        "packages/contracts/tsconfig.json": "{}\n",
        "packages/core/package.json": '{"name":"@demo/core","version":"1.0.0"}\n',
        "packages/core/src/index.ts": "export * from './iteration-budget';\n",
        "packages/core/src/iteration-budget.ts": "export const budget = 1;\n",
        "packages/test-harness/package.json": '{"name":"@demo/test-harness","version":"1.0.0"}\n',
        "packages/test-harness/src/index.ts": "export const harness = true;\n",
    }
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(tmp_path, "add", "packages")
    _git(tmp_path, "commit", "-q", "-m", "TASK-CONTRACTS initial packages")

    for rel in (
        "packages/contracts/package.json",
        "packages/core/package.json",
        "packages/test-harness/package.json",
    ):
        (tmp_path / rel).write_text(
            (tmp_path / rel).read_text(encoding="utf-8").replace("1.0.0", "1.0.1"),
            encoding="utf-8",
        )
    _git(tmp_path, "add", "packages/contracts/package.json", "packages/core/package.json", "packages/test-harness/package.json")
    _git(tmp_path, "commit", "-q", "-m", "TASK-CONTRACTS pin package versions")
    package_pin_commit = _git(tmp_path, "rev-parse", "HEAD")

    (tmp_path / "packages/contracts/src/tools.ts").write_text(
        "export const tool = 'v2';\n",
        encoding="utf-8",
    )
    (tmp_path / "packages/core/src/iteration-budget.ts").write_text(
        "export const budget = 2;\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "packages/contracts/src/tools.ts", "packages/core/src/iteration-budget.ts")
    _git(tmp_path, "commit", "-q", "-m", "TASK-CONTRACTS latest source tweak")
    final_commit = _git(tmp_path, "rev-parse", "HEAD")

    build_done = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-CONTRACTS",
        payload={
            "source_commit": final_commit,
            "source_branch": "worker/TASK-CONTRACTS",
            "base_git_head": package_pin_commit,
            "changed_files": [
                "packages/contracts/src/tools.ts",
                "packages/core/src/iteration-budget.ts",
            ],
        },
    )
    log.append(build_done)
    task_ref = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(build_done)
    assert task_ref is not None
    assert task_ref.status == "updated"
    assert task_ref.payload["changed_files"] == [
        "packages/contracts/src/tools.ts",
        "packages/core/src/iteration-budget.ts",
    ]
    _add_task(state_dir, log, task_id="TASK-CONTRACTS")
    _approve(log, "TASK-CONTRACTS")

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=EventWriter(log),
    )

    assert result is not None
    assert result.status == "updated", result.payload.get("error")
    assert _git(
        tmp_path,
        "show",
        "candidate/F-11111111:packages/contracts/package.json",
    ) == '{"name":"@demo/contracts","version":"1.0.1"}'
    assert _git(
        tmp_path,
        "show",
        "candidate/F-11111111:packages/contracts/src/index.ts",
    ) == "export * from './tools';"
    assert _git(
        tmp_path,
        "show",
        "candidate/F-11111111:packages/contracts/src/tools.ts",
    ) == "export const tool = 'v2';"
    assert _git(
        tmp_path,
        "show",
        "candidate/F-11111111:packages/core/package.json",
    ) == '{"name":"@demo/core","version":"1.0.1"}'
    assert _git(
        tmp_path,
        "show",
        "candidate/F-11111111:packages/core/src/iteration-budget.ts",
    ) == "export const budget = 2;"
    assert _git(
        tmp_path,
        "show",
        "candidate/F-11111111:packages/test-harness/package.json",
    ) == '{"name":"@demo/test-harness","version":"1.0.1"}'
    task_event = [
        event for event in log.read_all()
        if event.type == "candidate.task_ref.applied"
        and event.task_id == "TASK-CONTRACTS"
    ][0]
    assert package_pin_commit in task_event.payload["task_commits"]
    assert package_pin_commit in task_event.payload["applied_commits"]


def test_candidate_rebuild_materializes_stage_criteria_config_refs(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    config.workflow = WorkflowConfig(stages=[
        WorkflowStageConfig(
            id="final-judge",
            trigger="verify.passed",
            criteria=WorkflowStageCriteriaConfig(success_criteria=[{
                "kind": "artifact_matrix_gate",
                "config_ref": "docs/plans/gate.json",
            }]),
        ),
    ])
    gate = tmp_path / "docs" / "plans" / "gate.json"
    gate.parent.mkdir(parents=True)
    gate.write_text(
        json.dumps({
            "schema": "artifact-matrix-gate.v1",
            "required_artifacts": [
                "feature.txt",
                "docs/plans/acceptance.json",
                "docs/validation/module-parity",
            ],
        }) + "\n",
        encoding="utf-8",
    )
    governance = (
        state_dir
        / "artifacts"
        / "fanout-plan"
        / "candidate-governance"
    )
    (governance / "docs/plans").mkdir(parents=True)
    (governance / "docs/plans/acceptance.json").write_text(
        '[{"id":"CAP-1","status":"done"}]\n',
        encoding="utf-8",
    )
    (governance / "docs/validation/module-parity").mkdir(parents=True)
    (governance / "docs/validation/module-parity/core.json").write_text(
        '{"module_id":"core","open_p0_p1_gap_count":0}\n',
        encoding="utf-8",
    )
    commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1",
        file_name="feature.txt",
        content="feature\n",
        message="TASK-1",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
        changed_files=["feature.txt"],
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=EventWriter(log),
    )

    assert result is not None
    assert result.status == "updated"
    assert _git(tmp_path, "show", "candidate/F-11111111:feature.txt") == "feature"
    assert (
        _git(tmp_path, "show", "candidate/F-11111111:docs/plans/gate.json")
        == gate.read_text(encoding="utf-8").strip()
    )
    assert (
        _git(tmp_path, "show", "candidate/F-11111111:docs/plans/acceptance.json")
        == '[{"id":"CAP-1","status":"done"}]'
    )
    assert (
        _git(
            tmp_path,
            "show",
            "candidate/F-11111111:docs/validation/module-parity/core.json",
        )
        == '{"module_id":"core","open_p0_p1_gap_count":0}'
    )
    assert result.payload["materialized_config_refs"] == [
        {
            "path": "docs/plans/gate.json",
            "source": str(gate.resolve()),
            "reason": "workflow_stage_criteria_config_ref",
        },
        {
            "path": "docs/plans/acceptance.json",
            "source": str(
                governance / "docs/plans/acceptance.json",
            ),
            "reason": "workflow_stage_criteria_required_artifact",
        },
        {
            "path": "docs/validation/module-parity",
            "source": str(
                governance / "docs/validation/module-parity",
            ),
            "reason": "workflow_stage_criteria_required_artifact",
        },
    ]


def test_candidate_rebuild_fails_when_required_source_output_is_missing(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-1", "main")
    (tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
    (tmp_path / "required.txt").write_text("required\n", encoding="utf-8")
    _git(tmp_path, "add", "feature.txt", "required.txt")
    _git(tmp_path, "commit", "-q", "-m", "TASK-1 adds feature and required output")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
        changed_files=["feature.txt"],
    )
    _add_task(state_dir, log, task_id="TASK-1")
    log.append(ZfEvent(
        type="review.approved",
        actor="review",
        task_id="TASK-1",
        payload={
            "feature_id": "F-11111111",
            "required_source_outputs": ["required.txt"],
        },
    ))

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=EventWriter(log),
    )

    assert result is not None
    assert result.status == "quality_failed"
    assert result.event_type == "candidate.quality.failed"
    assert result.payload["missing_required_source_outputs"] == [{
        "task_id": "TASK-1",
        "path": "required.txt",
        "source": "review.approved",
        "reason": "missing_from_candidate_worktree",
        "rework_owner_hint": "assembly",
    }]
    assert result.payload["candidate_closure"]["status"] == "failed"
    assert result.payload["rework_owner_hint"] == "assembly"
    assert _git(tmp_path, "show", "candidate/F-11111111:feature.txt") == "feature"
    assert _git(
        tmp_path,
        "show",
        "candidate/F-11111111:required.txt",
        check=False,
    ) == ""
    event_types = [event.type for event in log.read_all()]
    assert "candidate.quality.failed" in event_types
    assert "candidate.updated" not in event_types


def test_candidate_rebuild_ignores_rejected_scope_events(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    package_path = tmp_path / "app" / "package.json"
    provider_path = tmp_path / "app" / "packages" / "provider" / "index.ts"
    provider_path.parent.mkdir(parents=True)
    package_path.write_text('{"name":"base"}\n', encoding="utf-8")
    provider_path.write_text("export const provider = 'base';\n", encoding="utf-8")
    _git(tmp_path, "add", "app/package.json", "app/packages/provider/index.ts")
    _git(tmp_path, "commit", "-q", "-m", "base app")

    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-1", "main")
    package_path.unlink()
    _git(tmp_path, "add", "-u", "app/package.json")
    _git(tmp_path, "commit", "-q", "-m", "rejected root cleanup")
    rejected_commit = _git(tmp_path, "rev-parse", "HEAD")
    provider_path.write_text("export const provider = 'task';\n", encoding="utf-8")
    _git(tmp_path, "add", "app/packages/provider/index.ts")
    _git(tmp_path, "commit", "-q", "-m", "TASK-1 provider")
    task_commit = _git(tmp_path, "rev-parse", "HEAD")

    log.append(ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"changed_files": ["app/package.json"]},
    ))
    log.append(ZfEvent(
        type="task.ref.rejected",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "source_commit": rejected_commit,
            "changed_files": ["app/package.json"],
        },
    ))
    log.append(ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={
            "source_commit": task_commit,
            "changed_files": ["app/packages/provider/index.ts"],
        },
    ))
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=task_commit,
        branch="worker/TASK-1",
        changed_files=["app/packages/provider/index.ts"],
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "updated", result.payload.get("error")
    assert _git(tmp_path, "show", "candidate/F-11111111:app/package.json") == (
        '{"name":"base"}'
    )
    assert _git(
        tmp_path,
        "show",
        "candidate/F-11111111:app/packages/provider/index.ts",
    ) == "export const provider = 'task';"
    task_event = [
        event
        for event in log.read_all()
        if event.type == "candidate.task_ref.applied" and event.task_id == "TASK-1"
    ][0]
    assert task_event.payload["scope_skipped_commits"] == [rejected_commit]


def test_candidate_rebuild_applies_scoped_file_deletions(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    obsolete_path = tmp_path / "packages" / "provider" / "obsolete.ts"
    obsolete_path.parent.mkdir(parents=True)
    obsolete_path.write_text("export const obsolete = true;\n", encoding="utf-8")
    _git(tmp_path, "add", "packages/provider/obsolete.ts")
    _git(tmp_path, "commit", "-q", "-m", "base provider file")

    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-1", "main")
    _git(tmp_path, "rm", "-q", "packages/provider/obsolete.ts")
    _git(tmp_path, "commit", "-q", "-m", "TASK-1 remove obsolete")
    task_commit = _git(tmp_path, "rev-parse", "HEAD")
    log.append(ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={
            "source_commit": task_commit,
            "changed_files": ["packages/provider/obsolete.ts"],
        },
    ))
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=task_commit,
        branch="worker/TASK-1",
        changed_files=["packages/provider/obsolete.ts"],
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "updated", result.payload.get("error")
    missing = subprocess.run(
        [
            "git",
            "cat-file",
            "-e",
            "candidate/F-11111111:packages/provider/obsolete.ts",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode != 0


def test_candidate_quality_gate_passes_before_updated(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, _, log = _state(tmp_path)
    config = _config(
        state_dir,
        quality_gates={
            "candidate": QualityGateConfig(
                enabled=True,
                required_checks=["test -f a.txt"],
            ),
        },
    )
    commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1",
        file_name="a.txt",
        content="TASK-1\n",
        message="TASK-1",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "updated"
    manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text()
    )
    assert manifest["quality_status"] == "passed"
    assert manifest["quality"]["gates_passed"] == ["candidate"]
    event_types = [event.type for event in log.read_all()]
    assert event_types.index("candidate.quality.started") < event_types.index(
        "candidate.quality.passed"
    )
    assert event_types.index("candidate.quality.passed") < event_types.index(
        "candidate.updated"
    )


def test_candidate_runs_declared_setup_before_quality_gate(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, _, log = _state(tmp_path)
    config = _config(
        state_dir,
        setup_script="test -f a.txt && printf ready > .candidate-env-ready",
        quality_gates={
            "candidate": QualityGateConfig(
                enabled=True,
                required_checks=["test -f .candidate-env-ready"],
            ),
        },
    )
    commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1",
        file_name="a.txt",
        content="TASK-1\n",
        message="TASK-1",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=EventWriter(log),
    )

    assert result is not None and result.status == "updated"
    assert result.payload["candidate_environment"]["status"] == "ready"
    assert result.payload["candidate_environment"]["setup_ran"] is True
    assert result.payload["quality"]["gates_passed"] == ["candidate"]


def test_candidate_setup_failure_blocks_quality_gate(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, _, log = _state(tmp_path)
    config = _config(
        state_dir,
        setup_script="echo setup-broke >&2; exit 7",
        quality_gates={
            "candidate": QualityGateConfig(
                enabled=True,
                required_checks=["touch quality-gate-ran"],
            ),
        },
    )
    commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1",
        file_name="a.txt",
        content="TASK-1\n",
        message="TASK-1",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=EventWriter(log),
    )

    assert result is not None and result.status == "quality_failed"
    assert result.payload["quality"]["failure"] == "candidate_environment_setup_failed"
    assert result.payload["candidate_environment"]["exit_code"] == 7
    assert not (
        Path(result.payload["merger_worktree"]) / "quality-gate-ran"
    ).exists()


def test_candidate_quality_gate_prefers_candidate_src_over_inherited_pythonpath(
    tmp_path: Path,
    monkeypatch,
):
    _init_repo(tmp_path)
    state_dir, _, log = _state(tmp_path)
    inherited_src = tmp_path / "inherited" / "src"
    inherited_src.mkdir(parents=True)
    (inherited_src / "candidate_marker.py").write_text(
        "VALUE = 'inherited'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(inherited_src))
    monkeypatch.setenv("ZF_PROJECT_ROOT", str(tmp_path / "inherited"))
    config = _config(
        state_dir,
        quality_gates={
            "candidate": QualityGateConfig(
                enabled=True,
                required_checks=[
                    "python -c 'import sys, candidate_marker; "
                    "sys.stdout.write(candidate_marker.VALUE)'",
                ],
            ),
        },
    )
    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-1", "main")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "candidate_marker.py").write_text(
        "VALUE = 'candidate'\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "src/candidate_marker.py")
    _git(tmp_path, "commit", "-q", "-m", "TASK-1")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "updated"
    manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text()
    )
    check = manifest["quality"]["gate_checks"]["candidate"][0]
    assert manifest["quality_status"] == "passed"
    assert check["stdout_tail"] == "candidate"


def test_candidate_quality_gate_failure_blocks_updated_result(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, _, log = _state(tmp_path)
    config = _config(
        state_dir,
        quality_gates={
            "candidate": QualityGateConfig(
                enabled=True,
                required_checks=["test -f missing.txt"],
            ),
        },
    )
    commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1",
        file_name="a.txt",
        content="TASK-1\n",
        message="TASK-1",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "quality_failed"
    manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text()
    )
    assert manifest["status"] == "quality_failed"
    assert manifest["quality_status"] == "failed"
    assert manifest["quality"]["gates_failed"] == ["candidate"]
    event_types = [event.type for event in log.read_all()]
    assert "candidate.quality.failed" in event_types
    assert "candidate.updated" not in event_types


def test_candidate_dirty_worktree_blocks_updated_result(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, _, log = _state(tmp_path)
    config = _config(
        state_dir,
        quality_gates={
            # A gate that mutates *committed* (tracked) content leaves the
            # candidate tree diverged from what was verified — that must still
            # block. (Untracked byproducts like package-lock.json do NOT block;
            # see test_candidate_worktree_clean_ignores_untracked_byproducts.)
            "candidate": QualityGateConfig(
                enabled=True,
                required_checks=["echo dirtied-by-gate >> a.txt"],
            ),
        },
    )
    commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1",
        file_name="a.txt",
        content="TASK-1\n",
        message="TASK-1",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "quality_failed"
    manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text()
    )
    assert manifest["status"] == "quality_failed"
    assert "candidate_worktree_clean" in manifest["quality"]["gates_failed"]
    clean = manifest["quality"]["intrinsic_checks"]["candidate_worktree_clean"]
    assert "a.txt" in clean["stdout_tail"]
    event_types = [event.type for event in log.read_all()]
    assert "candidate.quality.failed" in event_types
    assert "candidate.updated" not in event_types


def test_candidate_worktree_clean_ignores_untracked_byproducts() -> None:
    """Untracked files (``??``) are never shipped by ``git merge candidate`` —
    dependency-install byproducts (package-lock.json), build caches, and
    generated files a gate leaves behind must not fail the candidate clean
    check. Parity with ship.py _dirty_files (B-NEW-13). Only tracked changes
    (M/A/D/R) count as a dirty candidate."""
    reportable_fn = __import__(
        "zf.runtime.candidates",
        fromlist=["_candidate_reportable_status"],
    )._candidate_reportable_status

    untracked_only = (
        "?? .venv\n"
        "?? node_modules/\n"
        "?? app/package-lock.json\n"          # the E2E regression (2026-07-08)
        "?? yarn.lock\n"
        "?? src/generated.ts\n"
    )
    assert reportable_fn(untracked_only) == ""   # all untracked → clean

    # Tracked modifications still surface as dirty (they change shipped content).
    with_tracked = untracked_only + " M src/app.ts\n" + "D  removed.py\n"
    reportable = reportable_fn(with_tracked)
    assert "src/app.ts" in reportable
    assert "removed.py" in reportable
    assert "package-lock.json" not in reportable
    assert "generated.ts" not in reportable


def test_candidate_quality_gate_repairs_whitespace_in_submitted_diff(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, _, log = _state(tmp_path)
    config = _config(
        state_dir,
        quality_gates={
            "static": QualityGateConfig(
                enabled=True,
                required_checks=["git diff --check"],
            ),
        },
    )
    commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1",
        file_name="a.txt",
        content="bad whitespace   \n",
        message="TASK-1",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "updated"
    manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text()
    )
    assert manifest["quality_status"] == "passed"
    assert manifest["quality"]["gates_failed"] == []
    repair = manifest["quality"]["mechanical_repairs"][0]
    assert repair["status"] == "applied"
    assert repair["repaired_paths"] == ["a.txt"]
    assert _git(tmp_path, "show", "candidate/F-11111111:a.txt") == "bad whitespace"
    assert _git(tmp_path, "diff", "--check", "main..candidate/F-11111111") == ""
    event_types = [event.type for event in log.read_all()]
    assert "candidate.mechanical_fix.applied" in event_types
    assert "candidate.quality.failed" not in event_types
    assert "candidate.updated" in event_types


def test_candidate_quality_gate_does_not_repair_whitespace_outside_declared_scope(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir, _, log = _state(tmp_path)
    config = _config(
        state_dir,
        quality_gates={
            "candidate": QualityGateConfig(
                enabled=True,
                required_checks=["git diff --check ${BASE_COMMIT}..${HEAD_COMMIT}"],
            ),
        },
    )
    commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1",
        file_name="a.txt",
        content="bad whitespace  \n",
        message="TASK-1",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
        changed_files=["different.txt"],
    )
    log.append(ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-1",
        payload={"changed_files": ["different.txt"]},
    ))
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "quality_failed"
    event_types = [event.type for event in log.read_all()]
    assert "candidate.mechanical_fix.applied" not in event_types
    assert "candidate.quality.failed" in event_types


def test_candidate_quality_gate_does_not_repair_non_mechanical_diff_check(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir, _, log = _state(tmp_path)
    config = _config(
        state_dir,
        quality_gates={
            "candidate": QualityGateConfig(
                enabled=True,
                required_checks=[
                    "printf 'a.txt:1: semantic failure.\\n' >&2; exit 1",
                ],
            ),
        },
    )
    commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1",
        file_name="a.txt",
        content="content\n",
        message="TASK-1",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "quality_failed"
    event_types = [event.type for event in log.read_all()]
    assert "candidate.mechanical_fix.applied" not in event_types
    assert "candidate.quality.failed" in event_types


def test_candidate_quality_gate_accepts_matching_expected_red_evidence(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    state_dir, _, log = _state(tmp_path)
    config = _config(
        state_dir,
        quality_gates={
            "candidate": QualityGateConfig(
                enabled=True,
                required_checks=["false"],
            ),
        },
    )
    commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1",
        file_name="a.txt",
        content="TASK-1\n",
        message="TASK-1",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
    )
    _add_task(state_dir, log, task_id="TASK-1")
    log.append(ZfEvent(
        type="review.approved",
        actor="review",
        task_id="TASK-1",
        payload={
            "feature_id": "F-11111111",
            "checks": [
                {
                    "command": "false",
                    "exit_code": 1,
                    "tier": "runtime",
                    "status": "RED_expected",
                },
            ],
        },
    ))
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "updated"
    manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text()
    )
    assert manifest["quality_status"] == "passed"
    assert manifest["quality"]["gates_passed"] == ["candidate"]
    check = manifest["quality"]["gate_checks"]["candidate"][0]
    assert check["status"] == "RED_expected"
    assert check["expected_red_evidence"]["task_id"] == "TASK-1"
    event_types = [event.type for event in log.read_all()]
    assert "candidate.quality.passed" in event_types
    assert "candidate.updated" in event_types


def test_candidate_conflict_emits_event_and_does_not_update_ref(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    for task_id, content in (("TASK-1", "one\n"), ("TASK-2", "two\n")):
        branch = f"worker/{task_id}"
        commit = _task_commit(
            tmp_path,
            branch=branch,
            file_name="README.md",
            content=content,
            message=task_id,
        )
        _record_task_ref(
            tmp_path,
            state_dir,
            config,
            task_id=task_id,
            commit=commit,
            branch=branch,
        )
        _add_task(state_dir, log, task_id=task_id)
        _approve(log, task_id)
    writer = EventWriter(log)

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
    )

    assert result is not None
    assert result.status == "conflict"
    assert "README.md" in result.payload["conflict_files"]
    missing = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/heads/candidate/F-11111111"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode != 0
    manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text()
    )
    assert manifest["status"] == "conflict"
    assert any(event.type == "candidate.conflict" for event in log.read_all())


def test_candidate_conflict_event_carries_fanout_id_from_trigger(tmp_path: Path):
    """canonical-dag v1/v2/v3 require fanout_id on candidate.conflict, but the
    manifest-derived payload never carried it — under a blocking discriminator
    the real conflict signal would be rejected (2026-07-10 kernel-emission
    audit, same wedge class as refactor.scan.failed). The emission enriches
    fanout_id from the trigger event."""
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    for task_id, content in (("TASK-1", "one\n"), ("TASK-2", "two\n")):
        branch = f"worker/{task_id}"
        commit = _task_commit(
            tmp_path,
            branch=branch,
            file_name="README.md",
            content=content,
            message=task_id,
        )
        _record_task_ref(
            tmp_path,
            state_dir,
            config,
            task_id=task_id,
            commit=commit,
            branch=branch,
        )
        _add_task(state_dir, log, task_id=task_id)
        _approve(log, task_id)
    writer = EventWriter(log)
    trigger = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        payload={"fanout_id": "fanout-cand-1", "task_map_ref": "x"},
    )

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=writer,
        trigger_event=trigger,
    )

    assert result is not None
    assert result.status == "conflict"
    conflicts = [e for e in log.read_all() if e.type == "candidate.conflict"]
    assert conflicts
    assert conflicts[-1].payload.get("fanout_id") == "fanout-cand-1"
    assert conflicts[-1].payload.get("status") == "conflict"


def test_review_approval_housekeeping_rebuilds_candidate(tmp_path: Path):
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1",
        file_name="a.txt",
        content="TASK-1\n",
        message="TASK-1",
    )
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
    )
    _add_task(state_dir, log, task_id="TASK-1")
    approval = ZfEvent(
        type="review.approved",
        actor="review",
        task_id="TASK-1",
        payload={"feature_id": "F-11111111"},
    )
    log.append(approval)
    orch = Orchestrator(state_dir, config, _StubTransport())  # type: ignore[arg-type]

    orch._apply_housekeeping(approval)  # type: ignore[attr-defined]

    assert _git(tmp_path, "show", "candidate/F-11111111:a.txt") == "TASK-1"
    event_types = [event.type for event in log.read_all()]
    assert "candidate.started" in event_types
    assert "candidate.updated" in event_types


def test_candidate_default_main_base_falls_back_to_current_branch(tmp_path: Path):
    _init_repo(tmp_path)
    _git(tmp_path, "branch", "-M", "dev")
    state_dir, config, log = _state(tmp_path)
    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-1", "dev")
    (tmp_path / "a.txt").write_text("TASK-1\n", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "TASK-1")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    _record_task_ref(
        tmp_path,
        state_dir,
        config,
        task_id="TASK-1",
        commit=commit,
        branch="worker/TASK-1",
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")
    _git(tmp_path, "checkout", "-q", "dev")

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild("F-11111111")

    assert result is not None
    assert result.status == "updated"
    manifest = json.loads(
        (state_dir / "candidates" / "F-11111111" / "manifest.json").read_text()
    )
    assert manifest["requested_base_ref"] == "main"
    assert manifest["base_ref"] == "dev"


def test_candidate_rebuild_uses_task_index_changed_files_when_event_report_is_subset(
    tmp_path: Path,
):
    base = _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    _git(tmp_path, "checkout", "-q", "-B", "worker/TASK-ASSEMBLY", "main")
    (tmp_path / "package.json").write_text('{"name":"app"}\n', encoding="utf-8")
    boot = tmp_path / "packages" / "assembly" / "src" / "boot.ts"
    boot.parent.mkdir(parents=True)
    boot.write_text("export const boot = true;\n", encoding="utf-8")
    _git(tmp_path, "add", "package.json", "packages/assembly/src/boot.ts")
    _git(tmp_path, "commit", "-q", "-m", "assembly root")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", "main")

    build_done = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="TASK-ASSEMBLY",
        payload={
            "source_commit": commit,
            "source_branch": "worker/TASK-ASSEMBLY",
            "base_git_head": base,
            "files_touched": ["package.json"],
        },
    )
    log.append(build_done)
    task_ref = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(build_done)
    assert task_ref is not None
    assert task_ref.status == "updated"
    _add_task(state_dir, log, task_id="TASK-ASSEMBLY")
    _approve(log, "TASK-ASSEMBLY")

    result = _rebuilder(tmp_path, state_dir, config, log).rebuild(
        "F-11111111",
        event_writer=EventWriter(log),
    )

    assert result is not None
    assert result.status == "updated", result.payload.get("error")
    assert _git(tmp_path, "show", "candidate/F-11111111:package.json") == (
        '{"name":"app"}'
    )
    assert _git(
        tmp_path,
        "show",
        "candidate/F-11111111:packages/assembly/src/boot.ts",
    ) == "export const boot = true;"


def test_candidate_rebuild_idempotent_against_patch_equivalent_base(tmp_path: Path):
    """FIX-10(bizsim r4 F10):增量 base 含 cherry-pick 拷贝(hash 不同、
    patch 相同)时,同一补丁不得再次集成——r4 churn 期重复系列即树损坏根源。"""
    _init_repo(tmp_path)
    state_dir, config, log = _state(tmp_path)
    commit = _task_commit(
        tmp_path,
        branch="worker/TASK-1",
        file_name="a.txt",
        content="TASK-1\n",
        message="TASK-1",
    )
    _record_task_ref(
        tmp_path, state_dir, config,
        task_id="TASK-1", commit=commit, branch="worker/TASK-1",
    )
    _add_task(state_dir, log, task_id="TASK-1")
    _approve(log, "TASK-1")
    writer = EventWriter(log)
    rebuilder = _rebuilder(tmp_path, state_dir, config, log)

    first = rebuilder.rebuild("F-11111111", event_writer=writer)
    assert first is not None and first.status == "updated"
    first_head = _git(tmp_path, "rev-parse", "candidate/F-11111111")

    # 模拟增量 base = 旧 candidate(patch-equivalent 拷贝已在 base 侧)
    config.runtime.git.candidate_base_ref = "candidate/F-11111111"
    second = rebuilder.rebuild("F-11111111", event_writer=writer)

    assert second is not None
    second_head = _git(tmp_path, "rev-parse", "candidate/F-11111111")
    assert second_head == first_head, "同一补丁被重复集成(patch-id 幂等失效)"
    picked_again = [
        e for e in log.read_all()
        if e.type == "candidate.task_ref.applied"
    ]
    assert len(picked_again) == 1, "第二次 rebuild 不得再次 apply 等价补丁"
