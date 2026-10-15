"""Bounded, read-only text preview of files explicitly referenced in a Channel."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any


from zf.core.security.redaction import redact_text
from zf.runtime.channel_sidecar import hydrate_channel_message_text


class ChannelDocumentError(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def markdown_file_links(text: str) -> Iterator[str]:
    """Only explicit Markdown link destinations; no prose/path guessing."""
    for match in re.finditer(r'(?<!!)\[[^\]\n]+\]\((?:<([^>\n]+)>|([^\s()]+))(?:\s+"[^"\n]*")?\)', text):
        ref = match.group(1) or match.group(2)
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", ref) and not ref.startswith(("//", "#")):
            yield ref


MAX_PREVIEW_BYTES = 4 * 1024 * 1024
TEXT_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".css",
    ".html",
    ".csv",
    ".log",
}
REF_KEYS = {"path", "file", "ref", "artifact_ref", "content_ref", "raw_ref"}
REF_LISTS = {
    "artifacts",
    "artifact_refs",
    "source_refs",
    "evidence_refs",
    "attachments",
    "preview_refs",
}


def _cited_path(ref: str) -> Path:
    """Resolve explicit section/line notation after authorizing the raw ref."""
    # Keep spaces in filenames. Never interpret free-form notes, multiple file
    # citations, or URL fragments as local paths.
    section = r"\d+(?:\.\d+)*"
    section_range = rf"{section}(?:\s*[-–—]\s*{section})?"
    match = re.fullmatch(
        rf"([^§;,，\r\n]+?)\s+§\s*{section_range}(?:\s*[、,，及]\s*§?\s*{section_range})*",
        ref,
    ) or re.fullmatch(r"([^;,，\r\n]+?):(?:L)?\d+(?:[-–—](?:L)?\d+)?", ref)
    return Path(match.group(1) if match else ref)


def _references(value: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in REF_KEYS and isinstance(item, str):
                yield item, value
            elif key in REF_LISTS and isinstance(item, list):
                for ref in item:
                    if isinstance(ref, str):
                        yield ref, {}
                    else:
                        yield from _references(ref)
            elif isinstance(item, (dict, list)):
                yield from _references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item)


def _read_bounded(root: Path, relative: Path) -> bytes:
    """Open beneath the chosen root without following any symlink component."""
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in relative.parts[:-1]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        fd = os.open(
            relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ChannelDocumentError(403, "Only regular files can be previewed")
            data = stream.read(MAX_PREVIEW_BYTES + 1)
    finally:
        os.close(directory)
    if len(data) > MAX_PREVIEW_BYTES:
        raise ChannelDocumentError(
            413, "File exceeds preview limit; no partial document is shown"
        )
    return data


def read_channel_document(
    *,
    project_root: Path,
    state_dir: Path,
    channel: dict[str, Any],
    thread_id: str,
    ref: str,
) -> dict[str, Any]:
    descriptors = [
        descriptor
        for key in (
            "messages",
            "provider_runs",
            "contributions",
            "syntheses",
            "result_receipts",
        )
        for row in channel.get(key, [])
        if str(row.get("thread_id") or "main") == thread_id
        for candidate, descriptor in _references(row)
        if candidate == ref
    ]
    for row in channel.get("messages") or []:
        if str(row.get("thread_id") or "main") != thread_id:
            continue
        # Load the complete persisted message, never authorize a truncated preview.
        text = hydrate_channel_message_text(state_dir, row, strict=True)
        if ref in markdown_file_links(text):
            descriptors.append({})
    if not ref or not descriptors:
        raise ChannelDocumentError(403, "File is not referenced by this Channel thread")
    path = _cited_path(ref)
    if ".." in path.parts:
        raise ChannelDocumentError(403, "Unsafe file reference")
    roots = [project_root.resolve(), state_dir.resolve()]
    choices: list[tuple[Path, Path]] = []
    for root in roots:
        if path.is_absolute():
            if not path.is_relative_to(root):
                continue
            relative = path.relative_to(root)
        else:
            relative = path
        # A configured hidden state dir itself is permitted; other hidden or
        # credential-bearing paths are never part of the document surface.
        parts = relative.parts
        if root == roots[0] and roots[1].is_relative_to(root):
            state_parts = roots[1].relative_to(root).parts
            if parts[: len(state_parts)] == state_parts:
                parts = parts[len(state_parts) :]
        if any(
            part.startswith(".")
            or any(
                word in part.lower()
                for word in ("secret", "credential", "id_rsa", "id_ed25519")
            )
            for part in parts
        ):
            raise ChannelDocumentError(403, "Sensitive paths cannot be previewed")
        choices.append((root, relative))
    if not choices:
        raise ChannelDocumentError(403, "File is outside this project")
    if path.suffix.lower() not in TEXT_SUFFIXES:
        raise ChannelDocumentError(415, "This file type has no text preview")
    data = None
    for root, relative in choices:
        try:
            data = _read_bounded(root, relative)
            break
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ChannelDocumentError(403, "File cannot be safely opened") from exc
    if data is None:
        raise ChannelDocumentError(
            404, "Referenced file is unavailable; no summary substituted"
        )
    digest = hashlib.sha256(data).hexdigest()
    expected = {
        str(item.get("sha256") or item.get("artifact_digest") or "").removeprefix(
            "sha256:"
        )
        for item in descriptors
    } - {""}
    if expected and digest not in expected:
        raise ChannelDocumentError(409, "Referenced file has changed; refresh its reference")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ChannelDocumentError(415, "File is not UTF-8 text") from exc
    if "\x00" in content or redact_text(content) != content:
        raise ChannelDocumentError(403, "File contains sensitive or non-text content")
    return {
        "name": path.name,
        "ref": ref,
        "content": content,
        "sha256": digest,
        "byte_count": len(data),
        "format": "markdown" if path.suffix.lower() in {".md", ".markdown"} else "text",
    }
