"""Reconstruct only unique, registered, pre-cutoff Codex disk-usage receipts."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from types import SimpleNamespace
import json
from unittest.mock import patch
from uuid import uuid4

import pytest

from zf.core.config.schema import RoleConfig
from zf.runtime import codex_turn_sample
from zf.runtime.backend_session_reader import CodexSessionReader, read_historical_codex_usage
from zf.runtime.provider_usage_reconciliation import build_disk_usage_event


def stamp(second):
    return f"2026-11-26T01:00:{second:02d}.123Z"


def row(kind, second, **payload):
    return (json.dumps({"type": kind, "timestamp": stamp(second), "payload": payload}) + "\n").encode()


def token(second, count, *, total=None):
    info = {"last_token_usage": {"input_tokens": count}, "model_context_window": 300}
    if total is not None:
        info["total_token_usage"] = {"input_tokens": total, "output_tokens": 4}
    return row("event_msg", second, type="token_count", info=info)


@pytest.fixture
def source(tmp_path):
    role = "verify-3"
    session = str(uuid4())
    turn = str(uuid4())
    path = (tmp_path / "workdirs" / role / "codex-home/sessions/2026/11/26"
            / f"rollout-2026-11-26T01-00-00-{session}.jsonl")
    path.parent.mkdir(parents=True)
    header = (row("session_meta", 0, cwd="/product", id=session)
              + row("event_msg", 1, type="task_started", turn_id=turn)
              + row("turn_context", 1, cwd="/product", turn_id=turn, model="example/codex"))
    path.write_bytes(header)
    return tmp_path, role, session, path


def append(path, content):
    with path.open("ab") as stream:
        stream.write(content)


def poll(source):
    state, role, session, path = source
    report = CodexSessionReader().read_latest_usage(path, registered_rollout=(state, role, session))
    event = build_disk_usage_event(role=RoleConfig(name=role, backend="codex"), usage=report, config=None)
    return report, event.payload["usage_sample_id"]


@pytest.mark.parametrize("cumulative", [False, True])
def test_selects_original_receipt_from_multiple_rows_at_same_time_in_one_scan(source, cumulative):
    path = source[-1]
    append(path, token(2, 3, total=10 if cumulative else None))
    _, first_id = poll(source)
    append(path, token(2, 12, total=27 if cumulative else None))
    selected, selected_id = poll(source)
    prefix_hash = sha256(path.read_bytes()).hexdigest()
    append(path, token(4, 21, total=59 if cumulative else None))
    _, later_id = poll(source)
    append(path, row("response_item", 5, type="message", text="out of requested range"))
    append(path, b"not json\n")
    pinned_open = codex_turn_sample.open_rollout
    with patch.object(codex_turn_sample, "open_rollout", wraps=pinned_open) as opening:
        results = read_historical_codex_usage(*source, {stamp(2): selected_id, stamp(4): later_id})
    opening.assert_called_once_with(source[0], path)
    assert set(results) == {stamp(2), stamp(4)}
    assert results[stamp(2)].raw == selected.raw
    assert results[stamp(2)].effective_input_tokens == 12
    assert results[stamp(2)].native_turn_sample == {**selected.native_turn_sample, "rollout_prefix_sha256": prefix_hash}
    assert read_historical_codex_usage(*source, {stamp(2): first_id})[stamp(2)].effective_input_tokens == 3


@pytest.mark.parametrize("problem", ["duplicate", "invalid_last", "invalid_total", "invalid_cached", "negative_native", "wrong_id", "cross_turn"])
def test_ambiguous_or_invalid_bucket_preserves_other_samples(source, problem):
    path = source[-1]
    append(path, token(2, 3))
    _, before = poll(source)
    append(path, token(3, 12))
    _, candidate = poll(source)
    if problem == "duplicate":
        append(path, token(3, 12))
    elif problem == "invalid_last":
        append(path, row("event_msg", 3, type="token_count", info={"last_token_usage": [12]}))
    elif problem == "invalid_total":
        append(path, row("event_msg", 3, type="token_count",
                         info={"last_token_usage": {"input_tokens": 12}, "total_token_usage": [12]}))
    elif problem == "invalid_cached":
        append(path, row("event_msg", 3, type="token_count",
                         info={"last_token_usage": {"input_tokens": 12, "cached_input_tokens": []}}))
    elif problem == "negative_native":
        append(path, token(3, -1))
        _, candidate = poll(source)
    elif problem == "cross_turn":
        append(path, row("turn_context", 3, cwd="/wrong", model="other", turn_id=str(uuid4())))
        # This malformed identity compromises the rest of the registered session.
    elif problem == "wrong_id":
        candidate = "0" * 24
    append(path, token(4, 21))
    _, after = poll(source)
    result = read_historical_codex_usage(*source, {stamp(2): before, stamp(3): candidate, stamp(4): after})
    if problem == "cross_turn":
        assert set(result) == {stamp(2), stamp(3)}
    else:
        assert set(result) == {stamp(2), stamp(4)}


@pytest.mark.parametrize("tail", [b"{", b"not json\n", b"\xff\n", b"[]\n"])
def test_damage_in_open_bucket_does_not_discard_closed_samples(source, tail):
    path = source[-1]
    append(path, token(2, 8))
    _, prior = poll(source)
    append(path, token(4, 10))
    _, current = poll(source)
    append(path, tail)
    assert set(read_historical_codex_usage(*source, {stamp(2): prior, stamp(4): current})) == {stamp(2)}


def test_rejects_other_scope_or_changed_file_identity(source, tmp_path):
    state, role, session, path = source
    append(path, token(2, 3))
    _, expected = poll(source)
    args = {stamp(2): expected}
    assert read_historical_codex_usage(state, "other", session, path, args) == {}
    assert read_historical_codex_usage(state, role, str(uuid4()), path, args) == {}
    moved = path.rename(tmp_path / "outside")
    path.symlink_to(moved)
    assert read_historical_codex_usage(*source, args) == {}


def test_reads_two_real_turns_through_first_newer_record_only(source, monkeypatch):
    path = source[-1]
    _, _, first_context = path.read_bytes().splitlines()
    first_turn = json.loads(first_context)["payload"]["turn_id"]
    second_turn = str(uuid4())
    append(path, token(2, 17))
    _, first_id = poll(source)
    append(path, row("event_msg", 3, type="task_complete", turn_id=first_turn))
    append(path, row("event_msg", 7, type="task_started", turn_id=second_turn))
    append(path, row("turn_context", 7, turn_id=second_turn, model="other/provider", cwd="/other"))
    append(path, token(8, 32))
    _, second_id = poll(source)
    boundary = row("event_msg", 9, type="task_complete", turn_id=second_turn)
    append(path, boundary)
    lines = path.read_bytes().splitlines(keepends=True)

    def guarded_lines():
        yield from lines
        pytest.fail("historical reader accessed content after its last requested bucket")

    @contextmanager
    def guarded_open(state, registered_path):
        assert (state, registered_path) == (source[0], path)
        yield SimpleNamespace(buffer=guarded_lines())

    monkeypatch.setattr(codex_turn_sample, "open_rollout", guarded_open)
    result = read_historical_codex_usage(*source, {stamp(2): first_id, stamp(8): second_id})
    assert result[stamp(2)].native_turn_sample["turn_id"] == first_turn
    assert result[stamp(8)].native_turn_sample["turn_id"] == second_turn
    assert result[stamp(8)].model == "other/provider"


def test_non_chronological_prefix_cannot_select_a_historical_record(source):
    path = source[-1]
    append(path, token(4, 22))
    _, expected = poll(source)
    append(path, token(3, 21))
    assert read_historical_codex_usage(*source, {stamp(4): expected}) == {}
