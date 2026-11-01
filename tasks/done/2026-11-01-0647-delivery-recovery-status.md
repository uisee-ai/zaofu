# P2：Delivery 备用卡片忽略恢复事件

> 状态: done — `f52c9f5f` fix: reflect runtime recovery in delivery fallback cards

持续监工的真实 DL Board API 中，主任务 in_progress、Critic Operation running，
同一个 fanout 的 Delivery 备用卡片仍 cancelled，Trace 备用卡片仍 blocked。
用户已授权监工遇到问题时记录、验证、修复并合并到 dev；本项仅修正只读投影。

依据：`_delivery_runtime_statuses` 仅识别起始/终态，遗漏
`fanout.aggregate.started` 与 `run.goal.updated(status=active)`。
当前真实事件链：`evt-cd88061efa2a` 取消 → `evt-9a9de887fbb8` 恢复聚合；
`evt-3eaa00f725e2` 阻塞 → `evt-785c65f34174` 恢复 active。
历史终态和当前恢复事件都应保留，由事件顺序决定显示。

验收：
- 在当前 HEAD 复现显示问题 -> verify: API 回归测试先失败，证明恢复后仍旧状态。
- 投影消费明确恢复事实 -> verify: 当前目标转 active，别的目标仍保持原终态；后续新终态仍覆盖。
- 状态更新无 status 时保留现状 -> verify: 普通元数据更新不冒充恢复。
- 已失败聚合不显示 done -> verify: 按 terminal payload 展示 blocked/cancelled。
- 合并及部署 Web -> verify: 真实 DL Board 两张卡片正确；运行中 Critic PID/Operation 不变。

实现文档：`docs/impl/delivery-recovery-status.md`。

验证记录：
- 当前 HEAD 新回归：5 failed、6 passed，复现恢复遗漏及失败聚合误报完成。
- 修复后新回归 + TestApiState：15 passed。
- Web API / 来源缓存 / Board / 热路径 / Delivery Trace / 事件折叠：194 passed。
- 前端 `npm run typecheck`、`npm run test:unit`：通过。
- `scripts/dev-premerge-gate.sh`：51 passed；Ruff 新测试及 diff check 通过。
- dev-verify planner 因公共 Web 调用面扩到 99 文件并建议 full deterministic；
  按 AGENTS 的影响闭包规则执行上述定向层，未运行全量 pytest，不改选择器。
- Web 已部署 `f52c9f5f`，06:54 UTC Delivery 专用接口及完整 Board 均显示两个目标 active。
- Kernel PID 2101018、Critic PID 2112127/2113536、Autoresearch PID 970584 与启动时间未变；
  原 Operation/hash/attempt 及 redrive_count=2 保持不变。
- 首次部署断言读到后台重建前的旧 Board 缓存；等待新一代后验证通过，没有再次重启。
- Docker Playwright：Delivery 页面加载成功、page_errors=[]、实际页面请求得到 active。
  首次截图因容器挂载目录权限失败，使用宿主 UID/GID 重跑后成功，不安装宿主浏览器。
- 部署记录及浏览器证据：dl `artifacts/supervision/delivery-recovery-web-deployment-2026-11-01.json`、
  `delivery-recovery-browser.json`、`delivery-recovery.png`。
