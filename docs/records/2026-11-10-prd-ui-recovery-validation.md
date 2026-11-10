# PRD UI 恢复闭环修复验证

本轮针对 [前轮实测](2026-11-10-prd-ui-validation.md) 的三个 P1/P2，
基于 `6c86d822e81bd39745d3740df86b42dc0ef720d1`，在分支
`wip/codex-20261110-prd-recovery` 隔离实施。行为和交互分析见
[实现说明](../impl/prd-ui-recovery.md)。前轮真实失败记录保留，本轮结果不覆盖它们。

## 修复与证据

- P1：Apply/Start 接入执行策略交接，保护在途/blocked Run，空闲时确认新 watcher 后
  再发布 invoke；模板保留 resident 设置。首次强化 fake 复现 zombie PID 阻塞，随后
  修复进程探测。失败证据仍在 `/tmp/zf-prd-ui-fake-kanban-20261110-141321/evidence/`。
- P2：Channel 原生 abort 核验后追加新 generation，经受控 action/原 dispatcher
  恢复；无证据、错身份、仍运行/完成过、陈旧请求均拒绝。测试覆盖重放、并发和旧结果晚到。
- P2：真实 synth adapter 的语义拒绝携同版 refs 进入 Owner 检查点，保持摘要与身份
  校验；主界面可以回答问题并在原 Run 重规划。篡改或错版本保持不可用。

Docker Playwright 完整旅程使用生产 Web/API/Kernel，fake 仅替换 Provider。
初始配置刻意关闭 plan approval，Workflow Synthesis 选真实 registered controller、
启用审批并改变执行策略，以确保覆盖 warm handoff，而不是复用不变配置。
审计要求旧/新 watcher PID 与配置摘要不同，停机→启动→新 loop→唯一 invoke 顺序正确，
最终为同 Run 的完整待审批 Plan，没有批准实施或 writer 派发。

最终证据目录：

- `/tmp/zf-prd-ui-fake-kanban-20261110-final/evidence/`
- `/tmp/zf-prd-ui-fake-channel-20261110-final/evidence/`
- `/tmp/zf-prd-recovery-browser-20261110/`：Owner/Channel 定向浏览器 trace、video，
  以及历史真实 native abort 的只读核验回执。

浏览器交互测试覆盖等待审批身份绑定、旧预览不能回答新请求、Owner 答复 action/body，
缺少候选正文时仍可查看问题并答复，
以及 Channel 恢复按钮绑定原失败并展示核验拒绝原因。它们使用真实组件与局部 API fixture；
成功重投递由 controlled action → 原生文件 fixture → 真实 dispatcher 的后端测试覆盖，
不将这两层测试称为新的真实 Provider UI 重试全链路。

历史真实 Codex session `01a1c55c-8800-7c01-8db8-c0184a539f6c` 经新代码核验：
唯一 turn `01a1c55c-8843-7d52-957b-36e6faa1a5bd`，终止
`2026-11-10T12:18:57.941Z`。仅读取 metadata 并计算摘要，没有重新投递旧项目。
本轮没有新增付费 Provider 全程运行，不能宣称真实模型全链路已复验。

## 自动验证与既有失败

`dev-verify plan` 给出 393 个影响模块。自动策略因超过 80 模块扩大成全仓 pytest；
本轮采用其列出的精确 393 模块，8 worker、20 分钟进程预算，排除 serial/perf/host/
real_provider。命令和列表保存于 `/tmp/zf-prd-recovery-selected-command.json`，日志为
`/tmp/zf-prd-recovery-selected-closure.log`。结果 **4850 passed、9 skipped、2 failed**。
这不是全仓 pytest 或覆盖率验收。

两项失败在未改动的 dev 基线独立复现：

- `tests/test_web_measure_loop_routes.py::test_loop_view_cache_invalidates_on_config_and_signer_identity`
- `tests/test_feishu_channel_progress_card.py::test_confirm_click_is_wired_to_real_controlled_action`

均因测试的 SimpleNamespace 配置缺少 `observability`，在 `core/events/factory.py`
读取 controller 时失败。基线日志 `/tmp/zf-prd-recovery-baseline-failures.log`；不把它们
记为本轮修复成功，也未修改不相关 controller 代码。

剩余层通过 `dev-verify run --base dev --skip-tier deterministic --tests
tests/test_prd_ui_provider.py --tests tests/test_prd_ui_audit.py` 执行。显式测试映射说明：
provider/audit 单测覆盖 fixture 与 oracle 合同；`run.py` 的流程、启动和清理由上述两个
完整 Docker 旅程验证。首轮门禁发现 submit 文件超过 1000 行，已将既有 invoke 状态
投影移至 replay 模块并重新验证调用方及门禁；不增加结构债务豁免。

构建执行 `npm --prefix web run build`（含 typecheck/bundle budget），Web unit 全部通过；
最大 chunk 478943 bytes，小于 500000 限额。串行测试和最终哨兵的精确结果如下。

| 验证 | 结果 |
| --- | --- |
| 最终定向回归（新恢复入口、Owner 同 Run approve/reject、三组相关 mock E2E、fixture/oracle） | 65 passed；`/tmp/zf-prd-recovery-final-focused3.log` |
| CLI/Workflow invoke 直接调用方及交接回归 | 80 passed；`/tmp/zf-prd-recovery-final-callers.log` |
| 串行层 | 10 passed；`/tmp/zf-prd-recovery-remaining-tiers2.log` |
| 最终 premerge 哨兵 | 53 passed；`/tmp/zf-prd-recovery-gate-final.log` |
| 文档/instruction 合同 | 8 passed；`/tmp/zf-prd-recovery-instruction.log` |
| Docker UI | 两条完整旅程各 1 passed；Plan 阅读 6 passed、Inbox Owner 3 passed、Channel 恢复入口 1 passed |

最终 planner 无 errors，闭包扩为 395 模块；新增的 instruction 与 PRD audit 模块均已
补跑。没有因文档/局部防御性分支变化重复全部 393 模块。


## 收尾

所有临时运行按 simulation.done、停止本轮进程/容器/tmux、归档证据后删除运行目录处理。
保留失败证据及最终产物，诊断包排除 provider home、凭据和私有目录。
真实 dev 的 8001 与其他会话不属于本轮修改或清理范围。修复提交到独立分支，尚未 merge/push。
