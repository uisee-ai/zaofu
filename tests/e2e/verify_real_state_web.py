"""Validate real-provider E2E state projections and web live updates.

This is a post-run verifier for expensive provider scenarios. It reads the
runtime state produced by real ``dev-codex-backends`` / ``dev-codex-star``
runs, validates event/state invariants, then starts ``zf web`` against that
same state and verifies kanban changes are visible through snapshot + SSE.

Default inputs match the real smoke runners:

  PYTHONPATH=src python -m tests.e2e.verify_real_state_web --scenario both
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
DEFAULT_CODEX_STATE = Path("/tmp/zaofu-codex-smoke/.zf")
DEFAULT_STAR_STATE = Path("/tmp/zaofu-star-codex-smoke/.zf")
DEFAULT_STAR_ROLES = {
    "review-security",
    "review-architecture",
    "review-testing",
}

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class ValidationError(RuntimeError):
    """Raised when an E2E state or web projection check fails."""


@dataclass(frozen=True)
class ScenarioReport:
    scenario: str
    state_dir: Path
    event_count: int
    dispatch_by_instance: dict[str, int]
    done_tasks: int = 0
    fanout_id: str = ""
    web: dict[str, Any] | None = None


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "event" in value and "sig" in value:
            value = value.get("event") or {}
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("payload") or {}
    return value if isinstance(value, dict) else {}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _event_types(events: list[dict[str, Any]]) -> Counter:
    return Counter(str(event.get("type") or "") for event in events)


def _archive_tasks(state_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    archive_dir = state_dir / "kanban"
    for path in sorted(archive_dir.glob("*.json")) if archive_dir.exists() else []:
        data = _read_json(path, [])
        if isinstance(data, list):
            rows.extend(row for row in data if isinstance(row, dict))
    return rows


def _done_task_ids(events: list[dict[str, Any]], state_dir: Path) -> set[str]:
    done: set[str] = set()
    for event in events:
        if event.get("type") != "task.status_changed":
            continue
        if _payload(event).get("to") == "done":
            task_id = event.get("task_id")
            if task_id:
                done.add(str(task_id))
    for task in _archive_tasks(state_dir):
        if task.get("status") == "done" and task.get("id"):
            done.add(str(task["id"]))
    return done


def _dispatch_by_instance(
    events: list[dict[str, Any]],
    event_type: str,
) -> Counter:
    dispatch: Counter = Counter()
    for event in events:
        if event.get("type") != event_type:
            continue
        payload = _payload(event)
        instance = (
            payload.get("assignee")
            or payload.get("role_instance")
            or event.get("actor")
            or "?"
        )
        dispatch[str(instance)] += 1
    return dispatch


def validate_codex_state(
    state_dir: Path,
    *,
    expected_tasks: int,
) -> ScenarioReport:
    state_dir = state_dir.resolve()
    events = _read_jsonl(state_dir / "events.jsonl")
    counts = _event_types(events)
    dispatch = _dispatch_by_instance(events, "task.dispatched")
    done_ids = _done_task_ids(events, state_dir)

    _require(events, f"{state_dir}/events.jsonl has no events")
    for event_type in (
        "session.started",
        "loop.started",
        "user.message",
        "task.created",
        "task.assigned",
        "task.dispatched",
        "dev.build.done",
        "review.approved",
        "test.passed",
        "judge.passed",
        "task.status_changed",
    ):
        _require(counts[event_type] > 0, f"codex missing event {event_type}")
    _require(len(done_ids) >= expected_tasks, (
        f"codex expected >= {expected_tasks} done task(s), got {len(done_ids)}"
    ))
    _require(any(k == "dev" or k.startswith("dev-") for k in dispatch), (
        f"codex dispatch missing dev instance: {dict(dispatch)}"
    ))
    _require(dispatch.get("review", 0) >= expected_tasks, (
        f"codex dispatch missing review stage: {dict(dispatch)}"
    ))
    _require(any(k == "test" or k.startswith("test-") for k in dispatch), (
        f"codex dispatch missing test instance: {dict(dispatch)}"
    ))
    _require(dispatch.get("judge", 0) >= expected_tasks, (
        f"codex dispatch missing judge stage: {dict(dispatch)}"
    ))

    for task_id in done_ids:
        task_events = [event for event in events if event.get("task_id") == task_id]
        task_event_types = {str(event.get("type") or "") for event in task_events}
        for event_type in (
            "arch.proposal.done",
            "design.critique.done",
            "dev.build.done",
            "review.approved",
            "test.passed",
            "judge.passed",
        ):
            _require(event_type in task_event_types, (
                f"codex task {task_id} missing {event_type}"
            ))

    _require((state_dir / "kanban.json").exists(), "codex kanban.json missing")
    _require((state_dir / "role_sessions.yaml").exists(), "codex role_sessions.yaml missing")
    _require(_archive_tasks(state_dir), "codex terminal kanban archive is empty")

    return ScenarioReport(
        scenario="codex",
        state_dir=state_dir,
        event_count=len(events),
        dispatch_by_instance=dict(sorted(dispatch.items())),
        done_tasks=len(done_ids),
    )


def _star_expected_roles(state_dir: Path) -> set[str]:
    config_path = state_dir.parent / "zf.yaml"
    if not config_path.exists():
        return set(DEFAULT_STAR_ROLES)
    try:
        import yaml

        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception:
        return set(DEFAULT_STAR_ROLES)
    stages = ((config or {}).get("workflow") or {}).get("stages") or []
    if not isinstance(stages, list):
        return set(DEFAULT_STAR_ROLES)
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        if stage.get("topology") != "fanout_reader":
            continue
        roles = stage.get("roles") or []
        if isinstance(roles, list) and roles:
            return {str(role) for role in roles}
    return set(DEFAULT_STAR_ROLES)


def validate_star_state(
    state_dir: Path,
    *,
    expected_children: int,
) -> ScenarioReport:
    state_dir = state_dir.resolve()
    events = _read_jsonl(state_dir / "events.jsonl")
    counts = _event_types(events)
    dispatch = _dispatch_by_instance(events, "fanout.child.dispatched")
    expected_roles = _star_expected_roles(state_dir)

    _require(events, f"{state_dir}/events.jsonl has no events")
    for event_type in (
        "session.started",
        "loop.started",
        "candidate.ready",
        "fanout.started",
        "fanout.child.dispatched",
        "fanout.child.completed",
        "fanout.aggregate.started",
        "fanout.aggregate.completed",
        "review.approved",
    ):
        _require(counts[event_type] > 0, f"star missing event {event_type}")

    _require(sum(dispatch.values()) == expected_children, (
        f"star expected {expected_children} child dispatches, got {dict(dispatch)}"
    ))
    _require(set(dispatch) == expected_roles, (
        f"star child dispatch set mismatch: got {sorted(dispatch)}, "
        f"expected {sorted(expected_roles)}"
    ))
    _require(counts["fanout.child.completed"] == expected_children, (
        f"star expected {expected_children} completed children, "
        f"got {counts['fanout.child.completed']}"
    ))
    _require(counts["fanout.child.failed"] == 0, (
        f"star has failed child events: {counts['fanout.child.failed']}"
    ))

    aggregates = [
        event for event in events
        if event.get("type") == "fanout.aggregate.completed"
    ]
    aggregate = aggregates[-1]
    aggregate_payload = _payload(aggregate)
    fanout_id = str(aggregate_payload.get("fanout_id") or "")
    _require(fanout_id, "star aggregate event has no fanout_id")
    _require(aggregate_payload.get("status") == "completed", (
        f"star aggregate status is {aggregate_payload.get('status')!r}"
    ))
    _require(aggregate_payload.get("success_event") == "review.approved", (
        f"star aggregate success_event is {aggregate_payload.get('success_event')!r}"
    ))

    manifest = _read_json(state_dir / "fanouts" / fanout_id / "manifest.json", {})
    _require(isinstance(manifest, dict) and manifest, (
        f"star fanout manifest missing for {fanout_id}"
    ))
    _require(manifest.get("status") == "completed", (
        f"star manifest status is {manifest.get('status')!r}"
    ))
    children = manifest.get("children") or []
    _require(isinstance(children, list) and len(children) == expected_children, (
        f"star manifest expected {expected_children} children, got {len(children)}"
    ))
    manifest_roles = {
        str(child.get("role_instance") or child.get("child_id") or "")
        for child in children
        if isinstance(child, dict)
    }
    _require(manifest_roles == expected_roles, (
        f"star manifest roles mismatch: got {sorted(manifest_roles)}, "
        f"expected {sorted(expected_roles)}"
    ))
    for child in children:
        if not isinstance(child, dict):
            continue
        _require(child.get("status") == "completed", (
            f"star child {child.get('child_id')} status is {child.get('status')!r}"
        ))
        _require(child.get("recommendation") == "approve", (
            f"star child {child.get('child_id')} recommendation is "
            f"{child.get('recommendation')!r}"
        ))

    return ScenarioReport(
        scenario="star",
        state_dir=state_dir,
        event_count=len(events),
        dispatch_by_instance=dict(sorted(dispatch.items())),
        fanout_id=fanout_id,
    )


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_ROOT)
    return env


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_json(url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read().decode("utf-8")
    parsed = json.loads(data)
    _require(isinstance(parsed, dict), f"{url} did not return a JSON object")
    return parsed


def _wait_for_snapshot(base_url: str, proc: subprocess.Popen, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = ""
            if proc.stdout is not None:
                output = proc.stdout.read() or ""
            raise ValidationError(
                f"zf web exited early rc={proc.returncode}: {output[-1000:]}"
            )
        try:
            return _http_json(f"{base_url}/api/snapshot", timeout=2.0)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(0.25)
    raise ValidationError(f"timed out waiting for zf web: {last_error}")


def _start_web(state_dir: Path, port: int) -> subprocess.Popen:
    cwd = state_dir.parent if (state_dir.parent / "zf.yaml").exists() else REPO_ROOT
    cmd = [
        sys.executable,
        "-m",
        "zf.cli.main",
        "web",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    if not (cwd / "zf.yaml").exists():
        cmd.extend(["--state-dir", str(state_dir)])
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        env=_subprocess_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@contextmanager
def _preserved_files(paths: list[Path]) -> Iterator[None]:
    backups: dict[Path, bytes | None] = {}
    for path in paths:
        backups[path] = path.read_bytes() if path.exists() else None
    try:
        yield
    finally:
        for path, content in backups.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)


def _wait_sse_event(
    *,
    base_url: str,
    cursor: int,
    event_type: str,
    task_id: str,
    timeout_s: float,
) -> dict[str, Any]:
    url = f"{base_url}/api/stream?cursor={cursor}"
    deadline = time.monotonic() + timeout_s
    request = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    with urllib.request.urlopen(request, timeout=1.0) as response:
        data_lines: list[str] = []
        while time.monotonic() < deadline:
            try:
                raw = response.readline()
            except TimeoutError:
                continue
            except socket.timeout:
                continue
            if not raw:
                time.sleep(0.05)
                continue
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("data: "):
                data_lines.append(line[len("data: "):])
                continue
            if line:
                continue
            if not data_lines:
                continue
            payload = "\n".join(data_lines)
            data_lines = []
            event = json.loads(payload)
            if (
                event.get("type") == event_type
                and str(event.get("task_id") or "") == task_id
            ):
                return event
    raise ValidationError(f"SSE did not receive {event_type} for {task_id}")


def _start_sse_waiter(
    *,
    base_url: str,
    cursor: int,
    event_type: str,
    task_id: str,
    timeout_s: float,
) -> tuple[threading.Thread, dict[str, Any]]:
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["event"] = _wait_sse_event(
                base_url=base_url,
                cursor=cursor,
                event_type=event_type,
                task_id=task_id,
                timeout_s=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def _join_sse_waiter(
    thread: threading.Thread,
    result: dict[str, Any],
    *,
    timeout_s: float,
) -> dict[str, Any]:
    thread.join(timeout=timeout_s + 2.0)
    if thread.is_alive():
        raise ValidationError("SSE waiter did not finish")
    if "error" in result:
        raise ValidationError(str(result["error"]))
    event = result.get("event")
    _require(isinstance(event, dict), "SSE waiter returned no event")
    return event


def _emit_probe_event(
    state_dir: Path,
    *,
    event_type: str,
    task_id: str,
    payload: dict[str, Any],
) -> None:
    from zf.core.events.factory import event_log_from_project
    from zf.core.events.writer import EventWriter

    EventWriter(event_log_from_project(state_dir, warn=False)).emit(
        event_type,
        actor="e2e-web",
        task_id=task_id,
        payload=payload,
    )


def _task_from_snapshot(snapshot: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    for task in snapshot.get("tasks") or []:
        if isinstance(task, dict) and task.get("id") == task_id:
            return task
    return None


def verify_web_dynamic(state_dir: Path, *, scenario: str, timeout_s: float) -> dict[str, Any]:
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore

    state_dir = state_dir.resolve()
    probe_id = f"E2E-WEB-{scenario.upper()}-{int(time.time() * 1000)}"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    proc = _start_web(state_dir, port)
    try:
        with _preserved_files([state_dir / "kanban.json", state_dir / "events.jsonl"]):
            before = _wait_for_snapshot(base_url, proc, timeout_s)
            before_seq = int(before.get("seq") or 0)
            store = TaskStore(state_dir / "kanban.json")

            created_thread, created_result = _start_sse_waiter(
                base_url=base_url,
                cursor=before_seq,
                event_type="task.created",
                task_id=probe_id,
                timeout_s=timeout_s,
            )
            store.add(Task(
                id=probe_id,
                title=f"E2E web dynamic probe ({scenario})",
                status="backlog",
            ))
            _emit_probe_event(
                state_dir,
                event_type="task.created",
                task_id=probe_id,
                payload={"title": f"E2E web dynamic probe ({scenario})"},
            )
            _join_sse_waiter(created_thread, created_result, timeout_s=timeout_s)
            created_snapshot = _http_json(f"{base_url}/api/snapshot")
            created_task = _task_from_snapshot(created_snapshot, probe_id)
            _require(created_task is not None, "web snapshot did not show created probe task")
            _require(created_task.get("status") == "backlog", (
                f"created probe status is {created_task.get('status')!r}"
            ))

            created_seq = int(created_snapshot.get("seq") or 0)
            updated_thread, updated_result = _start_sse_waiter(
                base_url=base_url,
                cursor=created_seq,
                event_type="task.status_changed",
                task_id=probe_id,
                timeout_s=timeout_s,
            )
            store.update(probe_id, status="in_progress", assigned_to="e2e-web")
            _emit_probe_event(
                state_dir,
                event_type="task.status_changed",
                task_id=probe_id,
                payload={
                    "from": "backlog",
                    "to": "in_progress",
                    "source": "web_dynamic_probe",
                },
            )
            _join_sse_waiter(updated_thread, updated_result, timeout_s=timeout_s)
            updated_snapshot = _http_json(f"{base_url}/api/snapshot")
            updated_task = _task_from_snapshot(updated_snapshot, probe_id)
            _require(updated_task is not None, "web snapshot lost updated probe task")
            _require(updated_task.get("status") == "in_progress", (
                f"updated probe status is {updated_task.get('status')!r}"
            ))
            _require(updated_task.get("assigned_to") == "e2e-web", (
                f"updated probe assignee is {updated_task.get('assigned_to')!r}"
            ))
            updated_seq = int(updated_snapshot.get("seq") or 0)
            _require(updated_seq >= before_seq + 2, (
                f"web seq did not advance for live events: {before_seq} -> {updated_seq}"
            ))

            return {
                "base_url": base_url,
                "before_seq": before_seq,
                "created_seq": created_seq,
                "updated_seq": updated_seq,
                "probe_task": probe_id,
            }
    finally:
        _stop_process(proc)


def _print_report(report: ScenarioReport) -> None:
    print(f"[PASS] {report.scenario} state: events={report.event_count}")
    if report.done_tasks:
        print(f"       done_tasks={report.done_tasks}")
    if report.fanout_id:
        print(f"       fanout_id={report.fanout_id}")
    print(f"       dispatch={report.dispatch_by_instance}")
    if report.web:
        print(
            "[PASS] {scenario} web: {base_url} seq {before_seq}->{created_seq}->{updated_seq} "
            "probe={probe_task}".format(scenario=report.scenario, **report.web)
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=["codex", "star", "both"],
        default="both",
    )
    parser.add_argument("--codex-state-dir", type=Path, default=DEFAULT_CODEX_STATE)
    parser.add_argument("--star-state-dir", type=Path, default=DEFAULT_STAR_STATE)
    parser.add_argument("--expected-codex-tasks", type=int, default=1)
    parser.add_argument("--expected-star-children", type=int, default=3)
    parser.add_argument("--no-web", action="store_true")
    parser.add_argument("--web-timeout", type=float, default=20.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    reports: list[ScenarioReport] = []
    if args.scenario in {"codex", "both"}:
        report = validate_codex_state(
            args.codex_state_dir,
            expected_tasks=args.expected_codex_tasks,
        )
        if not args.no_web:
            web = verify_web_dynamic(
                report.state_dir,
                scenario="codex",
                timeout_s=args.web_timeout,
            )
            report = ScenarioReport(**{**report.__dict__, "web": web})
        reports.append(report)
    if args.scenario in {"star", "both"}:
        report = validate_star_state(
            args.star_state_dir,
            expected_children=args.expected_star_children,
        )
        if not args.no_web:
            web = verify_web_dynamic(
                report.state_dir,
                scenario="star",
                timeout_s=args.web_timeout,
            )
            report = ScenarioReport(**{**report.__dict__, "web": web})
        reports.append(report)
    for report in reports:
        _print_report(report)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
