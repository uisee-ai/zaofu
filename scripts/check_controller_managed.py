"""Managed tier for the disposable workspace checker; fixture owner, real machine wire."""

import time
import uuid


def qualify_managed(app, owner, workspace, path, raw, codex):
    import httpx2
    import yaml
    from sqlalchemy import select
    from zaofu_controller.control_models import PolicyInput
    from zaofu_controller.control_schema import entitlements
    from zaofu_controller.database import workspaces
    from zaofu_controller.budget_schema import budget_policies, budget_periods, reservations
    from zf.core.config.controller_workspace import load_controller_workspace
    from zf.integrations.controller.binding import bind
    from zf.integrations.controller.installation import Installation
    from zf.integrations.controller.workspace import project_contexts, synchronize_workspace
    from zf.integrations.controller.execution import Execution, check_project
    from zf.integrations.controller.ledger import Ledger
    from zf.web.headless_agent import CodexHeadlessBackend

    config_body = dict(cycle='day', timezone='UTC', tokens=100000, money=None, currency='USD',
        cost_basis='estimated', price_version='unknown', modes=['self_managed_cli'], lease_seconds=300,
        alert_percent=80, rollover='none', overrun='freeze')
    # A fixture operator provisions entitlements. This does not certify a browser approval flow.
    with app.state.access.engine.begin() as c:
        c.execute(workspaces.update().where(workspaces.c.id == workspace).values(spec={'projects': 2, 'agents': 1}))
        c.execute(entitlements.insert().values(user_id=owner.user_id, version=1, limits={'projects': 2, 'agents': 1}))
        c.execute(budget_policies.insert().values(id=str(uuid.uuid4()), user_id=owner.user_id,
            scope='user:' + owner.user_id, version=1, frozen=False, config=config_body))
    previous = app.state.commands.policy(owner, workspace)
    app.state.commands.update_policy(owner, workspace, PolicyInput(request_id='managed-qualification',
        version=previous['version'], reason='disposable managed runtime qualification', management='managed',
        projects=raw['workspace']['controller']['projects'], capabilities=['observe', 'commands', 'budget', 'gateway'],
        runtime_settings=dict(protocol='zfc.runtime-settings.v1', interval_seconds=3, batch_size=32,
            diagnostic_sample_rate=0.0, mode='self_managed_cli', request_tokens=32768,
            request_money='0', price_version='unknown', request_seconds=300)))
    raw['workspace']['controller'].update(management='managed', gateway_origin='https://workspace.example.test',
                                         request_seconds=300, request_tokens=32768)
    path.write_text(yaml.safe_dump(raw))
    config = load_controller_workspace(path)
    bind(config)
    synchronize_workspace(config)
    from zf.integrations.controller.runtime_settings import applied
    config = applied(config)
    assert config.interval_seconds == 3 and config.batch_size == 32
    accepted = app.state.commands.policy(owner, workspace)
    assert accepted['applied_version'] == accepted['version'] and not accepted['applied_error']
    credentials = Installation(config).credentials()
    contexts = project_contexts(config, credentials, config.projects)
    binding = config, credentials, contexts[0]
    check_project(contexts[0].project_root)
    blocker = Execution(binding, operation_id='concurrent-holder', provider='codex', model='unknown').begin(lambda: None)
    try:
        try:
            Execution((config, credentials, contexts[1]), operation_id='other-project', provider='codex', model='unknown').begin(lambda: None)
        except httpx2.HTTPStatusError as exc:
            assert exc.response.status_code == 409
        else:
            raise AssertionError('shared agent limit did not hold')
    finally:
        blocker.finish({'input_tokens': 0, 'output_tokens': 0}, status='not_started')
    for _ in range(2):
        synchronize_workspace(config)
    native = None
    if codex:
        result = CodexHeadlessBackend().run_turn(prompt='Reply exactly ZF_MANAGED_OK. Do not use tools.',
            cwd=contexts[0].project_root, system_prompt='This is a brief integration check.',
            thread_id='managed-native-check', provider_session_id='', on_session_id=lambda _: None,
            on_message=None, timeout_s=90, permission_profile='read_only')
        if not result.ok:
            print({"native_status": result.status, "native_error_present": bool(result.error)}, flush=True)
        assert result.ok, result.status
        native = result.provider_session_id
    else:
        execution = Execution(binding, operation_id='fixture-turn', provider='codex', model='unknown').begin(lambda: None)
        execution.finish({'input_tokens': 7, 'output_tokens': 3}, status='completed')
    for _ in range(3):
        synchronized = synchronize_workspace(config)
        time.sleep(.1)
    assert synchronized['unresolved_executions'] == 0, synchronized
    assert synchronized['health_delivery_failures'] == 0
    health = app.state.budget_support.health(owner, workspace_id=workspace)
    assert any(row['status'] == 'healthy' and row['payload']['mode'] == 'self_managed_cli' for row in health)
    rows = Ledger(config.state_dir).rows('execution')
    assert all(row['status'] in {'settled', 'not_started'} for row in rows), [(r['status'], r['body'].get('result')) for r in rows]
    with app.state.access.engine.begin() as c:
        period = c.execute(select(budget_periods)).mappings().one()
        assert period['token_used'] > 0 and period['token_reserved'] == 0
        counted = period['token_used']
        # Exhaustion closes new calls while the usage read projection remains accessible.
        c.execute(budget_periods.update().values(token_grant=counted))
        settled = list(c.execute(select(reservations)).mappings())
        assert all(row['status'] == 'settled' for row in settled)
    try:
        Execution(binding, operation_id='over-quota', provider='codex', model='unknown').begin(lambda: None)
    except httpx2.HTTPStatusError as exc:
        assert exc.response.status_code == 409
    else:
        raise AssertionError('quota did not prevent provider start')
    try:
        check_project(contexts[0].project_root)
    except httpx2.HTTPStatusError as exc:
        assert exc.response.status_code == 409
    else:
        raise AssertionError('new Run admitted after quota exhaustion')
    before = app.state.observations.overview(owner, workspace_id=workspace)['usage']
    synchronize_workspace(config)
    assert app.state.observations.overview(owner, workspace_id=workspace)['usage'] == before
    return {'status': 'passed', 'native_session_id': native, 'settled_tokens': counted,
            'billed_cost_usd': None, 'verified': ['applied runtime settings digest', 'durable runtime provider health',
                'shared slots across projects', 'per-call reservation',
                'native receipt settlement' if codex else 'fixture receipt settlement', 'replay deduplication', 'quota exhaustion before provider start'],
            'deferred': ['production gateway/TLS/sandbox/network', 'human email registration']}
