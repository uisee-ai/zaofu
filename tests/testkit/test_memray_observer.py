"""Explicit real-library calibration of the work-loop allocation observer."""

import gc
import json
import os
from pathlib import Path
import tracemalloc
import weakref

import pytest

from tests.testkit.memray_observer import MemrayWorkloopObserver


@pytest.mark.host
@pytest.mark.perf
@pytest.mark.serial
def test_memray_observer_records_retention_and_release(tmp_path, record_property, monkeypatch):
    if os.environ.get("ZF_RUN_MEMRAY_CALIBRATION") != "1":
        pytest.skip("set ZF_RUN_MEMRAY_CALIBRATION=1 and provide memray==1.20.0")
    import memray

    assert memray.__version__ == "1.20.0"
    assert not tracemalloc.is_tracing()

    def forbidden(*args, **kwargs):
        raise AssertionError("Memray mode must not enable tracemalloc")

    monkeypatch.setattr(tracemalloc, "start", forbidden)
    observer = MemrayWorkloopObserver(tmp_path / "capture")

    class Box:
        pass

    observer.start()
    try:
        observer.observe(tmp_path)
        box = Box()
        box.buffer = bytearray(2 * 1024**2)
        witness = weakref.ref(box)
        observer.observe(tmp_path)
        gc.collect()
        assert witness() is box
        observer.observe(tmp_path)
        del box
        gc.collect()
        assert witness() is None
        observer.observe(tmp_path)
    finally:
        observer.close()
    result = observer.finish()
    rows = result["observations"]
    assert len(rows) == 4
    own_bytes = [dict(row["top"]).get(str(Path(__file__).resolve()), 0) for row in rows]
    # Aggregate file attribution includes tiny test allocations, not just the buffer.
    assert own_bytes[1] - own_bytes[0] >= 2 * 1024**2
    assert own_bytes[2] - own_bytes[0] >= 2 * 1024**2
    assert own_bytes[2] - own_bytes[3] >= 2 * 1024**2 - 65536
    with pytest.raises(AssertionError):
        assert own_bytes[2] - own_bytes[0] < 65536  # A false release claim is rejected.
    with pytest.raises(AssertionError, match="cannot restart"):
        observer.start()
    record_property("memray_calibration", json.dumps({**result, "own_bytes": own_bytes}))
