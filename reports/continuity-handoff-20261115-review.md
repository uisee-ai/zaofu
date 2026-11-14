# 连续执行第二批：不可变交接与目标回执

> 状态：本增量已实现并完成确定性与浏览器验证；整份 Spec 仍未完成。

承接 [首批对账](continuity-handoff-20261114-review.md)，分支仍为
`wip/codex-20261114-continuity-handoff`，增量起点 `b4ba134d`。
对应 F04，以及 F03/F05/F16 的交接读取边界；没有启动已暂停的 Datal。

交接实现 commit：`88a7563d feat: persist task handoffs and require authenticated receipt`。
随后完成的环境增量见 [准备身份与安装互斥](../docs/impl/continuity-environment-preparation.md)，
含新增配置、bootstrap 回执、818 项定向回归与 281 项调用方回归；两组用例有重叠，不相加为独立测试总数。

## 本次交付

- Task Pipeline 派发后继阶段前，保存不可变 `task-handoff.v1`：目标、合同版本、前序结果引用、工作记录、候选工作区 HEAD/dirty、环境资格、剩余验收与未知交付效果。重放复用原正文，不因工作区后续变化重写。
- 同一 operation 的不同 attempt 共用正文，但分别读取与确认；第一阶段、没有前序的入口以及旧不可变 request 保持兼容。前序未结算、正文损坏时返回 `waiting_for_handoff`。
- 新交接加入实际 source manifest 和冻结 request 的必读项。`zf artifact handoff-accept --operation … --digest …` 校验当前角色凭据、Task/Run/attempt/dispatch/lease、工作区绑定、正文版本及完整读取证据。写回执前再次核对身份。
- 保存、派发、接收、业务完成分开表达。回执持久化到 EventLog；缺页、旧身份、错误 digest、损坏引用不能确认。回执重放幂等，不更新 Task 完成状态、不签发执行权限。
- 结果准入从 canonical operation 查必读要求，新交接未确认时要求补证。仅提交简化 operation 身份不能绕过；读取失败仍可走失败上报，避免补证死循环。
- `artifact recover` 暴露当前待接收命令；Dashboard Task Detail 展示待接收、已接收、证据不完整。回执不是业务完成。
- 大证据保留引用并以流式摘要校验，不把附件正文复制到交接上下文；5 MB 引用、分页未读完整和引用删除均有反例验证。
- Claude 受限角色仅增加精确的交接确认 CLI allowlist；同步生成中英文 CLI 索引。

## 验证与限制

1. 准入 RED：`test_pinned_handoff_requires_receipt_before_result_admission` 原先 admitted=True，新增 gate 后通过。前序 CLI/派发 RED 分别证明入口和 request 交接字段缺失。
2. 最终后端影响闭包 **601 passed**：Task Pipeline、交接/恢复/工作记录、读取账本与必读策略、runner policy、call-result runtime/admission、EventLog/contracts/registry。命令及 JUnit 见本地 `reports/continuity-handoff-20261115/`。
3. 前端 build（含 typecheck、bundle gate）、frontend unit 通过。Docker Playwright **3/3**：三种回执状态、刷新、业务状态独立、无写请求和 JS 错误。这是 UI API fixture 验收，不是真实 Provider 接力。
4. sentinel **53/53**，flow smoke **10/10**。新增 CLI 后 sentinel 曾报告生成索引过期；重新生成后通过，没有跳过检查。
5. `dev-verify plan/run --base dev` 已执行；planner 因累计改动选出宽全仓层，本批用上述明确影响闭包替代 deterministic/serial，自动 web/contract/mock_e2e 层通过。未运行全仓 pytest、真实 Provider 或 Datal 产品验收。
6. 浏览器：既有 `mcp/playwright:latest`，镜像 `sha256:8342e03ef3571d09afffcd00558b8067e66489a54e7f3f0d96a71a327fd45fe2`；Chromium `149.0.7827.22`，Playwright `1.59.1`。端口 8003，host network，保留 WebM/trace/截图；无宿主浏览器安装。临时服务已停止。

## 尚未关闭

F04 的上述 Task Pipeline 旅程已验证；任意会话失效/跨 Provider 物理接管仍属 F05，不能由本回执推断已完成。
剩余验收列保守保留当前合同全部条款，由接手者结合已接纳结果判断，不由 runtime 猜测语义完成。
环境资格未知仍标 unverified；回执不证明环境可用，也不证明脱离父进程的旧 writer 已被隔离。
活跃输入修订、环境实际持有者、全 Provider 持续接续、资源收尾与真实产品资格仍留在 active 任务。
没有把 B03 或整份 F01–F18 标为 done；没有合并、推送、部署。
