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

## 提交工具与预校验边界

结果 scratch 位于工作目录外时，只使用原生 Edit/Write 或 apply_patch 修改当前
briefing 明确授权的精确路径；Python/heredoc/shell 动态写入不享有这个路径例外。
被守卫拒绝时先改用授权工具，不放宽守卫、不改 canonical state、不重复动态写法。
工作目录内的审计或读取临时文件另行保存，提交前保全证据并确认工作区 clean。

仅在当前 briefing/profile 提供并支持 `zf result validate` 时执行预校验；它不是
所有角色通用的提交前条件。如果明确返回 `plan_candidate_validation_unavailable`，
记录其未执行事实，按 briefing 使用正式 `result submit` 的准入校验，不宣称预校验
通过，也不重复调用不支持的入口。实际 schema/证据错误必须修复，不能用此例外绕过。

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

### 输入恢复与版本

换会话/压缩后先使用 briefing 中的 `artifact recover --attempt <id>` 查看当前绑定、
授权目录和检查点，再补读必需正文。历史已读不证明当前记忆；旧 package 要求 rebind
时走受控恢复，不自行修改 manifest 或执行状态。可用 `artifact index --relation-id <id>`
定位声明的 REQ/AC，关系命中不能替代正文消费和结果证据。

## 阶段工作记录与恢复

长任务在完成一组证据、出现未决事项或准备换会话时，可用当前 operation 的
`zf artifact work-record publish --operation <id> --file <workdir 内 JSON> --request-id <唯一 ID>`
发布非终态记录。JSON 严格包含 completed_actions、pending、next_step、evidence_files；
前三者为动作列表、未决列表、下一步字符串，证据为工作区内相对路径列表。
不写凭据；测试日志不能包装成独立 Verify 结论。

发布前核对当前 `src/zf/runtime/work_record.py::publish_work_record` 与 `_read_scoped`
的机械限制：record JSON 至多 16 KiB，三个列表各至多 16 条，每条为非空字符串且
至多 1024 字符，next_step 至多 2048 字符；单份证据至多 64 KiB，必须是工作区内
普通 UTF-8 文本文件，不能是符号链接、敏感内容或二进制归档。request-id 至多
128 字符。该入口是阶段检查点，不是无界附件上传入口。

需要保存的证据超过单次限制时，按验收/命令划分批次，每批使用独立 request-id，
保留全部事件与 sidecar 引用并在交接中对账；同一 ID 重试不能改内容。已随提交保存
的完整历史证据可引用固定提交与路径，不必重复上传。二进制或超大原始文件沿合同
允许的 Git/工件入口保留；不能为了发布成功删证据、修改原始字节/摘要、提额或把
摘要冒充原文。若当前合同没有可用入口，记录确切限制并请求受控处理。

命令使用现有 result-submit 凭据，核验 current Task/attempt/lease/workdir/input 版本；
完整 sidecar 先写入，再发布事件。同一 request-id 必须保持记录和证据内容不变。
新会话先用 briefing 的 recover 入口，再用 `artifact work-record show --operation <id>`
按 digest/next_page_start 回读当前记录。历史陈述不证明当前理解，不豁免 required reads。
记录不消费正式 result submit、不完成 Task，也不授予执行权限；仍按原交付合同提交结果。
