"""Isolated fake-provider API for Docker Playwright libre_discuss checks."""
from contextlib import asynccontextmanager
import argparse
import os
from pathlib import Path

import uvicorn
from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter
from zf.core.task.store import TaskStore
from zf.core.workspace.onboarding import apply_action
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.sidecar_refs import write_sidecar_text
from zf.web.server import create_app


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",required=True,type=Path)
    parser.add_argument("--port",type=int,default=8031)
    args=parser.parse_args()
    root=args.root.resolve()
    if root.parent != Path('/tmp') or not root.name.startswith('zf-libre-') or root.exists() or args.port < 8002:
        parser.error('use a new /tmp/zf-libre-* directory and an isolated port >=8002')
    root.mkdir()
    os.environ['ZF_WORKSPACE_HOME']=str(root/'workspace')
    os.environ['ZF_WEB_ACTION_TOKEN']='libre-isolated-test-token'
    apply_action('skip')
    cfg=root/'zf.yaml'
    cfg.write_text("version: '1.0'\nproject:\n  name: libre-browser\n  description: 个人待办清单，不登录，本机保存；刷新后保留。\n  state_dir: runtime\n")
    config=load_config(cfg)
    state=root/'runtime'
    store=TaskStore(state/'kanban.json')
    with store._locked():
        store._save_raw([])
    writer=EventWriter(EventLog(state/'events.jsonl'))
    service=ControlledActionService(state,writer,project_root=root,config=config,actor='web',source='web',surface='web')
    payload={'channel_id':'ch-libre-browser','name':'自由讨论浏览器验收','template_id':'prd-clarification','overrides':{'backend':'fake'}}
    event=writer.emit('web.action.requested',actor='web',payload={'action':'channel-create-from-template','request':payload})
    result=service.execute(action='channel-create-from-template',requested_action='channel-create-from-template',requested=event,payload=payload)
    if not result.get('ok'):
        raise RuntimeError(result)
    candidate_id='ch-libre-candidate'
    payload={**payload,'channel_id':candidate_id,'name':'未通过校验候选稿验收'}
    event=writer.emit('web.action.requested',actor='web',payload={'action':'channel-create-from-template','request':payload})
    result=service.execute(action='channel-create-from-template',requested_action='channel-create-from-template',requested=event,payload=payload)
    if not result.get('ok'):
        raise RuntimeError(result)
    candidate=write_sidecar_text(state,Path('channels/ch-libre-candidate/rejected.txt'),
        '# 待办清单候选稿\n\n支持新增、完成和删除事项。\n\n验收条件和存储证据仍待补齐。',
        kind='channel_synthesis_invalid_reply',schema_version='channel.synthesis.invalid-reply.v1',
        created_by='synthesizer',source_event_id=event.id,
        access_scope={'channel_id':candidate_id,'thread_id':'main'},required=True)
    writer.emit('channel.synthesis.blocked',actor='synthesizer',payload={
        'channel_id':candidate_id,'thread_id':'main','request_id':'browser-rejected',
        'invalid_reply_ref':candidate,'reason':'验收条件和证据不完整'})
    writer.emit('simulation.started',actor='browser-fixture',payload={'purpose':'libre_discuss browser verification'})
    app=create_app(state,project_root=root,config=config)
    original_lifespan=app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        try:
            async with original_lifespan(application) as context:
                yield context
        finally:
            writer.emit('simulation.done',actor='browser-fixture',payload={'purpose':'libre_discuss browser verification finished'})

    app.router.lifespan_context=lifespan
    uvicorn.run(app,host='0.0.0.0',port=args.port,log_level='warning')


if __name__=='__main__':
    main()
