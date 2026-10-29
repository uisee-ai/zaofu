"""Evaluate one method-only candidate with disjoint selection and held-out cases."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from tests.e2e.evidence_review_194 import Provider, REPORT_SCHEMA
from tests.e2e.staged_workflow_comparison import digest
from tests.test_evolution_skill_optimizer_closure import _campaign
from zf.core.events import EventLog, EventWriter
from zf.runtime.evolution_skill_optimizer import SkillOptimizationService
from zf.runtime.evolution_skill_optimizer_contracts import normalize_campaign

CASES = {
    "selection": {"id": "shipment-observation", "claim": "Helix shipment arrived on Monday.",
        "sources": {"D1": "Dispatch plan: expected arrival Monday.", "D2": "Signed receiving record: arrived Tuesday."}},
    "test": {"id": "measurement-observation", "claim": "Atlas observed uptime was at least 99.9% in September.",
        "sources": {"D1": "Uptime objective for September: 99.9%.", "D2": "Final September measurement signed by auditor: 99.7%."}},
}
ADDITION = "\n逐项分别记录主张真假与证据充分性；目标/计划不能替代观测记录，冲突只能由有权且适用的修订消解。\n"


def evaluate(root: Path, train_path: Path, *, resume_results=False):
    root.mkdir(parents=True, exist_ok=resume_results)
    base = Path("skills/zf-evidence-synthesis/SKILL.md").read_text()
    candidate = base + ADDITION
    (root / "candidate.md").write_text(candidate)
    (root / "base.md").write_text(base)
    campaign = _campaign(max_epochs=1)
    campaign.update(campaign_id="evidence-method-194", skill_name="zf-evidence-synthesis", base_content=base)
    # Replace the generic test builder's identity and datasets with actual assets.
    campaign["score_dimensions"] = [{"id": "correctness", "weight": 1, "blocking": True}]
    descriptors = campaign["dataset_splits"]
    material = {"train": json.loads(train_path.read_text()), **CASES}
    for split, body in material.items():
        path = root / (split + ".json")
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2))
        descriptors[split].update(ref=str(path), digest=hashlib.sha256(path.read_bytes()).hexdigest(),
            case_ids=["orion-evidence" if split == "train" else body["id"]],
            fixture_digests=[digest(body)], lineage_refs=["source://" + ("orion" if split == "train" else body["id"])])
    campaign["eval_suite_digest"] = descriptors["selection"]["digest"]
    campaign["frozen_identity"] = frozen_identity(campaign["eval_suite_digest"], base)
    campaign["candidate_metadata"].update(task_families=["general"],
        source_trajectories=[{"ref": str(train_path), "digest": hashlib.sha256(train_path.read_bytes()).hexdigest(), "outcome": "neutral"}],
        applicability_ref=str(root / "candidate.md"), applicability_digest=hashlib.sha256(candidate.encode()).hexdigest(),
        public_eval_suite_ref=descriptors["test"]["ref"], public_eval_suite_digest=descriptors["test"]["digest"])
    normalized, _ = normalize_campaign(campaign)
    (root / "campaign.json").write_text(json.dumps(campaign, ensure_ascii=False, indent=2))
    provider = Provider(root, "gpt-6-astra", {"max_tokens":80000,"max_provider_calls":4,"turn_timeout_seconds":120}, True)
    if resume_results:
        provider.turns = json.loads((root / "audit.json").read_text())
    results = {}
    try:
        for split, case in CASES.items():
            results[split] = {}
            for arm, method in [("base", base), ("candidate", candidate)]:
                prompt = method + "\nClassify claim C1 using only the provided sources. Cite source IDs. " + json.dumps(case)
                name = split + "-" + arm
                if resume_results:
                    audit = next(turn for turn in provider.turns if turn["name"] == name)
                    assert audit["prompt_digest"] == digest("Use only inline evidence. Do not invoke tools, commands, search or subagents. Return JSON only.\n" + prompt)
                    assert audit["status"] == "completed" and audit["tools_used"] == 0
                    report = json.loads((root / name / "result.json").read_text())
                else:
                    report = provider.call(name, prompt, REPORT_SCHEMA)
                claims = report.get("claims", [])
                score = float(len(claims) == 1 and claims[0]["id"] == "C1" and claims[0]["status"] == "refuted" and "D2" in claims[0]["sources"])
                results[split][arm] = {"score": score, "report": report}
                (root / f"{split}-{arm}-grade.json").write_text(json.dumps(results[split][arm]))
        def evaluation(arm, content):
            descriptor = normalized["dataset_splits"]["selection"]
            result_path = root / f"selection-{arm}-grade.json"
            return {"schema_version": "skill-optimization-evaluation.v1", "campaign_id": campaign["campaign_id"],
                "candidate_digest": hashlib.sha256(content.encode()).hexdigest(), "eval_suite_digest": campaign["eval_suite_digest"],
                "frozen_identity_digest": normalized["frozen_identity_digest"], "split": "selection",
                "split_ref": descriptor["ref"], "split_digest": descriptor["digest"],
                "split_descriptor_digest": descriptor["descriptor_digest"], "evaluator_generation_ref": descriptor["generation_ref"],
                "scores": {"correctness": results["selection"][arm]["score"]},
                "case_result_refs": [{"case_id": CASES["selection"]["id"], "ref": str(result_path), "digest": hashlib.sha256(result_path.read_bytes()).hexdigest()}]}
        state = root / "optimizer-state"
        writer = EventWriter(EventLog(state / "events.jsonl"))
        service = SkillOptimizationService(state_dir=state, event_log=writer.event_log, event_writer=writer)
        initial = service.initialize(campaign, baseline_evaluation=evaluation("base", base))
        step = service.prepare_step(initial["state_ref"], proposal={"schema_version": "skill-edit-proposal.v1",
            "campaign_id": campaign["campaign_id"], "base_digest": hashlib.sha256(base.encode()).hexdigest(),
            "edits": [{"edit_id": "add-observation-rule", "operation": "replace", "old_text": base, "new_text": candidate}]})
        settled = service.settle_step(initial["state_ref"], step["step_ref"], evaluation=evaluation("candidate", candidate))
        report = {"decision": "HOLD", "reason": "single_case_per_split; unknown_monetary_cost; no automatic promotion",
            "selection": settled["selection"], "selection_reason": settled["selection_reason"], "results": results,
            "campaign_digest": digest(campaign), "turns": provider.turns,
            "equivalence": {"roles": "unchanged", "dependencies": "unchanged", "required_evidence": "unchanged", "completion_contract": "unchanged"},
            "production_changed": False, "state_ref": settled["state_ref"]}
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        return report
    finally:
        (root / "audit.json").write_text(json.dumps(provider.turns, indent=2))


def frozen_identity(suite_digest, base):
    command = lambda argv: subprocess.check_output(argv, text=True).strip()
    return {
        "eval_suite_digest": suite_digest,
        "grader_digest": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "model_digest": digest({"id": "gpt-6-astra", "effort": "low"}),
        "prompt_digest": digest("Classify claim C1 using only the provided sources. Cite source IDs."),
        "provider_digest": digest(command(["codex", "--version"])),
        "support_skill_inventory_digest": digest([]),
        "workspace_fixture_digest": digest(CASES),
        "runtime_commit_digest": digest(command(["git", "rev-parse", "HEAD"])),
        "role_profile_digest": digest({"role": "reader", "schema": REPORT_SCHEMA}),
        "tool_policy_digest": digest({"tools": [], "audit": "reject_any_tool_call"}),
        "sandbox_policy_digest": digest("read-only"),
        "network_policy_digest": digest("provider transport only; inline evidence"),
        "evaluator_generation_digest": digest({"cases": CASES, "method": base}),
        "budget_digest": digest({"max_tokens": 80000, "max_provider_calls": 4, "turn_timeout_seconds": 120}),
    }


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--confirm-real", action="store_true")
    parser.add_argument("--resume-results", action="store_true")
    args = parser.parse_args()
    if not args.confirm_real:
        parser.error("--confirm-real required")
    print(json.dumps(evaluate(args.root, args.train, resume_results=args.resume_results), ensure_ascii=False))


if __name__ == "__main__":
    main()
