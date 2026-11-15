"""Read-only, digest-bound chapter ranges; indexes are disposable projections."""
from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

MAX_BYTES = 8 * 1024 * 1024  # Same source limit as the existing full preview.
PAGE_BYTES = 64 * 1024
INDEX_VERSION = 1


class PlanDocumentError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def document_source(state_dir: Path, events: list[Any], *, plan_id: str, name: str,
                    sha256: str, project_root: Path | None) -> Path:
    from zf.runtime.operator_plan_preview import _plan_events
    from zf.runtime.plan_preview_sources import plan_authority_refs, plan_source_payload
    from zf.runtime.plan_preview_artifacts import (
        preview_artifact_path, preview_package_artifacts, read_plan_preview_bundle,
    )

    requested, resolved = _plan_events(events, plan_id)
    if requested is None and resolved is None:
        raise PlanDocumentError(404, "plan not found")
    payload, _ = plan_source_payload(state_dir, events, plan_id=plan_id,
        source_event=requested or resolved, project_root=project_root)
    ports, error = preview_package_artifacts(payload, state_dir=state_dir, project_root=project_root)
    bundle, bundle_error = read_plan_preview_bundle(payload, state_dir=state_dir, project_root=project_root)
    if error or bundle_error:
        raise PlanDocumentError(409, "plan source binding changed or is unavailable")
    matches = [row for row in ports if row["name"] == name]
    if len(matches) > 1:
        raise PlanDocumentError(409, "ambiguous plan source binding")
    item = matches[0] if matches else None
    if name == "accepted_plan":
        # Canonical Plan recovery also supports legacy synth evidence and logical
        # fanout paths. Keep that compatibility in its existing single owner.
        from zf.runtime.operator_plan_preview import build_plan_preview

        preview = build_plan_preview(state_dir, events, plan_id=plan_id, project_root=project_root)
        item = next((row for row in preview.get("artifacts", []) if row["name"] == name), None)
    elif item is None:
        authority = plan_authority_refs(state_dir, payload=payload, artifacts=ports,
            preview_bundle=bundle, project_root=project_root)
        for index, ref in enumerate(authority, 1):
            if name == f"requirement_{index}_{Path(ref).name}":
                item = {"ref": ref, "sha256": ""}
                break
    if item is None:
        raise PlanDocumentError(404, "source is not bound to this plan")
    if item.get("sha256") and item["sha256"] != sha256:
        raise PlanDocumentError(409, "source version changed; reload the preview")
    try:
        path = preview_artifact_path(item["ref"], state_dir=state_dir, project_root=project_root)
    except ValueError as exc:
        raise PlanDocumentError(403, "source is outside the current project") from exc
    if path.suffix.lower() not in {".md", ".markdown"}:
        raise PlanDocumentError(415, "chapter reading requires Markdown")
    if not path.is_file():
        raise PlanDocumentError(404, "bound source is unavailable")
    return path


def _stamp(stat: os.stat_result) -> list[int]:
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def _check_source(path: Path, stamp: list[int]) -> None:
    if _stamp(path.stat()) != stamp:
        raise PlanDocumentError(409, "source changed during reading; reload the preview")


def _build_index(path: Path, target: Path, sha256: str, stamp: list[int]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".document-", suffix=".sqlite", dir=target.parent)
    os.close(descriptor)
    db = sqlite3.connect(temporary)
    try:
        db.executescript("""CREATE TABLE sections(id INTEGER PRIMARY KEY, title TEXT, anchor TEXT,
            level INTEGER, start INTEGER, end INTEGER, page_count INTEGER DEFAULT 0);
            CREATE TABLE pages(section INTEGER, page INTEGER, start INTEGER, end INTEGER,
                PRIMARY KEY(section,page)); CREATE TABLE metadata(body TEXT);""")
        db.execute("INSERT INTO sections VALUES(0, '文档开头', 'document-start', 1, 0, 0, 0)")
        offset, section, heading_count = 0, 0, 0
        previous = None
        explicit = ""
        fence = ""
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            if _stamp(os.fstat(stream.fileno())) != stamp:
                raise PlanDocumentError(409, "source instance changed")
            for raw in stream:
                digest.update(raw)
                text = raw.decode("utf-8").rstrip("\r\n")
                marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", text)
                heading = None
                if marker:
                    token = marker[1]
                    if not fence:
                        fence = token
                    elif token[0] == fence[0] and len(token) >= len(fence) and not marker[2].strip():
                        fence = ""
                    previous, explicit = None, ""
                elif not fence:
                    atx = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$", text)
                    underline = re.fullmatch(r" {0,3}(=+|-+)\s*", text)
                    anchor = re.fullmatch(r'''\s*<a\s+id=["']([^"']+)["']\s*>\s*</a>\s*''', text)
                    if atx:
                        heading = (atx[2], len(atx[1]), offset)
                    elif underline and previous and previous[0].strip():
                        heading = (previous[0].strip(), 1 if underline[1][0] == "=" else 2, previous[1])
                    if heading:
                        heading_count += 1
                        title, level, start = heading
                        anchor_id = explicit[:256] or f"section-{heading_count}"
                        if start == 0 and section == 0:
                            db.execute("UPDATE sections SET title=?,anchor=?,level=? WHERE id=0",
                                       (title[:256], anchor_id, level))
                        else:
                            db.execute("UPDATE sections SET end=? WHERE id=?", (start, section))
                            section += 1
                            db.execute("INSERT INTO sections(id,title,anchor,level,start,end) VALUES(?,?,?,?,?,0)",
                                       (section, title[:256], anchor_id, level, start))
                        previous, explicit = None, ""
                    elif anchor:
                        explicit = anchor[1]
                        previous = None
                    else:
                        previous = (text, offset)
                        if text.strip(): explicit = ""
                offset += len(raw)
                if offset > MAX_BYTES:
                    raise PlanDocumentError(413, "source exceeds the 8 MiB preview limit")
            _check_source(path, stamp)
            if digest.hexdigest() != sha256:
                raise PlanDocumentError(409, "source digest changed; reload the preview")
            db.execute("UPDATE sections SET end=? WHERE id=?", (offset, section))
            # Persist only byte ranges, never another copy of source truth. A
            # long chapter uses bounded UTF-8 pages with explicit partial scope.
            for row in db.execute("SELECT id,start,end FROM sections ORDER BY id"):
                ident, start, end = row
                page = 0
                while start < end:
                    stream.seek(start)
                    chunk = stream.read(min(PAGE_BYTES + 4, end - start))
                    count = min(PAGE_BYTES, len(chunk))
                    while count < len(chunk) and chunk[count] & 0xC0 == 0x80:
                        count -= 1
                    chunk[:count].decode("utf-8")
                    db.execute("INSERT INTO pages VALUES(?,?,?,?)", (ident, page, start, start + count))
                    start += count
                    page += 1
                if page == 0:
                    db.execute("INSERT INTO pages VALUES(?,?,?,?)", (ident, 0, start, start))
                    page = 1
                db.execute("UPDATE sections SET page_count=? WHERE id=?", (page, ident))
            _check_source(path, stamp)
        db.execute("INSERT INTO metadata VALUES(?)", (json.dumps({"version": INDEX_VERSION,
            "sha256": sha256, "stamp": stamp, "byte_count": offset, "section_count": section + 1}),))
        db.commit()
        db.close()
        os.replace(temporary, target)
    finally:
        db.close()
        Path(temporary).unlink(missing_ok=True)


def _open_index(state_dir: Path, path: Path, sha256: str) -> tuple[sqlite3.Connection, dict[str, Any]]:
    stamp = _stamp(path.stat())
    if stamp[2] > MAX_BYTES:
        raise PlanDocumentError(413, "source exceeds the 8 MiB preview limit")
    key = hashlib.sha256(json.dumps([str(path), sha256, stamp, INDEX_VERSION]).encode()).hexdigest()
    directory = state_dir.resolve() / "cache" / "plan-documents"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{key}.sqlite"
    for attempt in range(2):
        if not target.exists() or attempt:
            _build_index(path, target, sha256, stamp)
        db = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            meta = json.loads(db.execute("SELECT body FROM metadata").fetchone()[0])
            if meta["sha256"] != sha256 or meta["stamp"] != stamp or meta["version"] != INDEX_VERSION:
                raise ValueError("stale document index")
            _check_source(path, stamp)
            return db, meta
        except (sqlite3.DatabaseError, ValueError, KeyError, TypeError):
            db.close()
            if attempt: raise
    raise PlanDocumentError(503, "document index unavailable")


def _search_sections(db: sqlite3.Connection, path: Path, meta: dict[str, Any],
                     query: str, offset: int, limit: int) -> tuple[list[dict[str, Any]], int | None]:
    """Explicit full-text search scans bounded chunks, returning navigation only."""
    needle = query.casefold()
    matches = []
    with path.open("rb") as stream:
        if _stamp(os.fstat(stream.fileno())) != meta["stamp"]:
            raise PlanDocumentError(409, "source instance changed")
        for row in db.execute("SELECT * FROM sections WHERE id>=? ORDER BY id", (offset,)):
            stream.seek(row["start"])
            remaining, tail, found = row["end"] - row["start"], "", False
            decoder = codecs.getincrementaldecoder("utf-8")()
            while remaining:
                chunk = stream.read(min(PAGE_BYTES, remaining))
                if not chunk:
                    raise PlanDocumentError(409, "source changed during search")
                text = decoder.decode(chunk, final=remaining == len(chunk)).casefold()
                if needle in tail + text:
                    found = True
                    break
                tail = (tail + text)[-len(needle):]
                remaining -= len(chunk)
            if found:
                matches.append({key: row[key] for key in ("id", "title", "anchor", "level", "page_count")})
                if len(matches) > limit:
                    break
        _check_source(path, meta["stamp"])
    return matches[:limit], matches[limit]["id"] if len(matches) > limit else None


def document_index(state_dir: Path, path: Path, sha256: str, *, offset: int = 0,
                   limit: int = 100, anchor: str = "", q: str = "") -> dict[str, Any]:
    db, meta = _open_index(state_dir, path, sha256)
    try:
        selected = db.execute("SELECT id FROM sections WHERE anchor=? ORDER BY id LIMIT 1", (anchor,)).fetchone() if anchor else None
        if selected:
            offset = selected[0] // limit * limit
        rows = [dict(row) for row in db.execute(
            "SELECT id,title,anchor,level,page_count FROM sections ORDER BY id LIMIT ? OFFSET ?", (limit, offset))]
        next_offset = offset + len(rows) if offset + len(rows) < meta["section_count"] else None
        if q:
            rows, next_offset = _search_sections(db, path, meta, q, offset, limit)
        return {"schema_version": "plan-document.v1", "sha256": sha256,
                "byte_count": meta["byte_count"], "section_count": meta["section_count"],
                "sections": rows, "offset": offset,
                "next_offset": next_offset, "query": q,
                "selected_section": selected[0] if selected else None,
                "anchor_found": bool(selected) if anchor else None,
                "authority": "navigation_only"}
    finally:
        db.close()


def document_section(state_dir: Path, path: Path, sha256: str, *, section: int, page: int = 0) -> dict[str, Any]:
    db, meta = _open_index(state_dir, path, sha256)
    try:
        row = db.execute("SELECT * FROM sections WHERE id=?", (section,)).fetchone()
        if row is None: raise PlanDocumentError(404, "chapter not found")
        part = db.execute("SELECT start,end FROM pages WHERE section=? AND page=?", (section, page)).fetchone()
        if part is None: raise PlanDocumentError(416, "chapter page out of range")
        start, end = part
        if not 0 <= start <= end <= meta["byte_count"] or end - start > PAGE_BYTES:
            raise PlanDocumentError(409, "document index invalid; reload the preview")
        with path.open("rb") as stream:
            if _stamp(os.fstat(stream.fileno())) != meta["stamp"]:
                raise PlanDocumentError(409, "source instance changed")
            stream.seek(start)
            content = stream.read(end - start).decode("utf-8")
            _check_source(path, meta["stamp"])
        return {"schema_version": "plan-document-section.v1", "sha256": sha256,
                "scope": "section_page", "section": section, "title": row["title"], "anchor": row["anchor"],
                "section_count": meta["section_count"], "page": page, "page_count": row["page_count"],
                "section_complete": row["page_count"] == 1, "content": content}
    finally:
        db.close()
