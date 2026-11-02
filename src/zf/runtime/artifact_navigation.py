"""Syntactic navigation over one authorized selection, never semantic authority."""
from __future__ import annotations

import hashlib
import json
import re
from itertools import islice
from typing import Any

NAVIGATION_SCHEMA = "artifact-navigation.v1"
MAX_ENTRIES = 512
MAX_NODES = 20000
RELATION_FIELDS = {
    "task_id": "task", "task_ids": "task",
    "requirement_id": "requirement", "requirement_ids": "requirement",
    "acceptance_id": "acceptance", "acceptance_ids": "acceptance",
    "blocked_by": "dependency", "depends_on": "dependency",
    "source_ref": "source", "source_refs": "source",
    "image_ref": "image", "image_refs": "image",
}


def build_navigation(content: str) -> dict[str, Any]:
    digest = hashlib.sha256(content.encode()).hexdigest()
    entries: list[dict[str, Any]] = []
    complete = True
    try:
        body = json.loads(content)
    except (ValueError, RecursionError):
        body = None
    if isinstance(body, (dict, list)):
        stack = [(body, "")]
        visited = 0
        while stack:
            value, pointer = stack.pop()
            visited += 1
            if visited > MAX_NODES or len(entries) >= MAX_ENTRIES:
                complete = False
                break
            if isinstance(value, dict):
                remaining = max(0, MAX_NODES - visited - len(stack))
                if len(value) > remaining:
                    complete = False
                for key, child in reversed(list(islice(value.items(), remaining))):
                    path = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
                    if len(path) > 512:
                        complete = False
                        continue
                    if key in RELATION_FIELDS:
                        values = child if isinstance(child, list) else [child]
                        capacity = MAX_ENTRIES - len(entries)
                        if len(values) > capacity:
                            complete = False
                        for index, identifier in enumerate(islice(values, capacity)):
                            if not isinstance(identifier, str) or not identifier.strip():
                                continue
                            if len(identifier) > 256 or len(path) > 512 or len(entries) >= MAX_ENTRIES:
                                complete = False
                                continue
                            entries.append({"kind": "relation", "relation": RELATION_FIELDS[key],
                                "id": identifier, "field_pointer": path + (f"/{index}" if isinstance(child, list) else "")})
                    stack.append((child, path))
            elif isinstance(value, list):
                remaining = max(0, MAX_NODES - visited - len(stack))
                if len(value) > remaining:
                    complete = False
                stack.extend((child, f"{pointer}/{index}")
                             for index, child in reversed(list(enumerate(islice(value, remaining)))))
    else:
        entries, complete = _headings(content)
    return {"schema_version": NAVIGATION_SCHEMA, "selected_sha256": digest,
            "complete": complete, "entries": entries,
            "authority": "navigation_only", "extractor": "declared-fields-and-markdown-headings.v1"}


def _headings(content: str) -> tuple[list[dict[str, Any]], bool]:
    entries: list[dict[str, Any]] = []
    offset = 0
    fence = ""
    previous: tuple[str, int, int] | None = None
    complete = True
    for number, line in enumerate(content.splitlines(keepends=True), 1):
        stripped = line.rstrip("\r\n")
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", stripped)
        if marker:
            token = marker[1]
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = ""
            previous = None
        elif not fence:
            atx = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$", stripped)
            underline = re.fullmatch(r" {0,3}(=+|-+)\s*", stripped)
            heading = None
            if atx:
                heading = (atx[2], len(atx[1]), offset, number)
            elif underline and previous and previous[0].strip():
                heading = (previous[0].strip(), 1 if underline[1][0] == "=" else 2, previous[1], previous[2])
            if heading:
                if len(entries) >= MAX_ENTRIES or len(heading[0]) > 256:
                    complete = False
                else:
                    entries.append({"kind": "section", "title": heading[0], "level": heading[1],
                                    "page_start": heading[2], "line": heading[3], "section_end": len(content)})
            previous = (stripped, offset, number) if not heading else None
        offset += len(line)
    for index, entry in enumerate(entries):
        for following in entries[index + 1:]:
            if following["level"] <= entry["level"]:
                entry["section_end"] = following["page_start"]
                break
    return entries, complete


def matching_navigation(navigation: Any, *, query: str = "", relation_id: str = "") -> list[dict[str, Any]]:
    if not isinstance(navigation, dict) or navigation.get("schema_version") != NAVIGATION_SCHEMA:
        return []
    return [dict(entry) for entry in navigation.get("entries", [])
            if isinstance(entry, dict)
            and (not relation_id or entry.get("id") == relation_id)
            and (not query or query.casefold() in str(entry.get("id") or entry.get("title") or "").casefold())]
