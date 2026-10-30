"""Bounded blind evidence review pilot, using existing Generic Workflow admission.

Only authors/planner/verifiers below use a real provider. Kernel orchestration,
replan and restart are driven by the existing deterministic scenario. This is
hybrid runtime evidence, not a qualification of every real worker transport.
"""
from __future__ import annotations

import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import time
import threading
from unittest.mock import patch

from tests.e2e.staged_workflow_comparison import digest, run_process
from tests.e2e.oa_provider_ab_pilot import provider_usage
from tests.e2e.test_generic_workflow_complex_mock_e2e import run_generic_workflow_complex_scenario

CLAIMS = {"C1": "Orion v2 supports CSV export.", "C2": "Orion v2 retains logs for 90 days.",
          "C3": "An actual Orion v2 recovery drill restored service within four hours."}
SOURCES = {"S1": "Release 2.0 notes: CSV export is available.",
           "S2": "Orion v2 operating settings, Oct 2: log retention = 30 days.",
           "S3": "Orion v2 policy, Oct 2: log retention = 90 days. Same authority as operating settings; conflict unresolved.",
           "S5": "Disaster recovery plan: target restore time is four hours. This document contains no actual drill result."}
SUPPLEMENT = {"S4": "Signed correction, Oct 3: S3's 90-day retention is erroneous. S2's 30-day retention is authoritative for Orion v2.",
              "S6": "Signed recovery drill log, Oct 4: Orion v2 service restore began 09:00 and finished 12:00; independent observer confirmed."}
REPORT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["claims"], "properties": {
    "claims": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["id", "status", "sources", "reason"], "properties": {
        "id": {"type": "string"}, "status": {"type": "string", "enum": ["supported", "refuted", "conflict", "unknown"]},
        "sources": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}}}}}}
VERIFY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["valid", "ready", "issues"],
    "properties": {"valid": {"type": "boolean"}, "ready": {"type": "boolean"},
                   "issues": {"type": "array", "items": {"type": "string"}}}}
PLAN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["groups"], "properties": {
    "groups": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "array", "items": {"type": "string"}}}}}


def hidden_grade(report, generation):
    # Evaluator-only labels. Never placed in provider working directories/prompts.
    expected = {"C1": ("supported", {"S1"}), "C2": ("conflict", {"S2", "S3"}), "C3": ("unknown", {"S5"})} if generation == 1 else {
        "C1": ("supported", {"S1"}), "C2": ("refuted", {"S4"}), "C3": ("supported", {"S6"})}
    rows = report.get("claims", [])
    issues = []
    if len(rows) != 3 or {row["id"] for row in rows} != set(expected):
        return ["claim_coverage"]
    allowed = set(SOURCES) | (set(SUPPLEMENT) if generation == 2 else set())
    for row in rows:
        status, refs = expected[row["id"]]
        if row["status"] != status or not refs <= set(row["sources"]) or not set(row["sources"]) <= allowed:
            issues.append(row["id"])
    return issues


class Provider:
    def __init__(self, root, model, budget, real):
        self.root, self.model, self.budget, self.real = root, model, budget, real
        self.turns = []
        self._lock = threading.Lock()
        self._reserved_calls = 0

    def call(self, name, prompt, schema):
        with self._lock:
            if self._reserved_calls >= self.budget.get("max_provider_calls", 10):
                raise RuntimeError("pilot provider call budget exhausted")
            self._reserved_calls += 1
        folder = self.root / name
        folder.mkdir()
        (folder / "schema.json").write_text(json.dumps(schema))
        prompt = "Use only inline evidence. Do not invoke tools, commands, search or subagents. Return JSON only.\n" + prompt
        result = run_process(["codex", "exec", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
            "--ephemeral", "--json", "--sandbox", "read-only", "--model", self.model,
            "--config", 'model_reasoning_effort="low"', "--output-schema", str(folder / "schema.json"),
            "--output-last-message", str(folder / "result.json"), "-C", str(folder), "-"],
            cwd=folder, timeout=self.budget.get("turn_timeout_seconds", 120), stdin=prompt, new_session=False)
        rows = []
        for line in result["stdout"].splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        usage = provider_usage(rows)
        tools = [row for row in rows if (row.get("item") or {}).get("type") in {"command_execution", "mcp_tool_call", "web_search", "file_change"}]
        audit = {"name": name, "prompt_digest": digest(prompt), "schema_digest": digest(schema),
                 "usage": usage, "elapsed_seconds": result["elapsed_seconds"], "status": result["status"], "tools_used": len(tools)}
        self.turns.append(audit)
        (folder / "trace.json").write_text(json.dumps({**result, "audit": audit}, ensure_ascii=False))
        if result["status"] != "completed" or tools:
            raise RuntimeError("provider failed or violated blind inline-only policy: " + json.dumps(audit))
        total = sum(turn["usage"]["input_tokens"] + turn["usage"]["output_tokens"] for turn in self.turns)
        if total > self.budget.get("max_tokens", 60000):
            raise RuntimeError("pilot token budget exhausted")
        return json.loads((folder / "result.json").read_text())


def author(provider, arm, generation):
    sources = {**SOURCES, **(SUPPLEMENT if generation == 2 else {})}
    context = {"claims": CLAIMS, "sources": sources}
    rubric = "Classify each assigned claim from the sources. Preserve unresolved same-authority conflicts. A target is not a measured result. Cite source IDs for every judgment. "
    prefix = f"g{generation}"
    if arm == "direct":
        return provider.call(prefix + "-author", rubric + json.dumps(context), REPORT_SCHEMA)
    groups = [["C1", "C2"], ["C3"]]
    if arm == "dynamic":
        plan = provider.call(prefix + "-plan", "Partition all claim IDs into at most two nonempty groups for independent evidence checks. " + json.dumps(context), PLAN_SCHEMA)
        groups = plan["groups"]
        if not 1 <= len(groups) <= 2 or any(not group for group in groups) or sorted(sum(groups, [])) != sorted(CLAIMS):
            raise RuntimeError("dynamic plan violates registered fanout bounds")
    def collect(item):
        index, group = item
        return provider.call(prefix + f"-collector-{index}", rubric + json.dumps({"claims": {key: CLAIMS[key] for key in group}, "sources": sources}), REPORT_SCHEMA)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(collect, enumerate(groups)))
    return provider.call(prefix + "-synth", rubric + "Synthesize all claims, checking these collector reports against sources. " + json.dumps({**context, "reports": reports}), REPORT_SCHEMA)


def run_pilot(job, root):
    model = job["identity"]["model"]
    provider = Provider(root, model, job.get("budget", {}), True)
    reports, verifications, grades = {}, {}, {}
    def transform(generation, _):
        report = author(provider, job["arm"], generation)
        sources = {**SOURCES, **(SUPPLEMENT if generation == 2 else {})}
        verdict = provider.call(f"g{generation}-verifier", "Independently check claim coverage, judgments and citations. valid means the report accurately characterizes evidence, including gaps. ready means valid and no unresolved conflict or unknown claim remains. " + json.dumps({"claims": CLAIMS, "sources": sources, "report": report}), VERIFY_SCHEMA)
        reports[generation], verifications[generation] = report, verdict
        grades[generation] = hidden_grade(report, generation)
        if grades[generation] or verdict["valid"] is not True or verdict["ready"] is not (generation == 2):
            raise AssertionError("blind evidence acceptance failed: " + json.dumps({"generation": generation, "grade": grades[generation], "verifier": verdict}))
        return {"evidence_review": report, "sources": sources, "independent_verifier": verdict}
    def admit_verifier(project_root, state_dir, sources, expected):
        # Only kernel-owned identity/envelope fields are adapted here. The actual
        # semantic verdict comes from a separate blind provider call above.
        result = deepcopy(dict(expected))
        result["summary"] = "Independent provider verified source-grounded evidence; evaluator labels were withheld."
        assert verifications[2]["valid"] and verifications[2]["ready"] and not grades[2]
        return result
    scenario_root = root / "runtime"
    scenario_root.mkdir()
    try:
        with patch("zf.runtime.workflow_runtime_start.ensure_project_runtime", return_value={"ok": True, "status": "test_driver", "launched": False}):
            runtime = run_generic_workflow_complex_scenario(scenario_root, provider_verifier=admit_verifier,
                provider_backend="codex", report_transform=transform,
                objective="核验 Orion v2 的 CSV 导出、90 天日志保留和四小时内实际恢复三项主张，记录来源、冲突与缺失证据。")
        usage = [turn["usage"] for turn in provider.turns]
        return {"identity": job["identity"], "execution_tier": "hybrid_real_provider", "runtime_receipts": runtime,
            "reports": reports, "verifications": verifications, "grades": grades, "turns": provider.turns,
            "metrics": {"accepted": True, "missed_checks": 0, "wrong_routes": 0, "retries": 1,
                "tokens": sum(row["input_tokens"] + row["output_tokens"] for row in usage),
                "cost_usd": sum(row["cost_usd"] for row in usage) if all(row["cost_usd"] is not None for row in usage) else None,
                "interventions": 0, "cancel_requested": 0, "cancel_confirmed": 0, "duplicate_effects": 0,
                "stale_rejected": 1, "integrated": bool(runtime["terminal_event_id"]) and runtime["dossier_status"] == "ready"}}
    finally:
        (root / "provider-audit.json").write_text(json.dumps(provider.turns, ensure_ascii=False, indent=2))
        from zf.core.config.loader import load_config
        from zf.core.events import EventLog, EventWriter

        project = scenario_root / "project"
        if (project / "zf.yaml").exists():
            state = Path(load_config(project / "zf.yaml").project.state_dir)
            state = state if state.is_absolute() else project / state
            log = EventLog(state / "events.jsonl")
            if not any(event.type == "simulation.done" for event in log.read_all()):
                EventWriter(log).emit("simulation.done", actor="zf-cli", payload={
                    "schema_version": "simulation-lifecycle.v1", "status": "aborted",
                    "reason": "pilot exited before admitted terminal", "sample_arm": job["arm"],
                })


def comparison_manifest():
    repo = Path(__file__).resolve().parents[2]
    paths = [*sorted((repo / "src").rglob("*.py")), *sorted((repo / "tests/e2e").glob("*.py")), repo / "uv.lock"]
    sources = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    budget = {"max_tokens": 60000, "max_provider_calls": 4, "turn_timeout_seconds": 120}
    cases = [{"id": "orion-evidence", "route": "general", "scenario": "user_revision",
              "input": {"claims": CLAIMS, "sources": SOURCES, "supplement": SUPPLEMENT}}]
    command = [sys.executable, "-c", "import runpy,sys; sys.path.insert(0," + repr(str(repo)) + "); sys.argv=['evidence_review_194','--job',sys.argv[1],'--confirm-real']; runpy.run_module('tests.e2e.evidence_review_194',run_name='__main__')", "{job}"]
    return {"schema_version": "staged-workflow-comparison.v1", "source_files": sources,
        "identity": {"source_digest": digest(sources), "input_digest": digest(cases), "budget_digest": digest(budget),
            "acceptance_digest": digest({"schema": REPORT_SCHEMA, "grader_source": sources[str(Path(__file__).resolve())]}),
            "provider": "codex", "model": "gpt-6-astra", "tools_digest": digest("inline-only/no-tools")},
        "budget": budget, "repeats": 1, "timeout_seconds": 600, "cases": cases,
        "commands": {arm: command for arm in ("direct", "fixed", "dynamic")}}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--job", type=Path)
    parser.add_argument("--write-manifest", type=Path)
    parser.add_argument("--confirm-real", action="store_true")
    args = parser.parse_args()
    if args.write_manifest:
        args.write_manifest.write_text(json.dumps(comparison_manifest(), ensure_ascii=False, indent=2))
        return
    if args.job is None:
        parser.error("--job or --write-manifest required")
    if not args.confirm_real:
        parser.error("--confirm-real required")
    os.environ["ZF_WORKSPACE_HOME"] = str(args.job.parent / "workspace-registry")
    print(json.dumps(run_pilot(json.loads(args.job.read_text()), args.job.parent), ensure_ascii=False))


if __name__ == "__main__":
    main()
