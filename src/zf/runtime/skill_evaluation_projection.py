"""Read-only ledger replay and an independently restartable Skill card projection."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Iterator

from zf.core.config.schema import ZfConfig
from zf.core.events.offset import current_offset
from zf.core.events.segments import iter_event_records, iter_event_records_from_cursor, list_event_segments
from zf.core.state.locks import locked_path
from zf.runtime.skill_attempt_evaluation import EvaluationHistory, IDENTITY

logger = logging.getLogger(__name__)
FILTERS = (*IDENTITY, "task_id")


def replay_cards(state: Path, root: Path, config: ZfConfig | None, **filters: str) -> list[dict]:
    """Compute current cards without creating a projection, lock, or cursor."""
    history = EvaluationHistory(state, root)
    for record in iter_event_records(state, config=config, strict_io=True):
        history.consume(record.event)
    keys = (
        key
        for key, (_, dispatch, _) in history.dispatches.items()
        if all(dispatch.get(field) == value for field, value in filters.items() if value)
    )
    return list(history.cards(keys))


def _database(state: Path) -> Path:
    return state / "projections" / "skill-evaluations.sqlite3"


def query_cards(state: Path, *, history: bool = False, **filters: str) -> list[dict]:
    return list(_iter_cards(state, history=history, **filters))


def _iter_cards(state: Path, *, history: bool = False, **filters: str) -> Iterator[dict]:
    path = _database(state)
    if not path.exists():
        return
    conditions = []
    values = []
    for field in FILTERS:
        if filters.get(field):
            conditions.append(f"c.{field} = ?")
            values.append(filters[field])
    if not history:
        conditions.append("c.revision = (SELECT MAX(revision) FROM cards WHERE card_key = c.card_key)")
    where = " AND ".join(conditions) or "1"
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cards'").fetchone():
            return
        found = db.execute(
            f"""SELECT body, revision, digest, predecessor, change_reason FROM cards c
            WHERE {where} ORDER BY workflow_run_id, operation_id, attempt_id, dispatch_id, revision""",
            values,
        )
        for body, revision, digest, predecessor, reason in found:
            yield {
                **json.loads(body),
                "revision": revision,
                "digest": digest,
                "predecessor": predecessor,
                "change_reason": reason,
            }


def project_skill_decisions(state: Path) -> list[dict]:
    """Latest frozen dispatch observations, grouped by measured scope, never causal lift."""
    groups = {}
    for card in _iter_cards(state):
        review = card.get("independent_review") or {}
        decision = review.get("decision") or {}
        adjudicated_release = (card.get("task_outcome") == "passed" and review.get("status") == "reviewed"
                               and decision.get("result") in ("pass", "fail"))
        false_release = decision.get("critical_false_release") if adjudicated_release else None
        for skill in card.get("skills") or []:
            mode = ("required" if skill.get("auto_inject") else
                    "on_demand" if skill.get("load_on_demand") else "unknown")
            scope = {field: card.get(field) for field in
                     ("role", "output_profile_id", "provider", "harness_version", "model", "configured_model")}
            scope.update(name=skill["name"], digest=skill.get("digest"), mode=mode)
            key = tuple(scope.values())
            row = groups.setdefault(key, {
                **scope, "dispatches": 0, "materialized": 0, "observed_reads": 0,
                "task_keys": set(), "reviewed_dispatches": 0, "confirmed_false_releases": None,
                "evidence": {field: card.get(field) for field in (*IDENTITY, "task_id")},
            })
            row["dispatches"] += 1
            row["task_keys"].add((card["workflow_run_id"], card["task_id"]))
            row["materialized"] += skill.get("materialization") == "resolved"
            row["observed_reads"] += skill.get("read_status") == "observed_header"
            if type(false_release) is bool:
                row["reviewed_dispatches"] += 1
                row["confirmed_false_releases"] = (row["confirmed_false_releases"] or 0) + false_release
    for row in groups.values():
        row["tasks"] = len(row.pop("task_keys"))
    return sorted(groups.values(), key=lambda r: (r["name"], str(r["role"]), str(r["model"] or r["configured_model"])))


def _publish(path: Path, cards: Iterator[dict]) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS cards (
            card_key TEXT NOT NULL, workflow_run_id TEXT, operation_id TEXT, attempt_id TEXT, dispatch_id TEXT,
            task_id TEXT, revision INTEGER NOT NULL, digest TEXT NOT NULL, predecessor TEXT, change_reason TEXT,
            body TEXT NOT NULL, PRIMARY KEY(card_key, revision))""")
        db.execute("CREATE INDEX IF NOT EXISTS cards_by_task ON cards(task_id)")
        for card in cards:
            identity = [card[field] for field in IDENTITY]
            key = json.dumps(identity)
            body = json.dumps(
                card, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            digest = hashlib.sha256(body.encode()).hexdigest()
            prev = db.execute(
                "SELECT revision, digest, body FROM cards WHERE card_key=? ORDER BY revision DESC LIMIT 1",
                (key,),
            ).fetchone()
            if prev and prev[1] == digest:
                continue
            reason = "terminal_observed"
            if prev:
                old = json.loads(prev[2])
                reason = (
                    "rubric_changed"
                    if old["rubric_version"] != card["rubric_version"]
                    else "terminal_updated"
                    if old["terminal_event_id"] != card["terminal_event_id"]
                    else "independent_review_updated"
                    if old.get("independent_review") != card.get("independent_review")
                    else "evidence_updated"
                )
            db.execute(
                "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    *identity,
                    card["task_id"],
                    prev[0] + 1 if prev else 1,
                    digest,
                    prev[1] if prev else None,
                    reason,
                    body,
                ),
            )


def _ledger_layout(state: Path):
    segments = list_event_segments(state, strict_io=True)
    archives = tuple((s.rel_path, s.size, s.mtime_ns) for s in segments if s.kind == "archive")
    active = current_offset(state / "events.jsonl")
    return segments, (archives, active.identity)


class SkillEvaluationObserver:
    def __init__(self, state: Path, root: Path, config: ZfConfig | None, *, interval: float = 5):
        self.state, self.root, self.config = state, root, config
        self.interval = interval
        self.history = EvaluationHistory(state, root)
        self.segment = ""
        self.offset = 0
        self.layout = None
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name="skill-evaluation", daemon=True)

    def poll(self) -> None:
        path = _database(self.state)
        # Serialize reading and publishing across consumers, including a catch-up after a restart.
        with locked_path(path, timeout_seconds=2):
            segments, layout = _ledger_layout(self.state)
            if self.layout != layout or (
                segments and self.segment == segments[-1].rel_path and segments[-1].size < self.offset
            ):
                self.history = EvaluationHistory(self.state, self.root)
                self.segment, self.offset = "", 0
            self.layout = layout
            if segments and (self.segment != segments[-1].rel_path or self.offset != segments[-1].size):
                for record in iter_event_records_from_cursor(
                    self.state,
                    segment=self.segment,
                    byte_offset=self.offset,
                    config=self.config,
                    strict_io=True,
                ):
                    self.history.consume(record.event)
                    self.segment, self.offset = record.raw_segment, record.raw_offset + record.raw_length
                # A concurrent rollover may have moved the active byte cursor into another file.
                if _ledger_layout(self.state)[1] != layout:
                    self.layout = None
                    return
            keys = self.history.dirty | self.history.ready_sidecars()
            if keys:
                self.history.dirty.update(keys)
                _publish(path, self.history.cards(keys))
                self.history.dirty.difference_update(keys)

    def start(self) -> None:
        try:
            self.thread.start()
        except RuntimeError:
            logger.exception("Could not start Skill evaluation observer")

    def stop(self) -> None:
        self.stopped.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)

    def _run(self) -> None:
        while not self.stopped.is_set():
            try:
                self.poll()
            except Exception:
                logger.exception("Skill evaluation projection will retry")
            self.stopped.wait(self.interval)
