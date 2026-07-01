#!/bin/bash
# 重构进度一览(对 events 按天 rotate 鲁棒:上游阶段从下游推断)
set -euo pipefail

TARGET_ROOT="${TARGET_ROOT:-$(pwd)}"
ZF_ROOT="${ZF_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STATE_DIR="${STATE_DIR:-.zf-cj-min-refactor}"
export STATE_DIR

cd "$TARGET_ROOT"
PYTHONPATH="$ZF_ROOT/src" "${ZF_PYTHON:-$ZF_ROOT/.venv/bin/python}" - <<'PY'
import json,collections,datetime,subprocess
import os
SD=os.environ.get('STATE_DIR', '.zf-cj-min-refactor')
ev=[json.loads(l) for l in open(f'{SD}/events.jsonl') if l.strip()]
c=collections.Counter(e.get('type','') for e in ev)
now=datetime.datetime.now(datetime.timezone.utc)
age=round((now-datetime.datetime.fromisoformat(ev[-1]['ts'])).total_seconds())
alive='UP' if subprocess.run(['pgrep','-f','[z]f start'],capture_output=True).stdout else 'DOWN'
SLICES=['CJMIN-PI-CORE-001','CJMIN-STATE-CONFIG-001','CJMIN-PROVIDER-001','CJMIN-GATEWAY-001','CJMIN-WEB-TUI-001']
done={e.get('payload',{}).get('task_id') or e.get('task_id') for e in ev if e.get('type')=='dev.build.done'}
nd=len([s for s in SLICES if s in done])
# 上游阶段从下游推断(rotate-鲁棒): 有 task_map 就说明 scan+plan 完成
has_tm = c['task_map.ready']>0 or nd>0 or c['candidate.ready']>0
has_cand = c['candidate.ready']>0
def ok(b): return '✅' if b else '—'
def bar(n,t): return '█'*n+'░'*(t-n)
print(f"\n  cj-min 重构进度   watcher={alive}  事件={len(ev)}  最后={ev[-1]['type'][:22]} ({age}s前)")
print("  "+"─"*54)
print(f"  scan + plan    {ok(has_tm)}  (task_map 已生成 → scan/plan 完成)")
print(f"  impl 切片      {bar(nd,5)} {nd}/5")
for s in SLICES:
    print(f"      {'✅' if s in done else '⏳'} {s.replace('CJMIN-','')}")
print("  "+"─"*54)
for name,key in [('candidate.ready','candidate.ready'),('review 通过','review.approved'),
                 ('review 驳回→自愈','review.rejected'),('verify 通过','test.passed'),
                 ('🎉 judge 交付','judge.passed')]:
    v=c[key]; print(f"  {name:18s} {'✅ '+str(v) if v else '— 0'}")
rw=sum(1 for e in ev if e.get('payload',{}).get('rework_of'))
print(f"\n  自愈返工={rw}  escalate={c['human.escalate']}  cancel={c['fanout.cancelled']}  integ.failed={c['integration.failed']}\n")
PY
