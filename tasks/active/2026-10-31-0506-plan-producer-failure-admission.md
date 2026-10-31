> 状态: active

# 允许 Planner 如实提交产物未完成的失败结果

Owner 授权监工记录问题、修复验证并继续。dl 首轮 Planner 明确报告必需产物未完成，
`result submit` 却因缺 task_map/product_acceptance_spec 拒收；随后被误分类为
`provider_turn_closed_without_child_result`，触发恢复。原失败与草稿保留。

源 operation：`wop-prd-plan-planner-trig-evt-7cf6aa0f-839d15ea6a02`；
真实 result 保存在 dl `.zf-dl/tmp/result-submit/<operation>/<dispatch>/result.json`。

- 如实声明执行失败 -> verify: validate 无事件写入；submit 接纳 child.failed、保留失败正文，无 completed。
- 维持成功 Plan 门禁 -> verify: 缺 task_map 的普通结果/语义否决继续拒收，成功候选既有测试保持通过。
- 维持安全与结构 -> verify: 错误 credential、空 summary、非数组 findings 拒收。
- 区分 artifact-production 执行失败与审查对象缺陷 -> verify: structured 与 legacy 两种合同测试。

实现说明：[Plan 失败准入](../../docs/impl/plan-producer-failure-admission.md)。

复现：原 HEAD 的失败预检与结构化 artifact-production 归一化两项回归失败。
验证：10 文件 focused 140 passed；dev-verify 自动影响闭包 29 文件 416 passed；
premerge sentinel 51 passed；flow smoke 10 passed；ruff、diff check 通过。
自动收据：`/tmp/zf-plan-failure-verification-20261031.json`。
