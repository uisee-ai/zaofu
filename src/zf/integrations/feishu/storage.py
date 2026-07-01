"""Small durable stores for the Feishu bridge."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from zf.core.state.locks import locked_path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class IdempotencyStore:
    """Append-only idempotency key store for webhook/message replay."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def check_and_record(
        self,
        key: str,
        *,
        command: str = "",
        user_id: str = "",
        chat_id: str = "",
        source: str = "",
    ) -> bool:
        """Return True when key was already recorded; otherwise record it."""
        with locked_path(self.path):
            if self._contains_unlocked(key):
                return True
            self.path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "key": key,
                "command": command,
                "user_id": user_id,
                "chat_id": chat_id,
                "source": source,
                "seen_at": _now_iso(),
            }
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return False

    def _contains_unlocked(self, key: str) -> bool:
        if not self.path.exists():
            return False
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("key") == key:
                return True
        return False


class OffsetStore:
    """JSON offset store for event-log push cursors."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> int:
        if not self.path.exists():
            return 0
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return 0
        try:
            return int(data.get("offset") or 0)
        except (TypeError, ValueError):
            return 0

    def write(self, offset: int) -> None:
        with locked_path(self.path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {"offset": int(offset), "updated_at": _now_iso()},
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
