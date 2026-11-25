"""Bounded, opt-in container probes using the existing host-assist evidence gate.

Product code is never run on the host. The container sees only a copied source
snapshot and declared dependencies, no credentials, Git metadata or Docker socket.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from zf.core.state.locks import locked_path
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json
from zf.runtime.task_host_actions import apply
from zf.runtime.task_host_manifest import binding, dependency_digest, digest, git, preview, source_files
from zf.runtime.workflow_operation import reduce_workflow_operations


def container_command(policy, source: Path, output: Path, command: str, name: str) -> list[str]:
    if not policy.enabled or command not in policy.commands:
        raise ValueError('command is not preauthorized')
    if any(',' in str(path) for path in (source, output)):
        raise ValueError('container mount paths may not contain commas')
    # Writable tmpfs contains the disposable build; only evidence leaves it.
    return ['docker', 'run', '--pull=never', '--rm', '--name', name,
        '--network', policy.network, '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
        '--pids-limit=256', '--memory=2g', '--cpus=2',
        '--user', f'{os.getuid()}:{os.getgid()}',
        '--tmpfs', '/tmp:rw,nosuid,nodev,size=1073741824,mode=1777',
        # Docker tmpfs defaults to noexec; provisioned build tools need execution
        # only in this disposable container workspace, never on the host.
        '--tmpfs', '/work:rw,nosuid,nodev,exec,size=1073741824,mode=1777',
        '--mount', f'type=bind,src={source},dst=/source,readonly',
        '--mount', f'type=bind,src={output},dst=/evidence',
        '-e', 'HOME=/tmp', '-e', 'ZF_EVIDENCE_DIR=/evidence', '-e', 'NODE_PATH=/app/node_modules',
        '--entrypoint', '/bin/sh', policy.image, '-c',
        'mkdir /work/project && cp -a /source/. /work/project/ && cd /work/project && exec /bin/sh -c "$1"', 'zf-probe', command]


def _run_container(argv, *, timeout):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def verify_in_container(service, root: Path, manifest: dict, policy) -> tuple[dict, str]:
    command = manifest['command']
    declared_timeout = int(command.get('timeout_seconds') or 0)
    if not 0 < declared_timeout <= 900:
        raise ValueError('container probe requires a bounded contract timeout')
    source_digests = {item['path']: item['sha256'] for item in manifest['files']}
    if any(source_digests.get(path) != expected for path, expected in policy.protected_files.items()):
        raise ValueError('preauthorized verification input was modified or removed')
    name = 'zf-probe-' + manifest['manifest_digest'][:24]
    evidence_ref = None
    with tempfile.TemporaryDirectory(prefix='zf-container-probe-') as temporary:
        folder = Path(temporary)
        source, output = folder / 'source', folder / 'output'
        source.mkdir()
        output.mkdir()
        for item in manifest['files']:
            target = source / item['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / item['path'], target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('source changed while copying frozen snapshot')
        # dependency_digest rejects escaping links before any directory copy.
        if dependency_digest(service, root) != manifest['dependency_digest']:
            raise ValueError('dependency changed before snapshot')
        cfg = service.config.runtime.workdirs
        for relative in sorted(set(cfg.required_paths + cfg.provision_paths)):
            target = source / relative
            if target.exists():
                raise ValueError('dependency overlaps tracked source; separate its provisioning scope')
            for item in (root / relative).rglob('*') if (root / relative).is_dir() else [root / relative]:
                if item.is_symlink() and Path(os.readlink(item)).is_absolute():
                    raise ValueError('dependency snapshot requires relative internal symlinks')
            if (root / relative).is_dir():
                shutil.copytree(root / relative, target, symlinks=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / relative, target)
        if dependency_digest(service, source) != manifest['dependency_digest']:
            # Dereferenced links have a different digest; preserving relative,
            # internal symlinks is required for an identical dependency snapshot.
            raise ValueError('dependency snapshot differs from authorized input')
        argv = container_command(policy, source, output, command['command'], name)
        image_index = argv.index(policy.image)
        argv[image_index:image_index] = ['-e', 'ZF_CANDIDATE_COMMIT=' + manifest['head'],
                                      '-e', 'ZF_BROWSER_IMAGE_ID=' + policy.image]
        try:
            completed = _run_container(argv, timeout=min(declared_timeout, policy.timeout_seconds))
            code, text = completed.returncode, completed.stdout + completed.stderr
        except subprocess.TimeoutExpired:
            # The Docker client may exit while its container stays alive.
            _run_container(['docker', 'rm', '-f', name], timeout=15)
            code, text = 124, 'preauthorized container probe timed out'
        from zf.runtime.host_evidence_collection import collect_container_evidence

        artifacts, collection_error = collect_container_evidence(output, policy)
        if collection_error is None:
            evidence_ref = write_sidecar_json(service.state_dir,
                f'artifacts/task-host-actions/{manifest["manifest_digest"]}/container-evidence.json',
                {'image': policy.image, 'target_commit': manifest['head'], 'files': artifacts},
                kind='container_evidence', schema_version='container-evidence.v1',
                created_by=service.actor, required=True)
    stable = (git(root, 'rev-parse', 'HEAD') == manifest['head'] and source_files(root) == manifest['files']
              and not git(root, 'status', '--porcelain', '--untracked-files=all'))
    raw_output = text.encode('utf-8')
    output_limit = 1024 * 1024
    return {'status': 'completed' if code == 0 and stable and collection_error is None else 'failed',
        'evidence_status': 'collection_failed' if collection_error else 'collected',
        'collection_error': collection_error,
        'output_bytes': len(raw_output), 'output_truncated': len(raw_output) > output_limit,
        'exit_code': code, 'source_stable': stable, 'target_commit': manifest['head'],
        'command_id': command['command_id'], 'command': command['command'],
        'command_digest': command['command_digest'], 'execution_origin': 'preauthorized_container',
        'image': policy.image, 'policy_digest': digest(asdict(policy)),
        'policy_snapshot': asdict(policy), 'evidence_ref': evidence_ref}, raw_output[:output_limit].decode('utf-8', errors='replace')


def reconcile_host_verification(service, *, allow_execution: bool = True,
                                workflow_run_id: str | None = None) -> int:
    policy = getattr(service.config.runtime, 'host_verification', None)
    if not policy or not policy.enabled:
        return 0
    with locked_path(service.state_dir / 'locks' / 'preauthorized-container-probe'):
        events = service.writer.event_log.read_all()
        if is_dispatch_paused(service.state_dir, events=events):
            return 0
        operations = reduce_workflow_operations(events)
        from zf.runtime.host_verification_handoff import completed_reader_probes, resume_reader_after_probes
        from zf.runtime.candidate_probe_source_retry import handed_off_probe_commands
        transferred = handed_off_probe_commands(events, policy)
        eligible = []
        candidate_probe_owners = {}
        for operation_id, operation in operations.items():
            if workflow_run_id and operation.get('workflow_run_id') != workflow_run_id:
                continue
            if operation.get('status') != 'settled' or operation.get('semantic_verdict') not in {'blocked', 'failed', 'abstained'}:
                continue
            from zf.runtime.host_verification_handoff import reader_probe_handoff_complete
            if reader_probe_handoff_complete(operation, events):
                continue
            try:
                _, request, contract, _ = binding(service, operation_id)
                if not allow_execution and contract.get('authority_scope') != 'candidate':
                    continue
                if contract.get('authority_scope') == 'candidate':
                    result = hydrate_sidecar_ref(service.state_dir,
                        operation.get('admitted_control_result_ref') or {}).payload
                    if result.get('failure_class') == 'reader_execution_failure':
                        # Product commands cannot repair missing input delivery.
                        # Preserve the failure for the existing Reader recovery owner.
                        continue
            except (ValueError, KeyError, OSError):
                continue
            group = ()
            if contract.get('authority_scope') == 'candidate':
                group = (operation.get('workflow_run_id'), request.get('fanout_id'),
                         request.get('target_commit'),
                         request.get('result_identity', {}).get('contract_snapshot_digest'))
                if not all(group):
                    group = ()
            _, receipts, failed_probe = completed_reader_probes(service, operation, contract, policy, events)
            eligible.append((operation_id, operation, contract, group, failed_probe, receipts))
            # Choose from the complete cohort before processing any sibling.
            # A later failure can precede the existing owner in operation order.
            starts = [index for index, event in enumerate(events)
                if group and event.origin == 'kernel'
                and event.type == 'runtime.action.attempt.started'
                and event.payload.get('action') == 'task-host-assist'
                and event.payload.get('kind') == 'verify'
                and event.payload.get('workflow_run_id') == operation.get('workflow_run_id')
                and event.payload.get('operation_id') == operation_id]
            if group and (starts or receipts):
                rank = (0, min(starts), operation_id) if starts else (1, 0, operation_id)
                if group not in candidate_probe_owners or rank < candidate_probe_owners[group]:
                    candidate_probe_owners[group] = rank

        for operation_id, operation, contract, group, failed_probe, receipts in eligible:
            run_id = operation.get("workflow_run_id") or ""
            owner = candidate_probe_owners.get(group)
            if owner and owner[-1] != operation_id:
                continue
            from zf.runtime.task_host_verification_handoff import resume_task_after_probes
            if resume_task_after_probes(service, operation, contract, policy, events):
                return 1
            if (allow_execution and failed_probe and contract.get('authority_scope') == 'candidate'
                    and policy.recollect_commands):
                from zf.runtime.host_probe_recollection import capture_limit_now_covered
                for ref in receipts.values():
                    receipt = hydrate_sidecar_ref(service.state_dir, ref).payload
                    if (receipt.get('operation_id') == operation_id and isinstance(receipt.get('manifest'), dict)
                            and receipt.get('command') in policy.recollect_commands
                            and capture_limit_now_covered(receipt, policy)
                            and _try_recollect(service, receipt['manifest'], events, policy, run_id)):
                        return 1
            if resume_reader_after_probes(service, operation, contract, policy, events):
                return 1
            if not allow_execution:
                continue
            if failed_probe:
                # The aggregate failure may still await a sibling Reader. Keep
                # this failure for handoff; do not spend its remaining probes.
                continue
            for command in contract['verification_commands']:
                if command['command_id'] in receipts or command['command_id'] in transferred.get(group, ()):
                    continue
                if command.get('command') not in policy.commands:
                    continue
                try:
                    manifest = preview(service, operation_id=operation_id, kind='verify', command_id=command['command_id'])
                except (ValueError, KeyError, OSError):
                    continue
                if not _authorized_host_policy(service, manifest, events, run_id):
                    continue
                prior = [e for e in events if e.payload.get('manifest_digest') == manifest['manifest_digest']
                         and e.type.startswith('runtime.action.attempt.')]
                if prior:
                    if not any(e.type == 'runtime.action.attempt.completed' for e in prior):
                        if _try_recollect(service, manifest, events, policy, run_id):
                            return 1
                        if command['command'] not in policy.recollect_commands:
                            _escalate_once(service, events, manifest, 'prior container effect is unresolved; inspect before retry')
                    continue
                run = operation['workflow_run_id']
                used = sum(e.type == 'runtime.action.attempt.started'
                    and e.payload.get('workflow_run_id') == run and e.payload.get('approval_ref') == policy.approval_ref
                    for e in events)
                escalations = [e for e in events if e.type == 'human.escalate'
                    and e.payload.get('host_manifest_digest') == manifest['manifest_digest']]
                # An exhausted allowance is rechecked against the current owner
                # config. Unknown effects and other escalation reasons stay held.
                if any(e.payload.get('reason') != 'preauthorized_probe_budget_exhausted' for e in escalations):
                    continue
                if used >= policy.max_executions_per_run:
                    _escalate_once(service, events, manifest, 'preauthorized_probe_budget_exhausted')
                    return 0
                try:
                    apply(service, manifest, approval_ref=policy.approval_ref,
                        reviewed_digest=manifest['manifest_digest'], readonly_snapshot=True,
                        verifier=lambda root, body: verify_in_container(service, root, body, policy))
                except BlockingIOError:
                    continue  # a live resource owner is a defer, not permission to steal its workspace
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    if command['command'] not in policy.recollect_commands:
                        _escalate_once(service, events, manifest, str(exc))
                    # Opt-in unknown probes already have a durable RM diagnosis
                    # route and one independent observation. Do not create a
                    # human gate before that authorized route can run.
                return 1  # one bounded effect per tick, never an unbounded queue
    return 0


def _authorized_host_policy(service, manifest, events, run_id) -> bool:
    from zf.runtime.recovery_authorization import current_run_grant

    grant, error = current_run_grant(service.config, events, run_id)
    if grant:
        from zf.runtime.run_continuation import blocking_decisions_for_action
        from zf.runtime.run_manager import _pending_human_decisions

        identity = manifest.get("identity")
        if not isinstance(identity, dict) or identity.get("workflow_run_id") != run_id:
            error = "host_probe_run_identity_mismatch"
        elif blocking_decisions_for_action(_pending_human_decisions(events), identity, events=events):
            return False  # The outstanding owner gate already supplies the recovery route.
    if error:
        _escalate_once(service, events, manifest, error, run_id=run_id)
    return not error


def _try_recollect(service, manifest, events, policy, run_id) -> bool:
    from zf.runtime.host_probe_recollection import recollection_manifest
    if not _authorized_host_policy(service, manifest, events, run_id):
        return False
    try:
        recollected = recollection_manifest(service, manifest, events, policy)
        if recollected:
            apply(service, recollected, approval_ref=policy.approval_ref,
                reviewed_digest=recollected['manifest_digest'], readonly_snapshot=True,
                recollection_base=manifest,
                verifier=lambda root, body: verify_in_container(service, root, body, policy))
            return True
    except (ValueError, OSError, subprocess.SubprocessError):
        pass  # The exact failure stays available for RM diagnosis.
    return False


def _escalate_once(service, events, manifest, reason, *, run_id=None):
    if run_id is None:
        run_id = manifest["identity"]["workflow_run_id"]
    if any(e.type == 'human.escalate' and e.payload.get('host_manifest_digest') == manifest['manifest_digest']
           and e.payload.get('reason') == reason and e.correlation_id == run_id
           for e in events):
        return
    service.writer.emit('human.escalate', actor='run-manager',
        correlation_id=run_id, payload={
            'reason': reason, 'host_manifest_digest': manifest['manifest_digest'],
            'owner_route': 'run_manager', 'blocking_scope': 'verification',
            'resume_condition': 'authorized resolution of the retained probe checkpoint'})
