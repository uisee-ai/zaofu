"""Warm per-consumer selection, including a genuinely new CLI process and source/archive recovery."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from tests.test_durable_mailbox import project, send
from zf.core.events import ZfEvent
from zf.runtime import durable_mailbox as mail


def test_many_recipients_warm_consumer_skips_other_threads_after_restart(project, monkeypatch):
    rt, (a, b, c) = project
    for _ in range(60):
        rt.event_writer.append(ZfEvent(type="agent.text", actor="irrelevant"))

    def send_one(i):
        return send(rt, a, to=("B", "C") if i in (0, 50, 100) else ("C",), key="m" + str(i))

    with ThreadPoolExecutor(16) as pool:
        assert len(list(pool.map(send_one, range(112)))) == 112
    assert mail.pending_for_consumer(rt, b)["total"] == 3
    assert mail.pending_for_consumer(rt, c, limit=100)["total"] == 112

    def no_full_scan(*args):
        raise AssertionError("warm mailbox lookup used full ledger")

    monkeypatch.setattr(rt.event_log, "read_all", no_full_scan)
    calls = []
    decoder = rt.event_log.decode_line
    monkeypatch.setattr(rt.event_log, "decode_line", lambda line: (calls.append(1), decoder(line))[1])
    assert mail.pending_for_consumer(rt, b, limit=2)["total"] == 3
    assert len(calls) <= 12
    # A fresh process has neither an in-memory EventIndex nor Python's read_all fold.
    program = """
from pathlib import Path
from types import SimpleNamespace
from zf.core.events import EventLog,EventWriter
from zf.core.config.loader import load_config
from zf.runtime.durable_mailbox import pending_for_consumer
import json,sys
state=Path(sys.argv[1])
log=EventLog(state/'events.jsonl')
log.read_all=lambda: (_ for _ in ()).throw(AssertionError('unbounded ledger read'))
original=log.decode_line
calls=[]
log.decode_line=lambda line:(calls.append(1),original(line))[1]
rt=SimpleNamespace(config=load_config(Path('zf-prd-v5.yaml')),state_dir=state,event_log=log,event_writer=EventWriter(log))
rows=pending_for_consumer(rt,{'actor':'B','attempt_id':'B','operation_id':'op-B'})
print(json.dumps([rows['total'],len(calls)]))
"""
    fresh = subprocess.run(
        [sys.executable, "-c", program, str(rt.state_dir)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        check=True,
    )
    count, decoded = json.loads(fresh.stdout)
    assert count == 3 and decoded <= 12
    db = rt.state_dir / "projections/durable-mailbox/locators.sqlite"
    # A missing row in a rewritten projection must not erase the real obligation.
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM label WHERE value='actor:B'")
    assert mail.pending_for_consumer(rt, b)["total"] == 3
    db.unlink()
    assert mail.pending_for_consumer(rt, b)["total"] == 3


def test_irrelevant_append_is_tolerated_by_the_atomic_consumer_fence(project, monkeypatch):
    rt, (a, b, c) = project
    msg = send(rt, a)
    delivery = mail.prepare(rt, b, request_id="before")
    append = rt.event_writer.append

    def heartbeat(event, **options):
        if event.type == "workflow.mailbox.acknowledged":
            append(ZfEvent(type="worker.heartbeat", actor="unrelated"))
        return append(event, **options)

    monkeypatch.setattr(rt.event_writer, "append", heartbeat)
    assert mail.acknowledge(rt, b, delivery_id=delivery["delivery_id"])["status"] == "acknowledged"
