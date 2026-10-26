"""Observed intervals and DAG longest path, explicitly incomplete without timings."""

from datetime import datetime


def _time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000
    except (ValueError, TypeError):
        return None


def build_timeline(operations, nodes, edges):
    intervals, weights = [], {}
    for op in operations:
        start, end = _time(op.get("started_at")), _time(op.get("finished_at"))
        requested = _time(op.get("requested_at"))
        intervals.append({"operation_id": op["operation_id"], "task_id": op.get("task_id", ""),
                          "role": op.get("role_instance", ""), "status": op["status"],
                          "start_ms": start, "end_ms": end,
                          "queue_wait_ms": start - requested if start is not None and requested is not None and start >= requested else None,
                          "duration_ms": end - start if start is not None and end is not None and end >= start else None})
        if start is not None and end is not None and end >= start:
            weights.setdefault("task:" + op.get("task_id", ""), []).append((start, end))
    durations = {}
    for task, spans in weights.items():
        merged = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(end, merged[-1][1])
            else:
                merged.append([start, end])
        durations[task] = sum(end - start for start, end in merged)
    task_ids = {n["id"] for n in nodes if n["kind"] == "task"}
    incoming = {key: [] for key in task_ids}
    for edge in edges:
        if edge["relation"] == "depends_on" and edge["source"] in task_ids and edge["target"] in task_ids:
            incoming[edge["target"]].append(edge["source"])
    paths, pending = {}, set(task_ids)
    while pending:
        ready = sorted(key for key in pending if all(p in paths for p in incoming[key]))
        if not ready:
            return {"intervals": intervals, "critical_path": {"status": "cycle", "node_ids": [], "duration_ms": None}}
        for key in ready:
            parent = max(incoming[key], key=lambda p: paths[p][0], default=None)
            cost, path = paths[parent] if parent else (0, [])
            paths[key] = (cost + durations.get(key, 0), [*path, key])
            pending.remove(key)
    duration, path = max(paths.values(), key=lambda pair: pair[0], default=(0, []))
    complete = bool(task_ids) and task_ids <= durations.keys() and all(i["duration_ms"] is not None for i in intervals)
    return {"intervals": intervals, "critical_path": {"status": "observed" if complete else "incomplete",
            "node_ids": path if complete else [], "duration_ms": duration if complete else None,
            "method": "DAG longest path; per-task union of observed operation intervals; excludes unmeasured waiting"}}
