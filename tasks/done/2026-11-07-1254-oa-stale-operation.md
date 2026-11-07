# R33：OA 过期结果的 operation 收口与重放恢复

> 状态: active

授权：继续监工、修复阻塞。真实 evt-59d725c28a35 将 OA 旧计划结果判为 superseded，但 operation 仍 running，导致 worker assistance-1fdb10f4c42c783584b92464 已由 RM 路由却无法派发。

- 复现入口漏洞 -> verify: 真实 result submit 拒绝旧包同时必须终止对应旧 operation，保留 Task canonical 状态。
- 补齐历史恢复 -> verify: 通过不可变 request 与当前作用域包证明已过期，结合历史 invalid 证据幂等收口；不终止当前包、其他 Run、未提交的运行中任务或身份不符事件。
- 防止排队旧计划继续消耗 -> verify: 尚未派发且包已失效的 OA 请求可收口，当前任务保留绑定有效。
- 实际接线与验证 -> verify: runtime dispatch/recovery caller 测试、相关闭包、sentinel、mock E2E；上线后观察 OA 求助可派发。
- 记录 -> verify: 问题、OA/RM 未恢复原因、实现 commit、实际恢复证据写入 LS 监工记录；不 push。

验证：结果入口先复现 RED（预期 superseded 实际 running），修复后新增/入口19通过；26文件影响闭包384通过；sentinel53、三流与噪声 mock E2E10通过。dev-verify 因通用 admission 依赖映射扩展至243测试文件而建议全量；按仓库“不得仅按数量触发全量”规则，显式 skip deterministic/serial 后以26文件闭包替代，全量与真实 Provider E2E未运行。实际状态只读预览精确选中1个旧 running+2个过期 requested，不修改当前包或任何业务 Task。部署及真实派发待验证。
