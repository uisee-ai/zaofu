> 状态: active

# 恢复状态一致性与上游契约阻塞分类

用户授权修复并恢复 datal / workflow-0ea133e63454375e。

- R1：P12A 在 06:48 UTC context critical 后退回 backlog，但 operation 保持 running、attempt 租约未结束，直到过期才 suspended。Completion Audit 必须按原 dispatch 同步中断 operation 与 attempt。step -> verify: 实际 reactor 入口回归、重复/过期事件、同请求 checkpoint 恢复。
- R2：P02/P03/P16B 已报告有证据的 upstream_contract_gap，manual_evidence_gate 却仅因合同保留 qualification 证据要求而改为人工等待。step -> verify: 未来人工证据不劫持语义重规划，显式人工失败与人工命令收据仍受门禁保护。
- R3：部署已验证代码后从受控 checkpoint 恢复；契约缺口交给已声明 gap-planning 路径。step -> verify: 原 Run 身份保留、新派发/心跳和重规划事件，未放宽预算、验收或修改 canonical JSON。
- R4（真实恢复发现）：clear 仅删除物理槽位 session，却保留耐久 Task-stage session，activate 复活 94.8% 超限会话。step -> verify: clear/重复 clear/registry reload/activate 不恢复旧 UUID，迟到 hook 被拒绝，新 UUID 可绑定；保留同一个 Task-stage 和 workspace。

复现：新增回归在原代码上 3 failed / 1 passed。P12A 断点 op-resume-eb82fa59a4e21196ae2f92b3；三个消费者都有已提交的失败反例，上游 schedule 需要业务发布验证接口，不能直接扩大消费者 allowed_paths 绕过计划。

验证：定向回归 141 passed（context retry / manual gate / Run Manager triage / TaskAttempt fault matrix / long horizon / context thresholds / resume / reader ownership / v4 mock E2E）；flow smoke 10 passed；premerge sentinel 50 passed, 1 skipped。真实账本只读重放确认三个消费者都回到 semantic-replan-request / prd-post-verify-discovery。

dev-verify plan --base 94e1ea9 已运行；共享 reactor 导入闭包推荐全量 248 文件。按 AGENTS.md 的领域闭包规则，改用上述实际调用链、共享 operation/attempt 契约与确定性流程验证，未宣称全量验证。

第一批实现 d399f8d 已部署。08:45 UTC P12A 恢复同 operation，重规划 reader 已派发；再次触发 critical 时立即产出 workflow.operation.interrupted、task.attempt.superseded 和 task.requeued，状态一致性现场验证成功。随后发现 R4，原代码新增回归 1 failed；修复后 session/registry/context/transport/pipeline 与 mock E2E 定向 59 passed。

## 增量计划操作员复核

审核对象：plan evt-7580cf4fa1f2，Package af280fcdd0f1100be8d34196dc4f3c53e09c0150587556581d702ff2fe966cb9，gap-amends/evt-3f91b5364a0d/task_map.json。本轮只审核由独立 discovery 产生的 P02 增量，不宣称重新审核整份 PRD。

- 与已批准来源比较：34→35 个任务，只将 P02 替换为 P02-PUBLISH-CONTRACT、P02-CLEAN-R1；其余仅五个消费者的依赖指向替代任务。P02 原 12 条 acceptance_criteria 逐值相同，原必需 Plan ports 保留。
- 生产者与时序：schedule owner 补齐版本化业务发布 API/验证器、清单/用途/配置复核与原子提交；独立 Task Verify、集成后才解锁 clean owner。两者写域互斥，前置五任务已有完成证据；共享发布事务未拆给多个 owner。
- 反例：clean 用途撤权而 schedule 用途仍有效，或清单成员改变但主索引不变时不得出现正式 artifact/outbox 或下载；新增 RECHECK/ATOMIC AC 和 runtime/browser 命令覆盖。保留 copy、旧租约、幂等、回滚兼容性回归。
- TDD：具名生产入口反例 RED→实现→同断言 GREEN；独立 Verify 复验 exact commit。既有 candidate/manual 义务保持未来阶段待验，不能算本片通过。
- 非阻断机械警告：增量 digest 的两任务没有 assembly/root owner；完整图的 F00/装配任务仍保留，该补丁不新增根文件写入。
- 边界：P03/P16B 的业务实现和最终 qualification 未通过，待上游接口交付后继续各自受控恢复/增量计划；不将本次 approve 当作全部缺陷已关闭。

按用户已授予的代审批权限批准此范围，未代替未来 Verify/Judge 或人工资格证据。

## 第二轮恢复暴露的边界

- R5：P12A p4 的合法 task workspace 被 Hook 拒绝，原因是 request 保留 p2 placement。step -> verify: 用当前 attempt 的 kernel 事实证明 p4，真实 write guard 放行；错误 attempt/lease/dispatch/run/op/placement 与非 kernel 事实拒绝。
- R6：P02 的 34→35 任务 amend 原样复制 34 项分区，导致 $.tasks[34].task_id 无读取归属。step -> verify: 按 task_id 重绑，不改变业务任务和 AC；实际部署解析器对重建的 35 个任务全覆盖通过。

R5/R6 修复前新增回归 2 failed；修复后定向调用链 122 passed，新增负例与幂等测试 4 passed。P12A g3 受控 retry 预览允许，尚未直接修改任何 Task 状态。

第二轮最终定向 124 passed，sentinel 50 passed / 1 skipped；dev-verify plan --base 59d574f 再次因共享 gap/hook 调用闭包推荐 broad deterministic，按 AGENTS.md 保持领域定向层级，包含 gap bridge、Goal gap、Hook 接收/写入守卫、受控 retry 与 flow smoke，未宣称全量通过。

## Package 级读取闭包

R7：继续派发发现 task_map 之外的 8 个继承端口还缺少 P02 新任务分区。step -> verify: 在 package 生产入口给显式 parent/supersedes 补齐只读分区，生成新 sidecar 并保留验收正文；缺失来源在接纳前拒绝。新增入口回归修复前 2 failed，修复后 package/admission/ports/acceptance/review/read-scope/gap runtime/flow smoke 定向 81 passed。实际 Package 13 端口 × 35 任务，共 455 次完整分区解析通过。

操作记录：首次按原始 base 重发 gap 时，ProductAcceptance exact prior lineage 正确拒绝（evt-ec5fb691795f），原因是上一次增量包已接纳。改为以已接纳版本为前版生成独立修复工件；期间历史 stage cap escalation 使 Run blocked，通过 zf goal set --status active（evt-2b3b64e36cda）恢复，无预算重置。evt-2a523a3f4ee9 / Package 6a4045063a40e651a4c084ef9c2db4e1dc06179c84f5aead0802e18281d30681 已接纳/审批；它修正 task_map，R7 继续修正整个 package，不能将中间版本当最终恢复成功。

P12A 已通过 zf recover task-impl 恢复 g3/p5（evt-b20a2130c69d），实际 hook 匹配正确 Task Workspace；09:23 UTC 已发生测试文件写入与 runtime/browser 执行，先前工作目录拒绝消失。部署回归曾因跨 worktree conftest/import-root 断言拒绝测试收集；把本轮新测试部署到同一源码目录后 4 passed，加已有 main 定向 47 passed。
