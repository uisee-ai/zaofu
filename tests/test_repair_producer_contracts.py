from zf.core.config.schema import ZfConfig
from zf.runtime.repair_dispatch import RepairRequest, write_repair_contract
from zf.runtime.run_manager import _repair_task_payload_from_agent_action
from zf.runtime.run_manager_watchdog import _source_repair_payload
from zf.runtime.self_repair_runner import _closeout_verification_plan


def _freeze(state, payload):
    req = RepairRequest(fingerprint='failure', attempt=1, candidate_id='candidate', candidate_path='', repair_task_payload=payload)
    return write_repair_contract(state, req, base_commit='abc123', created_by='test')[0]


def test_watchdog_focused_commands_survive_freeze_and_closeout(tmp_path):
    payload = _source_repair_payload(ZfConfig(), fingerprint='failure', reason='unhealthy', state_dir=tmp_path)
    contract = _freeze(tmp_path, payload['repair_task_payload'])
    commands = contract['verification_commands']
    assert len(commands) == 1
    assert all(path in commands[0] for path in ('tests/test_run_manager.py', 'tests/test_run_manager_watchdog.py', 'tests/test_tick_services.py'))
    plan = _closeout_verification_plan({'categories': ['runtime']}, repair_contract=contract)
    assert commands[0] in [row['command'] for row in plan if row['required'] == 'true']


def test_compat_recommendation_keeps_typed_commands_without_executing_prose(tmp_path):
    payload = _repair_task_payload_from_agent_action({}, {
        'verification': 'check the runtime; not executable shell',
        'verification_commands': ['pytest tests/test_run_manager.py -q', {'command': 'untrusted'}],
    })
    contract = _freeze(tmp_path, payload)
    assert len(contract['verification_commands']) == 1
    assert 'tests/test_run_manager.py' in contract['verification_commands'][0]
    assert 'untrusted' not in str(contract['verification_commands'])
