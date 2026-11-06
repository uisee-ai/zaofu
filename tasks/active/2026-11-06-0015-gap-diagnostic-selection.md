> 状态: active

# 已批准增量与历史故障选择分离

监工只读检查本轮38项Plan草稿，完整矩阵/schema通过，但真实writer选择仍为17项（旧3项的后继+14下游），漏掉align公共前置和资源读取守卫。根因是candidate replan把失败及下游ID作为诊断传播，writer将其当当前派发选择，覆盖了完整TaskMap的amend.gap_task_ids。本项在用户授权的断点恢复范围内修复；不改Planner语义、不绕过Critic/批准。

- 准确识别旧代次诊断 -> verify: 只有requested集合等于failed+downstream、failed集合精确匹配amend.superseded_task_ids且amend明确gap_tasks_only，才使用批准的gap_task_ids。
- 新前置必须纳入 -> verify: 真实load_writer_task_map入口同时选前置/续作，排除未修订下游及done；缺少声明任务fail closed。
- 保留明确选择 -> verify: 新任务子集、无诊断、错误failed集合、无关选择不被静默覆盖。
- 本轮实物只读预演 -> verify: 选择恰为五项，依赖图两根分别进入采样/融合与隐私；canonical状态不变化。

行为RED：2 failed / 4 passed，旧逻辑遗漏前置且未校验缺失gap ID。GREEN后置断言曾误读不存在的resume_scope属性，已删除该测试错误，不修改产品数据结构迎合测试。

验证：admission/范围/历史替代闭包70 passed，writer runtime/Task Pipeline入口与代次/materialization/mock闭包189 passed；sentinel 50 passed / 1 skipped，flow mock 10 passed，Ruff通过。dev-verify的722文件全量层按AGENTS不执行，改用明确影响闭包。使用真实已提交38项计划及原trigger在修复代码上调用load_writer_task_map，只读选出五项与两条前置链；未修改任务状态。
