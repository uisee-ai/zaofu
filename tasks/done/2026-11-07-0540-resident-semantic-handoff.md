# 修复恢复事实输入与 RM→OA 语义交接

> 状态: done
> 实现: `720d3e08 fix: bind resident semantic handoffs to current recovery facts`

依据：持续监工授权；[199](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。

## 真实复现

LS-v1 网关 dev.blocked evt-6f8c2abfeb23，经 resident 建议 evt-65bfaf042279 升级 human evt-afb2f2e70078。确有部署装配未完成，但 agent 将 contract.contract_revision 的旧投影 r7dd 与冻结快照 r2fea 比较而误判漂移；effective_contract_revision 当前仍 r2fea。planned owner dev-lane-4 被误当 writer；assigned_to/attempt 实际是 dev-lane-2。上游 AUTHZ-COORD-01 已在 TaskStore done，当前事件窗口未见完成不能推定未完成。

RM 建议入口白名单又不支持已存在的 orchestrator-rework-triage，导致可交 configured OA 的事实只能转人工。监工通过受控 action 审核解除误报，并 zf recover task-impl 续作 g2；这不替代修复缺失交接。

## 有界实施与验收

1. context 明确当前有效合同、投影版本、计划 owner、assigned writer/attempt 及直接依赖状态 -> verify: 投影版本旧/归档 done/当前窗口无完成事件时仍输出规范事实，不伪造证据或修改 TaskStore。
2. resident 方法说明 -> verify: 先核对有效身份和依赖状态再裁决漂移；已配置 OA 的语义问题可通过绑定 recommendation 交接。
3. 推荐入口复用当前 settled Impl 审核 action/准入谓词 -> verify: 原 request/case/身份沿用，source 是当前准入失败；缺请求、跨 task/run、旧 operation、禁用 OA、暂停或人工门禁均不派发，不降低三次失败阈值或直接重派 Worker。
4. 真实 RM tick → 既有 OA checkpoint -> verify: 单次请求、语义输入与失败证据完整、故障保留 effect pending；正常 OA 返回裁决再走现有执行路径。
5. 聚焦/影响闭包、sentinel/mock -> verify: 实施提交合并 dev，仅重载控制面，观察真实效果并记录；不 push。

## 验证

- 复现：6 failed / 6 passed；最终聚焦 12 passed。既有 OA 当前失败审核谓词被复用，没有新增状态机或降低重试阈值。
- 直接调用链 113 passed，包含实际 RM tick → OA immutable input → OA 裁决 → pipeline 续作请求；旧 operation、其他 task、无原请求、禁用 OA、暂停、人工门禁均不派发。
- `dev-verify plan --base dev --json` 的 381 文件影响闭包：5,046 passed / 7 skipped（135.52 秒）。按分域规则执行精确闭包，不称 full pytest。`dev-verify run --base dev --skip-tier deterministic`：serial 10、sentinel 53、mock flow 10 通过。
- 日志 `/tmp/zf-rm-oa-direct-20261107.log`、`/tmp/zf-rm-oa-closure-20261107.log`、`/tmp/zf-rm-oa-tiers-20261107.log`。尚待合并与控制面加载；本轮当前网关已由监工受控续作，不能称真实 RM→OA 自主闭环已验证。

## 实机进展（2026-11-07 05:56 UTC）

720d3e08 已合并 dev c9e5a4aa，控制面已加载，10 个 Impl pane/PID 保持。FMS-03-GAP-IMPLEMENT 新失败 evt-0d9036ba1a62 → 请求 evt-83374ed7d5fc → 绑定建议 evt-053de972b022 → 消费 evt-48d19c67e8fb → OA checkpoint evt-82f6021a37f6，真实 RM→OA 交接成立；等待 OA 裁决和后继 Impl，暂保持 active。另发现同 request 的旧上下文被新交接覆盖，单列 0600-recovery-context-snapshot 修复，不将最新可读冒充历史完整。

## 实机验收与归档

06:00:23 evt-a8cfaf80ab2d 提交精确返工；06:01:07 evt-13f59029cf4e 准入、evt-294548652d93 应用；06:01:14 evt-9192a31399df 自动派发 FMS-03-GAP-IMPLEMENT g2，op …cd04d9e4f72b。从失败到带指导续作 11 分 20.6 秒，监工未手动重派。RM→OA→WRC 本工作包成立；CHK-03 产品最终通过仍由实施/独立 Verify 负责。历史上下文覆盖另由 ef6330ac 修复。
