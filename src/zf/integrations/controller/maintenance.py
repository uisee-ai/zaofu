"""Connector lease, idle and export maintenance. Unknown responsibility is never idle."""

from zf.integrations.controller.ledger import Ledger


def maintain(config, credentials, transport, contexts, unresolved):
    ledger = Ledger(config.state_dir)
    unknown = 0
    for row in ledger.rows('project'):
        if row['status'] == 'unloaded':
            continue
        body = row['body']
        if row['status'] not in {'loaded', 'starting'}:
            unknown += 1
            continue
        from zf.integrations.controller.execution import process_identity
        owner = body.get('owner', {})
        if not owner.get('start') or owner != process_identity(body.get('pid', 0)):
            unknown += 1
            continue  # A dead/unknown watcher cannot keep its grant alive through the Connector.
        try:
            body['lease'] = transport.request('POST', '/internal/v1/me/execution-leases/' + body['lease']['id'], {
                'action': 'renew', 'policy_version': body['policy_version'], 'seconds': config.lease_seconds})
            ledger.put(row['id'], row['status'], body)
        except Exception:
            unknown += 1
    calls = [row for row in ledger.rows('execution') if row['status'] in {'preparing', 'running'}]
    from zf.integrations.controller.exports import collect_exports
    collect_exports(config, credentials, transport, contexts)
    transport.request('POST', '/internal/v1/me/activity', {
        'version': ledger.next_version('activity'), 'active_operations': len(calls) + unknown,
        'active_sessions': len(calls),
        'active_exports': sum(row['status'] == 'building' for row in ledger.rows('export'))})
