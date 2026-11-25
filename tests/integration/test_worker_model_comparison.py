"""Opt-in paired model evaluation of a ledger reconciliation query against a held-out oracle."""

import json
import os
import re
import sqlite3
import time

import pytest

from zf.runtime.worker_model_handoff import local_chat, local_plan

pytestmark = pytest.mark.real_provider

SPEC = (
    "Implement a single SQLite SELECT. Tables invoice(id INTEGER PRIMARY KEY, total_cents INTEGER NOT NULL) "
    "and payment(invoice_id INTEGER, cents INTEGER, status TEXT). Return columns id and due_cents. "
    "Count only posted payments. Include unpaid invoices. Floor the remaining balance at zero. Sort by id. "
    "Return only the SELECT, without Markdown."
)


def score(text):
    sql = re.sub(r"^\s*```(?:sql|sqlite)?\s*|\s*```\s*$", "", text, flags=re.I).strip()
    db = sqlite3.connect(":memory:")
    try:
        db.executescript(
            "CREATE TABLE invoice(id INTEGER PRIMARY KEY,total_cents INTEGER NOT NULL);"
            "CREATE TABLE payment(invoice_id INTEGER,cents INTEGER,status TEXT);"
            "INSERT INTO invoice VALUES(1,1000),(2,80),(3,150),(4,5),(5,70);"
            "INSERT INTO payment VALUES(1,100,'posted'),(1,800,'pending'),(1,200,'posted'),"
            "(2,90,'posted'),(4,2,'void'),(5,70,'posted');"
        )
        db.set_authorizer(
            lambda action, *_: (
                sqlite3.SQLITE_OK
                if action
                in {sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION}
                else sqlite3.SQLITE_DENY
            )
        )
        steps = iter(range(500))
        db.set_progress_handler(lambda: next(steps, None) is None, 100)
        result = db.execute(sql)
        if [x[0].lower() for x in result.description] != ["id", "due_cents"]:
            return 0, "column_contract"
        actual = result.fetchmany(6)
        expected = [(1, 700), (2, 0), (3, 150), (4, 5), (5, 0)]
        if len(actual) != 5:
            return 0, "row_count_contract"
        n = sum(a == b for a, b in zip(actual, expected))
        return n, "passed" if n == 5 else "balance_or_order"
    except sqlite3.Error as exc:
        return 0, f"sqlite:{exc}"
    finally:
        db.close()


def test_paired_local_worker_models():
    if os.getenv("ZF_REAL_MODEL_COMPARISON") != "1":
        pytest.skip("requires two installed local Ollama models")
    executor = os.getenv("ZF_EXECUTOR_MODEL", "qwen2.5:latest")
    planner = os.getenv("ZF_PLANNER_MODEL", "qwen2:7b")

    def profile(model):
        return dict(model=model, port=11434, timeout_seconds=300, max_output_tokens=160)

    measurements = []
    assert score(
        "SELECT i.id, MAX(0,i.total_cents-COALESCE(SUM(p.cents),0)) AS due_cents FROM invoice i LEFT JOIN payment p ON p.invoice_id=i.id AND p.status='posted' GROUP BY i.id ORDER BY i.id"
    ) == (5, "passed")
    modes = (
        ("dual", "single")
        if os.getenv("ZF_MODEL_PAIR_REVERSE") == "1"
        else ("single", "dual")
    )
    for mode in modes:
        start = time.monotonic()
        packet = {"goal": SPEC}
        usage = []
        if mode == "dual":
            advice = local_plan(profile(planner), packet)
            usage.append(advice)
            packet["advice"] = advice["advice"]
        verdicts = []
        for attempt in range(2):
            answer = local_chat(
                profile(executor),
                packet,
                "You implement SQLite queries. Follow the output contract exactly.",
            )
            usage.append(answer)
            passed, why = score(answer["advice"])
            verdicts.append(why)
            if passed == 5:
                break
            packet["feedback"] = (
                "The independent test database rejected the query or its output. Recheck joins, zero payments, floor, order, and column names."
            )
            packet["previous_candidate"] = answer["advice"]
        measurements.append(
            dict(
                mode=mode,
                executor=executor,
                planner=planner if mode == "dual" else None,
                input_tokens=sum(x["input_tokens"] for x in usage),
                output_tokens=sum(x["output_tokens"] for x in usage),
                passed=passed,
                total=5,
                rework=attempt,
                verdicts=verdicts,
                wall_seconds=round(time.monotonic() - start, 2),
            )
        )
    print("ZF_PAIRED_MEASUREMENTS", json.dumps(measurements))
    assert len(measurements) == 2 and all(m["input_tokens"] > 0 for m in measurements)


def test_real_independent_scheduled_report(tmp_path):
    if os.getenv("ZF_REAL_SCHEDULE_REPORT") != "1":
        pytest.skip("requires an installed local Ollama model")
    from tests.test_scheduled_agent_job import job
    from zf.runtime.workflow_schedule import reconcile_schedules
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from datetime import datetime

    runtime, definition = job(tmp_path)
    definition.update(
        model=os.getenv("ZF_EXECUTOR_MODEL", "qwen2.5:latest"),
        max_output_tokens=50,
        timeout_seconds=160,
        goal="Report the finding in the given artifact in one sentence. Do not use any tools.",
    )
    now = datetime.fromisoformat("2026-10-01T03:00:00Z")
    until = time.monotonic() + 165
    while time.monotonic() < until:
        reconcile_schedules(runtime, now=now)
        result = next(
            (
                e
                for e in runtime.event_log.read_all()
                if e.type == "workflow.schedule.finished"
            ),
            None,
        )
        if result:
            break
        time.sleep(0.1)
    assert result and result.payload["status"] == "completed"
    body = hydrate_sidecar_ref(tmp_path, result.payload["report_ref"]).payload
    assert "original" in body["reply"].lower() and body["input_tokens"] > 0
    print(
        "ZF_SCHEDULE_MODEL",
        json.dumps(
            {
                k: body[k]
                for k in ("session_id", "model", "input_tokens", "output_tokens")
            }
        ),
    )
