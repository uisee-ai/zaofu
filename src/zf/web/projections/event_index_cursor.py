"""Validate byte cursors without reading historical event bodies."""

from pathlib import Path


def active_identity(state_dir: Path) -> dict[str, int]:
    try:
        stat = (state_dir / "events.jsonl").stat()
        return {"dev": stat.st_dev, "ino": stat.st_ino, "size": stat.st_size, "mtime": stat.st_mtime_ns}
    except FileNotFoundError:
        return {}


def can_resume(meta: dict, identity: dict) -> bool:
    if not meta.get("byte_cursor_version"):
        return False
    if not identity:
        return not meta.get("active_identity")
    import json
    try:
        old = json.loads(meta.get("active_identity") or "{}")
        if not old:
            return False
        return (
            old["dev"] == identity["dev"] and old["ino"] == identity["ino"]
            and identity["size"] >= old["size"]
            and (meta.get("tail_segment") != "events.jsonl" or identity["size"] >= int(meta.get("tail_offset") or 0))
            and (identity["size"] > old["size"] or identity["mtime"] == old["mtime"])
        )
    except (KeyError, ValueError, TypeError):
        return False
