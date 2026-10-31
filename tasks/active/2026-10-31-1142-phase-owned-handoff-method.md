> 状态: active

# P1：返工指令混淆任务与候选的验收责任

Owner 已授权监工记录、修复、验证并合并后继续。现场证据：冻结 Plan 将 Foundation 三条专项命令分配给 task_verify，完整产品资格分配给 candidate_verify，app/** 唯一 Owner 为 DL-ASSEMBLY；OA 指令 evt-a4c419c8a64a 将全量 gate 纳入前置交付，导致专项通过的 49c191f 以 evt-1ba083e47378 dev.blocked 交付，并耗尽有界返工。修订方法，不硬编码项目语义或改判旧结果。

- OA 返工先核对 AC / command / 产物 Owner -> verify: canonical skill 与两套分发一致；保留候选最终 mandatory gate、原预算与旧失败证据。
- Impl 区分任务必需检查与未来候选缺口，冲突交回语义评审 -> verify: 元数据、技能装配/briefing 和现有规划方法回归通过，不添加仅匹配措辞的测试。
- 现有运行使用受控语义恢复 -> verify: 已创建 OA 请求 evt-dbddf1928016，绑定原冻结合同、实际失败、49c191f 与阶段责任证据，未改写 canonical verdict 或扩额。

说明见 [Plan 执行一致性](../../docs/impl/plan-execution-readiness.md)。技能静态校验不能证明模型行为正确；实际 OA/Planner 决定仍需独立审阅和运行接纳。

验证：六份 frontmatter 无 warning；两套分发与 canonical 逐字节一致。dev-verify 68 passed / 1 skipped（缺少未随 worktree 提供的外部 skills/external、skills/critic）；额外技能装配/适配器闭包 28 passed；premerge 51 passed，diff check 通过。未运行真实 Provider 评测，不以静态检查证明语义效果。
