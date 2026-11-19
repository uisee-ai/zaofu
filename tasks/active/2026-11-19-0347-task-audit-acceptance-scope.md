> 状态: active

# Task 审计与完整 Product Acceptance 的机械边界

真实混合轮 TOOL-NORMALIZE g2 已读取成功的宿主回执，但 TaskVerify 提交被要求
完整 product_acceptance_report。冻结合同的 candidate_verify 验收责任被误用来
判定整个 operation 是最终 Candidate；实际 operation 为 task-stage/verify/task-verify。

1. 根据内核绑定的 operation 种类、阶段和输出 profile 区分 Task 审计与最终
   Candidate -> verify: Task审计预检及正式准入通过；不生成全产品通过/Goal。
2. 保留单Task条款、目标与命令证据校验，以及最终Candidate/Discovery完整报告门禁
   -> verify: 缺条款、错目标、缺探针仍拒绝；worker伪装Task字段不能降级Candidate。
3. 复用相同判定于预检与正式准入 -> verify: 直接调用方影响闭包、哨兵和mock后，
   才允许新轮真实模型验证；保留旧轮scratch、失败与人工介入记录。

验证：旧HEAD复现1失败/6通过（错误要求完整Product Acceptance报告）；修复后29项定向
通过、影响闭包1200通过。真实停止Run的同一scratch在旧代码预检failed，在修复代码
预检passed，无账本写入、无准入或恢复。源码修复的真实资格由下一轮P2继续核验。
