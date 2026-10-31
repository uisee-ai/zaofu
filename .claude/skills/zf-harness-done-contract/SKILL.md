---
name: zf-harness-done-contract
description: "Use when an Impl/Fix worker is ready to hand off work; requires reproducible evidence while leaving identity, result admission, and terminal state to the deterministic runtime."
stages: [impl, fix]
tags: [contract, evidence, completion]
auto_inject: false
load_on_demand: true
---

# ZaoFu Implementation Handoff Contract

Use this reference only when an Impl/Fix worker is preparing its completion
handoff. The active role method is `zf-yoke-dev-worker-role-context`; this
contract explains what makes the implementation claim useful to the next
stage.

## Authority

The current task briefing, contract snapshot, output profile, and completion
command are authoritative. Do not memorize or recreate event schemas from this
file. Runtime owns:

- run/task/dispatch/generation identity;
- contract and target snapshot refs/digests;
- result admission and protocol repair;
- attempt counting, rework cap, stale/replay guards;
- task, candidate, and run terminal state.

If a required field or event name differs from an older example, follow the
current briefing. Never work around admission by emitting a different terminal
event or editing truth files.

## Handoff Method

Before submitting the completion claim:

1. Compare the assigned scope with the actual diff and produced artifacts.
2. Run the contract's focused checks exactly as stored plus the relevant
   build/typecheck command. A rewritten, re-escaped, or "equivalent" command is
   a different check and cannot produce a receipt for the declared command id.
3. For an isolated lane/worktree writer, commit the intended delta and leave
   the assigned worktree clean. Shared-checkout workers commit only when the
   task or operator explicitly requires it.
4. Map every acceptance criterion you own to reproducible command, file,
   artifact, or Git evidence.
5. Record skipped checks, known risks, and the next owner/gate.
6. On rework, identify the triggering finding and show the concrete delta from
   the previous attempt. A no-code correction needs equally concrete evidence.
7. Submit through the exact completion command/output profile from the
   briefing, once.

### 按冻结阶段责任交接

准备交付时，对照 AC 的 `verification_owner`、command id、写入范围和依赖产物，
区分当前任务必须满足的检查与后续候选的整体验收。全量命令提前执行后，若缺少
合同明确分配给后续任务的产物，应保留非零结果、缺口及其 Owner；不能把候选未完成
当作当前任务已失败，也不能声称全量通过。当前任务自己的必需检查仍须通过；最终
候选的 mandatory gate 保持原样，由后续 Owner 实际执行。

若 briefing / 返工指令要求的阶段前置条件与冻结责任冲突，报告具体 AC、命令和产物
归属供 OA 修订；不要自行把失败改成成功、跳过当前检查或扩大实现范围。达到重试上限
时走既有语义恢复路径，保留可复用代码和证据，不重写已接纳的失败结果。

## Evidence Quality

本片合同包含运行/部署交付时，提交同版指南：精确准备/启动/就绪命令、配置与测试
身份取得、初始化、关联 AC/command 的旅程及自有资源清理。恢复验证按合同要求。
不嵌入秘密；自检收据与独立 Verify 分开。后继不能依赖私聊或未声明的本机准备。
库和文档任务按实际入口交付，不默认增加 Compose；指南属于其他任务时保留其
owner 与依赖，不跨越写入范围代做。

The handoff must make these facts recoverable without replaying chat:

- what changed or what artifact was produced;
- which commands ran and whether they passed;
- which acceptance criteria those checks cover;
- the committed source ref when worktree mode requires one;
- evidence refs that resolve to real files, artifacts, commands, events, or
  Git objects;
- residual risks, omitted checks, and the expected next action.

Narrative statements such as "done" or "tests pass" are not evidence. Do not
copy large logs into the event payload; persist them and cite their refs.

## 查找已持久化的命令证据

使用当前 briefing 中的 CLI 和 state dir 查询实际 Provider 的 hook：

```text
zf events --type codex.hook.post_tool_use --actor <role_instance> --last 10 --index --json --state-dir <state_dir>
```

Claude 使用 `claude.hook.post_tool_use`。按 actor 定位后，用 `--id <event_id>`
读取选中事件的完整正文，并核对当前 Run、`causation_id` 对应的派发、cwd、命令
和执行结果。hook 的顶层 `task_id` 可能为空；不能用 `--task` 查询为空来断言
工具未执行或证据未采集，也不能把其他尝试的结果冒充当前尝试。

优先引用真实 `event:<event_id>` 和事件给出的 `refs.raw_output`（若存在）；
读取 sidecar 并核对其 digest。短输出或空 stdout 可以只保存在事件正文中，
不应仅因没有 raw_output 判定采集失败。若当前契约明确要求额外 sidecar，
先核对允许的证据形式；确实不满足时报告具体契约/环境阻塞，不伪造引用或通过。
保留一次索引和选中 ID，避免反复读取查询自身产生的大量 hook 输出。

## Cannot Claim Completion

Do not submit a success claim when:

- required checks failed or were silently skipped;
- a declared command passed only after changing its command string, quoting, or
  escaping; report a contract/plan blocker instead;
- the diff escapes the assigned paths or ownership;
- a lane worktree is dirty or lacks the required committed source ref;
- mandatory acceptance criteria have no evidence;
- the result relies only on worker self-report;
- an unresolved blocker prevents the next stage from consuming the handoff;
- a rework attempt cannot show what changed since the rejected target.

Report the real blocker through the briefing's failure/suspension channel.
Workers do not mark tasks, candidates, or goals done themselves.

## Related Methods

- `zf-yoke-dev-worker-role-context` - active Impl/Fix role boundary.
- `yoke/tdd-evidence` - test-first and bug-reproduction method.
- `yoke/incremental-delivery` - thin implementation/test/commit loop.
- `yoke/git-evidence` - commit, worktree, and evidence discipline.
