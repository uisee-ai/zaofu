import os, json, sys, subprocess
from pathlib import Path
from unittest.mock import patch
import uvicorn
from tests.test_web_workflow_proposals import _write_config
from zf.core.config.loader import load_config
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.core.workspace.onboarding import apply_action
from zf.runtime.workflow_intake import build_flow_intake
from zf.runtime.workflow_requests import revise_workflow_request
from zf.runtime.workflow_delivery import build_flow_submit_preview
from zf.web.server import create_app
root=Path(sys.argv[1]);root.mkdir()
subprocess.run(['git','init','-q',str(root)],check=True)
_write_config(root/'zf.yaml')
os.environ['ZF_WEB_ACTION_TOKEN']='raw-chat-real-fixture-token'
os.environ['ZF_SKIP_PROJECT_REGISTER']='1'
os.environ['ZF_WORKSPACE_HOME']=str(root/'workspace')
os.environ['ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S']='120'
apply_action('skip')
from zf.core.workspace.project_initializer import ProjectInitializer
ProjectInitializer().initialize(cwd=root,with_instruction_docs=False,with_git_hooks=False,workspace_register=False)
config=load_config(root/'zf.yaml');state=root/config.project.state_dir
writer=EventWriter(event_log_from_project(state,config=config))
intake=build_flow_intake(kind='issue',objective='Verify configuration preview editing',request_id='raw-real-review',backend='mock',project_id='default',acceptance=('Configuration edits create a new preview without starting a run.',),output=root/'docs/intake/review.md')
revise_workflow_request(state,Path(intake['workflow_input_manifest_ref']),actor='browser-test',confirm=True,writer=writer)
blocker={'severity':'STOP','kind':'fixture_missing_closure','message':'Isolated blocked-candidate fixture; execution remains prohibited.'}
# Only the blocker is a fixture. Chat invokes the installed Codex adapter.
with patch('zf.runtime.workflow_proposal._synthesis_compatibility_blockers',lambda **kwargs:[blocker]):
    preview=build_flow_submit_preview(config_path=root/'zf.yaml',intake_path=Path(intake['intake_ref']),flow_kind='issue',allow_missing_env=True)
    assert preview['proposal']['approval_status']=='blocked'
    app=create_app(state,config=config,project_root=root)
    app.router.add_event_handler('shutdown',lambda:writer.emit('simulation.done',actor='raw-chat-browser-test',payload={'root':str(root)}))
    uvicorn.run(app,host='0.0.0.0',port=8003)
