# 无外部监工的恢复与交付闭环

本轮基于 Kimi e12cad1a 与 Codex 44848822 源码调研，在现有 RecoveryCase、WRC、
Run Manager、OA 和受控动作上补交接；不新增第二状态机。对应任务为
`tasks/active/2026-11-22-2353-autonomous-closeout.md`，前轮证据见
[8×8 恢复记录](eight-by-eight-recovery-closure.md)。

## 验收目标

外部监工 session 退出后，在已有权限和任务边界内，ZaoFu 应自行发现阻塞、诊断、
派发修复、独立验证并继续交付；完整性、正确性与证据标准不降低。运行活跃、零退出码、
全部 Task done 均不等于最终交付通过。产品需要新决定、权限不足或外部服务确实不可用，
须给出具体请求和可恢复断点，不能假装工作仍在推进。

旧窗口的无人资格失败永久保留。操作者修复及受控部署后的原断点恢复是修复回归；
独立新窗口才判定零外部干预的完整交付。观察方不能代发审批、唤醒、重试或补写结果。

## mixed Candidate 的局部修复

聚合输入保留每个 admitted Reader 的非通过 requirement、rework item 和执行未知事实，
以结果 ref/digest、operation 与 JSON pointer 生成 finding_id。OA 为所有 finding 提交
`candidate_repair.finding_dispositions`，每项具有 classification、owner、next_action、
rationale。分类为 product_defect、contract_question 或 execution_unknown；内核仅校验
来源、覆盖、身份和允许的动作，不按错误文本推断业务原因。

仅 completed/rejected 且映射到 Task 的已失败条目可授权产品修复。gap_tasks 必须精确
复制原合同，只有新 task_id、冻结候选 base_commit、supersedes_task_ids 可变。缺项、
重复 finding、伪引用、未知执行当产品缺陷、扩大范围或改变验收均拒绝。

所有 finding 与分流决定保存在现有受控 amendment 的 rework_summary，不写旁路状态。
非产品义务直接进入 RM 的 durable diagnosis；局部修复与诊断可分别推进。下一 Candidate
合同携带 candidate_recovery_obligations；passed 必须逐项给 recovery_obligation_results，
包含 resolved、理由与当前冻结输入索引中真实 ref/sha256 对象；验证访问权限及实际内容。
原合同解释无需强制新审批；声称 owner 变更则校验真正的批准请求/trigger/当前代际链，
不能要求批准事件携带生产路径并不保证存在的 generation 字段。实际合同与原需求冲突
仍走既有 scoped Plan 修正。字段齐全只是机械准入，解决充分性由独立 Reader 判断，
不能借解释自行扩大需求、删掉正式义务或豁免。

```text
聚合失败 -> OA 按 finding 分流 -> 原合同局部修复 -> 独立 Task Verify
                   |                                  |
                   +-> RM 诊断未决义务 -----------------+
                                                      v
                                     Candidate 全部原始义务对账 -> Thin Judge -> 交付
```

## 派发失败的下一消费者

同一 repair request 的 worktree_add_failed / repair_contract_freeze_failed 不再每个
tick 重试。内核事件绑定 source_request_id；事件重放仍只产生一个诊断 checkpoint，
实际 RM pending-action 进入已有 resident/research 调度。新授权请求可产生新尝试；
不能删除/冒领已有工作区，也不能以持续追加同一失败伪装为进展。

## 执行事实与消费身份分开

新 Candidate 身份可消费旧执行事实，条件为 Run、Task、Task Map 代际、源码 commit
相同，完整合同除内核冻结身份/定位字段外逐项相同。child authorities、Plan 绑定、
验收、命令、未知新增字段仍参与比较；此证明不推断跨补丁语义等价。

容器环境、保护范围、网络、镜像和命令必须匹配；实际依赖字节摘要也须匹配。
明确增加执行/采证容量或新增独立补采授权，不使已存在且符合当前限额的真实事实失效。原 effect、attempt、receipt 身份保留，新的消费者得到新的
冻结绑定；历史调用不补写，新消费者仍独立评判 AC。源码/合同/环境变化不能借身份变更
取得复用；跨源码补丁的适用性仍由现有 Agent/影响分析路径决定。

## 未知容器探针的独立补采

`runtime.host_verification.recollect_commands` 默认空，必须由 owner 在 zf.yaml 明确
声明为可安全重复观测的精确命令，且是 commands 的子集；不根据命令名称推测安全。
原镜像摘要、网络、保护文件、超时及既有执行上限继续适用。

仅原生 verify start、当前来源/合同、相同既有授权、无 completed 且 Docker 只读查询
确认原命名容器不存在时，允许一次独立补采。仍存活、查询失败、来源改变、暂停、终态、
预算不足均不能执行。不强杀容器；不转到宿主 shell 执行业务代码。

新 manifest 绑定 recollection_of_start_id 和 original_manifest_digest，拥有自己的
effect、输出和 receipt 路径；原 unknown 永不改写为成功。一次补采的完成事实可解除
对应待诊断义务，已知非零仍交产品验收处理，不构成通过；补采再次未知不能无限重试。
新 effect 在开始前持久冻结完整 manifest_ref；若原 operation 已退休，当前目标同原
命令的独立事实可由 Reader 绑定说明适用性。旧未知结果不会被新结果覆盖。
已有人工决策保持由原受控决定入口处理，配置不会伪造 owner acknowledgement。

## 既有能力与方法

首次完整 Candidate 失败交 OA、无输入变化不泛化重跑、OA 未发送检查点断点续接、
WRC/索引精确补读，以及 candidate.verifyWidth 独立于 8 Impl + 8 Task Verify 的能力
沿用现有实现与回归。配置宽度不降低最终全局义务覆盖。

Critic/Verify 方法新增原需求边界核对：偏好的极端输入处理不自动变成阻塞 AC；有效
rejected/blocked 是语义结果，不能当技术失败反复调用直到出现 passed。空输出/格式错误
先处理结果交付。所有角色明确剩余工作、证据、下一消费者和合法等待条件。

## 本轮验证

聚焦回归、影响闭包、premerge sentinel 与确定性 mock 验证记录在任务及 reports。
真实恢复的版本、受控操作、实际派发、业务验收、无人资格分别记录；未完成部分不宣称通过。

本轮增量影响闭包 12142 passed、18 skipped；哨兵 53 passed、流程 mock 10 passed；
Docker 显式 tier 2 passed。原断点真实恢复和最终产品交付继续由 active 任务跟踪，
以上结果只证明实现与相关调用路径，尚不证明长任务无人资格。

## Planner 会话连续性

8×8 配置由 `tests/continuity_prd_fixture.py` 生成。具名 Planner 显式设置为
`reader_context_scope: dispatch` 曾导致同一 Task/Run 的 replan 清空 Codex UUID；
具名配置优先于 Flow 的默认值，因此仅修改默认值不能解决现场问题。
现将 Planner 设置为 `workflow`，独立 Critic、Verify、Judge 保持 `dispatch`。
运行项目必须同步修改 zf.yaml，并在控制面重新加载后核对 runtime-load 证据。

同一 Task、完整且相等的 Run 绑定允许复用原生会话。缺失 Run 或完全没有旧作用域却
持有缓存 UUID，不证明会话归属；沿用现有结算、活跃 turn 和进程退出检查后再隔离。
不带 Task ID 的 Plan Synth 仍按 Run 隔离；Task 与 Run 同时缺失时 defer，不轮换或冒领会话。
Provider 配置摘要、rollout 存在性与归属检查继续适用，不能使用 `resume --last` 或
手工把已替换的旧 UUID 写回当前活跃会话。

会话历史只提供上下文，不继承审批、验收结论或读取证明。当前交付仍为
`full-with-shadow-delta`；`artifact read --required --resume` 只续接当前
attempt/operation/bundle 及会话绑定下的已观测读取。跨 attempt 的读取复用尚不在本轮
实现范围内，当前 Source Manifest 与版本门禁保持不变。

## 终态历史与当前调度投影

调整最终Candidate并发宽度会改变完整Task Pipeline策略摘要。派发路径已排除done/
cancelled任务，投影视图也必须只对非终态任务执行当前策略的准入匹配；历史行、原
generation与结果继续从原账本展示，不能用新配置重写历史身份。顶层策略描述当前
配置；活动策略分组仅包含仍需调度的任务。混合投影保留已完成任务的依赖事实，
cancelled不自动满足依赖。活动任务摘要变化仍拒绝，不因“只读展示”豁免实际派发门。

本次监工发现旧投影将终态context重新送入严格策略匹配，导致WRC维护循环连续报错。
真实只读入口可复现，修复后用真实WRC入口重放和混合代际用例验证；不修改旧Task、
合同、generation或失败事件。重复失败的诊断调度与维护continuation处置独立跟踪，
不以这项投影修复声称所有恢复路径均已自动闭环。

## 审核纠错与诊断交接 P0

新增 Critic 请求冻结 `current-evidence-v1`。现有阻塞项的 basis 绑定原义务与当前输入；
validate、submit 和 canonical admission 都核对摘要、读取范围及精确 JSON 值/原文引用。
历史请求保留原合同，不能补造旧读取证明。引用正确不代表语义正确，现有技能负责
义务适用性与证据推论；真实缺陷继续拒绝。

`stage-replan-new-generation` 的 `review_only: true` 是原候选复审选项：需既有 cap
escalation、approval_ref、reason 和 current counter_evidence。它复用已接纳的原生产者
工件和完整合同，不触发 Planner、不改变 Task Map、不清零 replan 计数。原拒绝、当前
反证通过不可变 sidecar 提供给新 Critic operation；同一 Run/stage/候选仅一次，重放
同请求幂等。候选改变、更新 fanout、活跃 operation、终态或跨范围请求拒绝。现有
fanout aggregate 账本及 Orchestrator reconciliation 负责重启续接；新结果仍走原准入。

Run Manager 完整收集独立 attention 后按精确 source event 身份去重与仲裁；相同 Task
不等于相同故障。不可执行候选不能遮蔽独立诊断；同一 resident 忙时保留有绑定的等待。
task pipeline 维护异常复用已有 60 秒/step 告警去重，首个事实和后续再次发生均可观察。

AR loop.failed 产生独立后继 Case，保留 parent Case、失败阶段、partial diagnosis、
checkpoint、archive/cleanup 状态和下一 RM owner。consumed 仅证明收到失败，不证明
修复原故障；负责人不可用时明确升级，不自动递归开启 AR。清理失败与语义报告缺失
分别记录。owned runtime 定位仅解析生命周期所需 project.state_dir，再检查 containment
及 session owner；实际 stop 仍在隔离环境的新 CLI 中完整校验配置。此修复支持旧 resident
与新配置 schema 漂移；配置真的无效时保留并移交，不猜测 tmux 或父项目 state。
child observer 配置错误明确返回 observation unavailable，不能当作正常空观察。

现场 briefing 文件存在、派发路径正确；公开工具调用丢掉 repair-worktrees 层后读取了
父项目文件。派发方法现明确复用原始 locator、一次精确重读、失败回报；incident 证据
与本 operation 的角色合同分开。这是 Agent 方法，不能宣称实现了宿主文件系统隔离。

本批新回归覆盖 controlled action → Critic dispatch → 独立拒绝、重放/重启、当前事实
反例、独立诊断仲裁和 AR 后继。测试命令与结果记录于本批任务；此前 12142 等数字
属于前轮验证。真实 8×8 的旧 closed_failed 和未交付状态不由本批单元测试改写。
