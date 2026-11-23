"""Replayed provider accounting is not newly observed worker activity."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task
from zf.runtime.orchestrator import Orchestrator


def _runtime(tmp_path):
    state=tmp_path/'state';state.mkdir()
    cfg=ZfConfig(project=ProjectConfig(name='liveness', workspace=str(tmp_path)),
                roles=[RoleConfig(name='dev', backend='mock')])
    runtime=Orchestrator(state,cfg,transport=None)
    runtime.task_store.add(Task(id='T1',title='active',status='in_progress',assigned_to='dev'))
    before=datetime.now(timezone.utc)-timedelta(minutes=20)
    reg=RoleSessionRegistry(state/'role_sessions.yaml',str(tmp_path))
    with patch('zf.core.state.role_sessions._now_iso',return_value=before.isoformat()):
        reg.record_heartbeat('dev',{'state':'busy','current_task_id':'T1','source':'worker.heartbeat'})
    return runtime,reg,before


@pytest.mark.parametrize('rich_failure', [False, True])
@pytest.mark.parametrize('sample', ['older','equal','missing','invalid','future'])
def test_disk_accounting_cannot_refresh_liveness_without_new_sample(tmp_path,sample,rich_failure,monkeypatch):
    runtime,reg,before=_runtime(tmp_path)
    if rich_failure:
        def fail(*a,**k):raise RuntimeError('rich liveness unavailable')
        monkeypatch.setattr('zf.runtime.usage_liveness.apply_agent_usage_liveness',fail)
    old=reg.get_last_heartbeat('dev')
    ts={'older':before-timedelta(seconds=1),'equal':before,'future':datetime.now(timezone.utc)+timedelta(minutes=1)}
    p={'source':'disk_reader','usage':{'input_tokens':100},'task_id':'T1'}
    if sample in ts:p['usage_timestamp']=ts[sample].isoformat()
    elif sample=='invalid':p['usage_timestamp']='not-a-time'
    runtime._apply_housekeeping(ZfEvent(type='agent.usage',actor='dev',origin='kernel',payload=p))
    assert RoleSessionRegistry(reg.path,str(tmp_path)).get_last_heartbeat('dev')==old
    assert runtime.cost_tracker.per_role_totals()['dev'].entries==1


def test_fresh_disk_usage_then_controller_reconstruction_does_not_renew_again(tmp_path):
    runtime,reg,before=_runtime(tmp_path)
    sample=datetime.now(timezone.utc)-timedelta(seconds=2)
    payload={'source':'disk_reader','usage_timestamp':sample.isoformat(),'usage':{'input_tokens':100},'task_id':'T1','context_usage_ratio':.4}
    runtime._apply_housekeeping(ZfEvent(type='agent.usage',actor='dev',origin='kernel',payload=payload))
    saved=RoleSessionRegistry(reg.path,str(tmp_path)).get_last_heartbeat('dev')
    assert saved[0]!=before.isoformat()
    assert saved[1]['context_usage_ratio']==.4
    # A reconstructed controller receives a newly minted event for the same sample.
    restarted=Orchestrator(runtime.state_dir,runtime.config,transport=None)
    with patch('zf.runtime.housekeeping._ACTIVITY_LIVENESS_MIN_GAP_S',0):
        restarted._apply_housekeeping(ZfEvent(type='agent.usage',actor='dev',origin='kernel',payload=payload))
    assert RoleSessionRegistry(reg.path,str(tmp_path)).get_last_heartbeat('dev')==saved
