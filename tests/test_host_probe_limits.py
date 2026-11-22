from dataclasses import asdict
from pathlib import Path
import subprocess

import pytest

from tests.test_host_verification import policy, prepare
from tests.test_task_host_actions import case
from zf.core.config.host_verification import parse_host_verification
from zf.core.config.loader import ConfigError, _build_runtime
from zf.runtime.host_verification import verify_in_container
from zf.runtime.task_host_manifest import preview


@pytest.mark.parametrize('watchdog', [60,180])
def test_watchdog_cannot_expire_before_authorized_host_timeout(watchdog):
    with pytest.raises(ConfigError,match='host_watchdog_seconds'):
        _build_runtime({'host_watchdog_seconds':watchdog,'host_verification':asdict(policy())})


def test_disabled_watchdog_and_larger_deadline_remain_valid():
    for value in [0,300]:
        assert _build_runtime({'host_watchdog_seconds':value,'host_verification':asdict(policy())}).host_watchdog_seconds==value


@pytest.mark.parametrize('limits', [
    {'max_artifact_bytes':True}, {'max_artifact_bytes':0},
    {'max_artifact_bytes':65*1024*1024}, {'max_total_evidence_bytes':129*1024*1024},
    {'max_artifact_bytes':32,'max_total_evidence_bytes':16}])
def test_invalid_evidence_limits_rejected(limits):
    with pytest.raises(ValueError): parse_host_verification({**asdict(policy()),**limits})


@pytest.mark.parametrize(('sizes','passed'), [([12,12],True),([17],False),([12,13],False)])
def test_capture_enforces_file_and_total_limits(case,monkeypatch,sizes,passed):
    service,root=prepare(case)
    p=policy(max_artifact_bytes=16,max_total_evidence_bytes=24)
    manifest=preview(service,operation_id='op-1',kind='verify',command_id='contract-verification')
    def run(argv,**kwargs):
        mount=next(v for v in argv if v.startswith('type=bind,src=') and 'dst=/evidence' in v)
        output=Path(mount.split('src=')[1].split(',')[0])
        for index,size in enumerate(sizes): (output/f'{index}.trace').write_bytes(b'x'*size)
        return subprocess.CompletedProcess(argv,0,'passed','')
    monkeypatch.setattr('zf.runtime.host_verification._run_container',run)
    if passed:
        result,_=verify_in_container(service,root,manifest,p)
        assert result['status']=='completed'
    else:
        result, output = verify_in_container(service,root,manifest,p)
        assert result['status'] == 'failed' and result['exit_code'] == 0
        assert result['collection_error']['kind'] in {'per_file_limit', 'total_limit'}
        assert result['evidence_status'] == 'collection_failed'
        assert not result.get('evidence_ref')
        assert 'passed' in output


def test_legacy_policy_receipt_requires_actual_current_budget_proof(tmp_path):
    from zf.runtime.host_verification_policy import policy_receipt_matches
    from zf.runtime.task_host_manifest import digest
    from zf.runtime.sidecar_refs import write_sidecar_json
    p=policy();body=asdict(p)
    body.pop('max_artifact_bytes');body.pop('max_total_evidence_bytes')
    old={'policy_digest':digest(body)}
    assert not policy_receipt_matches(tmp_path,p,old)
    old['evidence_ref']=write_sidecar_json(tmp_path,'artifacts/old-evidence.json',
        {'files':[{'path':'trace','base64':'eHg='}]},kind='container_evidence',
        schema_version='container-evidence.v1',created_by='run-manager')
    assert policy_receipt_matches(tmp_path,p,old)
    p.max_artifact_bytes=64*1024*1024;p.max_total_evidence_bytes=128*1024*1024
    assert policy_receipt_matches(tmp_path,p,old)
    p.max_artifact_bytes=1
    assert not policy_receipt_matches(tmp_path,p,old)
    p=policy();p.commands.append('unauthorized for old receipt')
    assert not policy_receipt_matches(tmp_path,p,old)
