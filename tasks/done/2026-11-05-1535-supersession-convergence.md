> 状态: done

# 历史替代状态对账

P16B 已在历史 amendment 声明替代，但 canonical 旧任务与下游边没有物化；后续 Plan 只携带最近替代，不能让最新节点入库后留下旧阻塞。用户已授权修复和断点恢复。

- 正式 writer adoption -> verify: 先创建后继，再从当前图的有限历史链核对同 run 的 kernel task_map.amended、文件摘要与显式替代边，最后迁移保留节点依赖并取消旧节点。
- 不推断语义替代 -> verify: 缺事件、摘要不符、跨 run、不在历史链、后继链不闭合/未物化、旧任务仍活跃均不执行历史取消；done 不重开。
- 审计与终结 -> verify: task.superseded 保留历史事件 IDs，correlation 绑定 workflow_run_id，使 pipeline 能验证新代次的替代证据。

实际 Orchestrator 回归先 1 failed / 5 passed；新增后继未解析负例、历史已取消节点见证及重复应用无新增事件后，与 gap/Task Pipeline integration/mock E2E/fallback 测试合计 95 passed。变更仅为机械状态对账，不解释业务 AC 或解析 Planner 的自由文字。

最终验证：上述影响闭包 95 passed；新增幂等断言后定向 8 passed；dev-verify sentinel 50 passed / 1 skipped、flow mock 10 passed。receipt: /tmp/zf-task-recovery-20261105/supersession-final-verification.json。按仓库规则执行影响闭包，未运行全量 pytest。

实现提交：3381b333 fix: reconcile evidenced historical task replacements。产品续接另见 active/2026-11-05-1430-product-recovery.md。
