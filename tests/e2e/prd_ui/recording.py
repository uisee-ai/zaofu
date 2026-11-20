"""Recording integrity is independent of the browser body and product audit."""
from pathlib import Path
from zipfile import BadZipFile, ZipFile


def inspect_recordings(directory: Path) -> dict:
    valid, invalid = [], []
    for path in sorted(directory.rglob("*.zip")):
        try:
            with ZipFile(path) as archive:
                bad = archive.testzip()
                if bad:
                    raise BadZipFile(f"CRC failure: {bad}")
            valid.append(str(path.relative_to(directory)))
        except (BadZipFile, OSError) as exc:
            invalid.append({"path": str(path.relative_to(directory)), "error": str(exc)})
    return {"status": "failed" if invalid else "validated" if valid else "not_recorded",
            "valid": valid, "invalid": invalid}
