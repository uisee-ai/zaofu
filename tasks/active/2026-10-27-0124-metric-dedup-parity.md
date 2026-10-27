# usage 与成本去重口径

> 状态: active

来源：本轮 VoltAgent 对照分析及用户明确批准执行 1–5。基线：715ffbc9。

## 范围

沿用现有成本事实来源，保护累计/增量、重试、恢复和不同 run 的身份；本轮读取优化不得改变计算口径。

## 验收

- 重放重复结果、root/child usage、多 run 数据 -> verify: 与既有算法精确相等且无重复计数。
- 聚焦测试、API 调用方及浏览器验证 -> verify: 记录命令、结果和证据路径。

## 边界

canonical EventLog/Stores/必要 sidecar 不变；SQLite 仅为可重建投影；不采样、不截断统计、不修改 8001。

已按用户批准从 backlogs/ 移入 tasks/active/。

实现说明：docs/impl/monitoring-complete-refresh.md（遵循本地文档忽略策略）。

## 已验证，等待实现提交后归档

CostTracker 重复 sample 接纳一次，跨 run 独立统计，root/child agent 输出不重复计费；两个真实项目各 26 个核心字段与 canonical 原始重算精确一致，fleet 仅时钟年龄差。8001 核心读取失败/超时另列，未声称全部与基线可对等。

聚焦后端：initial-tests 104 passed；final-focused 68 passed；caller-contracts 50 passed；trace-final-tests 21 passed；agents-final-tests 6 passed（集合有重叠，不相加）。dev-verify 自动 Web typecheck/unit、sentinel 50 passed / 1 skipped、flow-smoke 10 passed；Trace UI 增量 plan/run 全通过。最终前端 build/bundle budget、Docker Playwright 15 项通过。未跑 full pytest 或真实 provider E2E。

8001→8002 暖读完整指标（秒，非同时采样）：Cargo Trace 28.77→2.14、Loop 4.55→2.29、Delivery 29.37→3.85、Agents 73.09→4.47；loshu-fms Trace 24.25→1.89、Loop 1.97→1.95、Delivery 15.04→4.73、Agents 54.49→7.22。服务重启后首次 Agents 为 9.70s/16.35s，保留 SQLite/OS 缓存；少量共享宿主机样本，不宣称 P95。大项目未注入测试事件。

证据：`/tmp/zf-monitoring-refresh-20261027T0124/` 的 `api-parity-classified.json`、`bundle-parity-final.json`、`raw-oracle.json`、`browser-refresh-candidate.json`、`refresh-results.json`、`final-verification-receipt.json`、`trace-refresh-verification-receipt.json`、`browser-trace-final.log`。精确脚本与失败/修复记录保留；完整方法和限制在本地 `docs/impl/monitoring-complete-refresh.md`。隔离 simulation.done 已记录并清理；8002 保留。
