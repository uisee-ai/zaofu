# V5 无固定执行期限与活性检测复测

本记录保留前轮运行当时的结论；后续修复、提交与复测见
[残余问题实施记录](2026-10-30-v5-delivery-residual-fixes.md)。

状态：期限修复已通过真实 Provider 验证；完整交付仍被 Discovery 输入分区问题阻塞。
改动位于独立 worktree，尚未提交、合并到 dev。

关联：[实现说明](../impl/v5-verification-efficiency.md)。

## 修改范围

基线 `ea24ce4c`，工作分支 `wip/codex-20261030-v5-progress-liveness`。
V5 operation/Run 的 `timeout_seconds` 为 0；已声明 fanout stage 的 0 不再替换成
7200 秒。关闭累计执行期限不关闭 reader/synth 活性检查，Kernel 状态镜像不续活；
Provider 用量即使由 Kernel 采集仍作为活动证据。保留正数期限、预算、结果门禁、
600 秒角色空闲配置和原有静默思考宽限。

## 确定性与宿主验证

- 新增回归先复现 6 个失败，再验证修复：长时间仍活跃的 child、静默 child、
  Kernel 状态噪声、零期限排队，以及无期限 synth 的活跃/静默两种情况。
- 定向一组 115 passed：`test_fanout_progress_liveness`、`test_stage_definition_binding`、
  `test_workflow_budget_guard`、`test_role_lifecycle_runtime`、`test_task_pipeline_worker_liveness`、
  `test_heartbeat_sweep`、`test_activity_liveness`（均在 `tests/`，`pytest -q --no-cov`）。
- 定向另一组 91 passed：`test_fanout_progress_liveness`、`test_fanout_hardening`、
  `test_prd_v5_config`、`test_prd_e2e_residual_eight`、`test_workflow_budget_status`、
  `test_lifecycle_fanout_child_queries`、`test_capture_logs_watchdog`、`test_provider_turn_liveness`。
- `dev-premerge-gate.sh`：51 passed；`run-flow-smoke.sh`：10 passed。
- `tests/e2e/test_prd_v5_config_start.py -m 'host and mock_e2e'`：1 passed；
  核对受控 Preview/批准/实际 runtime 启动携带 operation 与 Run 零期限。
- `dev-verify.py plan --base dev` 选出 203 个影响测试文件并升级全套；
  按 AGENTS.md 不因宽泛映射触发全 pytest，显式执行上述模块/调用者/共享合同闭包，
  `dev-verify.py run --base dev --skip-tier deterministic --skip-tier serial` 执行哨兵及 smoke。
  无 UI 修改；测试期间临时 node_modules 链接已移除。没有运行全 pytest。

## 前轮真实失败基线

`TASK-E1F954` / `prepare-TASK-E1F954-a187d461bf215f61` 在真实 Codex 下完成
Scan、Plan、Impl、Task Verify 和 8/8 candidate Verify。Discovery 仍在读取证据时，
604 秒触发 `workflow_budget_exceeded:wall_clock`，事件 `evt-f71d9a503492`；
仅 28/60 usage samples，不是 token 超限。未进入 Judge/最终交付。
此 Run 未重置预算或伪造续跑。证据、Git bundle 已归档，临时 runtime、Web、tmux、
16 个 worker worktree 和状态目录均已清理。

## 新轮真实运行

- 源码来自上述工作分支，独立服务 `0.0.0.0:8002`，不操作 dev 的 8001。
- Docker `mcp/playwright:latest`、host networking；没有安装宿主浏览器。
- Playwright 完成新建项目、Kanban Agent 提需求、Create task、完整 YAML Preview 和 Start Workflow。
- 项目 `hello-v5-liveness` / `project-13a273d0`；Task `TASK-CFB77C`；
  Run `prepare-TASK-CFB77C-5a1d34ce33d8dffb`。
- 真实 Codex `gpt-6.1-sol`，Kanban medium；保留 8 lanes、blocking WRC、全部交付门禁，
  默认自动 Plan 接纳。未覆盖人工 Plan 审批或 Agent 引用编辑。
- 产品仅 `hello.py`、`tests/test_hello.py`，要求精确 stdout、退出码与至少两个实际执行的 unittest。
- 生效快照证明 operation/Run timeout=0、token_budget=0，impl/verify capacity=8。
- 2026-10-30 14:12:08 UTC 正式接纳；Scan、Planner、Critic、Impl、Task Verify 已通过。
- product-scan、tech-scan 分别于 14:24:37、14:25:06 自动休眠，事件明确保留 session/workdir；
  执行中的角色有 Provider 活动证据。真实卡住故障未注入；静默检测依赖上述确定性故障测试。
- 冻结候选 `0ee49d5db3d6f91379c6e6bde0802569dc6bf3b5` 的 8 条 Verify lane 真实并行，
  全部通过，耗时 1302.68–1523.31 秒；15:00:52 UTC `test.passed` 事件
  `evt-1db9c3c764a8`。0 次预算超限，无固定 600 秒终止，保留成本和 usage sample 门禁。
  `busy-over-600-verify-lane-*.json` 保存运行时零期限、角色活动和 operation 身份证据。
- 从冻结 Git ref 独立导出候选，实际运行 2 个 unittest 均通过、0 skips，两个 CLI 场景的
  stdout 字节和退出码均符合要求；见 `independent-candidate-check.json`。
- Discovery 在 Provider 启动前失败：`execution_spec` 是 task-scoped 输入，只有子任务
  `HELLO-001` 分区；消费方按父任务 `TASK-CFB77C` 取分区。`artifact_query/handoff.py`
  仅为 `candidate-verify` 开启整体候选输入，Discovery 没有相同的范围处理。
  `task_scoped_plan_inputs.py` 因而正确拒绝缺失分区；错误是消费者范围与生产者合同不一致，
  不能通过伪造父任务内容或放松输入校验解决。相关路径本轮未修改。
- 自动重试耗尽后，15:01:31 UTC `run.goal.blocked`（`evt-828bb86a371e`），
  原因 `stage_replan_cap_exhausted`。未进入 Judge 或最终交付，不能宣称端到端闭环。
- 本轮未证明跨阶段真实同 session 恢复；已证明闲置 scan 保留 session/workdir 休眠。
  Task Verify 到 candidate Verify 的工作区归档伴随 continuity downgrade，应与同阶段恢复区分。
- 原始证据根：`/tmp/zf-v5-liveness-20261030-140230/evidence/`；包含浏览器 trace、截图、
  Preview、运行事件、配置快照、只读活性采样、终态和独立产品检查。
- 最终尾部补账为 24,687,918 tokens（含缓存输入），价格状态 `unpriced`；不能视为
  新增计费 token 或零美元成本。最小产品仍使用完整 8-lane 生产控制器，本轮不构成低成本基准。

## 清理与交接

`simulation.done` 已通过 CLI 发出（`evt-6f5782e07fc3`）；停止本轮 runtime、Run Manager、
tmux、8002 Web 和 Docker 浏览器，清理 16 个 worker worktree。
Git bundle 校验成功，运行状态以私有 `runtime-evidence.tar.gz` 归档后删除；移除临时访问令牌
和含启动凭据的 Web 日志。`cleanup.json` 保存结果。真实 dev 的 8001 未改动。
源码 worktree 保留，代码与新测试尚未提交；本记录受 `records/` ignore 规则影响，后续提交
需要显式包含此文件。完整复测未进入 Judge/交付，后续需先修复 Discovery 输入范围，再跑新 Run。

## 额外观察

8-lane Verify 活跃期间，浏览器运行图连续返回 503 `source_unavailable`，完整图加载亦出现
409 `structure_changed`，未宣称图上的 lane 明细验证通过。`workflow_workbench_cache.py`
在构建前后多次比较包含事件源的 fingerprint，最多尝试三次；持续事件写入可能使其始终
不能形成稳定 generation。这是根据读取路径与现象的诊断，非本次期限修复内容，需独立复现修复。
页面错误截图 `06-eight-verify-lanes.png` 与 `graph-read-response.json` 已保存；
8 lane 并行由各自 `workflow.operation.started` 和后续 Provider 活动事件证明。
Provider 停止后页面恢复，终态图显示 8 条 Verify 已结束、Discovery 失败及 Run 阻塞，
保存 `08-terminal-graph.png`；这不能替代运行期间实时图可用性验证。

宿主机在 14:41–14:56 UTC 附近有明显观测间隙，随后记录到 962.7 秒 watcher lag、
1142 秒 projection stale；宿主负载及 swap 占用很高。运行恢复后仍有新的 Provider 活动，
Verify 随后正常接纳。不能将上述 wall-clock 时长解释为连续计算时间或性能基准，
也不能仅凭这些现象认定停顿的唯一根因。详见 `runtime-lag-observations.json`、`host-load.txt`。

浏览器另记录一条 localStorage SecurityError，发生在测试脚本切换 `about:blank` 的操作期间；
保留 `browser-errors.json`，没有把本轮报告为零浏览器错误。
