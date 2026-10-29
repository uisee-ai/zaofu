"""Preregistered direct/fixed/dynamic comparison; failed samples remain visible.

Run with: uv run python -m tests.e2e.staged_workflow_comparison manifest.json output.json
Commands are explicit argv arrays; this evaluation tool is not a workflow scheduler.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile
import time

ARMS = ("direct", "fixed", "dynamic")
SCENARIOS = {"independent", "dependency", "file_conflict", "verify_failure", "user_revision", "restart"}
METRICS = ("accepted", "missed_checks", "wrong_routes", "retries", "tokens", "cost_usd", "interventions",
           "cancel_requested", "cancel_confirmed", "duplicate_effects", "stale_rejected", "integrated")
IDENTITY = ("input_digest", "acceptance_digest", "provider", "model", "tools_digest", "budget_digest", "source_digest")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def run_process(argv, *, cwd, timeout, stdin=None, new_session=True):
    """Bound one process group, including descendants left behind by its leader."""
    started = time.monotonic()
    process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=new_session)
    def kill():
        if new_session:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    status = "completed"
    try:
        try:
            stdout, stderr = process.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            status = "timeout"
            kill()
            stdout, stderr = process.communicate(timeout=5)
        return {"status": status if process.returncode == 0 or status == "timeout" else "failed",
                "returncode": process.returncode, "stdout": stdout, "stderr": stderr,
                "elapsed_seconds": time.monotonic() - started}
    finally:
        try:
            kill()
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def validate_manifest(manifest):
    if manifest.get("schema_version") != "staged-workflow-comparison.v1":
        raise ValueError("unsupported comparison schema")
    if set(manifest.get("commands", {})) != set(ARMS):
        raise ValueError("exactly three explicit arms required")
    if not all(manifest.get("identity", {}).get(key) for key in IDENTITY):
        raise ValueError("frozen identity incomplete")
    verify_inputs(manifest)
    if not 1 <= manifest.get("repeats", 0) <= 20 or not 0 < manifest.get("timeout_seconds", 0) <= 1800:
        raise ValueError("bounded repeats and timeout required")
    cases = manifest.get("cases", [])
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("unique cases required")
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", case["id"]) for case in cases):
        raise ValueError("case id must be a safe filename component")
    for argv in manifest["commands"].values():
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
            raise ValueError("commands must be argv arrays")


def verify_inputs(manifest):
    sources = manifest.get("source_files", {})
    if not sources or any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected for path, expected in sources.items()):
        raise ValueError("source file digest mismatch")
    for key, value in (("source_digest", sources), ("input_digest", manifest["cases"]), ("budget_digest", manifest.get("budget", {}))):
        if manifest["identity"][key] != digest(value):
            raise ValueError(key + " mismatch")


def valid_metrics(metrics):
    if not isinstance(metrics, dict) or set(METRICS) - metrics.keys():
        return False
    for key in METRICS:
        value = metrics[key]
        if value is None:
            continue  # Preserved as unknown and makes evaluation HOLD.
        if key in {"accepted", "integrated"}:
            if type(value) is not bool:
                return False
        elif type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            return False
    return True


def evaluate(manifest, rows):
    reasons = []
    expected = len(manifest["cases"]) * manifest["repeats"] * len(ARMS)
    if len(rows) != expected:
        reasons.append("missing_samples")
    routes = {}
    for route in sorted({case["route"] for case in manifest["cases"]}):
        selected = [row for row in rows if row["route"] == route]
        route_reasons = []
        if not SCENARIOS <= {row["scenario"] for row in selected}:
            route_reasons.append("scenario_coverage_incomplete")
        if manifest["repeats"] < 3:
            route_reasons.append("pilot_sample_size")
        summaries = {}
        for arm in ARMS:
            samples = [row for row in selected if row["arm"] == arm]
            valid = [row for row in samples if row["status"] == "completed"]
            if len(valid) != len(samples) or not samples:
                route_reasons.append(arm + ":failed_or_missing_samples")
            metrics = [row.get("metrics", {}) for row in samples]
            for values in metrics:
                if set(METRICS) - values.keys() or any(values.get(key) is None for key in METRICS):
                    route_reasons.append(arm + ":metrics_unknown")
                if values.get("accepted") is not True or values.get("integrated") is not True:
                    route_reasons.append(arm + ":acceptance_incomplete")
                if values.get("duplicate_effects") != 0 or values.get("cancel_requested") != values.get("cancel_confirmed"):
                    route_reasons.append(arm + ":lifecycle_not_closed")
                if any(type(value) in {int, float} and (not math.isfinite(value) or value < 0) for value in values.values()):
                    route_reasons.append(arm + ":invalid_metrics")
            if any(not row.get("evidence", {}).get("runtime_receipts") for row in samples):
                route_reasons.append(arm + ":runtime_evidence_missing")
            times = sorted(row["elapsed_seconds"] for row in samples)
            summaries[arm] = {"samples": len(samples), "completed": len(valid),
                "accepted": sum(row.get("accepted") is True for row in metrics),
                "tokens": sum(row.get("tokens") or 0 for row in metrics),
                "cost_usd": sum(row["cost_usd"] for row in metrics) if all(row.get("cost_usd") is not None for row in metrics) else None,
                "seconds": sum(times), "p95_seconds": times[max(0, math.ceil(len(times) * .95) - 1)] if times else None,
                "cancel_requested": sum(row.get("cancel_requested") or 0 for row in metrics),
                "cancel_confirmed": sum(row.get("cancel_confirmed") or 0 for row in metrics)}
        direct = summaries["direct"]
        for arm in ("fixed", "dynamic"):
            candidate = summaries[arm]
            if candidate["accepted"] < direct["accepted"]:
                route_reasons.append(arm + ":quality_regression")
            if candidate["seconds"] >= direct["seconds"] and candidate["tokens"] >= direct["tokens"]:
                route_reasons.append(arm + ":no_efficiency_gain")
        routes[route] = {"decision": "HOLD" if route_reasons else "CANARY_ELIGIBLE", "reasons": sorted(set(route_reasons)), "arms": summaries}
    if any(route["decision"] == "HOLD" for route in routes.values()):
        reasons.append("route_evidence_incomplete_or_regressed")
    return {"decision": "HOLD" if reasons else "CANARY_ELIGIBLE", "reasons": reasons, "routes": routes}


def run_comparison(manifest, output):
    validate_manifest(manifest)
    frozen = digest(manifest)
    rows = []
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    def persist():
        report = {"schema_version": "staged-workflow-comparison-result.v1", "manifest_digest": frozen,
                  "manifest": manifest, "rows": rows, **evaluate(manifest, rows)}
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(output)
        return report
    persist()  # Preregister before the first provider turn.
    for index in range(manifest["repeats"]):
        for case in manifest["cases"]:
            order = ARMS[index % 3:] + ARMS[:index % 3]
            for arm in order:
                row = {"case_id": case["id"], "route": case["route"], "scenario": case["scenario"],
                       "repeat": index, "arm": arm, "status": "interrupted", "elapsed_seconds": 0}
                try:
                    verify_inputs(manifest)
                    with tempfile.TemporaryDirectory(prefix="zf-194-comparison-", dir="/tmp") as directory:
                        root = Path(directory)
                        job = {"identity": manifest["identity"], "case": case, "arm": arm, "repeat": index,
                               "budget": manifest.get("budget", {})}
                        (root / "job.json").write_text(json.dumps(job))
                        argv = [arg.replace("{job}", str(root / "job.json")).replace("{root}", str(root))
                                for arg in manifest["commands"][arm]]
                        result = run_process(argv, cwd=root, timeout=manifest["timeout_seconds"])
                        row.update(result)
                        evidence_dir = output.with_suffix(".evidence") / f"{case['id']}-{index}-{arm}"
                        shutil.copytree(root, evidence_dir)
                        row["evidence_dir"] = str(evidence_dir)
                        if (root / "provider-audit.json").exists():
                            row["provider_audit"] = json.loads((root / "provider-audit.json").read_text())
                        if row["status"] == "completed":
                            body = json.loads(result["stdout"])
                            row["evidence"] = body
                            row["metrics"] = body["metrics"]
                            if not valid_metrics(row["metrics"]):
                                row.update(status="invalid_metrics", metrics={})
                            if body.get("identity") != manifest["identity"]:
                                row["status"] = "identity_mismatch"
                        verify_inputs(manifest)
                except (ValueError, KeyError, OSError) as exc:
                    row.update(status="failed", error=str(exc))
                finally:
                    rows.append(row)
                    persist()
    return persist()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    report = run_comparison(json.loads(args.manifest.read_text()), args.output)
    print(json.dumps({"decision": report["decision"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
