---
name: zaofu-bug-fix-cycle
description: "Operator playbook for resuming cangjie work after zaofu.bug.detected — stash, fix in ZaoFu repo, restart watcher, resume."
---

# zaofu-bug-fix-cycle

When cangjie's events.jsonl periodic scan emits `zaofu.bug.detected`, an
operator-mediated fix cycle takes over. The cangjie task state is paused;
zaofu kernel is patched + pushed; watcher restarts pick up the new code;
cangjie resumes with no work loss.

**Important**: zaofu must **not** modify itself. The fix cycle is an
operator activity (or a separate sprint-card-driven zaofu repo activity),
not an in-flight kernel patch.

## 分层边界(先判断该不该用本技能)

补救分四层,逐层升级,本技能只占最后一层:

1. **worker 排错** — `yoke/debugging-triage`。任务途中测试挂/构建断/行为不符,
   worker 自己 Stop-the-Line 分诊。属任务内部,不升级。
2. **Tier-2 诊断** — `yoke/diagnosis`(kernel: `diagnosis.requested` /
   `diagnosis.completed` / `diagnosis.failed`,`src/zf/runtime/diagnosis.py`,
   task `tasks/2026-07-06-0930-P0-tier2-diagnostic-intervention.md`)。同一
   stall 指纹 K≥3 不收敛时按需 spawn 诊断者 attach 读现场,产结构化 `next_action`
   (propose-only)。仍在**目标项目自身**的运行闭环内。
3. **授权自愈** — `zf-self-repair`。autoresearch/supervisor 点名一个 **harness
   自 bug** 且操作员已授权(`ZF_AUTORESEARCH_AUTO_REPAIR=authorized`),
   Claude/Codex 在隔离 worktree 里 backlog→修→按成功判据验→带 commit hash 标
   done,红则不合、升级。
4. **本技能(操作员中介的内核修复)** — `zaofu.bug.detected` 命中一个 zaofu
   **内核自 bug** 且**没有**授权自愈接管时,操作员手动介入:暂停目标项目、在
   zaofu 仓 patch+push、重启 watcher、resume。

移交:1/2 停在项目运行闭环;确认根因在 zaofu 内核后升到 3(有授权)或 4(操作
员手动)。**3 与 4 互斥**——同一 bug 要么走授权自愈的自动 loop,要么走本技能的
操作员 loop,不要两条同时跑。

## Triggers

`zaofu.bug.detected` event payload structure (β-1 output). This event
type is registered in `known_types` and is a `wake_pattern`, so it wakes
the watcher on emit:

```json
{
  "type": "zaofu.bug.detected",
  "actor": "zf-cli",
  "payload": {
    "signature": "ship_block_loop | respawn_failure_cascade | judge_failure_loop",
    "confidence": "high | medium | low",
    "evidence_event_ids": ["evt-...", "evt-..."],
    "suggested_fix_area": "src/zf/runtime/<module>.py",
    "run_state_snapshot": { /* 形状随 signature 变,见下表 */ }
  }
}
```

- `run_state_snapshot` 是当前字段名(`orchestrator.py:1331` 铸造)。旧名
  `cangjie_state_snapshot` 保留为 legacy alias(`SignatureMatch` 上的只读
  property;`zf bug-fix-cycle` 也向后兼容读 both keys),新代码只写
  `run_state_snapshot`。
- snapshot 字段随 signature 不同,**不存在**同时含 `pdd_id`+`task_id` 的组合
  (核实源 `src/zf/runtime/zaofu_bug_signatures.py`):

  | signature | run_state_snapshot 字段 |
  |---|---|
  | ship_block_loop | `pdd_id`, `blockers`, `occurrence_count` |
  | respawn_failure_cascade | `instance_id`, `occurrence_count` |
  | judge_failure_loop | `task_id`, `occurrence_count`, `rejection_summaries` |

## Cycle (step 0 诊断 + 4 步手动)

### 0. 诊断(`zf bug-fix-cycle` — 已落地,只读)

`zf bug-fix-cycle [--state-dir DIR] [--signature <name>] [--json]` 读目标项目
`.zf/events.jsonl` 里最近一条 `zaofu.bug.detected`(可按 signature 过滤),打印
证据事件、`suggested_fix_area`、`run_state_snapshot`,并回显下面 4 步剧本。这是
**只读**诊断助手——**不** stash、不重启、不 emit。剩余自动化仍规划中(见文末
「β-3 自动化」)。

```bash
cd /path/to/project
zf bug-fix-cycle --signature ship_block_loop              # human 诊断
zf bug-fix-cycle --json | jq .payload.run_state_snapshot  # 机读
```

拿到诊断后,手动走下面 4 步。

### 1. Stash cangjie task state

Cangjie's git working tree may be partially mid-task. Snapshot it:

```bash
cd /path/to/project
git stash push -u -m "zaofu-fix-pause-<bug-signature>"

# Record the bookmark so resume knows the precise return point.
mkdir -p .zf/tmp
echo "stashed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > .zf/tmp/fix-cycle-bookmark
echo "feature_id=<F-id from payload>" >> .zf/tmp/fix-cycle-bookmark
echo "task_id=<TASK-id from payload>" >> .zf/tmp/fix-cycle-bookmark
echo "branch=$(git branch --show-current)" >> .zf/tmp/fix-cycle-bookmark
echo "head=$(git rev-parse HEAD)" >> .zf/tmp/fix-cycle-bookmark
```

### 2. Fix zaofu (in /path/to/zaofu)

Read the evidence events to confirm the failure pattern, then patch the
suggested fix area:

```bash
cd /path/to/zaofu

# Read the evidence
for ev_id in <evidence_event_ids from payload>; do
    grep "\"id\":\"$ev_id\"" /path/to/project/.zf/events.jsonl
done

# Implement the fix in suggested_fix_area
# Validate-First Discipline: confirm the bug still reproduces against
# the current zaofu HEAD before writing the fix (see zaofu/CLAUDE.md §
# "Validate-First Discipline").

# TDD: add a regression test that replays the evidence events.
# 方法见 yoke/tdd-evidence(bug 先复现、测试即证据)。
.venv/bin/python -m pytest tests/test_<area>.py --no-cov -q

# Full suite
.venv/bin/python -m pytest --no-cov -q

# Commit + push
git add ...
GIT_AUTHOR_NAME="..." GIT_AUTHOR_EMAIL="..." \
GIT_COMMITTER_NAME="..." GIT_COMMITTER_EMAIL="..." \
  git commit -m "fix: <bug-sig> — <one-line>"
git push origin dev
```

### 3. Restart cangjie watcher

In-process zaofu instance is on pre-fix code. Restart to load the new
kernel:

```bash
cd /path/to/project
/path/to/zaofu/.venv/bin/python -m zf.cli.main stop
/path/to/zaofu/.venv/bin/python -m zf.cli.main start &
```

Wait for `session.started` + `loop.started` in
`.zf/events.jsonl`.

### 4. Resume cangjie task

```bash
cd /path/to/project
git stash pop  # restore mid-task working tree

zf emit zaofu.bug.fix_applied \
  --payload '{
    "bug_signature": "<from payload>",
    "fix_commit": "<git rev-parse HEAD in zaofu repo>",
    "evidence_event_ids": [...]
  }'
```

`zaofu.bug.fix_applied` 是 **skill-owned 审计约定(无内核校验)**——它未注册进
`known_types`、不在 `wake_patterns`、也没有专属 reactor 消费。这条 emit 只作
events.jsonl 里的审计标记(记录"哪个 bug / 哪个 fix commit 已应用"),**不**自行
唤醒 watcher、**不**触发状态迁移。真正让任务恢复的是第 3 步的 watcher 重启(加载
新内核)+ `git stash pop` 还原工作树;之后 orchestrator 既有 reactor 在下一 tick
或真实唤醒事件上重评被暂停的任务。(旧稿的 `cangjie.bug.zaofu_fix_applied` 已并入
`zaofu.bug.fix_applied`,与 CLI 回显对齐。)

ship-block-loop 若在重试点仍卡,操作员按项目自身的 ship 流程手动重试一次即可——
不要依赖一次性脚本。

## Rules

- **Never** push to zaofu `master` from the fix cycle. Stay on `dev`.
- **Never** force-push or amend pushed commits. Each fix is a new commit
  for clear history.
- **Always** add a regression test with the exact evidence events before
  declaring the fix done.
- **Always** validate-first: confirm the bug still reproduces against
  current zaofu HEAD before writing the fix.

## β-3 自动化(部分落地)

`zf bug-fix-cycle` CLI 已落地为**第 0 步只读诊断助手**(见上):读最近
`zaofu.bug.detected`、打印证据 + `run_state_snapshot` + 4 步剧本。**尚未**实现的
是把 stash / restart / resume 真正跑起来:auto-stash、逐步 confirm、resume 时自动
emit `zaofu.bug.fix_applied`。这些仍规划中,跟踪在 backlog
`backlogs/2026-05-17-1447-zero-touch-beta-self-healing.md` §β-3(设计母体
`docs/design/36-zero-touch-long-horizon-roadmap.md` §7.1)。

在剩余自动化落地前:**先跑 `zf bug-fix-cycle` 取诊断,4 步仍手动走本 markdown**。
目标仍是 ≤3 次显式 confirm(对比 r-next-8/9 的 ≥5 次手工触点)。
