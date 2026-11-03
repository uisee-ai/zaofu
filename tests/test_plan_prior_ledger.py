import hashlib
import json

import pytest

from zf.runtime.plan_prior_ledger import prior_observed_rows
from zf.runtime.sidecar_refs import build_sidecar_ref_descriptor


def _ledger(tmp_path, raw):
    path = tmp_path / "artifacts" / "prior.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    descriptor = build_sidecar_ref_descriptor(
        kind="artifact_read_ledger", ref="artifacts/prior.jsonl", sha256=hashlib.sha256(raw).hexdigest(),
        byte_count=len(raw), content_type="application/x-ndjson", schema_version="artifact-read-ledger.v1",
    )
    return path, descriptor


def _row(**kwargs):
    return {"schema_version": "artifact-read.v1", "attempt_id": "attempt", "consumer_actor": "critic",
            "consumer_provider": "codex", "provider_session_id": "session",
            "provider_delivery_status": "observed_in_transcript", **kwargs}


def _read(tmp_path, descriptor):
    return prior_observed_rows(tmp_path, descriptor, attempt_id="attempt", session_id="session", actor="critic")


def test_prior_ledger_streams_over_four_mib_and_filters_other_sessions(tmp_path):
    relevant = _row()
    irrelevant = _row(provider_session_id="other", padding="x" * 5000)
    raw = (json.dumps(irrelevant) + "\n").encode() * 1100 + (json.dumps(relevant) + "\n").encode()
    assert len(raw) > 4 * 1024 * 1024
    _, descriptor = _ledger(tmp_path, raw)
    assert _read(tmp_path, descriptor) == [relevant]


@pytest.mark.parametrize("problem", ["digest", "length", "attempt", "oversized_line", "bad_json"])
def test_prior_ledger_fails_closed_on_tampering(tmp_path, problem):
    raw = (json.dumps(_row()) + "\n").encode()
    if problem == "attempt": raw = (json.dumps(_row(attempt_id="wrong")) + "\n").encode()
    if problem == "oversized_line": raw = (json.dumps(_row(padding="x" * (1024 * 1024))) + "\n").encode()
    if problem == "bad_json": raw = b"not json\n"
    path, descriptor = _ledger(tmp_path, raw)
    if problem == "digest": path.write_bytes(raw.replace(b"critic", b"Critic"))
    if problem == "length": descriptor["byte_count"] += 1
    with pytest.raises(ValueError):
        _read(tmp_path, descriptor)
