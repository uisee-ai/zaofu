"""Separate user-visible Channel prose from trailing machine contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable


CHANNEL_CONTRACT_MARKER = "<!-- zf:channel-contract -->"
CHANNEL_CONTRACT_KEYS = (
    "channel_contribution",
    "channel_synthesis",
    "channel_cross_review",
    "channel_consensus_review",
    "channel_question_dedup",
)

_WAIT = "wait"
_VISIBLE = "visible"
_HIDE = "hide"


@dataclass
class ChannelReplyStreamFilter:
    """Filter one provider text stream without parsing incomplete JSON.

    The canonical response contract is a complete Markdown reply followed by a
    marker and one JSON object. Only the Markdown belongs on the ephemeral
    live bus. A small line-start probe also fail-closes legacy replies that
    omit the marker and start a known Channel contract directly.
    """

    _at_line_start: bool = True
    _hidden: bool = False
    _probe: str = ""

    @property
    def contract_hidden(self) -> bool:
        return self._hidden

    def feed(self, content: str) -> str:
        if self._hidden or not content:
            return ""
        visible: list[str] = []
        for char in str(content):
            if self._hidden:
                break
            if self._probe:
                self._probe += char
                decision = _classify_probe(self._probe)
                if decision == _HIDE:
                    self._probe = ""
                    self._hidden = True
                elif decision == _VISIBLE:
                    visible.append(self._probe)
                    self._at_line_start = self._probe.endswith("\n")
                    self._probe = ""
                continue

            if self._at_line_start and char in " \t\r{`<":
                self._probe = char
                continue
            # The canonical marker is required on its own line, but probing a
            # '<' elsewhere cheaply prevents a provider formatting slip from
            # exposing it after prose on the same line.
            if char == "<":
                self._probe = char
                continue
            visible.append(char)
            self._at_line_start = char == "\n"
        return "".join(visible)

    def finish(self) -> str:
        """Flush a non-contract tail when the provider reaches terminal state."""

        if self._hidden or not self._probe:
            self._probe = ""
            return ""
        probe = self._probe
        self._probe = ""
        if _classify_probe(probe) == _HIDE or _likely_contract_prefix(probe):
            self._hidden = True
            return ""
        return probe


class ChannelVisibleMessageEmitter:
    """Adapt provider messages so only visible text reaches a live emitter."""

    def __init__(self, emit: Callable[[Any], None]) -> None:
        self._emit = emit
        self._filter = ChannelReplyStreamFilter()
        self._last_text_message: Any | None = None
        self._visible = ""
        self._continuing = False

    def begin_continuation(self) -> None:
        # A new turn may continue an unfinished JSON string. Buffer its text
        # until the merged canonical reply establishes the public boundary.
        self._continuing = True

    def emit(self, message: Any) -> None:
        if str(getattr(message, "type", "") or "") != "text":
            self._emit(message)
            return
        self._last_text_message = message
        if self._continuing:
            return
        content = self._filter.feed(str(getattr(message, "content", "") or ""))
        if content:
            self._visible += content
            self._emit(replace(message, content=content))

    def finish(self, full_reply: str | None = None) -> None:
        tail = self._filter.finish()
        if tail and self._last_text_message is not None:
            self._visible += tail
            self._emit(replace(self._last_text_message, content=tail))
        if full_reply is not None and self._last_text_message is not None:
            final_filter = ChannelReplyStreamFilter()
            final = final_filter.feed(full_reply) + final_filter.finish()
            if final.startswith(self._visible):
                suffix = final[len(self._visible):]
                if suffix:
                    self._emit(replace(self._last_text_message, content=suffix))
                    self._visible = final


def _classify_probe(probe: str) -> str:
    stripped = probe.lstrip(" \t\r")
    if not stripped:
        return _WAIT

    marker = CHANNEL_CONTRACT_MARKER.lower()
    lowered = stripped.lower()
    if marker.startswith(lowered):
        return _HIDE if lowered == marker else _WAIT
    if lowered.startswith(marker):
        return _HIDE
    if stripped.startswith("<"):
        return _VISIBLE
    if stripped.startswith("`"):
        return _classify_fenced_probe(stripped)
    if stripped.startswith("{"):
        return _classify_json_probe(stripped)
    return _VISIBLE


def _classify_fenced_probe(probe: str) -> str:
    if len(probe) < 3:
        return _WAIT if set(probe) == {"`"} else _VISIBLE
    if not probe.startswith("```"):
        return _VISIBLE
    if "\n" not in probe:
        header = probe.lower().rstrip("\r")
        return _WAIT if "```json".startswith(header) or header == "```" else _VISIBLE
    header, body = probe.split("\n", 1)
    language = header[3:].strip().lower()
    if language not in {"", "json"}:
        return _VISIBLE
    body = body.lstrip(" \t\r\n")
    if not body:
        return _WAIT
    decision = _classify_json_probe(body)
    return decision if decision != _VISIBLE else _VISIBLE


def _classify_json_probe(probe: str) -> str:
    if not probe.startswith("{"):
        return _VISIBLE
    remainder = probe[1:].lstrip(" \t\r\n")
    if not remainder:
        return _WAIT
    if not remainder.startswith('"'):
        return _VISIBLE
    key_tail = remainder[1:]
    quote = key_tail.find('"')
    if quote < 0:
        partial_key = key_tail
        return (
            _WAIT
            if any(key.startswith(partial_key) for key in CHANNEL_CONTRACT_KEYS)
            else _VISIBLE
        )
    key = key_tail[:quote]
    return _HIDE if key in CHANNEL_CONTRACT_KEYS else _VISIBLE


def _likely_contract_prefix(probe: str) -> bool:
    stripped = probe.lstrip(" \t\r")
    lowered = stripped.lower()
    marker = CHANNEL_CONTRACT_MARKER.lower()
    if len(lowered) >= 4 and marker.startswith(lowered):
        return True
    if lowered.startswith("```json"):
        body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        return not body or _likely_contract_prefix(body)
    if not stripped.startswith("{"):
        return False
    remainder = stripped[1:].lstrip(" \t\r\n")
    if not remainder:
        return True
    if not remainder.startswith('"'):
        return False
    partial_key = remainder[1:].split('"', 1)[0]
    return any(key.startswith(partial_key) for key in CHANNEL_CONTRACT_KEYS)


__all__ = [
    "CHANNEL_CONTRACT_KEYS",
    "CHANNEL_CONTRACT_MARKER",
    "ChannelReplyStreamFilter",
    "ChannelVisibleMessageEmitter",
]
