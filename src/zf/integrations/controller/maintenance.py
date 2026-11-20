"""Connector lease, idle and export maintenance. Unknown responsibility is never idle."""

from zf.integrations.controller.ledger import Ledger


def maintain(config, credentials, transport, contexts, unresolved, *, observation_contexts=None, delivery_incomplete=False):
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
    from zf.integrations.controller.exports import collect_exports
    collect_exports(config, credentials, transport, contexts)
    inspect_idle = config.hosting == 'cloud' and 'idle_prepare_v1' in transport.hello.get('capabilities', ())
    targets = contexts if observation_contexts is None else observation_contexts

    def report(prepared_version=None):
        calls = sum(row['status'] in {'preparing', 'running'} for row in ledger.rows('execution'))
        active = {'version': ledger.next_version('activity'), 'active_operations': calls + unknown + unresolved,
                  'active_sessions': calls, 'active_exports': sum(row['status'] == 'building' for row in ledger.rows('export'))}
        if inspect_idle:
            sessions, pending = local_activity(config, credentials, targets)
            active['active_sessions'] += sessions
            active['active_operations'] += bool(pending or delivery_incomplete)
            if prepared_version is not None and not any(active[k] for k in ('active_operations', 'active_sessions', 'active_exports')):
                active['prepared_version'] = prepared_version
        return transport.request('POST', '/internal/v1/me/activity', active)

    result = report()
    if inspect_idle and result.get('prepare_version') is not None:
        report(result['prepare_version'])


def local_activity(config, credentials, contexts):
    """Read the durable terminal registry even after the browser has disconnected."""
    from zf.integrations.controller.collector import Collector
    from zf.integrations.controller.outbox import Outbox
    from zf.integrations.controller.workspace import observation
    from zf.web.terminal_registry import TerminalRegistry

    pending = Outbox(config.state_dir, observation(config, credentials)).status()['pending_records']
    terminals = 0
    for ctx in contexts:
        registry = TerminalRegistry(ctx.state_dir, project_id=ctx.config.observability.controller.project_id,
                                    project_root=ctx.project_root)
        # A missing pane or an unknown state is insufficient evidence that its job finished.
        terminals += sum(row['state'] != 'stopped' for row in registry.read()['sessions'])
        pending += Collector(ctx.state_dir, ctx.config).outbox.status()['pending_records']
    return terminals, pending
