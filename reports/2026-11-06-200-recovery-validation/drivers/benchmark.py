import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import tempfile
import time
import tracemalloc

from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.run_manager import build_run_manager_projection

root=Path(tempfile.mkdtemp(prefix='zf-200-perf-'))
state=root/'state'
state.mkdir()
source=Path(os.environ['ZF_BENCH_CONFIG'])
config=load_config(source)
store=TaskStore(state/'kanban.json')
for i in range(16):store.add(Task(id=f'T{i}',title=f'Work {i}',status='backlog'))
log=EventLog(state/'events.jsonl')
writer=EventWriter(log)
for i in range(4000):writer.append(ZfEvent(id=f'noise-{i}',type='agent.text',actor='impl',payload={'text':'bounded diagnostic sample'}))
for i in range(16):
 for kind in ['planned','applied','effect.pending','effect.passed']:
  writer.append(ZfEvent(id=f'{i}-{kind}',type=f'run.manager.action.{kind}',task_id=f'T{i}',payload={'recovery_case_id':f'case-{i}','task_id':f'T{i}','workflow_run_id':'benchmark'}))
events=log.read_all()
reads=0
original=EventLog.read_all
def counted(self):
 global reads
 reads+=1
 return original(self)
EventLog.read_all=counted
# Identical, frozen synthetic workload; 5 warmups and 30 measured reads.
for _ in range(5):build_run_manager_projection(state,events=events,config=config,project_root=root)
reads=0
samples=[]
tracemalloc.start()
for _ in range(30):
 start=time.perf_counter()
 result=build_run_manager_projection(state,events=events,config=config,project_root=root)
 samples.append((time.perf_counter()-start)*1000)
peak=tracemalloc.get_traced_memory()[1]
tracemalloc.stop()
print(json.dumps({'label':os.environ['ZF_BENCH_LABEL'],'events':len(events),'tasks':16,'iterations':30,
 'p50_ms':statistics.median(samples),'p95_ms':sorted(samples)[28], 'rss_peak_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
 'allocation_peak_bytes':peak,'eventlog_reads':reads,'pending_actions':len(result['pending_actions']),
 'config_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}))
import shutil
shutil.rmtree(root)
