"""Channel role definition references and bounded excerpts."""

from __future__ import annotations

from pathlib import Path, PurePosixPath


ROLE_CONTEXT_DIR = "channel_roles"
ROLE_CONTEXT_MAX_CHARS = 1200
_REPO_ROOT = Path(__file__).resolve().parents[3]


def normalize_role_context_ref(value: object) -> str:
    """Return a safe repo-local channel role ref, or empty string."""
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        return ""
    if len(path.parts) != 2 or path.parts[0] != ROLE_CONTEXT_DIR:
        return ""
    name = path.parts[1]
    if not name.endswith(".md"):
        return ""
    stem = name[:-3]
    if not stem or any(ch for ch in stem if not (ch.islower() or ch.isdigit() or ch == "-")):
        return ""
    return f"{ROLE_CONTEXT_DIR}/{name}"


def validate_role_context_ref(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if normalize_role_context_ref(raw):
        return ""
    return f"role_context_ref must be repo-local {ROLE_CONTEXT_DIR}/<role>.md"


def load_role_definition_excerpt(
    role_context_ref: object,
    *,
    repo_root: Path | None = None,
    max_chars: int = ROLE_CONTEXT_MAX_CHARS,
) -> dict[str, str]:
    """Load a bounded role definition excerpt for context packs.

    Missing files are represented as an empty excerpt. That keeps legacy event
    projections readable while invite validation prevents new invalid refs.
    """
    ref = normalize_role_context_ref(role_context_ref)
    if not ref:
        return {}
    root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    path = (root / ref).resolve()
    allowed_root = (root / ROLE_CONTEXT_DIR).resolve()
    try:
        path.relative_to(allowed_root)
    except ValueError:
        return {}
    if not path.is_file():
        return {"role_context_ref": ref, "status": "missing"}
    text = path.read_text(encoding="utf-8")
    excerpt = _clip_role_definition(text, max_chars=max_chars)
    return {
        "role_context_ref": ref,
        "status": "loaded",
        "excerpt": excerpt,
        "chars": str(len(excerpt)),
    }


def _clip_role_definition(text: str, *, max_chars: int) -> str:
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(max_chars - 3, 0)].rstrip() + "..."
