"""Rotate event segments without rewriting their inode or byte positions."""

from datetime import datetime, timezone
from pathlib import Path


def archive_segment(path: Path, day: str, *, numbered: bool = False) -> Path:
    """Rename under the caller's ledger lock, preserving chronological order.

    Size segments precede the day's final file (``day-0001``, ``day``).
    A collision after that final file uses ``day~0001`` so neither an older
    segment nor a durable cursor is rewritten by concatenation.
    """
    directory = path.parent / path.stem
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / f"{day}{path.suffix}"
    after_final = final.exists() or any(directory.glob(f"{day}~*{path.suffix}"))
    target = final
    if numbered or after_final:
        separator = "~" if after_final else "-"
        ordinal = 1
        while True:
            target = directory / f"{day}{separator}{ordinal:04d}{path.suffix}"
            if not target.exists():
                break
            ordinal += 1
    path.rename(target)
    return target


def rotate_by_date(path: Path) -> bool:
    try:
        day = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date()
    except FileNotFoundError:
        return False
    if day == datetime.now(timezone.utc).date():
        return False
    archive_segment(path, day.isoformat())
    return True
