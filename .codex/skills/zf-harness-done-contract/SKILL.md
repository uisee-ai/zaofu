---
name: zf-harness-done-contract
description: "Use when an Impl/Fix worker is ready to hand off work; requires reproducible evidence while leaving identity, result admission, and terminal state to the deterministic runtime."
stages: [impl, fix]
tags: [contract, evidence, completion]
dependencies: [zf-harness-evidence-collection]
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
   核对当前 contract snapshot 的 `evidence_contract`、声明的输出和测试义务，
   逐项绑定本阶段交付的文件/ref；只有合同允许时才用其他持久化证据等价替代，
   并记录对应关系。命令通过不自动满足另行声明的证据文件要求。
   返工的代码修复清单不删除原合同已授权的强制证据交付义务；为本次结果刷新这些
   证据，并保留历史来源。反馈若明确禁止所需输出，先列出冲突请求澄清；原合同未
   授权的路径不能仅凭证据要求写入。不要把返工漏列的已授权输出误报为原合同范围缺口。
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

### 后续验收程序的当前交付责任

候选阶段执行的命令也可能由当前 Task 生产。对照 command 的 `producer_task_id`、
声明输出和写域：属于本任务的程序，应已实现真实调用与断言，并交付参数、夹具、
故障注入、证据及清理约定。用语法/参数预检与有意义的程序自测证明可执行和失败传播，
不以固定退出的占位分支冒充已交付程序。缺真实环境时明确前置条件，保留最终 E2E
实际通过义务；程序自测不证明候选通过。程序属于后续 Task 时记录其 owner 与依赖，
不越界代做；归属不清时列出合同缺口供 OA 受控修订，不自行新增门禁或改变阶段责任。

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

### 输入恢复与版本

换会话/压缩后先使用 briefing 中的 `artifact recover --attempt <id>` 查看当前绑定、
授权目录和检查点，再补读必需正文。历史已读不证明当前记忆；旧 package 要求 rebind
时走受控恢复，不自行修改 manifest 或执行状态。可用 `artifact index --relation-id <id>`
定位声明的 REQ/AC，关系命中不能替代正文消费和结果证据。

`artifact recover` 的 `authorizes_execution=false` 表示报告本身不授予执行权限，
不是对当前合法派发的否决；`current_memory=unknown` 和 `reconstruction_required`
范围总数不会因读取变为已知或零。不要等这些字段变化或检查点自动出现。先核验当前
身份/租约/版本及真实暂停要求，再补足缺失必读和遗忘正文，按当前角色推进检查；
来源不可读或需要重绑定仍走受控恢复。历史收据不能证明当前理解。


## 阶段工作记录与恢复

需要发布非终态进展时，按需读取共享 `zf-harness-evidence-collection` 技能的
[WRC 正文与附件方法](../zf-harness-evidence-collection/references/work-record.md)。
Impl 与 Verify 使用同一四字段正文和相对附件路径约定，不直接 publish 原生日志。
现有 result scratch/证据 refs 继续按原合同交付；WRC 不新增写权限、必交产物或
完成门禁，也不代替当前必读、独立验收和正式结果准入。
