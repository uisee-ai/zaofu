"""Reconcile a local Project slot only against exact remote terminal evidence."""

from urllib.parse import urlencode


def retire_released(ledger, key, transport, policy, credentials, state_dir):
    from zf.integrations.controller.execution import process_identity
    from zf.runtime.workflow_runtime_start import _runtime_owner

    old = ledger.get(key)
    if not old or old['status'] == 'unloaded' or _runtime_owner(state_dir):
        return False
    body = old['body']
    lease_id = body.get('lease', {}).get('id')
    if not lease_id:
        return False
    query = urlencode({'project_id': key[8:], 'policy_version': policy['version']})
    try:
        proof = transport.request('GET', '/internal/v1/me/execution-leases/' + lease_id + '?' + query)
    except Exception:
        return False  # No offline fallback or deletion of unresolved state.
    if (proof.get('protocol') != 'zfc.control.v1' or proof.get('status') != 'released'
            or proof.get('workspace_id') != credentials['workspace_id']
            or proof.get('deployment_id') != credentials['deployment_id']
            or proof.get('generation') != credentials['generation']
            or proof.get('project_id') != key[8:] or proof.get('lease_id') != lease_id):
        return False
    # A verified old Pod fence supersedes local PID numbers reused in the new PID
    # namespace. Within one deployment, a surviving owner must still block restart.
    fenced = proof.get('physical_fence') is True
    same = proof.get('lease_deployment_id') == credentials['deployment_id']
    owner = body.get('owner', {})
    if not fenced and (not same or not owner.get('start')
                       or owner == process_identity(owner.get('pid', 0))):
        return False
    return ledger.put_if_current(old, 'unloaded', {**body, 'release_proof': proof})
