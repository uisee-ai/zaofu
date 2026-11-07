# 普通恢复诊断交给可用的 Run Manager 消费者

> 状态: done — `39627af6` fix: deliver recovery diagnoses to the resident consumer

依据：持续监工授权；设计 [199](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。

## 复现与职责

LS-v1 已加载 da14f7fd；05:08 tick evt-a1f62b950321 选中 semantic-diagnosis-d49cdb7926b5，seen=false、前提有效，但 actions_applied=0。RM agent 已启用且 observing；health-only 过滤阻止普通 RM-owned 诊断；反射禁用，Autoresearch 又被六条历史 inflight 请求占住。OA 不能替 RM 调度消费者。本轮不启用用户关闭的 Autoresearch、不改写历史请求、不重启产品 worker。

## 实施与验收

1. 普通 run_manager_recovery 诊断 -> verify: 已启用 resident 实际收到一次绑定 ID 的请求；旧无关 Autoresearch 请求不能阻挡。
2. 请求与响应 -> verify: 明确 recommendation 事件类型和语义交付清单；错误 ID/观察不能履约；正确回应释放等待槽位。
3. 单消费者与等待 -> verify: prompt 成功不再重复 fallback；有未决请求时不覆盖 resident 当前诊断，保留 owner/request/deadline 并在响应后继续；健康诊断仍可处理超时。
4. 上下文与证据 -> verify: 无 task_id 的 run 诊断同样有带摘要的持久化 context/read-set；任务/故障不因 prompt 变成 done，既有门禁与显式 OA/Autoresearch 路由保留。
5. 调用链与部署 -> verify: 聚焦回归、影响闭包、sentinel/mock 通过；合并 dev；仅重载控制面，核对 worker 连续性和真实投递/回应；记录剩余问题。

- 在线补充复现：05:18 网关失败诊断 evt-4721a23d9e0d 已走现有 failure-diagnosis 路由，但 resident Codex 最后 task_started 仍为 03:58、task_complete 04:00。05:21 pane 显示长提示停在输入框且 Current Focus 未提交。RM 自建 send-keys 没有复用 Worker 的 Codex bracketed paste；补充复用既有 TmuxSession.clear_input/send_paste，仅 Codex 路径改变，真实结果仍须绑定回应核验。首轮影响闭包 4,627 passed / 7 skipped（127.66 秒），传输修改后重跑相关闭包。

- 超时负例复现另一处：run 级诊断没有 task_id，旧 health reprompt 强制 materialize task context 导致 blocked。保留原始带摘要 context/read-set 与语义交付，重发同一 request_id；原始期限不重置。新增真实 tick 时钟推进回归；不得把超时重发误当新任务。

## 验证与交付

- 最终直接调用链、传输与超时回归：114 passed。新增 10 项真实 tick 回归覆盖绑定回应、旧请求共存、单消费者、暂停、失败出口、Codex 完整粘贴、无 task 的超时恢复。
- 最终 343 文件影响闭包：4,629 passed / 7 skipped（124.66 秒）；没有触发 full pytest。前一轮新增 backend 参数触发旧测试 mock 签名不匹配，已同步 mock 参数并回归。
- `dev-verify run --base dev --skip-tier deterministic` 的 serial 10、sentinel 53、mock flow 10 全部通过。日志 `/tmp/zf-diagnosis-closure-v3-20261107.log`、`/tmp/zf-diagnosis-tiers-v3-20261107.log`。
- 软件待合并 dev 和控制面重载；真实请求执行/回应尚待验证，本任务保持 active。

- 已合并 dev a98f73dd，05:29:46 控制面加载，worker pane/PID 保持。05:40:37 evt-a5e5b4ef17c9 投递 rmd-90752e5f30073cd3，05:40:44 provider 接收完整请求，05:43:12 evt-818a6ebfe53c 同 ID 回应，05:45:41 evt-5fc4adcbb85e 消费成功。
- 同 tick 05:45:46 evt-965dd655beeb 自动接续下一诊断 rmd-486b6820918c7474，无重复 Autoresearch。请求投递/响应/消费/下一条推进已真实验证。本轮缺口关闭，RM→OA 语义交接及有效合同读取独立进入 2026-11-07-0540-resident-semantic-handoff；不声称所有产品故障已解决。
