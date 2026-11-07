# R31：恢复提案方法就绪检查与 LS 技能源同步

> 状态: done — e815efde chore: align recovery task synthesis with admission contracts

授权：继续监工 LS-v1、修复阻塞并记录。源码基线 bdbfdf59。

## 已复现问题

FILES、SHELL、VEHICLES Reader 均漏 plan_input_selection，分别被 evt-61827bd7c7f3、evt-20faf535cb31 后续本地准入、evt-42e11eab11f5 拒绝；亦缺证据写入路径及结构化 rolling-smoke 声明。监工此前审批未覆盖这些关联字段。LS 技能源 verify-rescan-replan 摘要 2e4ea0ae，dev 为 8fa5ca27；源码重载不等于技能源更新。OA/RM已消费失败，但语义生产者仍运行旧方法，内核正确拒绝不完整提案。

## 实施与验收

- 补充通用 gap-task-synth 方法 -> verify: 输入选择、证据写入权、完整验证对象及命令责任消歧覆盖真实失败，保留最终集成要求，不将判断硬编码运行时。
- 正规更新 LS tracked skill source -> verify: 两个目标技能与已合并 dev 摘要一致，未修改在途 attempt/技能快照；后续按 runtime 正常物化加载。
- 检验 metadata 与正常物化入口 -> verify: quick_validate、read_skill_metadata、skill provenance 及 flow-role activation 调用用例通过；不以文字断言测试代替行为。
- 合并/归档 -> verify: 保存实现短 hash、测试结果、部署源摘要；保留无关用户改动，未 push。

后续真实 Reader 是否采纳仍需观察；方法部署不等于所有语义失败已消失。

## 验证

2026-11-07 UTC：quick_validate 与两项 metadata 无告警；dev-verify 自动档 41 passed / 1 skipped；flow-role activation 实际调用闭包 23 passed。跳过为既有 sentinel 条件，不是本次失败。未跑全量/真实 Provider。

部署：LS 两项技能源提交 ff4106b；新摘要 gap-task-synth 62dd06c1、verify-rescan-replan 8fa5ca27。5 份既有角色技能快照摘要保持不变，其他 3 项本地技能修改保持不变。正常后续 dispatch 的 role skill materialization 从 project_root/skills 解析；已有在途 attempt 未重写。部署回执位于 LS docs/records/2026-11-07-recovery-skill-source-deployment.json。premerge sentinel 53 passed。
