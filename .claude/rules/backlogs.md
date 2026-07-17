---
paths:
  - "backlogs/**"
  - "tasks/**"
---

# Backlog & Sprint Rules

This rule auto-loads only when working on `backlogs/` or `tasks/` files.
Keeps the main CLAUDE.md focused on architecture; pulls in
sprint/backlog discipline only when relevant.

## 目录边界(2026-05-19 audit 后确定)

- **`backlogs/` = 候选项**(候选 / 调研 / 想法 / DEFER)。状态为
  `proposed` 或 `defer`。**未立项时停留在这里**。注意:`backlogs/`
  在 .gitignore 里,候选项是 local working notes,不上 git。
- **`tasks/` = 活跃 sprint + 归档**(git-tracked)。状态为 `active` 或
  `done`。**一旦立项,整个文件移到这里并按精确路径 stage**;完成后**留在 tasks/
  归档**(不要再 mv 回去)。
- 两个目录文件命名一致:`YYYY-MM-DD-HHMM-<slug>.md`,UTC 时戳生成
  `date -u +%Y-%m-%d-%H%M`。Lex order = chronological,所以
  `ls tasks/ | tail` 永远是最新 sprint。
- 历史无时戳文件保留原样,**新文件必须遵守**。

## 状态字段强制(2026-05-19 起所有新建文件必须有)

每个 backlog / sprint 文件**第一段**必须含 `> 状态:` 行,取值之一:

| 状态 | 含义 | 必填附注 |
|---|---|---|
| `proposed` | backlog 候选项,未立项 | — |
| `active` | 已立项,正在做或即将做 | sprint plan 必须包含 TDD acceptance |
| `done` | 已完成 | **必须**带 commit hash 引用(短 7 位 + 提交标题) |
| `defer` | 延期 | **必须**带具体触发条件("当 X 发生时再做") |
| `superseded` | 被另一 sprint 取代 | 必须指向新 sprint 路径 + 解释为什么 |
| `obsoleted` | 设计前提已不成立 | 必须解释为什么(对比 superseded:前者主动取代,后者前提变了) |

**反 pattern 案例**(2026-05-19 audit 抓到的 21 个 "状态: backlog" 滞后头):
PWF-AUGMENT-BATCH-3、EVAL-METRIC-SNAPSHOT-IN-HEALTH、PREREQ-A/B/C、
ZF-LH-SP-001、Long-Horizon P0 batch-2、PWF-PRECOMPACT/SESSION-ISO、
11 个 EVAL-* — 都已实施但 backlog 文件状态头未更新。

## 立项 / 完成 / 延期的机械动作

```bash
# 立项(候选项 → 活跃 sprint)
mv backlogs/2026-05-19-tr-foo.md tasks/
# 然后编辑 status → active
# commit 时只 stage 精确目标: git add -- tasks/2026-05-19-tr-foo.md
# 仅当 git ls-files --error-unmatch <source> 成功时才使用 git mv

# 完成
# 编辑 status → done (2026-05-19 commit `abc1234` "feat: TR-FOO-001 — ...")
# 文件留在 tasks/(归档,不要 git rm)
# 用户已明确批准执行的 backlog/task batch,完成聚焦验证后直接 commit
# 实现与任务状态;未批准候选和纯分析不自动 commit;push 仍需显式要求。

# 延期
# 编辑 status → defer (2026-05-19 audit) — 具体触发条件: "<X>"
# 文件留在原目录
```

## Acceptance criteria 用 `step → verify: check` 格式

每个 sprint 任务机械化 TDD-able:

```
1. add MemoryStore.get(last_days=N)   → verify: unit test green
2. wire into recovery._render_memory  → verify: integration test green
3. delete StalenessChecker age branch → verify: full pytest green
```

Weak verification ("make it work") forces rework;strong verification
lets the TDD loop run unattended.

## 周期性 audit(2026-05-19 起每周或主线 batch 完成时跑一次)

**推荐**:如果本地存在 slash command `/audit-backlogs`,优先用它机械化执行;
否则直接按下面的底层配方 dry-run:

```
/audit-backlogs                         # dry-run report
/audit-backlogs --apply                 # 看完报告后应用 DONE 更新
/audit-backlogs --since "1 month ago"   # 拓宽时间窗
```

DEFER 类**不会被自动改**(需要人填触发条件),会列在报告里让你手工补。

**底层配方**:

```bash
# 找所有"状态: backlog/proposed"但实际可能已实施的文件
grep -lE "^> 状态: (backlog|proposed)$" backlogs/*.md tasks/*.md 2>/dev/null

# 交叉对照 git log 看是否有匹配 commit
git log --since="2 weeks ago" --oneline | grep -iE "<sprint-id-pattern>"

# 也查 src/ 是否有引用(本会话已实施但未 commit 的情况)
grep -rln "<sprint-id>" src/zf/ 2>/dev/null | grep -v __pycache__

# 滞后但已 commit → 改 status 为 done + commit hash
# 滞后但未 commit → 改 status 为 done UNCOMMITTED + src 引用
# 真未做的 → 改 status 为 defer + 触发条件(人填)
```

**经验值**:每开 ≥10 个 backlog 后,做一次 audit。本周 80 个 backlog
中 27 个标"状态: backlog",其中 **21 个其实已 done**,差点变成永久债。

## Validate-First Discipline (Stale-Backlog Anti-Pattern)

Bug candidates identified in one sprint (e.g. B-NEW-X "candidate"
items) **must be re-validated against current HEAD before
implementation**. Codebase moves fast — an upstream fix in a related
B-NEW commit can incidentally close a downstream candidate, and
implementing the obsolete fix wastes engineering or modifies code that
no longer needs it.

Before opening a kernel-fix PR for a carried-over backlog item:

1. **Reproduce on current HEAD** — find the original failure
   signature (events.jsonl line, log message, exception) and confirm
   it still appears. For ship-blocking bugs, the most recent
   `ship.blocked.blockers` payload is canonical.
2. **If not reproducing**, downgrade the item to "verified resolved",
   note which commit closed it, and skip the fix.
3. **If reproducing**, the test cases must include the **exact**
   replay payload from current state, not the historical one.

Past saves: 2026-05-17 confirmed B-NEW-13/14 still reproduce by
reading the configured state dir's `events.jsonl` tail (avoided premature fire); B-NEW-1..10
churn shows how fast the bug surface shifts within one cangjie cycle.

Corollary for cangjie-driven sprints: **fire a real cangjie task
before doing kernel work**, not the other way around. The cangjie
event stream is the current evidence source for "what zaofu still fails on
today".
