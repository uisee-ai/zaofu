> 状态: done
> 实现 commit: 86da380d chore: integrate BMAD method evaluation and feedback

# 返工保留清单

来源：用户批准上一轮 BMAD 六项增量建议并要求生成 backlog 后实施；承接 本地设计文档 187。

范围：yoke/tdd-evidence；yoke/verify-review；现有 rework feedback 测试。

## 验收

- 返工同时记录失败项、必须保留的行为、当前证据和最小复验范围 -> verify: 失败与已通过 AC 可经现有 feedback sidecar 读取，接收角色得到方法 reference
- 增加恢复/返工反例，防止通过删除既有要求修复当前错误 -> verify: 固定案例评分包含正确保持、失效证据和禁止改写冻结需求

## 约束

语义方法留在 Skill/评估工件；不新建任务合同，不更改 Kernel 状态、默认 rollout 或外部服务。工程完成与真实长程收益分开报告。

## 验证记录

test_impl_verify_contract 的反馈写入、读取及 briefing 行为覆盖 passed/failed AC、summary 与 required_actions；角色物化包含保留方法。固定案例四组均要求保留幂等、冲突拒绝和权限零副作用，拒绝用 v2 证据认证 v3。本轮未实施案例修复，不声称已验证真实返工后的保持率。

完整命令、逐项评分、调用轨迹摘录与限制见[验证报告](../../docs/records/2026-10-26-0504-bmad-followup-validation.md)。

本任务的 done 指批准的工程范围完成；真实长程吞吐、自动方法选择可靠性与收益没有被本批次证明。

归档检查：`tests/test_archive_hash_integrity.py` 与 `tests/test_instruction_stack_contracts.py` 已通过；本批次无剩余 active 项。
