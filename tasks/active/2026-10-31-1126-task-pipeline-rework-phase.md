> 状态: active

# P2：Task Pipeline 返工仍显示旧 Verify 阶段

Owner 已授权监工发现问题后修复、验证并合并 dev。2026-10-31 Foundation 第 3 代 Impl 已运行，但 Dashboard /api/tasks 投影仍使用第 2 代 dev.build.done，显示 Verify。Docker Playwright 断言失败；当前 HEAD 7 个聚焦回归用例全部失败，日志 /tmp/zf-rework-phase-baseline-20261031.log。

- 按当前派发和实现代次限定只读事件投影 -> verify: 新 Impl 阶段和看板列正确，旧代迟到通过/失败不污染当前徽标。
- 保留同代 Impl 到 Verify 的证据衔接 -> verify: 新 Verify 显示 pending，完成后保留真实 passed。
- 保护历史和业务状态 -> verify: 不改 EventLog/TaskStore，legacy 投影和共享调用方回归通过。
- 接通实际 Web caller -> verify: 详情、snapshot API 和 Docker Playwright 通过。

实现说明：[Task Pipeline 当前代次投影](../../docs/impl/task-pipeline-current-generation-projection.md)。

验证：修复前 7 failed；修复后 405 个聚焦影响闭包用例通过（含 10 个新增用例、Web API、Feishu、briefing、RM、共享 Store/EventWriter 与确定性 mock E2E）。dev-verify 自动 serial 10 passed、premerge sentinel 51 passed；Ruff 和 diff check 通过。选择器将 112 个匹配文件扩大到全量 deterministic，本次按仓库规则跳过该层并用上述明确调用闭包替代，未运行全量 pytest。真实 dl 账本只读复核显示 impl_running / impl / in_progress，当前派发身份保持不变。临时只读 Web（8002）Docker Playwright 通过：93 个任务、Foundation in_progress / impl / dev-lane-0、指定模型存在、无页面错误。证据 /tmp/zf-dl-rework-browser-20261031/rework-preview-result.json；合并后再次复核真实端口。
