"""Consumer cursor 投影 — last_seen_event 续读位。

`runtime_consumers.json` 记录每个 consumer(orchestrator/supervisor/
agent-view/channel/...)读到事件流的哪里。**纯投影,不替代
events.jsonl**(I1):cursor 是 consumer 私有进度,不是事实——删除文件
= 各 consumer 从各自策略的起点重读(K4 分类:可安全丢弃型投影,
"重建"语义即归零重读,本模块以 reset 显式提供)。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ConsumerCursor:
    consumer_id: str
    consumer_kind: str = ""
    last_seen_event_id: str = ""
    last_seen_ts: str = ""
    events_seen: int = 0
    updated_at: float = field(default_factory=time.time)


class ConsumerCursorStore:
    def __init__(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / "runtime_consumers.json"

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, consumer_id: str) -> ConsumerCursor | None:
        raw = self._load().get(consumer_id)
        if not isinstance(raw, dict):
            return None
        known = {f for f in ConsumerCursor.__dataclass_fields__}
        return ConsumerCursor(**{k: v for k, v in raw.items() if k in known})

    def advance(
        self,
        consumer_id: str,
        *,
        consumer_kind: str = "",
        event_id: str,
        event_ts: str = "",
        seen_delta: int = 1,
    ) -> ConsumerCursor:
        data = self._load()
        prev = data.get(consumer_id) if isinstance(data.get(consumer_id), dict) else {}
        cursor = ConsumerCursor(
            consumer_id=consumer_id,
            consumer_kind=consumer_kind or str(prev.get("consumer_kind", "")),
            last_seen_event_id=event_id,
            last_seen_ts=event_ts,
            events_seen=int(prev.get("events_seen", 0) or 0) + seen_delta,
            updated_at=time.time(),
        )
        data[consumer_id] = asdict(cursor)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)
        return cursor

    def reset(self, consumer_id: str | None = None) -> None:
        """可丢弃投影的显式'重建'语义:归零重读。"""
        if consumer_id is None:
            if self.path.exists():
                self.path.unlink()
            return
        data = self._load()
        data.pop(consumer_id, None)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
