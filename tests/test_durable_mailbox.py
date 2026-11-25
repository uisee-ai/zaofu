from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_workflow_board import seed
from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime import durable_mailbox as mail
from zf.runtime.sidecar_refs import SidecarRefError


@pytest.fixture
def project(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    cfg = load_config(Path(__file__).resolve().parents[1] / "zf-prd-v5.yaml")
    cfg.runtime.durable_mailbox.enabled = True
    rt = SimpleNamespace(config=cfg, state_dir=tmp_path, event_log=log, event_writer=EventWriter(log))
    return rt, [seed(rt, who) for who in "ABC"]


def send(rt, actor, to=("B",), key="m", **kwargs):
    args = dict(
        text="Confidential question",
        kind="notice",
        message_id=key,
        request_id=key,
        recipients=[{"mode": "execution", "operation_id": "op-" + who} for who in to],
    )
    args.update(kwargs)
    return mail.post(rt, actor, **args)


def test_crash_windows_idempotency_and_recoverable_batches(project, monkeypatch):
    rt, (a, b, c) = project
    accept = rt.event_writer.append

    def fail(event, **options):
        raise OSError("interrupted before accept")

    monkeypatch.setattr(rt.event_writer, "append", fail)
    with pytest.raises(OSError):
        send(rt, a)
    bodies = list(rt.state_dir.glob("artifacts/durable-mailbox/*.json"))
    assert len(bodies) == 1
    monkeypatch.setattr(rt.event_writer, "append", accept)
    assert mail.prepare(rt, b, request_id="empty")["messages"] == []
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: send(rt, a, to=("B", "C")), range(4)))
    assert len({m["event_id"] for m in results}) == 1
    with pytest.raises(ValueError, match="conflict"):
        send(rt, a, to=("B",))
    batch = mail.prepare(rt, b, request_id="read")
    ack = mail.acknowledge(rt, b, delivery_id=batch["delivery_id"])
    send(rt, a, key="late")
    assert mail.prepare(rt, b, request_id="read") == {**batch, "acknowledgement": ack}
    assert mail.acknowledge(rt, b, delivery_id=batch["delivery_id"]) == ack
    assert [item["message_id"] for item in mail.prepare(rt, b, request_id="next")["messages"]] == ["late"]
    assert len(mail.prepare(rt, c, request_id="other")["messages"]) == 1
    with pytest.raises(ValueError, match="bound"):
        mail.acknowledge(rt, c, delivery_id=batch["delivery_id"])
    original = bodies[0].read_bytes()
    bodies[0].write_text("damaged")
    with pytest.raises(SidecarRefError):
        mail.acknowledge(rt, b, delivery_id=batch["delivery_id"])
    assert sum(e.type == "workflow.mailbox.acknowledged" for e in rt.event_log.read_all()) == 1
    bodies[0].write_bytes(original)
    assert mail.acknowledge(rt, b, delivery_id=batch["delivery_id"]) == ack
    evidence = [
        e.payload["status"] for e in rt.event_log.read_all() if e.type == "workflow.mailbox.disposition"
    ]
    assert evidence == ["evidence_unavailable", "evidence_restored"]


def test_private_events_and_attachment_scope_are_not_public_metadata(project):
    rt, (a, b, c) = project
    send(rt, a)
    with pytest.raises((OSError, ValueError)):
        send(rt, a, key="private-index", attachments=["other-source"])
    forged = rt.event_writer.append(
        ZfEvent(
            type="workflow.mailbox.accepted",
            actor="durable-mailbox",
            origin="worker",
            payload={"workflow_run_id": "r", "visibility": "operator_metadata", "message_key": "forged"},
        )
    )
    assert mail.project_mailbox(rt.event_log.read_all(), run_id="r") == {
        "source_seq": 0,
        "items": [],
        "total": 0,
    }
    public = send(rt, a, key="w", visibility="operator_metadata")
    before = len(rt.event_log.read_all())
    batch = mail.prepare(rt, b, request_id="pick", message_ids=[public["message_key"]])
    mail.acknowledge(rt, b, delivery_id=batch["delivery_id"])
    rows = mail.project_mailbox(rt.event_log.read_all(), run_id="r", limit=None)
    (row,) = rows["items"]
    assert rows["total"] == 1 and row["delivery_status"] == "acknowledged"
    assert not any(s in str(rows) for s in ("Confidential", "body_ref", "forged", "artifacts/"))
    assert (
        mail.project_mailbox(rt.event_log.read_all(), run_id="r", cursor=before)["items"][0]["status"]
        == "pending"
    )
    for path in rt.state_dir.glob("artifacts/durable-mailbox/*.json"):
        path.unlink()
    assert (
        mail.project_mailbox(rt.event_log.read_all(), run_id="r", state_dir=rt.state_dir)["items"][0][
            "status"
        ]
        == "evidence_unavailable"
    )


def test_claim_answer_requires_current_receipt_and_does_not_settle_work(project):
    rt, (a, b, c) = project
    msg = send(rt, a, kind="question")["message_key"]

    def act(action, key="action", **kwargs):
        return mail.question_action(rt, b, message_id=msg, action=action, request_id=key, **kwargs)

    batch = mail.prepare(rt, b, request_id="read", message_ids=[msg])
    with pytest.raises(ValueError, match="acknowledged"):
        act("claim", delivery_id=batch["delivery_id"])
    did = batch["delivery_id"]
    mail.acknowledge(rt, b, delivery_id=did)
    with pytest.raises(ValueError, match="claim"):
        act("answer", text="proposal", delivery_id=did)
    rt.config.runtime.durable_mailbox.enabled = False
    assert act("claim", delivery_id=did)["status"] == "claimed"
    with pytest.raises(ValueError, match="accepting"):
        send(rt, a, key="disabled")
    ans = act("answer", key="reply", text="proposal", delivery_id=did)
    assert act("answer", key="reply", text="proposal", delivery_id=did) == ans
    assert mail.read_answer(rt, a, message_id=msg)["text"] == "proposal"
    with pytest.raises(ValueError):
        mail.read_answer(rt, c, message_id=msg)
    assert not any(e.type in {"workflow.operation.settled", "task.done"} for e in rt.event_log.read_all())


def test_cross_run_fencing_and_reconciliation(project):
    rt, (a, b, c) = project
    with pytest.raises(ValueError, match="cross-run"):
        send(
            rt,
            a,
            recipients=[
                {"mode": "execution", "operation_id": b["operation_id"], "workflow_run_id": "another"}
            ],
        )
    with pytest.raises(ValueError):
        send(rt, {**a, "lease_id": "earlier"})
    msg = send(rt, a, kind="question", visibility="operator_metadata")["message_key"]
    batch = mail.prepare(rt, b, request_id="poll")
    fact = next(
        e
        for e in rt.event_log.read_all()
        if e.type == "workflow.operation.started" and e.payload["operation_id"] == b["operation_id"]
    )
    rt.event_writer.append(
        ZfEvent(type="workflow.operation.cancelled", origin="kernel", payload=fact.payload)
    )
    with pytest.raises(ValueError, match="stale"):
        mail.acknowledge(rt, b, delivery_id=batch["delivery_id"])
    assert mail.reconcile_mailbox(rt) == 1
    assert mail.reconcile_mailbox(rt) == 0
    (row,) = mail.project_mailbox(rt.event_log.read_all(), run_id="r")["items"]
    assert row["status"] == "cancelled"
    assert row["cause"] == "execution_ended"


def test_append_time_fence_rejects_replacement_after_body_was_checked(project, monkeypatch):
    rt, (a, b, c) = project
    msg = send(rt, a)["message_key"]
    batch = mail.prepare(rt, b, request_id="read", message_ids=[msg])
    append = rt.event_writer.append

    def concurrent_transition(event, **kw):
        if event.type == "workflow.mailbox.acknowledged":
            append(
                ZfEvent(
                    type="workflow.operation.cancelled",
                    origin="kernel",
                    payload={"workflow_run_id": "r", "operation_id": "op-B", "request_hash": "hash"},
                )
            )
        return append(event, **kw)

    monkeypatch.setattr(rt.event_writer, "append", concurrent_transition)
    with pytest.raises(ValueError, match="conditional append"):
        mail.acknowledge(rt, b, delivery_id=batch["delivery_id"])
    monkeypatch.setattr(rt.event_writer, "append", append)
    with pytest.raises(ValueError, match="stale"):
        mail.acknowledge(rt, b, delivery_id=batch["delivery_id"])
    assert not any(e.type == "workflow.mailbox.acknowledged" for e in rt.event_log.read_all())


def test_two_channels_prepare_independently_but_consume_once(project):
    rt, (a, b, c) = project
    msg = send(rt, a)["message_key"]
    before = len(rt.event_log.read_all())
    assert mail.pending_for_consumer(rt, b)["items"][0]["message_key"] == msg
    assert mail.pending_for_consumer(rt, c)["total"] == 0
    assert len(rt.event_log.read_all()) == before
    deliveries = [mail.prepare(rt, b, request_id=channel)["delivery_id"] for channel in ("native", "cli")]
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda d: mail.acknowledge(rt, b, delivery_id=d), deliveries))
    acknowledgements = [e for e in rt.event_log.read_all() if e.type == "workflow.mailbox.acknowledged"]
    assert sum(len(e.payload["newly_consumed"]) for e in acknowledgements) == 1
    assert mail.pending_for_consumer(rt, b)["total"] == 0


def test_archive_replay_recovers_receipts_without_index_and_reports_missing_body(project, monkeypatch):
    rt, (a, b, c) = project
    monkeypatch.setenv("ZF_EVENT_LOG_MAX_ACTIVE_BYTES", "1")
    msg = send(rt, a, visibility="operator_metadata")["message_key"]
    received = mail.prepare(rt, b, request_id="batch")
    ack = mail.acknowledge(rt, b, delivery_id=received["delivery_id"])
    rt.event_log.close()
    (rt.state_dir / "event_index.json").unlink(missing_ok=True)
    (rt.state_dir / "projections/durable-mailbox/locators.sqlite").unlink()
    rt.event_log = EventLog(rt.state_dir / "events.jsonl")
    rt.event_writer = EventWriter(rt.event_log)
    assert mail.prepare(rt, b, request_id="batch")["acknowledgement"] == ack
    assert list((rt.state_dir / "events").glob("*.jsonl"))
    assert mail.project_mailbox(rt.event_log.read_all(), run_id="r")["items"][0]["message_key"] == msg
    for p in rt.state_dir.glob("artifacts/durable-mailbox/*.json"):
        p.unlink()
    with pytest.raises(SidecarRefError):
        mail.prepare(rt, b, request_id="batch")
    (row,) = mail.project_mailbox(rt.event_log.read_all(), run_id="r", state_dir=rt.state_dir)["items"]
    assert row["status"] == "evidence_unavailable" and row["delivery_status"] == "acknowledged"


def test_receiving_an_attachment_requires_original_scope_and_current_212_binding(project):
    import json
    from zf.runtime.artifact_read_capability import (
        bind_attempt_artifact_read_capability,
        provision_role_artifact_read_credential,
    )

    rt, (a, b, c) = project

    def manifest(identity, source):
        who = identity["actor"]
        provision_role_artifact_read_credential(rt.state_dir, who, role_name=who)
        data = {
            "workflow_run_id": "r",
            "attempt_id": identity["attempt_id"],
            "read_purpose": "verify",
            "sources": [source],
        }
        root = rt.state_dir / "artifacts/attempts" / identity["attempt_id"] / "source-manifests"
        root.mkdir(parents=True, exist_ok=True)
        (root / "in.json").write_text(json.dumps(data))
        bind_attempt_artifact_read_capability(
            rt.state_dir,
            manifest=data,
            attempt_id=identity["attempt_id"],
            operation_id=identity["operation_id"],
            role_instance=who,
        )
        return root / "in.json"

    original = {
        "source_id": "source",
        "artifact_id": "document",
        "ref": "artifacts/authorized.json",
        "sha256": "b" * 64,
        "occurrence_id": "v1",
        "allowed_paths": ["$.public"],
        "access_scope": {"actors": ["A", "B"]},
    }
    manifest(a, original)
    bpath = manifest(b, {**original, "source_id": "input-for-b"})
    with pytest.raises(ValueError, match="authorized"):
        send(rt, a, to=("C",), attachments=["source"])
    msg = send(rt, a, attachments=["source"])["message_key"]
    result = mail.prepare(rt, b, request_id="authorized", message_ids=[msg])
    assert result["messages"][0]["attachments"] == [
        {"source_id": "input-for-b", "sha256": original["sha256"]}
    ]
    body = json.loads(bpath.read_text())
    body["sources"][0]["allowed_paths"] = ["$"]
    bpath.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="source manifest"):
        mail.acknowledge(rt, b, delivery_id=result["delivery_id"])
    assert not any(e.type == "workflow.mailbox.acknowledged" for e in rt.event_log.read_all())
