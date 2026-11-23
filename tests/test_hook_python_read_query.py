"""Read-only event queries must not become shell redirection intents."""
import pytest

from tests.test_hook_recv_codex import _invoke, _seed_scoped_worker_task
from zf.core.events.log import EventLog


def query(path, comparison='>='):
    return ("python - <<'PY'\nimport json\nfrom pathlib import Path\n"
        f"p=Path({str(path)!r})\nfor line in p.open():\n"
        " e=json.loads(line); q=e.get('payload',{})\n"
        " if e.get('task_id')=='TASK-A' or q.get('task_id')=='TASK-A':\n"
        "  ts=e.get('ts','')\n"
        f"  if ts{comparison}'2026-11-20T17:25:00':\n"
        "   print(json.dumps({'id':e.get('id'),'type':e.get('type'),'payload':q},ensure_ascii=False))\nPY")


@pytest.mark.parametrize('role', ['dev-1', 'run-manager'])
@pytest.mark.parametrize('comparison', ['>', '>=', '<', '<='])
def test_runtime_event_query_can_filter_times(tmp_path, monkeypatch, role, comparison):
    state=tmp_path/'configured runtime';state.mkdir()
    transcript=_seed_scoped_worker_task(state,['docs/report.md'])
    transcript=type(transcript)(str(transcript).replace('dev-1',role))
    before=(state/'kanban.json').read_bytes()
    code=_invoke(state,event='codex.hook.pre_tool_use',backend='codex',
        payload={'session_id':'read-query','transcript_path':str(transcript),'tool_name':'Bash',
                 'tool_input':{'command':query(state/'events.jsonl',comparison)}},monkeypatch=monkeypatch)
    assert code==0
    events=EventLog(state/'events.jsonl').read_all()
    assert any(e.type=='codex.hook.pre_tool_use' for e in events)
    assert not any(e.type=='worker.runtime_write.rejected' for e in events)
    assert (state/'kanban.json').read_bytes()==before


@pytest.mark.parametrize('change', ['write_text','write_open','trailing','unquoted','rebind','reflect','import_os','dynamic','method_rebind','redirect','print_file'])
def test_query_shape_does_not_authorize_hidden_mutations(tmp_path, monkeypatch, change):
    state=tmp_path/'.state';state.mkdir();transcript=_seed_scoped_worker_task(state,['docs/report.md'])
    command=query(state/'events.jsonl')
    if change=='write_text': command=command.replace('for line in p.open():',"p.write_text('oops')\nfor line in p.open():")
    elif change=='write_open': command=command.replace('p.open()',"p.open('w')")
    elif change=='trailing': command+='\nrm '+str(state/'events.jsonl')
    elif change=='unquoted': command=command.replace("<<'PY'",'<<PY')
    elif change=='rebind': command=command.replace('for line in p.open():',"print=p.write_text\nfor line in p.open():")
    elif change=='reflect': command=command.replace('for line in p.open():',"getattr(p,'write_text')('oops')\nfor line in p.open():")
    elif change=='import_os': command=command.replace('import json','import json, os')
    elif change=='method_rebind': command=command.replace('for line in p.open():',"for json.loads in {Path.open:0}:\n json.loads(p,'w')\nfor line in p.open():")
    elif change=='redirect': command=command.replace("python - <<'PY'", "python - > '"+str(state/'events.jsonl')+"' <<'PY'")
    elif change=='print_file': command=command.replace('for line in p.open():',"print('bad',file=p.open())\nfor line in p.open():")
    else: command=command.replace('import json',"import json\nexec('pass')")
    code=_invoke(state,event='codex.hook.pre_tool_use',backend='codex',
        payload={'session_id':'unsafe-query','transcript_path':str(transcript),'tool_name':'Bash',
                 'tool_input':{'command':command}},monkeypatch=monkeypatch)
    assert code==2
    assert any(e.type=='worker.runtime_write.rejected' for e in EventLog(state/'events.jsonl').read_all())
