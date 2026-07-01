"""Shared lazy rotation helper for append-only state files.

Used by memory / events / cost stores. The pattern:

    .zf/<name>.<ext>                 ← active file (today)
    .zf/<name>/<YYYY-MM-DD>.<ext>    ← historical archive

rotate_if_needed() is idempotent:
  - active doesn't exist → noop
  - active.mtime is today → noop
  - active.mtime is earlier → mv to archive dir named by that date
  - archive target already exists → append-concat (rare edge case)

list_archives() enumerates archived files filtered by date range.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def rotate_if_needed(
    active: Path,
    archive_dir: Path | None = None,
    *,
    now_date: str | None = None,
) -> bool:
    """Lazy rotate an active file into a dated archive.

    Args:
        active: path to the active file (e.g. .zf/memory/shared.md)
        archive_dir: where to put archived files. Defaults to
            active.parent / active.stem (e.g. .zf/memory/shared/)
        now_date: override "today" for deterministic tests. YYYY-MM-DD.

    Returns:
        True if rotation happened, False otherwise.
    """
    if not active.exists():
        return False
    mtime_day = datetime.fromtimestamp(
        active.stat().st_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%d")
    today = now_date or _today_utc()
    if mtime_day == today:
        return False

    if archive_dir is None:
        archive_dir = active.parent / active.stem
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{mtime_day}{active.suffix}"

    if archive_path.exists():
        # Same-day collision: concat existing archive + active content,
        # then delete active. Separator depends on suffix (newline for
        # text-ish, none for structured JSON).
        old = archive_path.read_text(encoding="utf-8")
        new = active.read_text(encoding="utf-8")
        sep = "\n" if old and not old.endswith("\n") else ""
        archive_path.write_text(old + sep + new, encoding="utf-8")
        active.unlink()
    else:
        active.rename(archive_path)
    return True


def list_archives(
    archive_dir: Path,
    *,
    since: str | None = None,
    until: str | None = None,
    last_days: int | None = None,
    suffix: str = ".md",
) -> list[Path]:
    """List archived files filtered by date. Returns sorted oldest-first.

    Args:
        archive_dir: directory to scan (e.g. .zf/memory/shared/)
        since: inclusive YYYY-MM-DD lower bound (filename stem comparison)
        until: inclusive YYYY-MM-DD upper bound
        last_days: convenience — "K archive days back from today", i.e. the
            archive files whose stem is in [today - K, today - 1]. Since
            today is usually in the active file (not in archive), this
            yields up to K archive files.
            Mutually exclusive with `since` (since takes precedence).
        suffix: file suffix to match (".md" / ".jsonl" / ".json")
    """
    if not archive_dir.exists():
        return []
    files = sorted(f for f in archive_dir.glob(f"*{suffix}") if f.is_file())
    if last_days is not None and since is None:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=last_days)
        ).strftime("%Y-%m-%d")
        files = [f for f in files if f.stem >= cutoff]
    if since:
        files = [f for f in files if f.stem >= since]
    if until:
        files = [f for f in files if f.stem <= until]
    return files
