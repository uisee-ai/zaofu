"""LH-6.T5: results.tsv writer + reader.

TSV schema (8 columns, per-iteration row):
  iteration  commit  vcr  mtts  cost_per_task  rework_ratio  guard_status  note

status column enum (stored inside `note` as first token):
  baseline / keep / keep(reworked) / discard / crash / guard_fail /
  metric_error
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


STATUS_VALUES = frozenset({
    "baseline", "keep", "keep(reworked)", "discard",
    "crash", "guard_fail", "metric_error",
})


@dataclass
class ResultRow:
    iteration: int
    commit: str
    vcr: float
    mtts: float
    cost_per_task: float
    rework_ratio: float
    guard_status: str   # "pass" | "fail:<name>"
    note: str           # status token + human text

    def to_tsv(self) -> str:
        return "\t".join([
            str(self.iteration), self.commit,
            f"{self.vcr:.4f}", f"{self.mtts:.2f}",
            f"{self.cost_per_task:.4f}", f"{self.rework_ratio:.2f}",
            self.guard_status, self.note,
        ])


_HEADER = ("iteration", "commit", "vcr", "mtts",
           "cost_per_task", "rework_ratio", "guard_status", "note")


def append_row(path: Path, row: ResultRow) -> None:
    """Append a row to results.tsv. Writes the header if the file is new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        if new_file:
            w.writerow(_HEADER)
        w.writerow([
            row.iteration, row.commit,
            f"{row.vcr:.4f}", f"{row.mtts:.2f}",
            f"{row.cost_per_task:.4f}", f"{row.rework_ratio:.2f}",
            row.guard_status, row.note,
        ])


def read_recent(path: Path, n: int = 10) -> list[dict]:
    """Read up to last ``n`` rows of results.tsv as dicts (excludes header)."""
    if not path.exists():
        return []
    with path.open(newline="") as f:
        r = csv.DictReader(f, delimiter="\t")
        rows = list(r)
    return rows[-n:]
