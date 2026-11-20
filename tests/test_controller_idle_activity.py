from dataclasses import replace

from tests.test_controller_workspace import add_project, enrolled
from tests.test_controller_workspace import workspace as workspace

from zf.integrations.controller.maintenance import maintain
from zf.integrations.controller.workspace import project_contexts
from zf.web.terminal_registry import TerminalRegistry


def test_idle_confirmation_rescans_native_terminal_and_blocks_unknown_delivery(workspace, tmp_path):
    cfg, _, _ = workspace
    installation = enrolled(workspace)
    creds = installation.credentials()
    pid, _ = add_project(cfg, tmp_path / 'project')
    contexts = project_contexts(cfg, creds, [pid])
    ctx = contexts[0]
    registry = TerminalRegistry(ctx.state_dir, project_id=pid, project_root=ctx.project_root)
    config = replace(cfg, hosting='cloud')
    reports = []

    class Transport:
        hello = {'capabilities': ['idle_prepare_v1']}

        def request(self, method, path, body=None):
            if path == '/internal/v1/me/exports':
                return []
            assert path == '/internal/v1/me/activity'
            reports.append(body)
            if len(reports) == 1:
                # The browser is gone but a new native terminal appeared between the samples.
                registry.mutate(lambda data: data['sessions'].append({'state': 'active'}))
                return {'prepare_version': body['version']}
            return {}

    transport = Transport()
    maintain(config, creds, transport, contexts, 0)
    assert len(reports) == 2 and reports[0]['active_sessions'] == 0
    assert reports[1]['active_sessions'] == 1 and 'prepared_version' not in reports[1]
    registry.mutate(lambda data: data['sessions'][0].update(state='stopped'))
    maintain(config, creds, transport, contexts, 0, delivery_incomplete=True)
    assert reports[-1]['active_operations'] > 0
