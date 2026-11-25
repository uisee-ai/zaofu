"""Project a verdict-only Plan handoff from the producer reports the Critic actually read."""

from typing import Any

from zf.runtime.fanout_artifact_refs import rebind_fanout_refs
from zf.runtime.plan_synth_handoff import _child_artifact_refs
from zf.runtime.plan_synth_recovery_refs import bound_critic_candidate_refs
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, verify_sidecar_ref


def reviewed_plan_children(runtime: Any, manifest: dict, review: dict) -> list[dict]:
    if manifest.get("fanout_id") != review.get("fanout_id"):
        raise ValueError("reviewed Plan fanout identity differs from the current handoff")
    refs = bound_critic_candidate_refs(runtime.state_dir, runtime.event_log.read_all(), review)
    if not refs:
        raise ValueError("reviewed Plan has no matching frozen Critic sources")
    artifacts = {ref["source_id"]: ref for ref in refs if ref["source_id"].startswith("child-artifact-")}
    for artifact in artifacts.values():
        verify_sidecar_ref(runtime.state_dir, artifact)
    pinned_paths = {artifact["ref"] for artifact in artifacts.values()}
    producers = {}
    for ref in refs:
        source_id = ref["source_id"]
        if not source_id.startswith("child-result-"):
            continue
        child_id = source_id.removeprefix("child-result-")
        body = hydrate_sidecar_ref(runtime.state_dir, ref).payload
        if not isinstance(body, dict) or body.get("child_id", child_id) != child_id or child_id in producers:
            raise ValueError(f"invalid reviewed Plan producer report for {child_id}")
        # The candidate builder assigns this source ID before pinning a project
        # path into attempt-inputs. Reuse the exact binding after a workdir move.
        replacements = {}
        for index, original in enumerate(_child_artifact_refs(body), start=1):
            pinned = artifacts.get(f"child-artifact-{child_id}-{index}")
            if pinned:
                replacements[original] = pinned["ref"]
        for key in ("plan_artifact_ref", "plan_ref", "task_map_ref", "source_index_ref"):
            path = body.get(key)
            if isinstance(path, str) and path and ":" not in path and path not in replacements and path not in pinned_paths:
                raise ValueError(f"reviewed Plan {key} has no frozen artifact binding for {child_id}")
        producers[child_id] = {"child_id": child_id, "report_path": ref["ref"],
                               "report": rebind_fanout_refs(body, replacements)}
    children = manifest.get("children") or []
    ids = [row.get("child_id") for row in children if isinstance(row, dict)]
    if len(ids) != len(children) or len(ids) != len(producers) or set(ids) != set(producers):
        raise ValueError("reviewed Plan producers differ from the current fanout")
    return [producers[child_id] for child_id in ids]
