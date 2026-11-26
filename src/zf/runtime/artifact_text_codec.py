"""Decode artifact text without silently changing JSON numbers or object keys."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import math
from typing import Any


@dataclass(frozen=True)
class _JsonDecimal:
    token: str


@dataclass(frozen=True)
class _DuplicateKey:
    key: str
    first: Any
    second: Any


def _same_json_value(first: Any, second: Any) -> bool:
    if type(first) in (int, _JsonDecimal) and type(second) in (int, _JsonDecimal):
        return Decimal(first.token if isinstance(first, _JsonDecimal) else first) == Decimal(
            second.token if isinstance(second, _JsonDecimal) else second)
    if type(first) is not type(second):
        return False
    if isinstance(first, dict):
        return first.keys() == second.keys() and all(_same_json_value(v, second[k]) for k, v in first.items())
    if isinstance(first, list):
        return len(first) == len(second) and all(_same_json_value(a, b) for a, b in zip(first, second))
    return first == second


def _locate_duplicate(value: Any, path: str) -> None:
    if isinstance(value, _DuplicateKey):
        key = value.key
        location = f"{path}.{key}" if key.isidentifier() else f"{path}[{json.dumps(key)}]"
        _locate_duplicate(value.first, location)
        _locate_duplicate(value.second, location)
        relationship = "identical values" if _same_json_value(value.first, value.second) else "conflicting values"
        raise ValueError(f"JSON artifact has duplicate key {key!r} at {location} ({relationship})")
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if key.isidentifier() else f"{path}[{json.dumps(key)}]"
            _locate_duplicate(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _locate_duplicate(item, f"{path}[{index}]")


def _integer_token(value: str) -> int | _JsonDecimal:
    return _JsonDecimal(value) if value == "-0" else int(value)


def _reject_constant(value: str) -> None:
    raise ValueError(f"JSON artifact contains non-finite number {value}")


def decode_artifact_text(raw: bytes, json_path: str) -> Any:
    """Parse JSON exactly, or retain UTF-8 text when the root is non-JSON."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("artifact is not valid UTF-8 text") from exc
    duplicate: _DuplicateKey | None = None

    def object_without_duplicates(pairs: list[tuple[str, Any]]) -> Any:
        nonlocal duplicate
        obj: dict[str, Any] = {}
        for key, value in pairs:
            if key in obj:
                duplicate = _DuplicateKey(key, obj[key], value)
                return duplicate
            obj[key] = value
        return obj

    try:
        parsed = json.loads(text, parse_float=_JsonDecimal, parse_int=_integer_token,
                            parse_constant=_reject_constant, object_pairs_hook=object_without_duplicates)
        if duplicate:
            _locate_duplicate(parsed, "$")
        return parsed
    except json.JSONDecodeError as exc:
        if duplicate:
            raise ValueError(f"JSON artifact has duplicate key {duplicate.key!r} and invalid JSON syntax") from exc
        if json_path != "$":
            raise ValueError("json_path requires a JSON artifact") from exc
        return text


def _number_text(number: _JsonDecimal) -> str:
    # Preserve the old rendering for ordinary values, to keep existing
    # immutable selection digests usable. Retain the original numeric token
    # whenever binary float conversion would change its decimal value.
    if number.token == "-0":
        return "-0"
    converted = float(number.token)
    if math.isfinite(converted) and Decimal(str(converted)) == Decimal(number.token):
        return json.dumps(converted)
    return number.token


def _contains_decimal(value: Any) -> bool:
    if isinstance(value, _JsonDecimal):
        return True
    if isinstance(value, dict):
        return any(_contains_decimal(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_decimal(item) for item in value)
    return False


def _render_json(value: Any, depth: int) -> str:
    if isinstance(value, _JsonDecimal):
        return _number_text(value)
    if isinstance(value, dict) and value:
        indent = "  " * (depth + 1)
        body = [f"{indent}{json.dumps(key, ensure_ascii=False)}: {_render_json(item, depth + 1)}"
                for key, item in value.items()]
        return "{\n" + ",\n".join(body) + "\n" + "  " * depth + "}"
    if isinstance(value, list) and value:
        indent = "  " * (depth + 1)
        body = [f"{indent}{_render_json(item, depth + 1)}" for item in value]
        return "[\n" + ",\n".join(body) + "\n" + "  " * depth + "]"
    return json.dumps(value, ensure_ascii=False, default=str)


def encode_selected(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not _contains_decimal(value):
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return _render_json(value, 0)
