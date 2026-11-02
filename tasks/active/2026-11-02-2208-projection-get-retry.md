> 状态: active

# Dashboard 短暂投影不可用的 GET 重试

持续监工的 Docker Playwright 检查复现：`delivery-features` 因来源变化返回
`503 {status: unavailable}` 与 `Retry-After: 1`，GET client 直接抛出错误，
导致事件触发的 Delivery 刷新产生未处理异常。后续同端点返回 200，Planner
始终保持运行。用户已授权监工中记录问题、修复并继续。

范围：只改 Web GET client 对服务器声明的短暂投影不可用的处理；保留已有
等待期限、并发请求共享、取消、正常错误及 cache 行为，不改变后端一致性规则。

- 明确的 unavailable/503/Retry-After 后恢复 200 -> verify: 共享一次重试链，只缓存有效数据。
- 取消、等待到期或普通 HTTP 错误 -> verify: 终止等待并保留错误，不无限重试。
- Docker 浏览器注入一次短暂 503 -> verify: Delivery 正常恢复，无 pageerror。
- build/typecheck、相邻 Web 单测与合并哨兵 -> verify: 通过；不运行无关 backend pytest。

行为文档：[投影读取重试](../../docs/impl/projection-get-retry.md)。

验证：修改前新增 GET 回归复现 503 失败；修复后相邻 5 文件单测通过。
`dev-verify.py plan/run --base dev` 的 Web 全部单测、typecheck 和 8 项文档/指令
检查通过；`npm run build`（含 bundle check）通过，合并哨兵 53 项通过。
Docker Playwright 注入一次 503 后仅 2 次请求恢复 200，Delivery active 且无
pageerror。证据已存 DL 的 `artifacts/supervision/web-projection-retry-2026-11-02.*`。
临时 8002 UI 已停止，独立模拟账本记录 simulation.done。未运行 backend 全量测试。

实现 commit 待补。
