"""Redacted diagnostics material for trace debugging."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from zf.core.security.redaction import redact_obj
from zf.core.state.locks import locked_path


_SAFE_TRACE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class DiagnosticsCollector:
    def __init__(self, state_dir: Path, trace_id: str) -> None:
        self.state_dir = state_dir
        self.trace_id = trace_id
        self.path = state_dir / "diagnostics" / _safe_trace_id(trace_id)

    def write_orchestration(self, record: dict[str, Any]) -> Path:
        return self.append("orchestration", record)

    def write_error(self, record: dict[str, Any]) -> Path:
        return self.append("errors", record)

    def append(self, stream: str, record: dict[str, Any]) -> Path:
        if stream not in {"orchestration", "errors"}:
            raise ValueError(f"unknown diagnostics stream: {stream}")
        self.path.mkdir(parents=True, exist_ok=True)
        out = self.path / f"{stream}.jsonl"
        payload = {
            "trace_id": self.trace_id,
            **redact_obj(record),
        }
        with locked_path(out):
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return out


def _safe_trace_id(trace_id: str) -> str:
    safe = _SAFE_TRACE_RE.sub("_", trace_id).strip("._")
    return safe or "unknown"
