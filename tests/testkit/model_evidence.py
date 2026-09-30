"""Bounded pre-teardown evidence for test-owned property models.

Only synthetic fixture roots are supported. First/latest failing candidates
are retained; neither is advertised as Hypothesis's final minimal example.
Decorators belong INSIDE @rule/@invariant so their registered function runs
the capture. Constructor, precondition and framework failures are not caught.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from functools import cache, wraps
import hashlib
from importlib.metadata import version
import inspect
import json
import os
from pathlib import Path
import platform
import tempfile
from typing import Any, Callable, Iterator

from hypothesis.errors import UnsatisfiedAssumption

from tests.testkit.evidence import sha256_file, write_json_atomic
from zf.core.state.locks import locked_path


@cache
def _standalone_output() -> Path:
    # Shared across shrinking instances; create nothing until actual failure.
    return Path(tempfile.mkdtemp(prefix="zf-model-evidence-"))


class ModelEvidence:
    """Two atomic JSON slots per pytest node/model/process; no writes on pass."""

    def __init__(
        self, *, root: Path, model_name: str, source: Path,
        reference: Callable[[], Any], settings: str = "",
        max_files: int = 128, max_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self.root = root.resolve()
        self.model_name = model_name
        self.source = source.resolve()
        self.reference = reference
        self.settings = settings
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.steps: list[dict[str, Any]] = []

    @contextmanager
    def guard(
        self, name: str, arguments: dict[str, Any], *, action: bool = True,
    ) -> Iterator[None]:
        if action:
            # Freeze arguments before the operation can mutate a list/dict.
            self.steps.append(json.loads(json.dumps({"action": name, "arguments": arguments})))
        try:
            yield
        except UnsatisfiedAssumption:
            raise
        except Exception as error:
            try:
                path, digest = self.capture(error, failed_check=name)
                error.add_note(f"TestKit model evidence: {path}; trace_sha256={digest}; candidate, not guaranteed minimal")
            except Exception as capture_error:
                error.add_note(
                    "TestKit model evidence unavailable: "
                    f"{type(capture_error).__name__}: {capture_error}"
                )
            raise

    def _files(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            raise ValueError("model fixture root is missing before capture")
        rows: list[dict[str, Any]] = []
        total = 0

        def fail_on_walk_error(error: OSError) -> None:
            raise error  # Never label a silently skipped subtree complete.

        for directory, subdirs, names in os.walk(self.root, followlinks=False, onerror=fail_on_walk_error):
            for name in sorted([*subdirs, *names]):
                path = Path(directory) / name
                if path.is_symlink():
                    raise ValueError(f"model evidence refuses symlink: {path}")
                if path.is_dir():
                    continue
                if not path.is_file():
                    raise ValueError(f"model evidence requires regular file: {path}")
                if len(rows) >= self.max_files or total + path.stat().st_size > self.max_bytes:
                    raise ValueError("model evidence exceeds file/byte budget")
                with path.open("rb") as stream:
                    body = stream.read(self.max_bytes - total + 1)
                total += len(body)
                if len(body) != path.stat().st_size or total > self.max_bytes:
                    raise ValueError("model evidence file changed or exceeded byte budget")
                rows.append({
                    "path": path.relative_to(self.root).as_posix(),
                    "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                    "base64": base64.b64encode(body).decode("ascii"),
                })
        return sorted(rows, key=lambda row: row["path"])

    def capture(self, error: Exception, *, failed_check: str) -> tuple[Path, str]:
        nodeid = os.environ.get("PYTEST_CURRENT_TEST", "standalone").rsplit(" (", 1)[0]
        output = os.environ.get("ZF_TESTKIT_MODEL_EVIDENCE", "")
        if output:
            destination = Path(output).resolve()
        else:
            destination = _standalone_output()
        if destination == self.root or destination.is_relative_to(self.root):
            raise ValueError("model evidence output must be outside fixture root")
        files = self._files()
        key = hashlib.sha256(f"{nodeid}|{self.model_name}".encode()).hexdigest()[:24]
        destination = destination / f"{key}-{os.getpid()}"
        trace_text = json.dumps(self.steps, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(trace_text.encode()).hexdigest()
        payload = {
            "schema_version": "zf-testkit-model-failure.v1", "complete": True,
            "retention": "first/latest failing candidates, not guaranteed final/minimal",
            "nodeid": nodeid, "model": self.model_name, "original_root": str(self.root),
            "source": str(self.source), "source_sha256": sha256_file(self.source),
            "helper_sha256": sha256_file(Path(__file__)),
            "python_version": platform.python_version(), "hypothesis_version": version("hypothesis"),
            "pytest_version": version("pytest"),
            "settings": self.settings, "seed": None,
            "seed_note": "No seed inferred; consult pytest invocation/final failure report.",
            "steps": self.steps, "trace_sha256": digest, "reference": self.reference(),
            "failed_check": failed_check,
            "exception": {"type": type(error).__qualname__, "message": str(error)},
            "files": files,
        }
        repo = Path(__file__).resolve().parents[2]
        payload["repository_inputs"] = {
            name: sha256_file(repo / name) for name in ("zf.yaml", "uv.lock")
            if (repo / name).is_file()
        }
        with locked_path(destination / "capture"):
            first = destination / "first.json"
            if not first.exists():
                write_json_atomic(first, payload)
            latest = destination / "latest.json"
            write_json_atomic(latest, payload)
        return latest, digest


def record_action(function: Callable[..., Any]) -> Callable[..., Any]:
    """Put directly above def, below @rule; replay only these marked actions."""
    signature = inspect.signature(function)

    @wraps(function)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        arguments = {key: value for key, value in bound.arguments.items() if key != "self"}
        with self.evidence.guard(function.__name__, arguments):
            return function(self, *args, **kwargs)

    wrapped.testkit_model_action = True
    return wrapped


def record_invariant(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        with self.evidence.guard(function.__name__, {}, action=False):
            return function(self, *args, **kwargs)
    return wrapped


def replay_steps(model: Any, steps: list[dict[str, Any]], check: Callable[[], None]) -> None:
    """Caller supplies the trusted model and check; evidence cannot import/eval."""
    check()  # Hypothesis checks invariants immediately after construction too.
    for step in steps:
        action = getattr(model, str(step.get("action", "")), None)
        if not callable(action) or not getattr(action, "testkit_model_action", False):
            raise ValueError("replay requires an explicitly recorded model action")
        action(**step["arguments"])
        check()
