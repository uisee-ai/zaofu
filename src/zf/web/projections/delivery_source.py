"""Metadata-exact Delivery dependencies, including referenced task-map files."""
from pathlib import Path

from zf.runtime.delivery_trace_resolve import (
    _candidate_paths, _feature_current_bundle, _task_map_event_bindings,
)
from zf.web.projections.events import _events_with_seq
from zf.web.projections.source_cache import fingerprint, read_source_projection, task_paths


def delivery_source_version(state_dir: Path, feature_id: str, config=None) -> str:
    # Include directory identities: new versions and formerly missing sidecars
    # must invalidate the summary even without a new ledger occurrence.
    paths = task_paths(state_dir)
    for name in ("artifacts", "refs"):
        directory = state_dir / name
        paths.extend([directory, *directory.rglob("*")])

    def referenced_maps():
        events = [event for _, event in _events_with_seq(state_dir, config=config)]
        refs = [ref for ref, _ in _task_map_event_bindings(events, feature_id=feature_id)]
        refs.append(str(_feature_current_bundle(state_dir, feature_id).get("current_task_map_ref") or ""))
        return sorted({str(path) for ref in refs for path in _candidate_paths(state_dir, ref)})

    refs = read_source_projection(state_dir, f"delivery-map-refs:{feature_id}",
                                 [state_dir / "refs" / "feature-index.json"], referenced_maps, config=config)
    return fingerprint(state_dir, paths + [Path(ref) for ref in refs], config)
