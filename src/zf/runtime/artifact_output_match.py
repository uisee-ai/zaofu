"""Strict normalization of observable tool output, never arbitrary JSON fields."""
from __future__ import annotations

import json


def observable_outputs(output: str) -> list[str]:
    texts = [output]

    def peel(value: object, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, list):
            for item in value:
                peel(item, depth + 1)
        elif isinstance(value, dict):
            if (value.get("chunk_id") and "wall_time_seconds" in value
                    and isinstance(value.get("output"), str)):
                texts.append(value["output"])
            elif value.get("status") == "fulfilled" and "value" in value:
                peel(value["value"], depth + 1)
            elif "result" in value and set(value) <= {"i", "result"}:
                peel(value["result"], depth + 1)

    for candidate in [output, *output.splitlines()]:
        try:
            peel(json.loads(candidate))
        except ValueError:
            continue
    return list(dict.fromkeys(texts))


def complete_output_observed(body: str, output: str) -> bool:
    if "\n" + body + "\n" in "\n" + output + "\n":
        return True
    try:
        expected = json.loads(body)
    except ValueError:
        return False
    if not (isinstance(expected, dict)
            and expected.get("schema_version") == "required-artifact-page.v2"
            and expected.get("delivery_id") and expected.get("attempt_id")):
        return False
    # Permit formatting/key-order changes only. Stripping metadata, alias
    # identities, a fragment, or a cursor is not a complete delivery witness.
    for candidate in [output, *output.splitlines()]:
        try:
            if json.loads(candidate) == expected:
                return True
        except ValueError:
            continue
    return False
