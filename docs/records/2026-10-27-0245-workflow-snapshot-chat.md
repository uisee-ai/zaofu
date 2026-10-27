# DAG Raw Agent 在运行后允许提问

用户反馈 DAG → Raw → Agent 打开后无法输入。前端用 `reviewablePreview` 同时控制编辑和输入，后端也只接收未启动的 Preview；因此正常的运行中 Workflow 会被整个禁用。

## 修正与边界

- 分开“可以对话”与“可以编辑”：运行快照可输入、发送及引用讨论；未启动 Preview 保留 Diff / Apply。
- 只读上下文从已验证的 Proposal 快照读取，保留 request/revision/digest 绑定，全部字段 editable=false。将已有 YAML 恢复逻辑提升为共享 runtime 读取模块，Web 原导入路径保留兼容入口。
- Prompt 明确冻结快照只回答问题，changes=[]；代码独立拒绝 revise、Provider 补丁和启动前旧候选 Apply。期间发生启动或版本变化仍拒绝迟到结果。
- 保留原 token 校验、read_only headless 权限和事件入口。运行快照冻结提示不再禁用整个对话，缺少调用权限仍禁止发送。

## 验证

- 定向自动验证通过：十个 Python 测试模块共 209 passed，包含真实 API 调用、快照引用、既有编辑/Apply 和 stale-contract mock E2E；前端 typecheck 与单元测试通过；sentinel 50 passed / 1 skipped。
- 新增六项回归覆盖冻结来源、token、拒绝补丁和旧 Apply、引用损坏/缺失/过期，以及启动期间迟到结果。首轮把当前配置改成了不合法 YAML，触发 Web resolver 的既有配置校验；改为合法的 lanes=9 配置后，证明回答仍读取冻结的 lanes=1。
- Docker Playwright 完成未启动聊天 → 生成 Diff → Apply → Start → 已启动聊天的回归。启动后输入可用，收到只读回复，没有 Apply，YAML 未变。
- 8002 已重启，action token 保持不变。真实 `ty-review-42cb2551` / `workflow-3dc6ff5623188714` 在 DAG Raw 中输入并发送了 lanes 问题，真实 headless Agent 返回解释，changes=[] / draft_ref=null。桌面及手机输入可用；request revision、Proposal digest、Run 绑定及快照 ref 均未变，没有 Apply 或 Workflow action 请求。

证据：[自动检查](2026-10-27-workflow-snapshot-chat/verification.log)、[验证计划](2026-10-27-workflow-snapshot-chat/verification-plan.log)、[新增回归](2026-10-27-workflow-snapshot-chat/backend-final.log)、[构建](2026-10-27-workflow-snapshot-chat/build-final.log)、[浏览器回归](2026-10-27-workflow-snapshot-chat/browser.log)、[真实回复](2026-10-27-workflow-snapshot-chat/result.json)、[实测脚本](2026-10-27-workflow-snapshot-chat/live.mjs)、[桌面](2026-10-27-workflow-snapshot-chat/02-real-answer.png)、[手机](2026-10-27-workflow-snapshot-chat/03-mobile.png)。

这是定向验证与一次真实 Provider 对话，不是全量测试或产品交付 E2E。本轮未提交、合并或推送。
