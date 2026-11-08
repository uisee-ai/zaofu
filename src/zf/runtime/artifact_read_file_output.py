"""Atomic local presentation cache; never substitutes for read-ledger evidence."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Iterator, TextIO


@contextmanager
def required_read_stream(output_file: str | None) -> Iterator[TextIO]:
    if not output_file:
        yield sys.stdout
        return
    cwd = Path.cwd().resolve()
    path = Path(output_file).absolute()
    if (
        path.resolve() != path
        or not path.is_relative_to(cwd)
        or not {".cache", "__pycache__"} & set(path.relative_to(cwd).parts)
    ):
        raise ValueError(
            "output file must be a local ignored .cache or __pycache__ path without symlinks"
        )
    if (
        subprocess.run(
            ["git", "check-ignore", "--quiet", "--", str(path)],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        != 0
    ):
        raise ValueError("output file must be gitignored")
    if path.exists():
        raise ValueError(
            "output file already exists; preserve it and choose a fresh cache path"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".required-read-",
            delete=False,
        ) as stream:
            name = stream.name
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        # Publish without replacing a concurrent file or following a symlink.
        os.link(name, path)
        raw = path.read_bytes()
        print(
            json.dumps(
                {
                    "schema_version": "artifact-read-cache.v1",
                    "path": str(path),
                    "byte_count": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "model_consumption_proven": False,
                },
                ensure_ascii=False,
            )
        )
    finally:
        if name:
            Path(name).unlink(missing_ok=True)
