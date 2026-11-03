# 控制 Plan 结果预检输出体积

> 状态: done — `bb5aae06 fix: bound plan preflight diagnostics for workers`

## 背景

LS 真实恢复中，`zf result validate --scratch` 将完整 `execution_review` 输出到 Planner，分别产生约 83,319 和 78,572 tokens 的工具输出并造成上下文压缩。该输出是可重新计算的预检投影，不是已准入的审查结论；完整结果仍需可按原命令获得。

## 范围与验收

- 为 `zf result validate` 增加显式有界摘要选项 -> verify: 保留 status、错误总数与有界错误明细，标明截断和获取完整结果的方法；默认输出保持兼容。
- Plan 候选工作简报使用该选项，保留原始完整验证命令可供排查 -> verify: 测试实际生成简报和 CLI 输出；不能改变正式准入、任务证据、账本或完整报告。
- 大量错误与大体积审查正文 -> verify: 确定性测试覆盖成功、失败、错误计数及非列表错误的处理，确认摘要大小受限且不会错误声称通过。
- 更新 CLI/实现文档并执行影响闭包 -> verify: 按仓库 dev-verify 计划运行相关测试和门禁，记录精确结果。

## 验证

- `tests/test_result_submit.py`：38 passed；实际 Plan briefing 接线：1 passed。
- `dev-verify.py run --base dev --workers 2 --keep-going`：确定性 14,270 passed / 20 skipped / 7 failed；7 项与原 dev 预先复现的已知基线失败一致（Feishu 一项、mock E2E 一项、缺失导出工具四项、缺失 prompt 一项）。串行 10 passed、sentinel 53 passed、flow smoke 10 passed；总命令因既有失败退出 1。
- 未运行真实 provider/host 测试；预检默认完整输出与结果准入保持不变，`--summary` 只改变有界展示。
