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

Read the complete evidence lookup method below, including after compaction.
Do not stop at the first 100 lines or treat a shortened command preview as the
complete execution history. Existing event evidence needs no new product file.

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
briefing 明确授权的精确路径。如果 Provider 只通过 exec_command/Bash 暴露 apply_patch，
已有守卫也支持单独的 `apply_patch <<'PATCH'`：引号 heredoc 仅包含当前 scratch 的
Update File patch，以独立 `PATCH` 行结束；禁止前后附加命令、替换、输出重定向、
创建/删除/移动文件。该适配不绕过 Provider 权限；不可用或被拒绝时仍走协助通道。
Python 或其他 shell 动态写入不享有这个路径例外。
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

恢复尝试先使用 briefing 的 `artifact handoff-evidence --operation <当前 id>`。
索引中的 `command_preview` 只有前160字符，`command_truncated=true` 表示必须按该
`--event <id>` 回读完整 command/output；旧索引没有此字段也不证明预览完整。
复合命令前部可能是 apply_patch，后部才有测试，不能仅搜索预览中的 npm/test 关键词
就断言 RED 丢失。核对候选事件的完整正文、执行顺序、源码和断言；混合输出未能证明
各条命令的退出码时明确缺口，不从最后一个命令成功推断之前全成功。
沿 `previous_operation` 和 `--source-operation` 查询已绑定的更早尝试，不猜测其他 Run。
分页保持相同 digest；CLI 返回的 `ref` 是可引用的持久工件，未读完不能用摘要替代正文。

当前尝试尚未进入前任交接索引时，使用以下 hook 入口，而不是等待下一次返工。
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

If the assigned worktree has a legitimate delta but the sandbox denies Git
publication, preserve it and submit the current blocked result with
`failure_class: host_publication_required`, exact `changed_files`,
`target_commit` and the failed command evidence. Do not call it a product
failure, request a new Plan, change sandbox permissions or alter Git metadata
through an alternate path. Only the configured finite host-publication policy
may authorize the effect; otherwise request owner guidance. The resulting
commit receipt is not an Impl pass: the successor must inspect the exact commit,
read its own required inputs/handoff and produce its required checks before
independent Verify.

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
发布非终态记录。JSON 必须包含 completed_actions、pending、next_step、evidence_files；
前三者为动作列表、未决列表、下一步字符串，证据为工作区内相对路径列表。
不写凭据；测试日志不能包装成独立 Verify 结论。
可选 report_draft 保存至多 8192 字符的未完成报告，整体仍受 16 KiB 上限约束。
也可用互斥的 `--stdin` 传入同一 JSON，不新增工作区文件。共享候选 Reader 优先
使用此入口，evidence_files 必须为空，正文引用既有获准证据；它校验自身 call、
传输 attempt/lease、fanout 和输入绑定，不要求独占锚点 Task。不覆盖正式结果草稿。

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

### 失败路径、SOP 与决定的交接

有可复用的失败经验、操作步骤或关键决定时，在工作区内保存短 Markdown 文件，并通过
现有 `evidence_files` 固定其正文与摘要，不增加另一份任务状态。逐项写明来源事件/ref 或
固定提交、适用输入/源码版本、成立条件、失效条件、被替代的结论及仍待验证的部分。
语义适用性由接手者核对；不能把经验记录包装成新授权或独立验收。

接手 operation 使用当前凭据执行
`zf artifact work-record show --operation <当前 id> --predecessor`，读取本次交接固定的前任
记录；按其中的 `evidence_refs` 索引加 `--evidence <n>` 回读原文。沿用 digest 与分页参数，
不改用前任凭据，不通过猜测 state dir 文件路径扩大读取范围。缺失/损坏记录应报告具体
缺口，不能自行补造历史。该入口仅提供当前交接的直接前任，不是全局历史搜索。

读完后在当前记录的 completed_actions 中写核查结果，pending 保留未决/失效问题，
next_step 指明下一动作；必要时通过新 evidence_files 保留带来源的后续决定。不能仅因
前任说“已完成”就跳过当前 required reads、handoff 接收回执或结果准入。

### 受控宿主协助后的交接

沙箱禁止监听、Docker 或 Git 索引写入时，保留代码与失败，通过当前 blocked 通道报告；
不改权限、不取 action token。操作员可以审阅 `zf recover host-assist` 的精确清单，
通过 token-gated `task-host-assist` 为已结束的阻塞尝试执行提交或合同命令。

blocked 结果也必须填写真实事实：`target_commit` 取当前已观察 HEAD，`changed_files`
列出实际本任务变更路径，`evidence_refs` 引用具体失败输出。不可只改 verdict/summary
而留下 `<HEAD commit>`、示例证据或默认通过的自检行。无法观察的事实留空并解释；
未执行的自检删除示例行或明确标记未通过，不伪造收据。Git 提交受阻时，当前 HEAD
仍是提交前基线，不能声称未提交修改已被该 commit 包含。恢复机制只依据签名清单及
精确预授权发布；它不会从描述猜测可提交路径，也不以发布成功代替后续独立 Verify。

恢复 briefing 明确引用宿主回执时，先读取账本中对应 `runtime.action.attempt.completed`
及其 receipt_ref/output_ref，校验摘要、Run/Task/合同/命令及目标提交、source_stable和退出码；
身份或源码已变则不复用。回执仅证明命令事实，不是产品通过。允许引用已授权、同版且
可复用的宿主执行事实完成自检材料，明确 execution_origin=operator_authorized_host，
不可写成新 Worker 亲自执行，也不可把原回执的 operation/attempt 填入本次结果身份。
已有精确源码提交且不需改代码时，不为绕开权限制造空提交或改测试；仍完整核对本任务
AC和当前结果合同。独立 Verify 不被该自检回执替代；失败或缺证据时继续报告真实阻塞。
