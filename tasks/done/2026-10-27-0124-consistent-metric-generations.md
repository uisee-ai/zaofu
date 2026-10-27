# 跨指标源版本一致

> 状态: done
> 实现提交: `50d2b6c4` fix: load coherent monitoring metrics and refresh trace views

来源：本轮 VoltAgent 对照分析及用户明确批准执行 1–5。基线：715ffbc9。

## 范围

共享读取绑定事件及 store/sidecar 源版本，变化时重新构建，保留已有缓存有效性检查。

## 验收

- 变更事件、任务、会话与必要 sidecar -> verify: 更新后的结果可见，不返回混合版本；冷暖读取一致。
- 聚焦测试、API 调用方及浏览器验证 -> verify: 记录命令、结果和证据路径。

## 边界

canonical EventLog/Stores/必要 sidecar 不变；SQLite 仅为可重建投影；不采样、不截断统计、不修改 8001。

已按用户批准从 backlogs/ 移入 tasks/active/。

实现说明：docs/impl/monitoring-complete-refresh.md（遵循本地文档忽略策略）。

## 验收结果

任务/session/外部及内部 task-map/账单对账变化使版本失效；并发变化重试或返回 503；成本不可读不冒充零。相关后端测试通过。

聚焦后端：initial-tests 104 passed；final-focused 68 passed；caller-contracts 50 passed；trace-final-tests 21 passed；agents-final-tests 6 passed（集合有重叠，不相加）。dev-verify 自动 Web typecheck/unit、sentinel 50 passed / 1 skipped、flow-smoke 10 passed；Trace UI 增量 plan/run 全通过。最终前端 build/bundle budget、Docker Playwright 15 项通过。未跑 full pytest 或真实 provider E2E。

8001→8002 暖读完整指标（秒，非同时采样）：Cargo Trace 28.77→2.14、Loop 4.55→2.29、Delivery 29.37→3.85、Agents 73.09→4.47；loshu-fms Trace 24.25→1.89、Loop 1.97→1.95、Delivery 15.04→4.73、Agents 54.49→7.22。服务重启后首次 Agents 为 9.70s/16.35s，保留 SQLite/OS 缓存；少量共享宿主机样本，不宣称 P95。大项目未注入测试事件。

证据：`/tmp/zf-monitoring-refresh-20261027T0124/` 的 `api-parity-classified.json`、`bundle-parity-final.json`、`raw-oracle.json`、`browser-refresh-candidate.json`、`refresh-results.json`、`final-verification-receipt.json`、`trace-refresh-verification-receipt.json`、`browser-trace-final.log`。精确脚本与失败/修复记录保留；完整方法和限制在本地 `docs/impl/monitoring-complete-refresh.md`。隔离 simulation.done 已记录并清理；8002 保留。
