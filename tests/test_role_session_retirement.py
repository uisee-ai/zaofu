"""A recycled Codex rollout must not become a resume target again."""

import os
from pathlib import Path
from uuid import uuid4

from zf.core.events.log import EventLog
from zf.core.state.role_sessions import RoleSessionRegistry
from tests.test_hook_recv_codex import _invoke


def registry(tmp_path: Path) -> RoleSessionRegistry:
    return RoleSessionRegistry(tmp_path / "role_sessions.yaml", str(tmp_path))


def test_late_hook_cannot_restore_cleared_session_from_stale_registry(tmp_path):
    owner = registry(tmp_path)
    old = str(uuid4())
    owner.bind_codex_session("verify", old)
    stale_hook = registry(tmp_path)
    owner.clear("verify")

    assert not stale_hook.bind_codex_session("verify", old)
    assert registry(tmp_path).get("verify") is None


def test_old_hook_cannot_replace_new_session_or_mutate_metadata(tmp_path):
    owner = registry(tmp_path)
    old, fresh = str(uuid4()), str(uuid4())
    owner.bind_codex_session("verify", old)
    owner.clear("verify")
    assert owner.bind_codex_session("verify", fresh, session_path=tmp_path / "fresh")
    before = owner.path.read_bytes()

    assert not owner.bind_codex_session("verify", old, session_path=tmp_path / "old")
    assert owner.path.read_bytes() == before
    assert str(registry(tmp_path).get("verify")) == fresh


def test_retirement_survives_multiple_recycles_and_registry_reload(tmp_path):
    old_ids = [str(uuid4()), str(uuid4())]
    for session in old_ids:
        owner = registry(tmp_path)
        assert owner.bind_codex_session("verify", session)
        owner.clear("verify")
    reloaded = registry(tmp_path)
    for session in old_ids:
        assert not reloaded.bind_codex_session("verify", session)
    assert reloaded.bind_codex_session("other", str(uuid4()))


def test_observer_skips_retired_rollout_even_when_it_has_latest_mtime(tmp_path):
    owner = registry(tmp_path)
    folder = tmp_path / "sessions/2026/10/26"
    folder.mkdir(parents=True)
    old, fresh = uuid4(), uuid4()
    paths = [folder / f"rollout-2026-10-26T00-00-00-{sid}.jsonl"
             for sid in (old, fresh)]
    for path, stamp in zip(paths, (20, 10)):
        path.write_text("")
        os.utime(path, (stamp, stamp))
    owner.bind_codex_session("verify", str(old), session_path=paths[0])
    owner.clear("verify")

    assert owner.observe_codex_session("verify", max_wait_seconds=0,
        sessions_root=tmp_path / "sessions") == (fresh, paths[1])


def test_observer_does_not_return_session_retired_during_scan(tmp_path, monkeypatch):
    owner = registry(tmp_path)
    old = uuid4()
    owner.bind_codex_session("verify", str(old))

    def scan(*args, **kwargs):
        registry(tmp_path).clear("verify")
        return tmp_path / "old", old

    monkeypatch.setattr(owner, "_glob_newest_since", scan)
    assert owner.observe_codex_session("verify", max_wait_seconds=0) is None
    assert registry(tmp_path).get("verify") is None


def test_late_codex_hook_remains_auditable_without_restoring_session(tmp_path, monkeypatch):
    state_dir = tmp_path / "custom-state"
    state_dir.mkdir()
    owner = RoleSessionRegistry(state_dir / "role_sessions.yaml", str(tmp_path))
    old = str(uuid4())
    transcript = state_dir / f"workdirs/verify/codex-home/sessions/2026/10/26/rollout-{old}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("")
    owner.bind_codex_session("verify", old, session_path=transcript)
    owner.clear("verify")

    assert _invoke(state_dir, "codex.hook.post_tool_use", "codex", {
        "session_id": old, "transcript_path": str(transcript),
        "tool_name": "Read", "tool_input": {},
    }, monkeypatch) == 0
    events = EventLog(state_dir / "events.jsonl").read_all()
    hook = next(event for event in events if event.type == "codex.hook.post_tool_use")
    assert hook.actor == "verify"
    assert RoleSessionRegistry(owner.path, str(tmp_path)).get("verify") is None
