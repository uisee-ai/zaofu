# 182-B17 · 受控返工与完整规划交接验收

> 状态: active
> 来源: 最新54任务候选的正式Critic缺陷 B08/B09/B11 收尾；用户于2026-10-20明确要求在新worktree生成backlog并执行修复。

## 范围

将通用方法接入真实Planner/Critic；依据本次用户授权通过stage-replan-new-generation开展有界返工，修复B12–B16。校验同版工件、规格可读、依赖/阶段闭包和最终Preview；保留产品人工审批门。

通用语义方法归 skills/提示/评审工件；Kernel仅做机械校验，不硬编码洛书业务规则。旧运行态只读，候选修订由正式Planner产出。

## 验收

- 复现并修订 → verify: 正式Planner修订且独立Critic无高风险遗漏；新Plan待人工审批、范围保留、未决Q不伪造关闭；Docker只读Preview验证；聚焦测试和本地提交后逐项归档。
- 回归与证据 → verify: 实际角色消费当前方法；保留来源与旧新映射，不能把规划用例当作产品已执行TDD。
- 规格交接 → verify: 引用式端口经正式搬移和完整Package读取，删除测试作者目录仍可读；摘要失配与越界继续拒绝；实际工件满足Package/Preview大小约束。

## 授权边界

本次授权覆盖这五项缺陷的规划返工；不自动批准产品Impl、数据库操作、部署或push。
首轮有界 generation 因供应商容量错误耗尽，未取得正式 verdict。
后续已验证引用端口修复，按[有界续跑记录](../../docs/records/2026-10-20-0820-taskmap-bounded-continuation.md)再开展一轮；保留原升级和计数，不无限续跑。
该轮正式 Critic 已关闭 PC-BC-F1/F2，仅剩新负例前态矛盾；最后单项修订的
原授权依据与边界见[夹具收尾记录](../../docs/records/2026-10-20-0929-taskmap-fixture-closeout.md)。
