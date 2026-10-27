# 完整指标加载与刷新状态

> 状态: done
> 实现提交: `50d2b6c4` fix: load coherent monitoring metrics and refresh trace views

来源：本轮 VoltAgent 对照分析及用户明确批准执行 1–5。基线：715ffbc9。

## 范围

消除 cockpit 人为串行等待；摘要与详情独立；初始未知显示 loading/unknown，失败可见，事件后重新读取。

## 验收

- 延迟/失败/跨项目切换/事件更新 -> verify: 不显示假零，不残留旧项目指标；所有卡片完成可被检测。
- 聚焦测试、API 调用方及浏览器验证 -> verify: 记录命令、结果和证据路径。

## 边界

canonical EventLog/Stores/必要 sidecar 不变；SQLite 仅为可重建投影；不采样、不截断统计、不修改 8001。

已按用户批准从 backlogs/ 移入 tasks/active/。

实现说明：docs/impl/monitoring-complete-refresh.md（遵循本地文档忽略策略）。

刷新实测补充：Trace 原页面没有事件刷新链路，30 秒内无重读，已复现。纳入同项修复：接入 SSE、合并请求、保留旧读数及失败提示、重置分页 generation，并验证选中详情/spans 更新。Loop 原断言按标题定位失败，实际页面按任务 ID 显示，API 与 T2 行已更新；修正评估脚本定位。

## 验收结果

Docker Playwright 最终 15 passed；包括首次未知、SSE、503 保留旧读数、跨项目迟到、Trace index/detail/spans 刷新及旧分页契约。首次 fixture 暴露 Trace 无刷新已修复，真实事件到页面最终四项全部通过。

聚焦后端：initial-tests 104 passed；final-focused 68 passed；caller-contracts 50 passed；trace-final-tests 21 passed；agents-final-tests 6 passed（集合有重叠，不相加）。dev-verify 自动 Web typecheck/unit、sentinel 50 passed / 1 skipped、flow-smoke 10 passed；Trace UI 增量 plan/run 全通过。最终前端 build/bundle budget、Docker Playwright 15 项通过。未跑 full pytest 或真实 provider E2E。

8001→8002 暖读完整指标（秒，非同时采样）：Cargo Trace 28.77→2.14、Loop 4.55→2.29、Delivery 29.37→3.85、Agents 73.09→4.47；loshu-fms Trace 24.25→1.89、Loop 1.97→1.95、Delivery 15.04→4.73、Agents 54.49→7.22。服务重启后首次 Agents 为 9.70s/16.35s，保留 SQLite/OS 缓存；少量共享宿主机样本，不宣称 P95。大项目未注入测试事件。

证据：`/tmp/zf-monitoring-refresh-20261027T0124/` 的 `api-parity-classified.json`、`bundle-parity-final.json`、`raw-oracle.json`、`browser-refresh-candidate.json`、`refresh-results.json`、`final-verification-receipt.json`、`trace-refresh-verification-receipt.json`、`browser-trace-final.log`。精确脚本与失败/修复记录保留；完整方法和限制在本地 `docs/impl/monitoring-complete-refresh.md`。隔离 simulation.done 已记录并清理；8002 保留。
