# 182-B17 · 受控返工与完整规划交接验收

> 状态: active
> 来源: 最新54任务候选的正式Critic缺陷 B08/B09/B11 收尾；用户于2026-10-20明确要求在新worktree生成backlog并执行修复。

## 范围

将通用方法接入真实Planner/Critic；依据本次用户授权通过stage-replan-new-generation开启一次有界代际，修复B12–B16。校验同版工件、规格可读、依赖/阶段闭包和最终Preview；保留产品人工审批门。

通用语义方法归 skills/提示/评审工件；Kernel仅做机械校验，不硬编码洛书业务规则。旧运行态只读，候选修订由正式Planner产出。

## 验收

- 复现并修订 → verify: 正式Planner修订且独立Critic无高风险遗漏；新Plan待人工审批、范围保留、未决Q不伪造关闭；Docker只读Preview验证；聚焦测试和本地提交后逐项归档。
- 回归与证据 → verify: 实际角色消费当前方法；保留来源与旧新映射，不能把规划用例当作产品已执行TDD。

## 授权边界

本次授权覆盖这五项缺陷的规划返工及一次受控新generation；不自动批准产品Impl、数据库操作、部署或push。
