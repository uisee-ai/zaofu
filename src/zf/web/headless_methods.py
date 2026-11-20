"""Small built-in method body supplied to isolated Kanban provider turns."""
from __future__ import annotations

import hashlib
import json

from zf.core.skills.provenance import resolve_builtin_skill_source


def document_method_context() -> str:
    name = "zf-document-reading"
    source = resolve_builtin_skill_source(name)
    if source is None:
        raise ValueError(f"built-in document method unavailable: {name}")
    with source.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError(f"built-in document method exceeds 64 KiB: {name}")
    return (
        "When authoring or reviewing a PRD/Plan, apply this supplied method. "
        "It grants no additional permissions or actions. Otherwise it is not applicable.\n"
        + json.dumps({"logical_ref": f"skills/{name}/SKILL.md",
                      "sha256": hashlib.sha256(raw).hexdigest(),
                      "content": raw.decode("utf-8")}, ensure_ascii=False)
    )
