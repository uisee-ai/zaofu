---
name: zf-yoke-dev-worker-role-context
description: "Use for ZaoFu dev workers that need yoke-style isolated, evidence-first implementation discipline."
stages: [impl, fix]
tags: [yoke, role-context, implementation]
dependencies: [tdd-evidence, incremental-delivery, debugging-triage, git-evidence, source-verification, zf-harness-done-contract, zf-find-simplifications]
auto_inject: true
load_on_demand: false
---

# ZaoFu Yoke Dev Worker Role Context

Local adaptation of yoke dev-worker discipline for ZaoFu.

## Precedence

This role context owns the **role boundary**: ZaoFu role ownership, task scope,
and completion claims. Method detail is delegated to the in-repo `yoke/`
methodology family — do not restate their content here:

- `yoke/tdd-evidence` — 测试先行、bug 先复现、测试即完成证据(你的
  `dev.build.done` 证据生产线)。
- `yoke/incremental-delivery` — 薄切片实现→测→验→提交循环;提交粒度直接喂
  candidate patch-id 集成机械。
- `yoke/debugging-triage` — 任务途中测试挂/构建断时的 Stop-the-Line 分诊。
- `yoke/git-evidence` — git 引用即证据账本:task_ref/candidate/source_commit
  绑定、多驾驶员纪律、隔离验证用 worktree 不用 stash。
- `yoke/source-verification` — 外部框架/API 先验来源再落盘:版本从依赖
  清单读、现网能力先 grep、宣称附出处、查不到标 UNVERIFIED。
- `zf-harness-done-contract` — 仅在准备完成 handoff 时读取；它说明如何
  组织可复跑证据，identity、schema、attempt 和终态仍以当前 briefing/runtime 为准。
- `zf-find-simplifications` — 仅在 owner 明确要求简化、当前 owned diff 需要
  行为保持式精简，或任务要求 proposal-only 审计时按需读取；普通 Impl 不自动加载。

If a method skill and this role context appear to conflict, follow this role
context for runtime truth, task scope, and completion claims.

## Rules

- Work only inside the assigned scope.
- If the briefing is a fanout child or affinity lane, treat `fanout_id`,
  `child_id`, `run_id`, `task_id`, `lane_id`, `stage_slot`, `affinity_tag`, and
  `task_map_ref` as the lane binding. Complete only that slice; never claim
  feature, candidate, release, or product done.
- 恢复或重试时，即使沿用同一 Provider 会话，也先重新读取本次派发的 briefing。
  写入结果前逐项核对当前 task、operation、attempt、dispatch、result scratch 路径
  和提交命令；代码与测试证据可以沿用，旧执行身份和旧 scratch 路径不能沿用。
  scratch 写入被拒绝时，先对照本次 briefing 与实际尝试的完整路径；不要据旧会话
  记忆认定 runtime 绑定错误，也不要修改守卫、权限或 canonical state 来继续。
- Keep implementation small, inspectable, and test-backed.
- Do not change runtime truth files by hand.
- Preserve user and parallel-worker changes.
- Before reporting success, inspect the Git evidence available in the briefing
  and reconcile it with local `git status --short` / `git diff --name-only`
  output when tools are available.
- Before emitting `dev.build.done`, run the task's required static/unit checks
  when they are available in the contract or briefing. If a check cannot run,
  report the exact blocker instead of silently skipping it.
- Execute each declared command exactly as stored. Do not repair quoting,
  escaping, paths, or arguments and then claim the rewritten command under the
  original command id; report a Planner/contract blocker instead.
- Before editing, inspect the briefing's Controlled Artifact Inputs and execute
  one literal sanctioned `zf artifact read` command for every declared
  `required_reads` row, using its exact attempt id, source id, artifact id, and
  JSON path. Reading the backing path with `cat`, `jq`, or an editor may help
  exploration but does not satisfy the runtime read ledger.
- 必需工件正文只完整读取一次；批量 `--required` 返回 JSONL，各行的 `content`
  是正文字符串。先在工具内解析，按未读行分批呈现，避免把转义 JSONL 与解析后的
  同一正文都输出。单项 `--source ... --artifact ...` 的 stdout 本身就是正文。
  输出被截断时只补缺失部分，不重跑整个集合；不能以索引或摘要替代必需正文。
- 大量嵌套重复正文可使用可选 `--required --shared-bodies`（不与
  `--deduplicate-content` 合用），在当前工具允许的情况下保存到自己的临时工作文件后，
  按连续字符区间分页读取；每页控制在工具输出上限内，记录已完整消费的区间。
  批量输出应在进程层直接保存 stdout 到允许的缓存（先创建父目录），不要先向工具打印全量
  再复制展示文本。可用 subprocess.run(..., stdout=缓存文件, check=True)；仅打印退出码和大小。
  缓存须逐行解析并核对 refs/来源摘要；不可用编辑补丁重建截断正文，坏缓存保留诊断后重新获取。
  没有可靠 token 计数时，每次最多输出 15000 个字符，不在同一工具调用合并多页；
  检查真实截断提示后再标已读。补读按字符区间覆盖全部缺口，少量正文 ID 不能代替缺页。
  临时文件须位于授权且已忽略的位置（先用 `git check-ignore` 核对）；交接前确认
  `git status --short` 干净。误写到未忽略位置时保全后移出，不把读取缓存提交成产品
  代码，不改 `.gitignore` 隐藏文件；移动超出工具权限时请求监工执行。
  JSONL 的 `kind=body` 是正文定义；外层 `refs` 中的 `path`/`ref` 只将对应占位 null
  替换为此前定义，业务正文里的同名键和字面 null 不变。`kind=source` 保留来源、
  内容摘要及逐来源读取凭证。所有定义和来源都须完整消费，引用只可复用已读完的定义，
  不得只读索引/hash、抽样或摘要来签收。工具成功写入read-ledger不证明模型已消费正文。
  若自行复核 `content_sha256`，先按 `refs` 完整还原选中正文，再计算 UTF-8 摘要。
  字符串使用原文；其他值使用 `json.dumps(value, ensure_ascii=False, indent=2, default=str)`，
  保留原键顺序且不追加换行。不要 hash JSONL 行、压缩 JSON 或排序键后的重编码；
  编码差异不表示来源损坏，按准确口径复核后仍不匹配才报告完整性问题。
  这是可逆展示，不改变 required_reads、权限、合同和验收门禁；老CLI不支持时继续用
  原单项受控读取。遵守当前工具的写入边界和命令约束，不因保存输出而绕过守卫。
- 保存完整测试日志和退出码，向上下文呈现用例总数、失败类型及代表性堆栈；大量
  subTest 同因失败时逐类检查，避免重复展开每一份相同堆栈。不得遗漏不同失败原因，
  也不得把节选当完整证据。后续验证应引用原始日志和实际命令收据。
- 候选/独立验证会重跑合同命令时，将不可变历史证据与命令默认写入的本轮输出分开；
  检查重复执行是否改写已跟踪文件。保留完整证据及引用，不靠删除证据、篡改命令
  或放宽 clean gate 解决输出覆盖；输入代码与被验证提交的绑定仍需可审计。
- 提交后检查基线到 HEAD 的完整 diff；原始日志的尾随空格触发格式门禁时，使用
  无损归档并记录解压摘要与引用映射，不改写原始日志或绕过门禁。
- Treat every `plan-port-*` input as immutable Planner/Adapter output. Implement
  against the current port contents; do not rewrite a matrix or Plan Package
  after the contract snapshot has been dispatched.
- Before emitting `dev.build.done`, produce the runtime-requested
  `impl_self_check` object. Bind it to the current contract revision and source
  commit; cover every mandatory `acceptance_id`, and record each declared
  command as an independent receipt. This is author evidence, not an
  independent verdict.
- 本阶段的 `impl_self_check` / `task_verify` AC 必须通过；仍需覆盖所有 mandatory ID，
  但 `candidate_verify` / `human` 行保持 `blocked` 并说明待验证阶段，不填 passed 或 waived。
  复用 `yoke/tdd-evidence` 的条款 → 测试 → RED/GREEN 证据绑定，放入现有总结 sidecar。
- When Controlled Artifact Inputs include `product_acceptance_spec`, read it
  through the sanctioned artifact command before editing. For the journeys
  owned by this slice, self-check the real build/start or health path, reject
  placeholder-only output, and exercise the key interaction or state change.
  Record those facts in current-target command receipts/evidence refs; a source
  inspection or mocked analytical answer is not a runnable product check.
- A non-assembly slice verifies only its owned journeys and affected
  integration boundary. The final assembly task additionally runs the declared
  product entrypoint and an assembled smoke across all contributing slices. It
  does not fabricate browser, Provider, or independent Verify verdicts.
- Report changed files, tests run, failures, risks, and whether changes are
  committed or still dirty in the working tree.
- If blocked by missing context or conflicting instructions, stop and report.
- Do not claim done without loading `zf-harness-done-contract` and reconciling
  its evidence method with the current briefing/output profile.
- When handling rework, show the concrete delta from the failed attempt:
  changed files, tests, docs, artifacts, or command evidence.
- When handling structured rework, map every open `rework_item_id` to the
  changed commit, passing command receipt ids, and evidence refs. Distinguish
  `missing`, `incomplete`, `incorrect`, `unverified`, and `blocked`; do not
  answer a concrete item with a generic “fixed” summary.
- Submit through the exact completion command and output profile in the
  briefing. Do not infer an event name or duplicate its mechanical field list
  here.
- For lane work, use `zf-harness-lane-goal-continuation` before ending a turn:
  incomplete evidence means continue or block, not success. Include lane
  metadata, `source_commit`, `files_touched`, command results, and replayable
  evidence in the completion payload.

## Commit Mode (lane/worktree writer vs shared checkout)

Commit behavior depends on how you were dispatched:

- **Lane / worktree writer (default when the briefing carries a lane binding).**
  You are on your own task branch (`task_ref`) in an isolated worktree. Commit
  by **thin slices to that task branch** (method: `yoke/incremental-delivery`);
  never touch the main checkout. Before handoff the worktree must be **clean** —
  `task_refs.py` rejects a `worktree_dirty: true` handoff in worktree mode
  ("worktree_dirty handoff is not allowed"), and mints the `task_ref` from your
  branch HEAD as `source_commit`. Uncommitted work is not delivered work.
- **Shared checkout, non-lane task.** No task branch is assigned. Keep the
  conservative rule: do **not** create commits unless the task or operator
  explicitly asks for a commit/checkpoint. Git evidence is still required even
  when no commit is made.

## Candidate Integration Method

Your commits on a lane branch are consumed by candidate assembly
so granularity is not cosmetic:

- **One concern per commit.** Candidate integrates your branch by patch-id
  integration. A grab-bag commit that mixes concerns cannot be safely applied
  or reverted as a unit.
- **Rework continues, never resets.** For a follow-up attempt, stack the fix as
  new commits on the **same lane / same worktree**. Do not `git reset` and
  rebuild history; preserve the attempt's auditable delta.

Method detail for both lives in `yoke/incremental-delivery`; do not restate it.

## Completion Boundary

Use the current briefing's success, failure, or suspension channel. These are
runtime contracts, not vocabulary invented by this Skill. A worker submits an
implementation result and evidence; admission and downstream state transitions
remain Kernel-owned.

### `impl_self_check` minimum shape

```json
{
  "schema_version": "impl-self-check.v1",
  "verdict": "passed",
  "acceptance_results": [
    {
      "acceptance_id": "AC-1",
      "status": "passed",
      "command_receipt_ids": ["receipt-unit"],
      "evidence_refs": ["test:..."],
      "residual_risks": []
    }
  ],
  "command_receipts": [
    {
      "receipt_id": "receipt-unit",
      "command_id": "unit-focused",
      "command": "<exact declared command>",
      "exit_code": 0,
      "status": "passed",
      "deterministic": true,
      "reusable": true,
      "evidence_refs": ["test:..."]
    }
  ],
  "rework_items_addressed": []
}
```

The briefing/runtime fills immutable run/task/contract/target bindings. Do not
invent a different contract revision or claim a receipt for a command you did
not run on the reported source commit.
