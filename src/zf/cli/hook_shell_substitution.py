"""Bounded lexical extraction of active shell command contexts; never executes."""

from __future__ import annotations

import re


MAX_SUBSTITUTION_NESTING = 32
MAX_SUBSTITUTION_CONTEXTS = 64


def _backtick_end(text: str, start: int) -> int | None:
    index = start
    while index < len(text):
        if text[index] == "\\":
            index += 2
        elif text[index] == "`":
            return index
        else:
            index += 1
    return None


def _paren_end(text: str, start: int) -> int | None:
    """Match one active $(...) / <(...) without flattening nested quote state."""
    frames: list[tuple[int, str]] = []
    depth, quote, index, word_start = 1, "", start, True
    while index < len(text):
        char = text[index]
        if quote == "'":
            if char == "'":
                quote = ""
            index += 1
            continue
        if char == "\\":
            if text[index:index + 2] != "\\\n":
                word_start = False
            index += 2
            continue
        if char == "#" and not quote and word_start:
            index = text.find("\n", index)
            if index < 0:
                return None
            continue
        if char == "`":
            end = _backtick_end(text, index + 1)
            if end is None:
                return None
            index = end + 1
            word_start = False
            continue
        if text.startswith("$(", index) or (
            not quote and text[index:index + 2] in {"<(", ">("}
        ):
            if len(frames) + 1 >= MAX_SUBSTITUTION_NESTING:
                return None
            frames.append((depth, quote))
            depth, quote, index, word_start = 1, "", index + 2, True
            continue
        if char == '"':
            quote = "" if quote == '"' else '"'
            word_start = False
        elif char == "'" and not quote:
            quote = "'"
            word_start = False
        elif not quote and char == "(":
            depth += 1
            word_start = True
            if depth > MAX_SUBSTITUTION_NESTING:
                return None
        elif not quote and char == ")":
            depth -= 1
            word_start = True
            if depth == 0:
                if not frames:
                    return index
                depth, quote = frames.pop()
                word_start = False
        elif not quote:
            word_start = char.isspace() or char in ";|&<>"
        index += 1
    return None


def split_active_substitutions(text: str) -> tuple[str, list[tuple[str, bool]], bool]:
    """Return masked outer text, child (body, is-command) contexts, completeness.

    Match the existing hook substitution detector's quote/escape discipline:
    single quotes and escaped sigils are data; double quotes still activate
    dollar/backtick substitution. Arithmetic text is not a command, but its
    nested substitutions must still be scanned. Incomplete/over-deep input
    never establishes a copy exemption.
    """
    chunks: list[str] = []
    bodies: list[tuple[str, bool]] = []
    quote, index, last, word_start = "", 0, 0, True
    while index < len(text):
        char = text[index]
        if quote == "'":
            if char == "'":
                quote = ""
            index += 1
            continue
        if char == "\\":
            if text[index:index + 2] != "\\\n":
                word_start = False
            index += 2
            continue
        if char == "#" and not quote and word_start:
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            chunks.extend((text[last:index], " "))
            index = last = end
            continue
        prefix = text[index:index + 2]
        if char == "`" or prefix == "$(" or (not quote and prefix in {"<(", ">("}):
            backtick = char == "`"
            start = index + (1 if backtick else 2)
            end = _backtick_end(text, start) if backtick else _paren_end(text, start)
            if end is None:
                return text, bodies, False
            body = text[start:end]
            if backtick:
                body = re.sub(r"\\([$`\\])", r"\1", body)
            bodies.append((body, not text.startswith("$((", index)))
            if len(bodies) >= MAX_SUBSTITUTION_CONTEXTS:
                return text, bodies, False
            chunks.extend((text[last:index], "__zf_substitution__"))
            index = last = end + 1
            word_start = False
            continue
        if char == "'" and not quote:
            quote = "'"
            word_start = False
        elif char == '"':
            quote = "" if quote == '"' else '"'
            word_start = False
        elif not quote:
            word_start = char.isspace() or char in ";|&<>()"
        index += 1
    chunks.append(text[last:])
    return "".join(chunks), bodies, True
