"""Read-only autoresearch experiment graph projection."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _clean_refs(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    refs: list[str] = []
    for value in values:
        text = _clean_str(value)
        if text:
            refs.append(text)
    return refs


def _score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return 0.0
    if parsed > 100:
        return 100.0
    return round(parsed, 2)


@dataclass
class ExperimentNode:
    experiment_id: str
    parent_id: str = ""
    kind: str = "fix"
    hypothesis: str = ""
    status: str = "created"
    gate_status: str = "unknown"
    score_total: float | None = None
    eval_result_ref: str = ""
    trace_refs: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_rejected(self) -> bool:
        return self.gate_status in {"failed", "blocked", "rejected"}

    @property
    def is_frontier(self) -> bool:
        if self.is_rejected:
            return False
        return self.status in {"created", "started", "pending"} or self.score_total is None

    def merge(self, payload: dict[str, Any], *, event_type: str = "") -> None:
        for attr in ("parent_id", "kind", "hypothesis", "status", "gate_status", "eval_result_ref"):
            value = _clean_str(payload.get(attr))
            if value:
                setattr(self, attr, value)
        if "score_total" in payload:
            self.score_total = _score(payload.get("score_total"))
        refs = _clean_refs(payload.get("trace_refs"))
        if refs:
            self.trace_refs = sorted(set([*self.trace_refs, *refs]))
        created_at = _clean_str(payload.get("created_at"))
        updated_at = _clean_str(payload.get("updated_at"))
        if created_at and not self.created_at:
            self.created_at = created_at
        if updated_at:
            self.updated_at = updated_at
        elif event_type:
            self.updated_at = _clean_str(payload.get("at")) or self.updated_at
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            self.metadata.update(metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "parent_id": self.parent_id,
            "kind": self.kind,
            "hypothesis": self.hypothesis,
            "status": self.status,
            "gate_status": self.gate_status,
            "score_total": self.score_total,
            "eval_result_ref": self.eval_result_ref,
            "trace_refs": list(self.trace_refs),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }


@dataclass
class ExperimentGraph:
    nodes: dict[str, ExperimentNode] = field(default_factory=dict)

    def add_record(self, record: dict[str, Any]) -> None:
        event_type = _clean_str(record.get("type") or record.get("event_type"))
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
        experiment_id = _clean_str(
            payload.get("experiment_id")
            or payload.get("id")
            or record.get("experiment_id")
        )
        if not experiment_id:
            return
        node = self.nodes.get(experiment_id)
        if node is None:
            node = ExperimentNode(experiment_id=experiment_id)
            self.nodes[experiment_id] = node
        node.merge(payload, event_type=event_type)

    @property
    def roots(self) -> list[str]:
        return sorted(
            node.experiment_id
            for node in self.nodes.values()
            if not node.parent_id or node.parent_id not in self.nodes
        )

    @property
    def edges(self) -> list[dict[str, str]]:
        return sorted(
            [
                {"from": node.parent_id, "to": node.experiment_id}
                for node in self.nodes.values()
                if node.parent_id
            ],
            key=lambda row: (row["from"], row["to"]),
        )

    @property
    def frontier(self) -> list[str]:
        return sorted(node.experiment_id for node in self.nodes.values() if node.is_frontier)

    @property
    def best_experiment_id(self) -> str:
        passed = [
            node
            for node in self.nodes.values()
            if node.gate_status == "passed" and node.score_total is not None
        ]
        if not passed:
            return ""
        best = sorted(
            passed,
            key=lambda node: (node.score_total or 0.0, node.updated_at, node.experiment_id),
            reverse=True,
        )[0]
        return best.experiment_id

    @property
    def best_path(self) -> list[str]:
        best_id = self.best_experiment_id
        if not best_id:
            return []
        path: list[str] = []
        seen: set[str] = set()
        current = self.nodes.get(best_id)
        while current is not None and current.experiment_id not in seen:
            seen.add(current.experiment_id)
            path.append(current.experiment_id)
            current = self.nodes.get(current.parent_id)
        return list(reversed(path))

    @property
    def rejected(self) -> list[str]:
        return sorted(node.experiment_id for node in self.nodes.values() if node.is_rejected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "autoresearch-experiment-graph.v1",
            "nodes": [self.nodes[key].to_dict() for key in sorted(self.nodes)],
            "roots": self.roots,
            "edges": self.edges,
            "frontier": self.frontier,
            "best_experiment_id": self.best_experiment_id,
            "best_path": self.best_path,
            "rejected": self.rejected,
        }


def build_experiment_graph(records: list[dict[str, Any]]) -> ExperimentGraph:
    graph = ExperimentGraph()
    for record in records:
        if isinstance(record, dict):
            graph.add_record(record)
    return graph


def read_experiment_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def project_experiment_graph(state_dir: Path) -> dict[str, Any]:
    records_path = state_dir / "autoresearch" / "experiments" / "events.jsonl"
    graph = build_experiment_graph(read_experiment_records(records_path))
    result = graph.to_dict()
    result["source"] = {
        "path": str(records_path),
        "exists": records_path.exists(),
    }
    return result


__all__ = [
    "ExperimentGraph",
    "ExperimentNode",
    "build_experiment_graph",
    "project_experiment_graph",
    "read_experiment_records",
]
