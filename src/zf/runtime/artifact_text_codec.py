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


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON artifact has duplicate key {key!r}")
        result[key] = value
    return result


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
    try:
        return json.loads(text, parse_float=_JsonDecimal, parse_int=_integer_token,
                          parse_constant=_reject_constant,
                          object_pairs_hook=_object_without_duplicate_keys)
    except json.JSONDecodeError as exc:
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
