"""Collect public Codex text by native item; completion replaces only that item."""

from dataclasses import dataclass
from typing import Any


@dataclass
class PublicTextItem:
    item_id: str
    turn_id: str = ""
    phase: str = ""
    text: str = ""
    completed: bool = False


class CodexPublicText:
    def __init__(self) -> None:
        self.items: dict[str, PublicTextItem] = {}
        self._visible_items: set[str] = set()

    @property
    def reply(self) -> str:
        return "\n\n".join(item.text for item in self.items.values()
                           if item.phase != "commentary" and item.text)

    def observe(self, method: str, params: dict[str, Any]) -> tuple[str, str] | None:
        native = params.get("item") or {}
        delta = method.endswith("item/agentMessage/delta")
        if not delta and not (isinstance(native, dict) and native.get("type") == "agentMessage"):
            return None
        item_id = str(params.get("itemId") or native.get("id") or "anonymous")
        item = self.items.setdefault(item_id, PublicTextItem(item_id))
        item.turn_id = str(params.get("turnId") or item.turn_id)
        item.phase = str(native.get("phase") or params.get("phase") or item.phase)
        old = item.text
        if delta:
            if item.completed:
                return None  # Late deltas cannot reopen a completed native item.
            item.text += str(params.get("delta") or "")
        elif method.endswith("item/completed"):
            if item.completed:
                return None
            item.text = str(native.get("text") or item.text)
            item.completed = True
        else:
            return None
        # A non-prefix authoritative correction is retained in the final snapshot;
        # an append-only live transport cannot retract previously displayed text.
        tail = item.text[len(old):] if item.text.startswith(old) else ""
        if not tail:
            return None
        kind = "status" if item.phase == "commentary" else "text"
        if kind == "text" and item_id not in self._visible_items:
            if self._visible_items:
                tail = "\n\n" + tail
            self._visible_items.add(item_id)
        return kind, tail
