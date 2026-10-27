#!/usr/bin/env python3
"""Export label-masked review packets and summarize evidence-backed human grades.

This tool never grades semantic quality or grants runtime approval. Share only
review/ with the grader; source text may still reveal a method's identity.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import statistics


def read_json(path: Path):
    return json.loads(path.read_text())


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_digest(root: Path) -> str | None:
    if not root.exists():
        return None
    paths = sorted(root.rglob("*"))
    if root.is_symlink() or any(path.is_symlink() for path in paths):
        raise ValueError("symlink input evidence is not allowed")
    files = {p.relative_to(root).as_posix(): sha256(p) for p in paths if p.is_file()}
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def child(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("invalid run/packet id")
    path = root / name
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise ValueError("run/packet must be a direct real child")
    return path


def load_rubric(path: Path) -> list[dict]:
    rows = read_json(path)
    if not isinstance(rows, list) or not rows:
        raise ValueError("rubric must be a nonempty list")
    ids = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not row["id"].strip() or row["id"] in ids
                or not isinstance(row.get("expectation"), str) or not row["expectation"].strip()
                or type(row.get("mandatory")) is not bool):
            raise ValueError("rubric needs unique ids, expectations and boolean mandatory")
        ids.add(row["id"])
    return rows


def export(run: Path, rubric_path: Path, output: Path) -> dict:
    rubric = load_rubric(rubric_path)
    manifest = read_json(run / "manifest.json")
    if manifest.get("schema_version") != "plan-execution-eval.v1":
        raise ValueError("unsupported evaluation manifest")
    records = manifest.get("runs", [])
    if not records or len({r["id"] for r in records}) != len(records):
        raise ValueError("evaluation needs unique run ids")
    # Validate before writing; external evidence paths are never followed.
    for record in records:
        folder = child(run, record["id"])
        for name in ("result.json", "trace.jsonl", "prompt.txt"):
            if (folder / name).is_symlink():
                raise ValueError("symlink evidence is not allowed")
    output.mkdir(parents=True, exist_ok=False)
    review = output / "review"
    review.mkdir()
    write_json(review / "rubric.json", rubric)
    mapping, metrics = {}, []
    for record in records:
        folder = child(run, record["id"])
        packet_id = "p-" + secrets.token_hex(6)
        packet = review / packet_id
        packet.mkdir()
        result = read_json(folder / "result.json") if (folder / "result.json").exists() else {}
        trace = folder / "trace.jsonl"
        trace_ok = (trace.is_file() and sha256(trace) == record.get("trace_sha256")
                    == result.get("trace_sha256"))
        valid = bool(not manifest.get("prepared_only") and trace_ok
                     and result.get("returncode") == 0 and result.get("turn_completed") is True
                     and result.get("timed_out") is False and result.get("changed_paths") == []
                     and result.get("errors") == [])
        answers = result.get("answers", [])
        valid = valid and bool(answers and isinstance(answers[-1], str) and answers[-1].strip())
        (packet / "answer.txt").write_text(answers[-1] if answers else "No completed answer.\n")
        for name in ("trace.jsonl", "prompt.txt"):
            if (folder / name).is_file():
                shutil.copyfile(folder / name, packet / name)
        project = folder / "project"
        if project.exists():
            if project.is_symlink() or any(p.is_symlink() for p in project.rglob("*")):
                raise ValueError("symlink project evidence is not allowed")
            shutil.copytree(project, packet / "inputs", ignore=shutil.ignore_patterns(".git", ".agents"))
        write_json(packet / "grading.json", {
            "packet_id": packet_id,
            "expectations": [{"id": r["id"], "passed": None, "evidence": ""} for r in rubric],
            "rubric_feedback": {"weak": [], "uncovered": [], "reviewed": False},
        })
        mapping[packet_id] = {
            "run_id": record["id"], "arm": record["arm"], "valid": valid,
            "answer_sha256": sha256(packet / "answer.txt"),
            "trace_sha256": sha256(packet / "trace.jsonl") if trace.is_file() else None,
            "input_digest": input_digest(packet / "inputs"),
        }
        commands = [c["command"] for c in result.get("commands", []) if c.get("command")]
        metrics.append({"arm": record["arm"], "valid": valid,
                        "seconds": result.get("seconds"), "usage": result.get("usage", {}),
                        "command_count": len(commands),
                        "repeated_command_count": len(commands) - len(set(commands))})
    write_json(output / "private-mapping.json", mapping)
    write_json(output / "metrics.json", metrics)
    write_json(output / "provenance.json", {
        "manifest_sha256": sha256(run / "manifest.json"),
        "rubric_sha256": sha256(review / "rubric.json"),
        "limitations": ["Only version labels are masked; content can reveal identity.",
                        "No semantic pass is inferred from execution success.",
                        "Repeated commands are observations, not necessarily waste.",
                        "Token usage is provider-reported; bytes are not tokens or prices.",
                        "Full source artifacts remain in the original run directory."],
    })
    return summarize(output)


def summarize(output: Path) -> dict:
    review = output / "review"
    provenance = read_json(output / "provenance.json")
    if sha256(review / "rubric.json") != provenance["rubric_sha256"]:
        raise ValueError("frozen rubric changed")
    rubric = load_rubric(review / "rubric.json")
    required = {r["id"] for r in rubric if r["mandatory"]}
    expected = {r["id"] for r in rubric}
    arms = defaultdict(lambda: {"runs": 0, "valid_runs": 0, "reviewed_valid_runs": 0,
                               "pending_or_invalid_grades": 0, "mandatory_failures": 0,
                               "reported_passes": 0, "rubric_warnings": 0})
    for packet_id, record in read_json(output / "private-mapping.json").items():
        row = arms[record["arm"]]
        row["runs"] += 1
        row["valid_runs"] += int(record["valid"])
        packet = child(review, packet_id)
        evidence_ok = sha256(packet / "answer.txt") == record["answer_sha256"]
        if record["trace_sha256"]:
            evidence_ok &= sha256(packet / "trace.jsonl") == record["trace_sha256"]
        evidence_ok &= input_digest(packet / "inputs") == record["input_digest"]
        try:
            grade = read_json(packet / "grading.json")
            entries = grade["expectations"]
            feedback = grade["rubric_feedback"]
            complete = (grade["packet_id"] == packet_id and len(entries) == len(expected)
                        and {e["id"] for e in entries} == expected
                        and all(type(e["passed"]) is bool and isinstance(e["evidence"], str)
                                and e["evidence"].strip() for e in entries)
                        and feedback["reviewed"] is True
                        and isinstance(feedback["weak"], list) and isinstance(feedback["uncovered"], list))
        except (OSError, ValueError, KeyError, TypeError):
            complete = False
        if not (record["valid"] and evidence_ok and complete):
            row["pending_or_invalid_grades"] += 1
            continue
        row["reviewed_valid_runs"] += 1
        row["reported_passes"] += sum(e["passed"] for e in entries)
        row["mandatory_failures"] += sum(not e["passed"] and e["id"] in required for e in entries)
        row["rubric_warnings"] += len(feedback["weak"]) + len(feedback["uncovered"])
    metrics = read_json(output / "metrics.json")
    for arm, row in arms.items():
        valid = [r for r in metrics if r["arm"] == arm and r["valid"]]
        seconds = [r["seconds"] for r in valid if isinstance(r["seconds"], (int, float))]
        row["seconds_mean"] = statistics.mean(seconds) if seconds else None
        row["seconds_sample_sd"] = statistics.stdev(seconds) if len(seconds) > 1 else None
        row["usage_totals"] = {}
        for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
            values = [r["usage"].get(key) for r in valid]
            row["usage_totals"][key] = (sum(values) if values and all(
                isinstance(v, int) and not isinstance(v, bool) for v in values) else None)
        row["repeated_command_count"] = sum(r["repeated_command_count"] for r in valid)
    return {"schema_version": "method-eval-summary.v1", "arms": dict(arms),
            "verdict": "human_review_required", "limitations": provenance["limitations"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    create = sub.add_parser("export")
    create.add_argument("--run", type=Path, required=True)
    create.add_argument("--rubric", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    score = sub.add_parser("summarize")
    score.add_argument("output", type=Path)
    args = parser.parse_args()
    result = (export(args.run, args.rubric, args.output) if args.mode == "export"
              else summarize(args.output))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
