"""Accumulate Codex usage and native turn identity without reading or emitting events."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from zf.runtime.codex_turn_sample import CodexTurnSample, CodexUsageTurn

if TYPE_CHECKING:
    from zf.runtime.backend_session_reader import UsageReport


def valid_record(row: object) -> bool:
    if not isinstance(row, dict) or not isinstance(row.get("type"), (str, type(None))):
        return False
    if row.get("type") not in ("session_meta", "turn_context", "event_msg"):
        return True
    payload = row.get("payload")
    return isinstance(payload, dict) and (
        row["type"] != "event_msg" or isinstance(payload.get("type"), (str, type(None))))


class CodexUsageSnapshot:
    def __init__(self, session: str = "", *, scoped: bool = False):
        self.tracker = CodexTurnSample(session) if scoped else None
        self.turn = CodexUsageTurn(session) if session else None
        self.session_id = ""
        self.started_at = ""
        self.provider = "openai"
        self.model = ""
        self.stamp = ""
        self.info: dict | None = None
        self.rate_limits: dict = {}

    def incomplete(self) -> None:
        if self.tracker:
            self.tracker.boundary.valid = False
        if self.turn:
            self.turn.sample = ""

    def observe(self, row: dict, *, complete: bool) -> bool:
        """Keep the most recent provider facts; return whether this is a usage row."""
        if complete:
            if self.tracker:
                self.tracker.consume(row)
            if self.turn:
                self.turn.consume(row)
        else:
            self.incomplete()
        kind = row.get("type")
        payload = row.get("payload") or {}
        if kind == "session_meta":
            self.session_id = str(payload.get("id") or self.session_id)
            self.started_at = str(payload.get("timestamp") or row.get("timestamp") or "")
            self.provider = str(payload.get("model_provider") or self.provider)
        elif kind == "turn_context":
            self.model = str(payload.get("model") or self.model)
        elif kind == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            if isinstance(info, dict):
                self.info = info
                self.stamp = row.get("timestamp", "")
                limits = payload.get("rate_limits")
                self.rate_limits = dict(limits) if isinstance(limits, dict) else {}
                return True
        return False

    def valid_historical_counters(self) -> bool:
        """An accounting fallback for malformed data cannot establish historical proof."""
        if not self.info or not isinstance(self.info.get("last_token_usage"), dict):
            return False
        for name in ("last_token_usage", "total_token_usage"):
            counts = self.info.get(name)
            if counts is None:
                continue
            if not isinstance(counts, dict) or any(
                type(value) is not int or value < 0
                for key, value in counts.items() if key.endswith("_tokens")
            ):
                return False
        window = self.info.get("model_context_window", 0)
        return type(window) is int and window >= 0

    def report(self, path: Path, *, fallback_window: int | None = None) -> UsageReport | None:
        from zf.runtime.backend_session_reader import UsageReport

        if self.info is None:
            return None
        last = self.info.get("last_token_usage") or {}
        total = self.info.get("total_token_usage")
        if not isinstance(last, dict):
            return None
        try:
            effective = int(last.get("input_tokens", 0))
            output = int(last.get("output_tokens", 0))
            window = int(self.info.get("model_context_window", 0)) or fallback_window or 258_400
        except (OverflowError, ValueError, TypeError):
            return None
        model = str(self.info.get("model") or self.model)
        cumulative = isinstance(total, dict)
        series = (f"codex:{self.session_id}" if self.session_id else
                  "codex-path:" + hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:24])
        return UsageReport(
            effective_input_tokens=effective,
            output_tokens=output,
            model_context_window=window,
            ratio=effective / window if window > 0 else 0.0,
            timestamp=self.stamp,
            raw=dict(total if cumulative else last),
            model=model,
            usage_semantics="cumulative" if cumulative else "incremental",
            series_started_at=self.started_at,
            provider_session_id=self.session_id,
            provider_usage_turn_id=self.turn.sample if self.tracker else "",
            registered_first_turn=self.turn.first_sample() if self.turn else None,
            native_turn_sample=self.tracker.receipt(self.stamp, model) if self.tracker else {},
            usage_series_id=series,
            provider=self.provider,
            accounting_mode="subscription" if str(self.rate_limits.get("plan_type") or "").strip() else "unknown",
            input_semantics="combined_includes_cache",
        )
