"""Read-only exact-repetition feedback for Plan authors; never a quality gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def inspect(matrix: dict[str, Any], source_index: dict[str, Any]) -> dict[str, Any]:
    duplicates = []
    for index, row in enumerate(matrix.get("tests", [])):
        if not isinstance(row, dict) or not isinstance(row.get("then"), str) or not row["then"]:
            continue
        for key in ("expected_result", "parameter_coverage"):
            if row.get(key) == row["then"]:
                duplicates.append({
                    "path": f"$.tests[{index}].{key}",
                    "same_as": f"$.tests[{index}].then",
                    "test_id": row.get("test_id", row.get("id")),
                    "value_utf8_bytes": len(row[key].encode("utf-8")),
                })
    projections = []
    for index, row in enumerate(source_index.get("traceability", [])):
        if isinstance(row, dict) and "stage_projections" in row:
            projections.append({
                "path": f"$.traceability[{index}].stage_projections",
                "serialized_utf8_bytes": len(json.dumps(
                    row["stage_projections"], ensure_ascii=False).encode("utf-8")),
            })
    return {
        "boundary": "exact text diagnostics only; not semantic equivalence, deletion authorization or acceptance",
        "duplicates": duplicates,
        "duplicate_value_utf8_bytes": sum(row["value_utf8_bytes"] for row in duplicates),
        "stage_projection_locations": projections,
        "stage_projection_note": "Inventory only; unique obligations and configured consumers require author review.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("--source-index", type=Path)
    args = parser.parse_args()
    result = inspect(json.loads(args.matrix.read_text()),
                     json.loads(args.source_index.read_text()) if args.source_index else {})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
