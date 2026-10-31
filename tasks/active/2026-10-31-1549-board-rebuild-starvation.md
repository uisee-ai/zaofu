> 状态: active

# P2：活跃项目看板重建长时间无法发布

用户已授权监工发现问题后验证、修复并合并 dev。dl 在 2026-10-31
15:43 UTC 的看板仍显示 14:32 的旧任务状态，8 个 Impl / Verify lane
实际空闲，但旧 Foundation 卡片显示运行中，误导操作判断。

当前 HEAD `362e2fd0` 与部署 `7515359c` 的相关源码相同。真实重建
16.07 秒并抛出 `ProjectionSourceChanged`；期间 events、角色会话和日志
持续追加。独立性能剖析中任务/事件匹配约 5.96 秒，重复扫描大型正文。
诊断证据在 dl 的 `artifacts/supervision/web-projection-drift-probe-2026-10-31.json`
及 `/tmp/zf-dl-supervision-20261031/kanban-profile.txt`。

实现说明：[活跃看板的任务事件复用](../../docs/impl/board-task-event-reuse.md)。

只优化投影计算，不修改事件、任务状态、依赖、审批或一致性门。运行中的
Plan Critic 保持原进程、原模型和原输入。

- 缓存精确解码代次的任务匹配并增量处理追加 -> verify: warm/append 回归及原值不变。
- 替换、轮转、不同项目和任务集合变化 -> verify: 旧匹配不泄漏；保留引用上限及回退。
- 真实 snapshot 调用 -> verify: 新事件可见，完整生成可发布，Docker 浏览器无新错误。
- 聚焦验证和 sentinel -> verify: 记录实际测试层，不宣称全套测试或产品验收。

## 验证记录

- 新回归：修改前 3 failed；修改后新增 5 项通过，真实 `_kanban` 调用覆盖追加及替换。
- dev-verify planner 选择 99 个受影响文件并按数量升级全套；遵循 AGENTS 的影响闭包规则，
  显式分成 25/25/25/24 文件批次执行同一闭包：1,133 passed，1 项既有基线失败。
- 既有失败 `test_channel_execution_approval.py::test_controlled_start_accepts_later_owner_approval_and_preserves_prd`
  在未修改 dev `362e2fd0` 上复现同样 409 / clarification_required；未修改这个无关启动行为。
- `dev-verify run --base dev --skip-tier deterministic --skip-tier serial` 的自动 Web
  typecheck/unit 通过；deterministic 由上述四批精确闭包替代，没有运行 full pytest。
- `npm run build` 通过，47 个 chunk，最大 460385 bytes；premerge sentinel 51 passed。
- 真实数据预览：冷构建约 13 秒，热构建约 4 秒；保持 94 张卡片，稳定窗口完整生成通过。
