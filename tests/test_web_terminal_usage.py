from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zf.core.cost.tracker import CostTracker
from zf.web.terminal_backend import TerminalSessionRecord
from zf.web.terminal_usage import TerminalUsageService


def _record(
    *,
    provider: str,
    provider_session_id: str,
    provider_session_path: str,
    title: str = "Review API",
    project_root: str = "/workspace/project-a",
    usage_binding_status: str = "",
    usage_binding_started_at_ns: int = 0,
) -> TerminalSessionRecord:
    return TerminalSessionRecord(
        session_id="term-usage-a",
        slot="review-api",
        title=title,
        provider=provider,
        provider_kind="claude" if provider == "claude-code" else provider,
        project_id="project-a",
        project_root=project_root,
        state="active",
        generation=2,
        created_at="2026-08-20T15:00:00Z",
        updated_at="2026-08-20T15:00:00Z",
        herdr_session="zf-project-a",
        workspace_id="w1",
        tab_id="w1:t1",
        pane_id="w1:p1",
        terminal_id="term-native-a",
        agent_name="zftermusagea",
        provider_session_id=provider_session_id,
        provider_session_path=provider_session_path,
        usage_binding_status=usage_binding_status,
        usage_binding_started_at_ns=usage_binding_started_at_ns,
    )


def _append_jsonl(path: Path, *records: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def test_provider_roots_follow_inherited_cli_config_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    service = TerminalUsageService(state_dir=tmp_path / "state")

    assert service.claude_projects_root == (tmp_path / "claude-home" / "projects")
    assert service.codex_sessions_root == (tmp_path / "codex-home" / "sessions")
    assert service.codex_shell_snapshots_root == (
        tmp_path / "codex-home" / "shell_snapshots"
    )


def test_claude_launch_binding_and_snapshot_dedupes_message_identity(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project.a"
    project_root.mkdir()
    service = TerminalUsageService(
        state_dir=tmp_path / "state",
        claude_projects_root=tmp_path / "claude-projects",
        codex_sessions_root=tmp_path / "codex-sessions",
    )

    launch = service.prepare_launch("claude-code", project_root)
    binding = service.complete_launch(launch, wait_seconds=0)

    assert launch.provider_args == ("--session-id", launch.provider_session_id)
    assert binding.status == "bound"
    assert binding.provider_session_id == launch.provider_session_id
    transcript = Path(binding.provider_session_path)
    usage_one = {
        "input_tokens": 100,
        "cache_read_input_tokens": 50,
        "cache_creation_input_tokens": 25,
        "output_tokens": 10,
    }
    _append_jsonl(
        transcript,
        {
            "type": "assistant",
            "timestamp": "2026-08-20T15:01:00Z",
            "message": {"id": "msg-1", "model": "claude-sonnet-4-6", "usage": usage_one},
        },
        {
            "type": "assistant",
            "timestamp": "2026-08-20T15:01:01Z",
            "message": {"id": "msg-1", "model": "claude-sonnet-4-6", "usage": usage_one},
        },
        {
            "type": "assistant",
            "timestamp": "2026-08-20T15:02:00Z",
            "message": {
                "id": "msg-2",
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 20,
                    "cache_read_input_tokens": 5,
                    "output_tokens": 3,
                },
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-08-20T15:03:00Z",
            "message": {
                "id": "synthetic-1",
                "model": "<synthetic>",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
    )

    projection = service.snapshot(
        _record(
            provider="claude-code",
            provider_session_id=binding.provider_session_id,
            provider_session_path=binding.provider_session_path,
        )
    )

    assert projection["status"] == "observed"
    assert projection["model"] == "claude-sonnet-4-6"
    assert projection["fresh_input_tokens"] == 120
    assert projection["cached_input_tokens"] == 55
    assert projection["cache_creation_input_tokens"] == 25
    assert projection["input_tokens"] == 200
    assert projection["output_tokens"] == 13
    assert projection["total_tokens"] == 213
    assert projection["cost_usd"] > 0
    assert projection["cost_kind"] == "estimated"


def test_codex_launch_diff_binding_and_cumulative_settlement_are_idempotent(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    state_dir = tmp_path / "state"
    sessions_root = tmp_path / "codex-sessions"
    service = TerminalUsageService(
        state_dir=state_dir,
        claude_projects_root=tmp_path / "claude-projects",
        codex_sessions_root=sessions_root,
        codex_shell_snapshots_root=tmp_path / "codex-shell-snapshots",
    )
    launch = service.prepare_launch("codex", project_root)
    native_id = "01a01f2e-dfd8-70f2-8283-e1d08dd4bd01"
    shell_snapshot = tmp_path / "codex-shell-snapshots" / f"{native_id}.123.sh"
    shell_snapshot.parent.mkdir(parents=True)
    shell_snapshot.write_text("# shell metadata is never read\n", encoding="utf-8")
    binding = service.complete_launch(launch, wait_seconds=0)
    transcript = sessions_root / "2026" / "08" / "20" / f"rollout-test-{native_id}.jsonl"
    _append_jsonl(
        transcript,
        {
            "type": "session_meta",
            "timestamp": "2026-08-20T15:01:00Z",
            "payload": {"id": native_id, "cwd": str(project_root), "model_provider": "openai"},
        },
        {
            "type": "turn_context",
            "timestamp": "2026-08-20T15:01:01Z",
            "payload": {"model": "gpt-5.6-sol"},
        },
        {
            "type": "event_msg",
            "timestamp": "2026-08-20T15:01:02Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 1_000,
                        "cached_input_tokens": 400,
                        "output_tokens": 100,
                        "reasoning_output_tokens": 40,
                    },
                    "last_token_usage": {"input_tokens": 1_000},
                    "model_context_window": 258_400,
                },
                "rate_limits": {"plan_type": "pro"},
            },
        },
    )
    record = _record(
        provider="codex",
        provider_session_id=binding.provider_session_id,
        provider_session_path=binding.provider_session_path,
        project_root=str(project_root),
    )

    assert binding.status == "bound"
    assert binding.provider_session_id == native_id
    assert binding.provider_session_path == ""
    first = service.snapshot(record)
    assert first["fresh_input_tokens"] == 600
    assert first["cached_input_tokens"] == 400
    assert first["input_tokens"] == 1_000
    assert first["output_tokens"] == 100
    assert first["total_tokens"] == 1_100
    assert first["accounting_mode"] == "subscription"

    service.settle(record)
    service.settle(record)
    tracker = CostTracker(state_dir / "terminal-cost.jsonl")
    instance_id = "terminal:project-a:term-usage-a:g2"
    totals = tracker.usage_totals(instance_id=instance_id)
    assert totals["input_tokens"] == 600
    assert totals["cache_read_tokens"] == 400
    assert totals["output_tokens"] == 100
    assert totals["entries"] == 1
    assert not (state_dir / "cost.jsonl").exists()

    _append_jsonl(
        transcript,
        {
            "type": "event_msg",
            "timestamp": "2026-08-20T15:02:02Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 1_500,
                        "cached_input_tokens": 600,
                        "output_tokens": 200,
                        "reasoning_output_tokens": 70,
                    },
                    "last_token_usage": {"input_tokens": 500},
                    "model_context_window": 258_400,
                },
                "rate_limits": {"plan_type": "pro"},
            },
        },
    )
    service.settle(record)
    totals = tracker.usage_totals(instance_id=instance_id)
    assert totals["input_tokens"] == 900
    assert totals["cache_read_tokens"] == 600
    assert totals["output_tokens"] == 200
    assert totals["entries"] == 2


def test_codex_launch_binding_fails_closed_when_new_rollout_is_ambiguous(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    sessions_root = tmp_path / "codex-sessions"
    service = TerminalUsageService(
        state_dir=tmp_path / "state",
        claude_projects_root=tmp_path / "claude-projects",
        codex_sessions_root=sessions_root,
        codex_shell_snapshots_root=tmp_path / "codex-shell-snapshots",
    )
    launch = service.prepare_launch("codex", project_root)
    session_timestamp = datetime.fromtimestamp(
        launch.binding_started_at_ns / 1_000_000_000,
        tz=timezone.utc,
    ).isoformat()
    for suffix in ("01a01f2e-dfd8-70f2-8283-e1d08dd4bd01", "01a01f2e-a818-73f0-8305-dd68dc32c424"):
        path = sessions_root / "2026" / "08" / "20" / f"rollout-test-{suffix}.jsonl"
        _append_jsonl(
            path,
            {
                "type": "session_meta",
                "timestamp": session_timestamp,
                "payload": {
                    "id": suffix,
                    "timestamp": session_timestamp,
                    "cwd": str(project_root),
                },
            },
        )

    binding = service.complete_launch(launch, wait_seconds=0)

    assert binding.status == "unavailable"
    assert binding.provider_session_id == ""
    assert binding.provider_session_path == ""
    assert binding.reason == "provider session binding was ambiguous"


def test_codex_pending_binding_resolves_after_browser_startup_gate(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    sessions_root = tmp_path / "codex-sessions"
    service = TerminalUsageService(
        state_dir=tmp_path / "state",
        claude_projects_root=tmp_path / "claude-projects",
        codex_sessions_root=sessions_root,
        codex_shell_snapshots_root=tmp_path / "codex-shell-snapshots",
    )
    launch = service.prepare_launch("codex", project_root)

    initial = service.complete_launch(launch, wait_seconds=0)

    assert initial.status == "pending"
    session_timestamp = datetime.fromtimestamp(
        launch.binding_started_at_ns / 1_000_000_000,
        tz=timezone.utc,
    ).isoformat()
    unrelated_id = "01a01f2e-a818-73f0-8305-dd68dc32c424"
    _append_jsonl(
        sessions_root / "2026" / "08" / "20" / f"rollout-other-{unrelated_id}.jsonl",
        {
            "type": "session_meta",
            "timestamp": session_timestamp,
            "payload": {"id": unrelated_id, "cwd": str(tmp_path / "other")},
        },
    )
    native_id = "01a01f2e-dfd8-70f2-8283-e1d08dd4bd01"
    transcript = sessions_root / "2026" / "08" / "20" / f"rollout-demo-{native_id}.jsonl"
    _append_jsonl(
        transcript,
        {
            "type": "session_meta",
            "timestamp": session_timestamp,
            "payload": {
                "id": native_id,
                "timestamp": session_timestamp,
                "cwd": str(project_root),
            },
        },
    )
    record = _record(
        provider="codex",
        provider_session_id="",
        provider_session_path="",
        project_root=str(project_root),
        usage_binding_status="pending",
        usage_binding_started_at_ns=launch.binding_started_at_ns,
    )

    binding = service.complete_pending_binding(record)

    assert binding.status == "bound"
    assert binding.provider_session_id == native_id
    assert binding.provider_session_path == str(transcript)


def test_unqualified_provider_usage_is_explicitly_unsupported(tmp_path: Path) -> None:
    service = TerminalUsageService(state_dir=tmp_path / "state")
    record = _record(
        provider="opencode",
        provider_session_id="",
        provider_session_path="",
    )

    projection = service.snapshot(record)

    assert projection["status"] == "unsupported"
    assert projection["cost_usd"] is None
    assert projection["total_tokens"] is None


def test_codex_hook_requires_matching_project_and_native_session_meta(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sessions"
    service = TerminalUsageService(state_dir=tmp_path, codex_sessions_root=root)
    native_id = "90857bb3-480c-43c0-897c-6d212be10409"
    path = root / "2026" / "11" / "23" / f"rollout-test-{native_id}.jsonl"
    assert service.bind_codex_reference(native_id, tmp_path).status == "pending"
    _append_jsonl(
        path,
        {"type": "session_meta", "payload": {"id": native_id, "cwd": str(tmp_path)}},
    )
    assert (
        service.bind_codex_reference(native_id, tmp_path / "other").status == "pending"
    )
    assert service.bind_codex_reference(
        native_id, tmp_path
    ).provider_session_path == str(path)
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "different", "cwd": str(tmp_path)},
            }
        )
    )
    assert service.bind_codex_reference(native_id, tmp_path).status == "pending"


def test_incremental_rollout_matches_fresh_read_across_file_changes(tmp_path, monkeypatch):
    from zf.web.terminal_usage_cursor import UsageCursor

    def service():
        return TerminalUsageService(state_dir=tmp_path / "state", codex_sessions_root=tmp_path)

    reader = service()
    path = tmp_path / "rollout-fixture.jsonl"
    record = _record(provider="codex", provider_session_id="sample",
                     provider_session_path=str(path), project_root=str(tmp_path))
    meta = {"type": "session_meta", "payload": {"id": "sample", "cwd": str(tmp_path)}}

    def count(tokens, model):
        return {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "model": model, "total_token_usage": {"input_tokens": tokens}}}}

    reads = []
    original = UsageCursor.records

    def measured(cursor, handle, size):
        entry = [cursor.offset, 0, 0]
        reads.append(entry)
        for row in original(cursor, handle, size):
            entry[1] += 1
            yield row
        entry[2] = handle.tell() - entry[0]

    monkeypatch.setattr(UsageCursor, "records", measured)

    def compare():
        expected = service().snapshot(record)
        reads.clear()
        assert reader.snapshot(record) == expected
        return expected

    _append_jsonl(path, meta, *[{"padding": "x" * 256}] * 64, count(50, "first"))
    assert compare()["input_tokens"] == 50
    assert reads == [[0, 66, path.stat().st_size]]
    assert compare()["input_tokens"] == 50
    assert reads == []
    offset = path.stat().st_size
    receipt = json.dumps(count(7, "second")).encode() + b"\n"
    with path.open("ab") as handle:
        handle.write(receipt[:12])
    assert compare()["input_tokens"] == 50
    assert reads == [[offset, 0, 12]]
    with path.open("ab") as handle:
        handle.write(receipt[12:])
    assert compare()["models"] == ["first", "second"]
    assert reads == [[offset, 1, len(receipt)]]
    assert reader.snapshot(record)["input_tokens"] == 57

    path.write_text("")
    _append_jsonl(path, meta, count(3, "third"))
    assert compare()["input_tokens"] == 3
    assert reads[0][0] == 0
    replacement = path.with_suffix(".next")
    _append_jsonl(replacement, meta, count(25, "fourth"))
    replacement.replace(path)
    assert compare()["models"] == ["fourth"]
    assert reads[0][0] == 0
    assert reader.settle(record)["status"] == "observed"
    _append_jsonl(path, {"type": "session_meta", "payload": {"id": "another"}})
    assert reader.snapshot(record)["status"] == "unavailable"


def test_concurrent_claude_sessions_keep_incremental_receipts_separate(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace

    def new_reader():
        return TerminalUsageService(state_dir=tmp_path / "state", claude_projects_root=tmp_path)

    def receipt(session, tokens):
        return {"type": "assistant", "sessionId": session, "message": {
            "id": "shared-message-id", "model": "claude-sonnet", "usage": {"input_tokens": tokens}}}

    records = []
    for session, tokens in [("first", 11), ("second", 19)]:
        path = tmp_path / f"{session}.jsonl"
        _append_jsonl(path, receipt(session, tokens))
        record = _record(provider="claude-code", provider_session_id=session, provider_session_path=str(path))
        records.append(replace(record, session_id="term-" + session))
    reader = new_reader()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert [s["input_tokens"] for s in pool.map(reader.snapshot, records)] == [11, 19]
        _append_jsonl(tmp_path / "first.jsonl", receipt("first", 15))
        snapshots = list(pool.map(reader.snapshot, records))
        assert snapshots == [new_reader().snapshot(record) for record in records]
        assert [s["input_tokens"] for s in snapshots] == [15, 19]
