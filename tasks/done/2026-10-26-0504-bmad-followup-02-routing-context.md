> 状态: done
> 实现 commit: 86da380d chore: integrate BMAD method evaluation and feedback

# 路由近似案例与按需上下文

来源：用户批准上一轮 BMAD 六项增量建议并要求生成 backlog 后实施；承接 本地设计文档 187。

范围：skills/zf-workflow-start-planner；skills/zf-plan-execution-readiness；评估 fixtures。

## 验收

- 建立近似措辞、不同授权及风险的固定路由案例与外置评分标准 -> verify: 只读演练区分讨论、仅规划、轻量实现及未决前置
- 把特定条件的规划说明移入按条件读取的 reference，保留必读验收/安全边界 -> verify: 真实角色物化保持依赖与内容完整；读取轨迹和 token 分开记录
- 不修改当前路线目录、默认 lanes 或预算 -> verify: diff 不含运行配置和 kernel 路由变更

## 约束

语义方法留在 Skill/评估工件；不新建任务合同，不更改 Kernel 状态、默认 rollout 或外部服务。工程完成与真实长程收益分开报告。

## 验证记录

固定路由案例旧/新均 6/6，区分讨论、规划、已授权小修复、仅建单与未决技术前置。真实角色物化及源/分发一致性通过；移出的条件正文已逐段对照保留。读取命令与真实 token 分开归档；未改变 src、运行路线或默认 lanes。尚未证明上下文或成本减少。

完整命令、逐项评分、调用轨迹摘录与限制见[验证报告](../../docs/records/2026-10-26-0504-bmad-followup-validation.md)。

本任务的 done 指批准的工程范围完成；真实长程吞吐、自动方法选择可靠性与收益没有被本批次证明。

归档检查：`tests/test_archive_hash_integrity.py` 与 `tests/test_instruction_stack_contracts.py` 已通过；本批次无剩余 active 项。
