"""Keep Plan team retries and handoffs tied to the original membership."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent


def plan_team_generation_started(runtime: Any, stage: Any, event: Any) -> bool:
    if (stage.id not in {"prd-scan", "prd-plan"} or not any(
            child.payload.get("stage_obligation") for child in stage.children)):
        return False
    run = str(event.payload.get("workflow_run_id") or event.correlation_id or "")
    if not run:
        return False
    # The workflow request fixes the Requirement version for the Run. A
    # configured bounded replan or an authorized new generation may retry.
    keys = ("request_revision", "requirement_spec_digest", "stage_replan_generation", "rework_of", "rework_attempt")

    def identity(payload: dict) -> tuple:
        return tuple(str(payload.get(k) or "") for k in keys)

    generation = identity(event.payload)
    return any(e.type == "fanout.started" and e.payload.get("stage_id") == stage.id
               and (e.payload.get("workflow_run_id") or e.correlation_id) == run
               and identity(e.payload.get("trigger_payload") or {}) == generation
               for e in runtime.event_log.read_all())


def plan_member_results(manifest: dict) -> list[dict]:
    """Bind upstream evidence to the named recipient using the kernel child manifest."""
    results = []
    for child in manifest.get("children", []):
        obligation = (child.get("payload") or {}).get("stage_obligation")
        if not obligation or child.get("status") != "completed":
            continue
        results.append({"obligation_id": obligation["obligation_id"], "recipient": obligation["recipient"],
                        "role_instance": child["role_instance"], "child_id": child["child_id"],
                        "attempt": child["run_id"], "result_event_id": child.get("result_event_id", ""),
                        "report_ref": child.get("report_path", ""),
                        "admitted_call_result_ref": child.get("admitted_call_result_ref", {})})
    return results


def _plan_inputs_match(author: dict, manifest: dict, receipt: dict) -> bool:
    results = plan_member_results(manifest)
    expected = set(author["input_refs"][1:])
    expected.update(c["payload"]["stage_obligation"]["obligation_id"] for c in manifest.get("children", [])
                    if c.get("extension_event_id"))
    return bool(results and {r["obligation_id"] for r in results} == expected
                and all(r["result_event_id"] and r["recipient"] == author["role"] for r in results)
                and results == receipt.get("stage_member_results"))


def admission_replan_entry(
    config: Any, stage: Any, events: list[ZfEvent], rejected: ZfEvent, failure: ZfEvent,
    state_dir: Path | None, payload: dict,
) -> tuple[str, set[str]]:
    """Recover the rejected Planner's actual scan, or send its approved roster fresh inputs."""
    if stage.id != "prd-plan" or stage.trigger != "prd.scan.completed" or not getattr(stage, "children", None):
        return stage.trigger, {stage.trigger}
    author = stage.children[0].payload.get("stage_obligation") or {}
    scan = next((s for s in config.workflow.stages if s.id == "prd-scan"), None)
    if author.get("obligation_id") != "plan-author" or not scan:
        return stage.trigger, {stage.trigger}

    from zf.runtime.fanout import FanoutManifestProjector

    entries = {stage.trigger, scan.trigger}
    run = rejected.payload.get("workflow_run_id") or rejected.correlation_id
    # A manual amendment can have no Planner fanout. Never borrow a different
    # Plan's old scan just because it happens to be the latest one in the Run.
    plan_id = payload.pop("fanout_id", None)
    payload.pop("stage_member_results", None)
    if not run or not plan_id or not state_dir or rejected.origin != "kernel":
        return scan.trigger, entries
    scoped = [e for e in events if e.origin == "kernel"
              and (e.payload.get("workflow_run_id") or e.correlation_id) == run]
    prior = {e.id: e for e in scoped[:scoped.index(rejected)]}
    planner = next((e for e in prior.values() if e.type == "fanout.started"
                    and e.payload.get("stage_id") == stage.id and e.payload.get("fanout_id") == plan_id), None)
    receipt = prior.get(planner.payload.get("trigger_event_id")) if planner else None
    current = {**rejected.payload, **{k: v for k, v in failure.payload.items() if v}}
    bindings = ("request_id", "request_revision", "requirement_spec_digest", "pdd_id", "prd_ref",
                "target_ref", "source_ref", "workflow_input_manifest_ref", "workflow_input_manifest_digest",
                "effective_config_digest", "prd_digest", "source_digest", "prd_revision", "source_revision")
    if (receipt and receipt.type == stage.trigger
            and receipt.payload.get("fanout_id")
            and all(not current.get(k) or receipt.payload.get(k) == current[k] for k in bindings)):
        manifest = FanoutManifestProjector(state_dir).rebuild(receipt.payload["fanout_id"], scoped)
        if (manifest.get("stage_id") == scan.id and (manifest.get("workflow_run_id") or manifest.get("trace_id")) == run
                and _plan_inputs_match(author, manifest, receipt.payload)):
            payload["fanout_id"] = receipt.payload["fanout_id"]
            payload["stage_member_results"] = receipt.payload["stage_member_results"]
            return stage.trigger, entries
    return scan.trigger, entries


def plan_member_inputs_admitted(runtime: Any, stage: Any, event: Any) -> bool:
    if stage.id != "prd-plan" or stage.trigger != "prd.scan.completed" or not stage.children:
        return True
    author = stage.children[0].payload.get("stage_obligation")
    if not author or author.get("obligation_id") != "plan-author":
        return True
    run = event.payload.get("workflow_run_id") or event.correlation_id
    scan = event.payload.get("fanout_id")
    events = runtime.event_log.read_all()
    source = next((e for e in events if scan and run and e.origin == "kernel" and e.type == "fanout.started"
                   and e.payload.get("stage_id") == "prd-scan" and e.payload.get("fanout_id") == scan
                   and (e.payload.get("workflow_run_id") or e.correlation_id) == run), None)
    if source:
        manifest = runtime._fanout_manifest(scan)
        if not manifest:
            from zf.runtime.fanout import FanoutManifestProjector

            scoped = [e for e in events if e.origin == "kernel"
                      and (e.payload.get("workflow_run_id") or e.correlation_id) == run]
            manifest = FanoutManifestProjector(runtime.state_dir).rebuild(scan, scoped)
        if _plan_inputs_match(author, manifest, event.payload):
            return True
    if not any(e.type == "task.fanout.rejected" and e.causation_id == event.id
               and e.payload.get("reason") == "plan_team_handoff_incomplete" for e in events):
        runtime.event_writer.append(ZfEvent(type="task.fanout.rejected", actor="zf-cli", origin="kernel",
            task_id=event.task_id, causation_id=event.id, correlation_id=event.correlation_id,
            payload={"reason": "plan_team_handoff_incomplete", "stage_id": stage.id,
                     "workflow_run_id": run, "source_event_id": event.id,
                     "recovery_owner": "run_manager", "diagnosis_status": "required"}))
    return False
