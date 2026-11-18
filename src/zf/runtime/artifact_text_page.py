"""A length-delimited view of artifact pages without escaped body text.

The exact rendered output remains the delivery witness. This is presentation,
not a new source, read receipt, or authorization contract.
"""
from __future__ import annotations

import json
from typing import Any

PREFIX = "ZF_ARTIFACT_TEXT_PAGE_V1\n"
END = "\nZF_ARTIFACT_TEXT_PAGE_END"


def render_text_page(page: dict[str, Any]) -> str:
    metadata = {**page, "items": []}
    chunks = []
    for index, item in enumerate(page["items"]):
        content = item["content"]
        if not isinstance(content, str):
            raise ValueError("artifact page content must be text")
        metadata["items"].append({**{k: v for k, v in item.items() if k != "content"},
                                  "content_chars": len(content)})
        chunks.append(f"\n--- artifact {index + 1} ---\n{content}")
    return PREFIX + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + "".join(chunks) + END


def parse_artifact_page(output: str) -> dict[str, Any]:
    if not output.startswith(PREFIX):
        return json.loads(output)
    header, separator, _ = output[len(PREFIX):].partition("\n")
    if not separator:
        raise ValueError("incomplete artifact text header")
    page = json.loads(header)
    offset = len(PREFIX) + len(header)
    for index, item in enumerate(page["items"]):
        marker = f"\n--- artifact {index + 1} ---\n"
        size = item.pop("content_chars")
        if type(size) is not int or size < 0 or not output.startswith(marker, offset):
            raise ValueError("invalid artifact text boundary")
        offset += len(marker)
        item["content"] = output[offset:offset + size]
        if len(item["content"]) != size:
            raise ValueError("incomplete artifact text content")
        offset += size
    if output[offset:] != END:
        raise ValueError("incomplete artifact text trailer")
    return page
