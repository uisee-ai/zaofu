# 固定基线对照与收口

> 状态: done

实现 commit：`99e6e71b chore: streamline ZaoFu review skill with paired evaluation`

来源：用户 2026-10-20 已批准在独立 worktree 生成并实施本批次。

范围：修改前固定旧包、输入样例和质量准则；相同 Codex model / sandbox / fixtures 独立会话重复对照，记录 raw trace 摘要与 digest。

验收：每个 forced case 每臂 3 次 -> verify: 已知缺陷检出无回退、干净样例无新增误报、零文件变更；仅在质量成立时比较 input tokens 和耗时。自然触发另报，不能用 forced 证明 routing。效果不成立则明确失败或限制，不伪造通过。

基线：`7bee177aabe46b4f94dc7cf7b74551f6b8d4c72b`；参考 `/tmp/gm/agent-skills@b8caa26` 的渐进披露结构，不导入其框架规则。

非目标：runtime bug 修复、前端框架迁移、全量技能改造、合并或 push。

## 实施与验收记录

- 旧包、最终候选、模型、CLI、镜像、样例摘要固定 -> verify: 4 个 forced case 每臂 3 次，共 24 轮；另有 4 轮自然触发 smoke。
- 逐轮语义和工具轨迹复核 -> verify: 两臂各 12/12 场景验收通过；28 轮输入文件均未变，未发生 file_change。
- 初版少做核验而漏掉测试可移植性问题 -> verify: 拒绝初版，修正规则后最终 3/3 检出；补充负例 3/3 无误报。
- 效率 -> verify: scoped 三场景 input-token 中位数下降 26.60%、39.75%、26.60%；full 增加 8.43%，但动态复核 1/3 → 3/3。有限结论是 scoped 收益成立，不宣称所有模式更省。
- 评测产物与复现方法落盘 -> verify: 28 轮 compact 证据、完整包摘要、原始 trace digest、失败尝试和基线测试失败均可回读。

评估工作已完成；未将 full 成本增加、Claude 未真测或生产泛化限制隐藏为通过。
详情：[对照报告](../../docs/records/2026-10-20-review-skill-evaluation.md)。
