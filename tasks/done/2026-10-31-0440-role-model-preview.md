> 状态: done；ee10c352 fix: preserve role behavior in model reviews and revision recovery

# 按角色审查模型配置

监工恢复 `dl` 后发现，已生成的 V5 候选使用 Sol/medium，而 Preview 只暴露公共默认 model，
无法表达 Owner 已要求的 planner/orchestrator Astra/xhigh、Impl Terra/xhigh、Verify/Judge Sol/high。
用户已授权遇到问题记录并修复后继续。

1. 暴露实际编译角色的 model/effort -> verify: 生成角色和已有角色可在同一审核补丁中分别修改。
2. 保留角色行为 -> verify: 编译前后除指定 model/effort 外的角色字段、技能、权限、拓扑完全相同。
3. 保持审批合同 -> verify: 不存在的角色、非模型字段不能修改；候选需精确配置审批，不自动开始 Run。

不增加模型选型策略；型号由 Owner/agent 决定，runtime 只检查合法字段和绑定。

监工更新 Task 的旧“仅准备”授权后，发现新 Requirement revision 继承了上一 revision 的
恢复上下文；摘要校验正确拒绝旧结果，但新需求因此无法直接进入新合成。已记录为同批恢复
边界修复：真实需求变化清空旧重试/复用引用，保持同 revision 环境修复可以复用。
verify: 恢复成功后再修订需求，新版本实际调用选路模型且生成 proposed，不复用旧语义。

验证：17 个直接调用/受控审批/PRD mock E2E 文件 177 passed；premerge gate 51 passed；
三流与流水线噪声 mock E2E 10 passed，ruff 与 diff check 通过。
dev-verify 自动扩大到 36 文件时发现 Generic Workflow artifact-read 的绝对路径 sidecar
基线失败（3 个场景），在未包含本批改动的 dev `b312e629` 单独复现；与 PRD/模型修改无关。
本批不改动该研究流程，保留 `/tmp/zf-generic-baseline-check-20261031.log`，不宣称全量通过。
