> 状态: done
> 实现 commit: 86da380d chore: integrate BMAD method evaluation and feedback

# 方法贡献评估与匿名审阅包

来源：用户批准上一轮 BMAD 六项增量建议并要求生成 backlog 后实施；承接 本地设计文档 187。

范围：scripts/eval-plan-execution.py；scripts/eval-method-report.py；对应测试。

## 验收

- 扩展现有隔离评估入口，支持删减版本与无方法对照，冻结输入与版本 -> verify: CLI prepare-only 集成测试核对各组差异、摘要、预算和输入不变
- 输出隐藏组别标签的审阅材料、评分模板和成本/有效性统计 -> verify: 缺证据、失败运行、重复评分和弱标准不能被报告为语义通过
- 保留真实运行和模拟运行的区别 -> verify: 报告明确语义评分、可观察范围和未验证收益

## 约束

语义方法留在 Skill/评估工件；不新建任务合同，不更改 Kernel 状态、默认 rollout 或外部服务。工程完成与真实长程收益分开报告。

## 验证记录

CLI 的四组隔离输入、prepare-only、调用上限、变体约束和摘要绑定已通过测试；审阅包拒收缺证据、失效输入及不完整评分。完整闭包 122 passed/2 skipped；补强后的两份评估工具测试 26 passed。真实模型 10 次全部有效完成，汇总仍为 human_review_required，未把执行成功当作语义通过。

完整命令、逐项评分、调用轨迹摘录与限制见[验证报告](../../docs/records/2026-10-26-0504-bmad-followup-validation.md)。

本任务的 done 指批准的工程范围完成；真实长程吞吐、自动方法选择可靠性与收益没有被本批次证明。

归档检查：`tests/test_archive_hash_integrity.py` 与 `tests/test_instruction_stack_contracts.py` 已通过；本批次无剩余 active 项。
