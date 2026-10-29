> 状态: active

# 恢复状态一致性与上游契约阻塞分类

用户授权修复并恢复 datal / workflow-0ea133e63454375e。

- R1：P12A 在 06:48 UTC context critical 后退回 backlog，但 operation 保持 running、attempt 租约未结束，直到过期才 suspended。Completion Audit 必须按原 dispatch 同步中断 operation 与 attempt。step -> verify: 实际 reactor 入口回归、重复/过期事件、同请求 checkpoint 恢复。
- R2：P02/P03/P16B 已报告有证据的 upstream_contract_gap，manual_evidence_gate 却仅因合同保留 qualification 证据要求而改为人工等待。step -> verify: 未来人工证据不劫持语义重规划，显式人工失败与人工命令收据仍受门禁保护。
- R3：部署已验证代码后从受控 checkpoint 恢复；契约缺口交给已声明 gap-planning 路径。step -> verify: 原 Run 身份保留、新派发/心跳和重规划事件，未放宽预算、验收或修改 canonical JSON。

复现：新增回归在原代码上 3 failed / 1 passed。P12A 断点 op-resume-eb82fa59a4e21196ae2f92b3；三个消费者都有已提交的失败反例，上游 schedule 需要业务发布验证接口，不能直接扩大消费者 allowed_paths 绕过计划。

验证：定向回归 141 passed（context retry / manual gate / Run Manager triage / TaskAttempt fault matrix / long horizon / context thresholds / resume / reader ownership / v4 mock E2E）；flow smoke 10 passed；premerge sentinel 50 passed, 1 skipped。真实账本只读重放确认三个消费者都回到 semantic-replan-request / prd-post-verify-discovery。

dev-verify plan --base 94e1ea9 已运行；共享 reactor 导入闭包推荐全量 248 文件。按 AGENTS.md 的领域闭包规则，改用上述实际调用链、共享 operation/attempt 契约与确定性流程验证，未宣称全量验证。
