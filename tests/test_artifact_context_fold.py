"""Append folding must not trade away the conservative context view."""
import io
import os
import time
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_context_events import ArtifactContextEvents


def _row(event_id, kind="task.dispatched", **payload):
    return (ZfEvent(id=event_id, type=kind, payload=payload).to_json() + "\n").encode()


def _fixture(tmp_path):
    path = tmp_path / "events.jsonl"
    archive = tmp_path / "events"
    archive.mkdir()
    (archive / "2026-11-01.jsonl").write_bytes(_row("archived"))
    path.write_bytes(_row("active"))
    return path, EventLog(path)


def test_append_decodes_only_new_complete_rows_and_retains_filtered_obligations(tmp_path, monkeypatch):
    path, log = _fixture(tmp_path)
    decoded = []
    decode = log.decode_line
    monkeypatch.setattr(log, "decode_line", lambda line: (decoded.append(line), decode(line))[1])
    view = ArtifactContextEvents(log, preserve_run_aliases=True, preserve_event_ids=("pinned",))
    assert [e.id for e in view.read_all()] == ["archived", "active"]
    decoded.clear()
    with path.open("ab") as stream:
        stream.write(_row("noise", "codex.hook.post_tool_use"))
        stream.write(_row("alias", "codex.hook.post_tool_use", run_id="run"))
        stream.write(_row("pinned", "codex.hook.post_tool_use"))
        stream.write(_row("required", "codex.hook.post_tool_use", required_reads=[{"source_id": "s"}]))
        stream.write(b'{"type":"task.dispatched","id":"partial"')
    assert [e.id for e in view.read_all()] == ["archived", "active", "alias", "pinned", "required"]
    assert len(decoded) == 3  # No old archive/active rows are decoded again.
    decoded.clear()
    with path.open("ab") as stream:
        stream.write(b',"payload":{}}\n')
    assert [e.id for e in view.read_all()][-1] == "partial"
    assert len(decoded) == 1


@pytest.mark.parametrize("mutation", ["same_size", "rewrite_growth", "truncate", "replace", "archive"])
def test_modified_prefix_or_file_is_not_accepted_as_append(tmp_path, mutation):
    path, log = _fixture(tmp_path)
    view = ArtifactContextEvents(log)
    assert [e.id for e in view.read_all()] == ["archived", "active"]
    original = path.read_bytes()
    before = path.stat()
    # This filesystem can report ctime at millisecond resolution. Exercise a
    # changed ctime with restored mtime, not indistinguishable stat metadata.
    time.sleep(0.01)
    if mutation == "archive":
        archive = tmp_path / "events/2026-11-01.jsonl"
        stamp = archive.stat()
        archive.write_bytes(archive.read_bytes().replace(b"archived", b"modified"))
        os.utime(archive, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        expected = ["modified", "active"]
    elif mutation == "replace":
        other = tmp_path / "replacement"
        other.write_bytes(original.replace(b"active", b"edited"))
        other.replace(path)
        expected = ["archived", "edited"]
    elif mutation == "truncate":
        path.write_bytes(b"")
        expected = ["archived"]
    else:
        path.write_bytes(original.replace(b"active", b"edited") + (_row("new") if mutation == "rewrite_growth" else b""))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        expected = ["archived", "edited"] + (["new"] if mutation == "rewrite_growth" else [])
    assert [e.id for e in view.read_all()] == expected


def test_rotation_after_cache_preserves_order(tmp_path):
    path, log = _fixture(tmp_path)
    view = ArtifactContextEvents(log)
    view.read_all()
    path.rename(tmp_path / "events/2026-11-02.jsonl")
    path.write_bytes(_row("new-active"))
    assert [e.id for e in view.read_all()] == ["archived", "active", "new-active"]


@pytest.mark.parametrize("replace", [False, True])
def test_repeated_read_race_fallback_does_not_trust_eventlog_cache(tmp_path, monkeypatch, replace):
    path, log = _fixture(tmp_path)
    assert [e.id for e in log.read_all()] == ["archived", "active"]
    stamp = path.stat()
    decode = log.decode_line
    edits = iter([(b"active", b"edited"), (b"edited", b"latest")])

    def race(line):
        event = decode(line)
        if event.id in {"active", "edited"}:
            old, new = next(edits)
            time.sleep(0.01)
            target = tmp_path / "replacement" if replace else path
            target.write_bytes(path.read_bytes().replace(old, new))
            if replace:
                target.replace(path)
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        return event

    monkeypatch.setattr(log, "decode_line", race)
    with pytest.raises(OSError, match="remained unstable"):
        ArtifactContextEvents(log).read_all()
    # A later stable attempt can recover; the unsafe cache was not admitted.
    monkeypatch.setattr(log, "decode_line", decode)
    assert [e.id for e in ArtifactContextEvents(log).read_all()] == ["archived", "latest"]


def test_signed_view_revalidates_unchanged_files(tmp_path, monkeypatch):
    from zf.core.security.signing import EventSigner

    log = EventLog(tmp_path / "events.jsonl", signer=EventSigner(b"test-only"))
    log.append(ZfEvent(type="task.dispatched", id="signed"))
    decode = log.decode_line
    decoded = []
    monkeypatch.setattr(log, "decode_line", lambda line: (decoded.append(line), decode(line))[1])
    view = ArtifactContextEvents(log)
    assert [e.id for e in view.read_all()] == ["signed"]
    assert [e.id for e in view.read_all()] == ["signed"]
    assert len(decoded) == 2


def test_unstable_view_cannot_authorize_an_already_settled_attempt(tmp_path, monkeypatch):
    from zf.runtime.artifact_read_capability import ArtifactReadCapabilityError, _require_current_operation

    path = tmp_path / "events.jsonl"
    archive = tmp_path / "events"
    archive.mkdir()
    (archive / "2026-11-01.jsonl").write_bytes(
        _row("request", "workflow.operation.requested", operation_id="op")
        + _row("started", "workflow.operation.started", operation_id="op", active_attempt_id="attempt")
    )
    path.write_bytes(_row("settled", "workflow.operation.settled", operation_id="op"))
    log = EventLog(path)
    decode = log._decode
    races = 0
    rotated = False

    def race(line):
        nonlocal races, rotated
        event = decode(line)
        if event.id == "settled" and races < 2:
            with path.open("ab") as stream:
                stream.write(_row(f"noise-{races}", "codex.hook.post_tool_use"))
            races += 1
        elif races == 2 and event.id == "request" and not rotated:
            # An unchecked fallback enumerated archiveA already. Rotating now
            # would hide the pre-existing settled event in the omitted archiveB.
            path.rename(archive / "2026-11-02.jsonl")
            path.write_bytes(_row("new-active", "codex.hook.post_tool_use"))
            rotated = True
        return event

    monkeypatch.setattr(log, "_decode", race)
    with pytest.raises((OSError, ArtifactReadCapabilityError)):
        _require_current_operation(
            {"operation_id": "op", "attempt_id": "attempt"}, log, use_artifact_context=True,
        )
    assert races in {1, 2}  # Proven append may preserve the complete settled snapshot.
    assert not rotated  # No unchecked third scan may turn settled into running.


def test_concurrent_append_keeps_proven_snapshot_and_reads_tail_next_time(tmp_path, monkeypatch):
    path, log = _fixture(tmp_path)
    decode = log.decode_line
    decoded = []

    def append_once(line):
        event = decode(line)
        decoded.append(event.id)
        if event.id == "active":
            with path.open("ab") as stream:
                stream.write(_row("new-tail"))
        return event

    monkeypatch.setattr(log, "decode_line", append_once)
    view = ArtifactContextEvents(log)
    assert [e.id for e in view.read_all()] == ["archived", "active"]
    assert decoded == ["archived", "active"]
    assert [e.id for e in view.read_all()] == ["archived", "active", "new-tail"]
    assert decoded == ["archived", "active", "new-tail"]


def _try_append_while_prefix_is_verified(path, result):
    from zf.core.state.locks import locked_path

    try:
        with locked_path(path, timeout_seconds=0):
            with path.open("ab") as stream:
                stream.write(_row("concurrent-hook", "codex.hook.post_tool_use"))
        result.put("appended")
    except TimeoutError:
        result.put("deferred")


def test_busy_append_does_not_starve_prefix_verification(tmp_path, monkeypatch):
    import multiprocessing
    import zf.runtime.artifact_context_events as module

    path, log = _fixture(tmp_path)
    view = ArtifactContextEvents(log)
    read_file = view._read_file
    prefix_hash = module._prefix_hash
    context = multiprocessing.get_context("fork")
    outcomes = []

    def append_after_scan(target, signature):
        result = read_file(target, signature)
        if target == path:
            log.append(ZfEvent(id="tail", type="task.dispatched"))
        return result

    def append_during_hash(stream, size):
        digest = prefix_hash(stream, size)
        result = context.Queue()
        process = context.Process(target=_try_append_while_prefix_is_verified, args=(path, result))
        process.start()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join()
            pytest.fail("event writer did not complete its bounded lock attempt")
        assert process.exitcode == 0
        outcomes.append(result.get(timeout=1))
        result.close()
        return digest

    monkeypatch.setattr(view, "_read_file", append_after_scan)
    monkeypatch.setattr(module, "_prefix_hash", append_during_hash)
    assert [e.id for e in view.read_all()] == ["archived", "active"]
    assert outcomes == ["deferred"]
    monkeypatch.setattr(view, "_read_file", read_file)
    monkeypatch.setattr(module, "_prefix_hash", prefix_hash)
    assert [e.id for e in view.read_all()] == ["archived", "active", "tail"]


def test_next_authorization_consumes_terminal_appended_during_prior_snapshot(tmp_path, monkeypatch):
    from zf.runtime.artifact_read_capability import ArtifactReadCapabilityError, _require_current_operation

    path = tmp_path / "events.jsonl"
    path.write_bytes(
        _row("request", "workflow.operation.requested", operation_id="op")
        + _row("started", "workflow.operation.started", operation_id="op", active_attempt_id="attempt")
    )
    log = EventLog(path)
    decode = log.decode_line

    def append_terminal(line):
        event = decode(line)
        if event.id == "started":
            with path.open("ab") as stream:
                stream.write(_row("settled", "workflow.operation.settled", operation_id="op"))
        return event

    monkeypatch.setattr(log, "decode_line", append_terminal)
    binding = {"operation_id": "op", "attempt_id": "attempt"}
    _require_current_operation(binding, log, use_artifact_context=True)
    with pytest.raises(ArtifactReadCapabilityError) as error:
        _require_current_operation(binding, log, use_artifact_context=True)
    assert error.value.code == "operation_terminal"


@pytest.mark.parametrize("short_partial", [False, True])
def test_short_read_before_snapshot_end_cannot_hide_existing_terminal(tmp_path, monkeypatch, short_partial):
    from zf.runtime.artifact_read_capability import ArtifactReadCapabilityError, _require_current_operation

    path = tmp_path / "events.jsonl"
    prefix = (
        _row("request", "workflow.operation.requested", operation_id="op")
        + _row("started", "workflow.operation.started", operation_id="op", active_attempt_id="attempt")
    )
    original = prefix + _row("settled", "workflow.operation.settled", operation_id="op")
    path.write_bytes(original)
    log = EventLog(path)
    decode = log.decode_line
    open_path = Path.open
    truncated = restored = False

    class ShortRead:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def readline(self, size):
            nonlocal restored
            line = self.stream.readline(size)
            if truncated and not restored and not line.endswith(b"\n"):
                # Restore before final stat/hash; old complete prefix survives,
                # but a read short of the original bound omitted settled.
                with open_path(path, "wb") as stream:
                    stream.write(original + _row("noise", "codex.hook.post_tool_use"))
                restored = True
            return line

    def open_unbuffered(target, mode="r", *args, **kwargs):
        if target == path and mode == "rb":
            return ShortRead(open_path(target, mode, buffering=0))
        return open_path(target, mode, *args, **kwargs)

    def truncate_once(line):
        nonlocal truncated
        event = decode(line)
        if event.id == "started" and not truncated:
            with open_path(path, "wb") as stream:
                stream.write(prefix + (b'{"type"' if short_partial else b""))
            truncated = True
        return event

    monkeypatch.setattr(Path, "open", open_unbuffered)
    monkeypatch.setattr(log, "decode_line", truncate_once)
    with pytest.raises(ArtifactReadCapabilityError) as error:
        _require_current_operation(
            {"operation_id": "op", "attempt_id": "attempt"}, log, use_artifact_context=True,
        )
    assert error.value.code == "operation_terminal"
    assert truncated and restored


def test_buffered_partial_snapshot_cannot_hide_existing_terminal_after_growth(tmp_path, monkeypatch):
    from zf.runtime.artifact_read_capability import ArtifactReadCapabilityError, _require_current_operation

    path = tmp_path / "events.jsonl"
    prefix = (
        _row("request", "workflow.operation.requested", operation_id="op")
        + _row("started", "workflow.operation.started", operation_id="op", active_attempt_id="attempt")
    )
    terminal = _row("settled", "workflow.operation.settled", operation_id="op", detail="x" * (40 * 1024))
    original = prefix + terminal
    path.write_bytes(original)
    log = EventLog(path)
    decode = log.decode_line
    open_path = Path.open
    overwritten = restored = False
    partial_lengths = []

    class BufferedRead:
        def __init__(self, raw):
            self.stream = io.BufferedReader(raw, buffer_size=8192)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def readline(self, size):
            nonlocal restored
            line = self.stream.readline(size)
            if overwritten and not restored and not line.endswith(b"\n"):
                partial_lengths.append((len(line), size))
                # The reader consumed the entire old bound, mixing its real
                # 8192-byte buffer with the overwritten suffix. Restore and
                # append before its post-stat, preserving the complete prefix.
                with open_path(path, "r+b") as stream:
                    stream.write(original + _row("noise", "codex.hook.post_tool_use"))
                restored = True
            return line

    def open_buffered(target, mode="r", *args, **kwargs):
        if target == path and mode == "rb":
            return BufferedRead(open_path(target, mode, buffering=0))
        return open_path(target, mode, *args, **kwargs)

    def overwrite_once(line):
        nonlocal overwritten
        event = decode(line)
        if event.id == "started" and not overwritten:
            with open_path(path, "r+b") as stream:
                stream.seek(len(prefix))
                stream.write(b"x" * len(terminal))
            overwritten = True
        return event

    monkeypatch.setattr(Path, "open", open_buffered)
    monkeypatch.setattr(log, "decode_line", overwrite_once)
    with pytest.raises(ArtifactReadCapabilityError) as error:
        _require_current_operation(
            {"operation_id": "op", "attempt_id": "attempt"}, log, use_artifact_context=True,
        )
    assert error.value.code == "operation_terminal"
    assert overwritten and restored
    assert partial_lengths == [(len(terminal), len(terminal))]
