"""Bound repeated maintenance failures without requiring an Orchestrator instance."""
import sys
import time
from typing import Any

from zf.core.events.model import ZfEvent


def emit_housekeeping_failure(runtime: Any, step: str, exc: BaseException) -> None:
    now = time.time()
    last = getattr(runtime, '_housekeeping_failure_last', None)
    if last is None:
        last = runtime._housekeeping_failure_last = {}
    if now - last.get(step, 0.0) < getattr(runtime, '_housekeeping_failure_dedup_seconds', 60.0):
        return
    last[step] = now
    try:
        runtime.event_writer.append(ZfEvent(type='kernel.housekeeping.failed', actor='orchestrator',
            origin='kernel', payload={'step': step, 'exc_type': type(exc).__name__, 'exc_repr': repr(exc)[:500]}))
    except Exception as emit_exc:
        print(f'[kernel.housekeeping.failed] step={step} exc={exc!r} emit_failed={emit_exc!r}', file=sys.stderr)
