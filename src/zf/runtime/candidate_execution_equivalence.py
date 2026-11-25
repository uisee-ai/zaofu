"""Prove identity-only refreezes without weakening either consumer's authority."""
from copy import deepcopy
from pathlib import Path

from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def same_execution_contract(state_dir: Path, source: dict, consumer: dict, *, command_id: str) -> bool:
    """Compare all contract content except kernel-generated freeze locators.

    Task/Plan generations, child authorities and every unknown future field stay
    significant. This narrow proof does not infer patch or semantic equivalence.
    """
    def contract(request):
        request = {**request.get("result_identity", {}), **request}
        body = hydrate_sidecar_ref(state_dir, {"ref": request["contract_snapshot_ref"],
            "sha256": request["contract_snapshot_digest"]}).payload
        target = hydrate_sidecar_ref(state_dir, {"ref": request["target_snapshot_ref"],
            "sha256": request["target_snapshot_digest"]}).payload
        if (body.get("authority_scope") != "candidate"
                or target.get("contract_snapshot_digest") != request["contract_snapshot_digest"]
                or target.get("target_commit") != request["target_commit"]):
            raise ValueError("candidate execution snapshot binding mismatch")
        scope = body.get("candidate_reader_scope")
        if scope:
            # A shared command proves only execution on the same kernel-frozen
            # parent. Reader-specific acceptance stays with each consumer.
            binding = ("candidate_event_id", "target_commit", "plan_artifact_package_digest",
                       "assignments_digest", "parent_contract_digest")
            if (not all(scope.get(k) for k in binding)
                    or scope["role_instance"] != request.get("role_instance")
                    or scope["candidate_event_id"] != body.get("candidate_event_id")
                    or scope["target_commit"] != target.get("target_commit")
                    or scope["plan_artifact_package_digest"] != body.get("plan_artifact_package_digest")):
                raise ValueError("scoped command parent binding mismatch")
            commands = [cmd for cmd in body["verification_commands"] if cmd.get("command_id") == command_id]
            if len(commands) != 1:
                raise ValueError("shared command is not pinned to this Reader")
            cmd = commands[0]
            if not cmd.get("acceptance_ids") or set(cmd["acceptance_ids"]) - set(scope["acceptance_ids"]):
                raise ValueError("shared command lacks Reader acceptance binding")
            return ("scoped", tuple(scope[k] for k in binding),
                    {k: v for k, v in cmd.items() if k != "acceptance_ids"})
        normalized = deepcopy(body)
        for field in ("contract_revision", "candidate_event_id", "source_ref"):
            normalized.pop(field, None)
        for field in ("source_refs", "evidence_contract"):
            nested = normalized.get(field, {})
            for key in ("candidate_event_id", "freeze_receipt_ref", "freeze_receipt_digest"):
                nested.pop(key, None)
            for key in ("freeze_receipt_ref", "freeze_receipt_digest"):
                nested.get("source_refs", {}).pop(key, None)
        return normalized

    return (all(source.get(k) == consumer.get(k) for k in
                ("workflow_run_id", "task_id", "task_map_generation", "target_commit"))
            and contract(source) == contract(consumer))


def consumer_dependency_digest(runtime, payload: dict) -> str | None:
    """Pin declared dependency bytes from the actual consumer workspace."""
    from zf.runtime.task_host_manifest import dependency_digest, git
    from zf.runtime.workdirs import WorkdirManager

    config = getattr(getattr(runtime.config, 'runtime', None), 'workdirs', None)
    if config is None:
        return None
    root = runtime.project_root
    if config.required_paths or config.provision_paths:
        role = next((r for r in runtime.config.roles if r.instance_id == payload.get('role_instance')), None)
        if role is None:
            return None
        plan = WorkdirManager(state_dir=runtime.state_dir, project_root=root, config=runtime.config).plan(role)
        if not plan.enabled or plan.mode != 'worktree' or plan.role_kind != 'reader':
            return None
        root = Path(plan.project_path)
        if git(root, 'rev-parse', 'HEAD') != payload['target_commit']:
            return None
    return dependency_digest(runtime, root)
