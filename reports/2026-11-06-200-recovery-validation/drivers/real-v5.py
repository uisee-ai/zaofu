"""Approved isolated real Codex canary. No synthetic agent/gate completion."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import yaml
from fastapi.testclient import TestClient
from zf.core.config.loader import load_config
from zf.core.events import EventLog
from zf.core.state.session import SessionStore
from zf.runtime.workflow_proposal import load_workflow_proposal
from zf.web.server import create_app

SOURCE = Path('/home/min/workspace/zaofu-worktrees/design199-20261106')
RECEIPTS = Path('/tmp/zf-200-verification-20261106')
MANIFEST = RECEIPTS/'real-canary.json'
REQUIREMENT = '''# 客户名册 CSV 导出（最小业务任务）

实现标准库 Python 命令行程序 `export_csv.py`：用户可以将 UTF-8 JSON 客户列表转换为 CSV，方便导入表格。
接口固定为 `python3 export_csv.py input.json`，CSV 写 stdout；无数据库、Web、网络、外部 Python 依赖。

验收条件：
- AC1：输入为对象数组，每条记录有字符串 name 和 email；CSV 第一行为 name,email，数据顺序与输入相同。
- AC2：中文、逗号、双引号通过 Python csv.reader 往返后与原字符串相同；使用标准 CSV 转义。
- AC3：空列表只产生表头；成功退出码为 0。
- AC4：无效 JSON、非数组和缺少所需字段时退出码非 0，stderr 有可读说明，stdout 不产生成功数据。
- AC5：交付标准库 unittest，覆盖上述行为；`python3 -m unittest discover -s tests -v` 实际运行至少 5 个测试且通过。

建议单一有价值的纵向 Task 完成交付，不人为拆分无依赖意义的微任务。保留真实 Scan、Plan、独立 Verify 和 Judge。
最终交付必须针对实际冻结提交，不能用报告存在、实现者自评或退出码 0 代替验收行为。

附加 harness 运行验证（不扩展产品功能）：
Impl 在首次写代码前，使用当前 briefing 的 `recover assistance request` 向 orchestrator 提交一个只读问题：
“当前合同的 CSV 转义是否可以直接使用 Python 标准库 csv.writer？”证据引用当前 requirement.md，说明已读 AC2。
保留当前 attempt/lease/heartbeat；用返回的 request_id 读取绑定回答后按原合同继续。该求助不是重新计划或扩权请求。
独立 Verify 检查业务 AC；harness 操作员另行检查求助身份、回答、派发和最终交付事件，不把 harness 机制显示为产品功能。
'''


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def prepare():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    root = Path(tempfile.mkdtemp(prefix=f'zf-200-real-{stamp}-'))
    state = root/'.canary-state'
    state.mkdir(); (root/'tmux').mkdir()
    docs = list(yaml.safe_load_all((SOURCE/'zf-prd-v5.yaml').read_text()))
    flow, controller = docs
    flow['spec'].update(prdRef='requirement.md', targetRoot=str(root))
    spec = controller['spec']
    spec['profile_sources'] = [str(SOURCE/p) for p in spec['profile_sources']]
    spec['skill_sources'][0]['path'] = str(SOURCE/'skills')
    spec['project'] = {'name':'design200-contact-export', 'state_dir':'.canary-state'}
    spec['session'] = {'tmux_session':f'zf-200-real-{root.name[-8:]}'}
    # Project-specific commands; keep the v5 verification floor, blocking gates,
    # independent pools, serial integration, RM and exact-commit verification.
    spec['quality_gates']['static']['required_checks'] = ['python3 -m compileall -q export_csv.py tests']
    # The user authorized completing this isolated test. Auto-ship stays local
    # to its dev branch after all existing Judge/candidate gates pass.
    spec['runtime']['git']['auto_ship_on_judge_passed'] = True
    (root/'zf.yaml').write_text(yaml.safe_dump_all(docs, sort_keys=False))
    (root/'requirement.md').write_text(REQUIREMENT)
    (root/'README.md').write_text('# 客户名册 CSV 导出\n由 ZaoFu v5 的真实 Codex 工作流实现 requirement.md。\n')
    (root/'AGENTS.md').write_text('''# 隔离业务验证项目
使用 Python 标准库完成 requirement.md。所有状态变更经 briefing 的受控 CLI/结果协议。
运行状态目录由 zf.yaml 定义；不得手写 terminal passed 或修改 canonical Store。
实际生成和执行非空测试；按当前角色交付工件及证据。Impl 的附加只读求助不得启动第二写入者。
''')
    (root/'.gitignore').write_text('.canary-state/\nartifacts/\ndocs/intake/\n.codex/\n.claude/\n__pycache__/\ntmux/\nworkspace-registry/\n')
    SessionStore(state/'session.yaml').create(project_root=str(root))
    def git(*args):
        return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
    git('init','-q','-b','dev')
    git('add','--','zf.yaml','requirement.md','README.md','AGENTS.md','.gitignore')
    git('-c','user.name=ZaoFu Canary','-c','user.email=canary@example.invalid','commit','-qm','docs: freeze approved contact export canary')
    config=load_config(root/'zf.yaml')
    pipeline=config.workflow.flow_metadata['task_pipeline']
    assert config.version=='5.0.1' and pipeline['mode']=='blocking'
    assert config.workflow.orchestration.normal_frontier_owner=='wrc'
    manifest={'root':str(root),'state':str(state),'session':spec['session']['tmux_session'],
        'prepared_at':stamp,'baseline_commit':git('rev-parse','HEAD'),
        'zf_yaml_sha256':hashlib.sha256((root/'zf.yaml').read_bytes()).hexdigest(),
        'requirement_sha256':hashlib.sha256((root/'requirement.md').read_bytes()).hexdigest(),
        'source_head':subprocess.check_output(['git','-C',str(SOURCE),'rev-parse','HEAD'],text=True).strip(),
        'status':'prepared'}
    write_json(MANIFEST,manifest)
    print(json.dumps(manifest,ensure_ascii=False))


def start():
    manifest=json.loads(MANIFEST.read_text());root=Path(manifest['root']);state=Path(manifest['state'])
    os.chdir(root)
    for key in ('ZF_TMUX_SESSION','ZF_STATE_DIR','ZF_PROJECT_ROOT','ZF_CLI_CMD'):os.environ.pop(key,None)
    os.environ['TMUX_TMPDIR']=str(root/'tmux')
    os.environ['ZF_WORKSPACE_HOME']=str(root/'workspace-registry')
    os.environ['PYTHONPATH']=str(SOURCE/'src')
    os.environ['PATH']=str(SOURCE/'.venv/bin')+os.pathsep+os.environ['PATH']
    token='canary-'+os.urandom(24).hex()
    os.environ['ZF_WEB_ACTION_TOKEN']=token
    config=load_config(root/'zf.yaml')
    with TestClient(create_app(state,config=config,project_root=root)) as client:
        def action(name,payload):
            response=client.post(f'/api/actions/{name}',json=payload,headers={
                'X-ZF-Web-Token':token,'X-Idempotency-Key':f'design200:{name}:{time.time_ns()}'})
            result=response.json()
            write_json(RECEIPTS/f'real-{name}.json',result)
            if response.status_code>=300 or not result.get('ok'):
                raise RuntimeError(f'{name}: {response.status_code}: {result}')
            return result
        prepared=action('workflow-request',{'kind':'prd','objective':'实现已冻结客户名册 CSV 导出及其验收',
            'from':'requirement.md','target_root':str(root),'backend':'codex'})
        if prepared.get('status')!='proposal_ready':raise RuntimeError(prepared)
        binding={key:prepared[key] for key in ('request_id','proposal_ref','proposal_digest')}
        proposal=load_workflow_proposal(state,prepared['proposal_ref'])
        if proposal.get('change_mode')=='config_change':
            action('workflow-config-apply',{**binding,'proposal_id':proposal['proposal_id'],
                'validation_result_ref':proposal['validation_result_ref'],
                'approval_ref':'operator:2026-11-06-approved-design199-200-isolated-real-canary',
                'establish_delivery_baseline':True})
        submitted=action('workflow-submit',{**binding,'intake_ref':prepared['intake_ref'],'kind':'prd'})
    manifest.update(status='started',request_id=prepared['request_id'],submitted=submitted)
    write_json(MANIFEST,manifest)
    print(json.dumps({'root':str(root),'request_id':prepared['request_id'],'runtime_start':submitted.get('runtime_start')},ensure_ascii=False))


def status():
    manifest=json.loads(MANIFEST.read_text());state=Path(manifest['state'])
    events=EventLog(state/'events.jsonl').read_all()
    from zf.core.task.store import TaskStore
    from zf.runtime.workflow_operation import reduce_workflow_operations
    operations=reduce_workflow_operations(events)
    wanted=('failed','blocked','rejected','passed','completed','assistance','triage','ready')
    result={'events':len(events),'tasks':[{'id':t.id,'status':t.status,'title':t.title,'blocked_reason':t.blocked_reason}
        for t in TaskStore(state/'kanban.json').list_all_with_archive()],
        'operations':[{k:o.get(k) for k in ('operation_id','task_id','operation_type','status','role_instance','semantic_verdict','reason')}
        for o in operations.values()],
        'latest':[{'type':e.type,'id':e.id,'task_id':e.task_id,'payload':e.payload} for e in events if any(x in e.type for x in wanted)][-14:]}
    write_json(RECEIPTS/'real-status.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','start','status'])
    {'prepare':prepare,'start':start,'status':status}[parser.parse_args().mode]()
