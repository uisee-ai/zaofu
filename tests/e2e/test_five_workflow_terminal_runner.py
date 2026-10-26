from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys
import time

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore

from tests.e2e import five_workflow_terminal_runner as runner


def _manifest(
    *,
    family: str,
    task_id: str = "TASK-1",
    cursor: int = 0,
    require_task_terminal: bool | None = None,
) -> dict:
    return {
        "schema_version": runner.CASE_MANIFEST_SCHEMA,
        "family": family,
        "task_id": task_id,
        "route_id": (
            f"delivery:{family}:standard"
            if family in runner.DELIVERY_FAMILIES
            else f"{family}:entry"
        ),
        "entry_pattern_id": f"{family}-entry",
        "event_cursor": cursor,
        "require_task_terminal": (
            family in runner.DELIVERY_FAMILIES | {"research"}
            if require_task_terminal is None
            else require_task_terminal
        ),
    }


def _invoke(*, family: str, task_id: str = "TASK-1", run_id: str = "RUN-1") -> ZfEvent:
    return ZfEvent(
        type="workflow.invoke.requested",
        task_id=task_id,
        payload={
            "task_id": task_id,
            "workflow_run_id": run_id,
            "pattern_id": f"{family}-entry",
        },
    )


def _state(tmp_path: Path, *, task_status: str = "in_progress") -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(parents=True)
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="Five workflow case",
        status=task_status,
    ))
    return state_dir


def test_suite_preflight_requires_clean_seed_fresh_state_and_all_families(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    implementation_root = tmp_path / "implementation"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True)
    implementation_root.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    routes = [
        {"route_id": "delivery:issue:standard", "family": "delivery", "kind": "issue"},
        {"route_id": "delivery:prd:standard", "family": "delivery", "kind": "prd"},
        {"route_id": "delivery:refactor:standard", "family": "delivery", "kind": "refactor"},
        {"route_id": "general:audit", "family": "general", "kind": "workflow"},
        {"route_id": "research:fixed", "family": "research", "kind": "research"},
    ]
    fake_config = object()
    monkeypatch.setattr(runner, "load_config", lambda _path: fake_config)
    monkeypatch.setattr(
        runner,
        "workflow_route_catalog",
        lambda _config: {"config_digest": "catalog-v1", "routes": routes},
    )
    monkeypatch.setattr(runner, "_effective_config_digest", lambda _config: "config-v1")
    monkeypatch.setattr(
        runner,
        "_route_snapshot",
        lambda _config, route: {
            **route,
            "route_digest": f"digest:{route['route_id']}",
            "role_bindings": [],
        },
    )
    dirty_roots: set[Path] = set()

    def _git(root: Path) -> dict:
        resolved = Path(root).resolve()
        return {
            "root": str(resolved),
            "head": "a" * 40,
            "dirty": resolved in dirty_roots,
            "status": [" M seed.js"] if resolved in dirty_roots else [],
            "errors": [],
        }

    monkeypatch.setattr(runner, "git_snapshot", _git)

    passed = runner.build_suite_preflight_manifest(
        project_root=project_root,
        state_dir=state_dir,
        config_path=project_root / "zf.yaml",
        implementation_root=implementation_root,
        require_backend="",
    )

    assert passed["status"] == "passed"
    assert set(passed["checks"]["route_matrix"]) == runner.FAMILIES

    EventLog(state_dir / "events.jsonl").append(ZfEvent(type="task.created"))
    dirty_roots.add(project_root.resolve())
    failed = runner.build_suite_preflight_manifest(
        project_root=project_root,
        state_dir=state_dir,
        config_path=project_root / "zf.yaml",
        implementation_root=implementation_root,
        require_backend="",
    )

    assert failed["status"] == "failed"
    assert "project seed is dirty" in failed["errors"]
    assert any("already contains" in error for error in failed["errors"])


def test_case_manifest_freezes_task_route_roles_and_disjoint_refactor_roots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "project"
    state_dir = _state(project_root)
    source_root = tmp_path / "legacy"
    target_root = tmp_path / "modern"
    source_root.mkdir()
    target_root.mkdir()
    route = {
        "route_id": "delivery:refactor:standard",
        "family": "delivery",
        "kind": "refactor",
        "entry_pattern_id": "refactor-entry",
    }
    fake_config = object()
    monkeypatch.setattr(runner, "load_config", lambda _path: fake_config)
    monkeypatch.setattr(
        runner,
        "workflow_route_catalog",
        lambda _config: {"config_digest": "catalog-v1", "routes": [route]},
    )
    monkeypatch.setattr(
        runner,
        "resolve_workflow_route",
        lambda _config, route_id, **_kwargs: route if route_id == route["route_id"] else None,
    )
    monkeypatch.setattr(runner, "_effective_config_digest", lambda _config: "config-v1")
    route_snapshot = {
        **route,
        "route_digest": "route-v1",
        "role_bindings": [{"role": "refactor-reader", "backend": "codex"}],
    }
    monkeypatch.setattr(runner, "_route_snapshot", lambda _config, _route: route_snapshot)
    monkeypatch.setattr(
        runner,
        "git_snapshot",
        lambda root: {
            "root": str(Path(root).resolve()),
            "head": "b" * 40,
            "dirty": False,
            "status": [],
            "errors": [],
        },
    )
    suite = {
        "status": "passed",
        "project_root": str(project_root),
        "state_dir": str(state_dir),
        "config_path": str(project_root / "zf.yaml"),
        "implementation_commit": "a" * 40,
        "seed_commit": "b" * 40,
    }

    manifest = runner.build_case_manifest(
        suite_manifest=suite,
        family="refactor",
        task_id="TASK-1",
        route_id=route["route_id"],
        source_root=source_root,
        target_root=target_root,
    )

    assert manifest["route_digest"] == "route-v1"
    assert manifest["task_contract_digest"].startswith("sha256:")
    assert manifest["task_semantic_identity_digest"].startswith("sha256:")
    assert manifest["root_binding"]["source_root"] == str(source_root.resolve())
    assert runner.validate_case_identity(manifest) == []

    task = TaskStore(state_dir / "kanban.json").get("TASK-1")
    assert task is not None
    task.contract.evidence_contract = {
        "execution_owner": "workflow",
        "workflow_request_id": "RUN-1",
        "workflow_request_revision": 2,
    }
    task.contract.task_doc_ref = str(state_dir / "task_docs/TASK-1/task.md")
    TaskStore(state_dir / "kanban.json").update(
        "TASK-1",
        contract=task.contract,
    )
    approved_start = ZfEvent(
        type="web.action.requested",
        task_id="TASK-1",
        payload={
            "requested_action": "workflow-start",
            "request": {
                "task_id": "TASK-1",
                "route_id": route["route_id"],
                "task_contract_digest": manifest["task_contract_digest"],
                "config_digest": manifest["catalog_config_digest"],
            },
        },
    )
    mismatched_start = ZfEvent(
        type="web.action.requested",
        task_id="TASK-1",
        payload={
            "requested_action": "workflow-start",
            "request": {
                "task_id": "TASK-1",
                "route_id": "delivery:issue:standard",
                "task_contract_digest": manifest["task_contract_digest"],
                "config_digest": manifest["catalog_config_digest"],
            },
        },
    )
    mismatch_errors = runner.validate_case_identity(
        manifest,
        events=[mismatched_start],
    )
    # Execution bindings and document refs are projections, not authority.
    assert mismatch_errors == ["workflow start route id does not match the frozen case"]
    assert runner.validate_case_identity(
        manifest,
        events=[approved_start],
    ) == []

    TaskStore(state_dir / "kanban.json").update("TASK-1", title="changed")
    assert "Task contract digest changed after case freeze" in (
        runner.validate_case_identity(manifest)
    )
    assert "Task semantic identity changed after case freeze" in (
        runner.validate_case_identity(manifest, events=[approved_start])
    )

    nested = source_root / "nested"
    nested.mkdir()
    with pytest.raises(runner.RunnerPreflightError, match="fully disjoint"):
        runner.build_case_manifest(
            suite_manifest=suite,
            family="refactor",
            task_id="TASK-1",
            route_id=route["route_id"],
            source_root=source_root,
            target_root=nested,
        )


def test_delivery_terminal_waits_for_task_projection_and_uses_first_terminal() -> None:
    manifest = _manifest(family="issue")
    invoke = _invoke(family="issue")
    success = ZfEvent(
        type="run.goal.completed",
        task_id="TASK-1",
        correlation_id="RUN-1",
        payload={"workflow_run_id": "RUN-1", "task_id": "TASK-1"},
    )
    late_failure = ZfEvent(
        type="run.goal.blocked",
        task_id="TASK-1",
        correlation_id="RUN-1",
        payload={"workflow_run_id": "RUN-1", "reason": "late noise"},
    )

    pending = runner.observe_case_terminal(
        [invoke, success],
        manifest,
        task_status="in_progress",
    )
    passed = runner.observe_case_terminal(
        [invoke, success, late_failure],
        manifest,
        task_status="done",
    )

    assert pending.status == "pending"
    assert "Task terminal" in pending.reason
    assert passed.status == "passed"
    assert passed.event_id == success.id


def test_general_terminal_resolves_entry_and_binding_from_start_pipeline() -> None:
    manifest = {
        **_manifest(family="general", require_task_terminal=False),
        "route_id": "general:scope",
        "entry_pattern_id": "scope",
        "task_contract_digest": "sha256:task-v1",
        "catalog_config_digest": "sha256:catalog-v1",
    }
    approved_start = ZfEvent(
        type="web.action.requested",
        task_id="TASK-1",
        payload={
            "requested_action": "workflow-start",
            "request": {
                "task_id": "TASK-1",
                "route_id": "general:scope",
                "task_contract_digest": "sha256:task-v1",
                "config_digest": "sha256:catalog-v1",
            },
        },
    )
    submitted = ZfEvent(
        type="workflow.submit.requested",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "run_id": "RUN-1",
            "pattern_id": "scope",
        },
    )
    invoke = ZfEvent(
        type="workflow.invoke.requested",
        task_id="TASK-1",
        payload={"task_id": "TASK-1", "workflow_run_id": "RUN-1"},
    )

    pending = runner.observe_case_terminal(
        [approved_start, submitted, invoke],
        manifest,
        task_status="in_progress",
    )
    passed = runner.observe_case_terminal(
        [
            approved_start,
            submitted,
            invoke,
            ZfEvent(
                type="run.goal.completed",
                task_id="TASK-1",
                payload={"workflow_run_id": "RUN-1"},
            ),
        ],
        manifest,
        task_status="in_progress",
    )

    assert pending.status == "pending"
    assert pending.reason == "waiting for run.goal.completed"
    assert passed.status == "passed"


def test_duplicate_invoke_and_terminal_failure_fail_immediately() -> None:
    manifest = _manifest(family="general", require_task_terminal=False)
    first = _invoke(family="general")
    duplicate = _invoke(family="general", run_id="RUN-2")
    duplicated = runner.observe_case_terminal(
        [first, duplicate],
        manifest,
        task_status="in_progress",
    )
    blocked = ZfEvent(
        type="run.goal.blocked",
        task_id="TASK-1",
        payload={"workflow_run_id": "RUN-1", "reason": "gate rejected"},
    )
    failed = runner.observe_case_terminal(
        [first, blocked],
        manifest,
        task_status="blocked",
    )

    assert duplicated.status == "failed"
    assert "more than one" in duplicated.reason
    assert failed.status == "failed"
    assert failed.event_type == "run.goal.blocked"
    assert failed.reason == "gate rejected"


def test_terminal_classifier_ignores_old_run_cancellation_for_same_task() -> None:
    manifest = _manifest(family="general", require_task_terminal=False)
    invoke = _invoke(family="general", run_id="RUN-CURRENT")
    stale_cancel = ZfEvent(
        type="run.cancelled",
        task_id="TASK-1",
        correlation_id="RUN-OLD",
        payload={"workflow_run_id": "RUN-OLD", "reason": "superseded"},
    )
    success = ZfEvent(
        type="run.goal.completed",
        task_id="TASK-1",
        correlation_id="RUN-CURRENT",
        payload={"workflow_run_id": "RUN-CURRENT"},
    )

    result = runner.observe_case_terminal(
        [invoke, stale_cancel, success],
        manifest,
        task_status="in_progress",
    )

    assert result.status == "passed"
    assert result.event_id == success.id


def test_research_requires_verified_aggregate_artifact_lineage() -> None:
    manifest = _manifest(family="research")
    invoke = _invoke(family="research")
    digest = "c" * 64
    aggregate = ZfEvent(
        type="fanout.aggregate.completed",
        task_id="TASK-1",
        payload={
            "workflow_run_id": "RUN-1",
            "status": "completed",
            "artifact_refs": [{
                "kind": "research_report",
                "ref": "research/TASK-1/report.md",
                "sha256": digest,
                "task_id": "TASK-1",
            }],
        },
    )
    result = ZfEvent(
        type="workflow.result.available",
        task_id="TASK-1",
        causation_id=aggregate.id,
        payload={
            "task_id": "TASK-1",
            "workflow_run_id": "RUN-1",
            "result_kind": "research_report",
            "status": "available",
            "terminal_event_id": aggregate.id,
            "artifact_ref": "research/TASK-1/report.md",
            "artifact_digest": digest,
        },
    )

    passed = runner.observe_case_terminal(
        [invoke, aggregate, result],
        manifest,
        task_status="done",
    )
    bad_result = ZfEvent.from_dict({
        **json.loads(result.to_json()),
        "id": "evt-bad-lineage",
        "causation_id": "evt-missing",
    })
    pending = runner.observe_case_terminal(
        [invoke, aggregate, bad_result],
        manifest,
        task_status="done",
    )

    assert passed.status == "passed"
    assert passed.event_type == "workflow.result.available"
    assert pending.status == "pending"


def test_wait_captures_failure_bundle_and_screenshot_without_blind_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = _state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    log.append(_invoke(family="issue"))
    terminal = ZfEvent(
        type="run.goal.blocked",
        task_id="TASK-1",
        payload={
            "workflow_run_id": "RUN-1",
            "reason": "terminal gate failed",
            "artifact_refs": [{
                "kind": "diagnostic",
                "ref": "diagnostics/RUN-1/failure.json",
            }],
        },
    )
    log.append(terminal)
    evidence = tmp_path / "evidence"
    screenshot = evidence / "failure.png"
    monkeypatch.setattr(
        runner,
        "validate_case_identity",
        lambda _manifest, *, events=None: [],
    )
    started = time.monotonic()

    result = runner.wait_for_case_terminal(
        {
            **_manifest(family="issue"),
            "state_dir": str(state_dir),
            "project_root": str(tmp_path),
        },
        timeout_seconds=30,
        poll_seconds=0.01,
        evidence_dir=evidence,
        screenshot_argv=[
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(screenshot)!r}).write_bytes(b'png')",
        ],
    )

    assert result.status == "failed"
    assert time.monotonic() - started < 5
    assert screenshot.read_bytes() == b"png"
    assert (evidence / "terminal.json").exists()
    assert (evidence / "related-events.json").exists()
    assert (evidence / "task.json").exists()
    assert (evidence / "run-admission.json").exists()
    assert (evidence / "workflow-operations.json").exists()
    refs = json.loads((evidence / "artifact-refs.json").read_text())
    assert refs["refs"][0]["ref"] == "diagnostics/RUN-1/failure.json"
    command = json.loads((evidence / "screenshot-command.json").read_text())
    assert command["status"] == "passed"


def test_wait_captures_identity_drift_before_polling_to_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = _state(tmp_path)
    evidence = tmp_path / "drift-evidence"
    monkeypatch.setattr(
        runner,
        "validate_case_identity",
        lambda _manifest, *, events=None: [
            "effective config digest changed after case freeze"
        ],
    )

    result = runner.wait_for_case_terminal(
        {
            **_manifest(family="general", require_task_terminal=False),
            "state_dir": str(state_dir),
            "project_root": str(tmp_path),
        },
        timeout_seconds=30,
        evidence_dir=evidence,
    )

    assert result.status == "failed"
    assert result.event_type == "runner.identity_drift"
    assert (evidence / "terminal.json").exists()


def test_terminal_capture_script_uses_local_docker_browser_without_token_argv() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (
        root / "tests/e2e/scripts/capture_five_workflow_terminal.sh"
    ).read_text(encoding="utf-8")
    spec = (
        root / "web/tests/five-workflow-terminal-capture.spec.ts"
    ).read_text(encoding="utf-8")

    assert "docker image inspect" in script
    assert "playwright install" not in script
    assert "ZF_E2E_CHROMIUM_EXECUTABLE_PATH" in script
    assert "-e ZF_WEB_ACTION_TOKEN_FOR_TEST" in script
    assert '-e ZF_WEB_ACTION_TOKEN_FOR_TEST="$' not in script
    assert "five-workflow-terminal-capture.spec.ts" in script
    assert "page.screenshot" in spec
    assert "related_items" in spec
