# R57：允许验证执行失败如实放弃产品裁决

> 状态: active

授权：继续监工 LS-v1、修复阻塞并记录。基于 dev 1c23654d 隔离实施，本地合并，不 push。

现场治理 Verify 已确认 runner_environment 缺失，修正为 failed + abstained 后，只读预检仍以缺完整 product_acceptance_report 拒绝。结果尚未进入 canonical failure，OA/RM 无法消费正式失败；问题在确定性准入边界，不应要求 worker 杜撰产品报告或把环境缺失转成产品返工。

1. 修复执行失败收尾 -> verify: task-verify/candidate-verify 调用在 candidate owner 下 failed+abstained 且未提供报告时，预检只读通过、正式提交生成 verify.child.failed，绝不生成 passed。
2. 保留完整验收门 -> verify: passed/rejected 不豁免报告，非法执行状态仍拒绝；显式提供的报告、ref、digest 仍须验证，不用 abstained 隐藏坏引用。
3. 影响闭包与真实入口 -> verify: SemanticResultSubmitService 预检及提交、typed admission、Candidate、Product Acceptance、design199 mock E2E、sentinel。
4. 合并部署并现场复验 -> verify: 保持在途 worker 身份/工作区，原 scratch 由原 worker 重试，记录真实准入与后续恢复，不代写 worker 结果。

验证：首轮 RED 2 failed / 12 passed，随后修正测试夹具的冻结 owner 与提交 owner 一致；聚焦实际预检、正式提交、typed admission、Candidate、Product Acceptance、design199 mock E2E 共84 passed。dev-verify自动62文件闭包1086 passed（74.46秒），结果见/tmp/zf-r57-closure.log；sentinel53 passed。代码待合并部署，现场准入尚待复验，保持active。
