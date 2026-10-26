# Hello World 控制链缺陷修复与断点验证

> 状态: done — `0115979b fix: fold dispatch ownership incrementally during worker observation`；前序实现 `0fb743f7`、`aea63c57`。

用户已批准在独立 worktree 修复本轮问题并基于该代码验证。基线 fe6f3bd6；证据 `/tmp/zf-hello-world-20261026T015754Z/evidence/`。只修改可复现缺陷，不把缺失验证直接认定为缺少实现；不操作 loshu-fms、8001 或其他 worktree。

## Backlog 与验收

- [x] HW-01 正式配置准入：开放实现已具备的 Product Flow pilot 检查点 -> verify: YAML loader 到 OA/WRC caller 回归通过；仍拒绝无 pilot、非 Product Flow、micro/light。
- [x] HW-02 人工审批边界：OA adopt 与 Owner approval 分离 -> verify: 审批开启时 adopt 后只有精确版本审批请求且无 Impl；正式批准后派发；拒绝/重放不绕过。
- [x] HW-03 Provider 传输准入：拦截 Codex + Claude-only stream-json -> verify: 配置/preflight 给出明确错误，Codex tmux 与 Claude stream-json 保持可用。
- [x] HW-04 活动事件与阶段交接：复现注册表热路径并修复 -> verify: 噪声事件回归保证有界加载、保留活动/状态顺序；真实断点 Verify→集成继续且没有重复 Impl；新 Run 观察连续交接。
- [x] HW-05 OA 单会话互斥：多个检查点排队、结算后继续 -> verify: 同角色不同时注入两个 operation；恢复与重复事件不会丢失请求。
- [x] HW-06 恢复服务与交付闭环：核查 RM recommendation 消费和 AR 受控调用 -> verify: 实际入口回归覆盖建议→调用/明确拒绝；断点跑到 Judge/终态，有不足如实记录。
- [x] HW-07 复测与收尾：focused closure + sentinel + deterministic E2E + 隔离真实 Codex 入口/断点验证 -> verify: 保存命令/计数/证据；实现与任务状态提交，未通过项不得标 done。

## 实施原则

配置灰度仍需显式 pilot。人工审批不能由 OA 代替。性能优化不能绕过 EventWriter、store 锁、新鲜状态或重放语义。断点已越过审批，不能用于证明修复后的审批入口，必须另起隔离新 Run。先复现再修复。实现结束提交本工作分支，不自行合并或 push。

## 验证中发现的根因补充

HW-06：断点恢复后 RM 建议已消费、AR invocation 与 loop 均启动；loop 在严格 skill 校验处失败。Autoresearch 配置重写把技能源指向 driver checkout，同时 experiment 中已有本地同名技能，造成重复来源。增加本地实验版本优先的修复和回归；保留严格碰撞校验及外部客户技能来源。

## 验证记录

- 新增首轮 7 个复现用例在 fe6f3bd6 上全部失败；修复后通过。
- OA 队列/恢复与人工审批追加回归 3 passed；核心 OA/活动事件回归 24 passed。
- 首轮影响闭包 155 passed、2 failed；两项为自恢复重试被自身锁挡住和新 fixture 缺 transport，已修复后复测。
- AR/配置闭包 192 passed、1 failed；旧 stream-json fixture 未声明 provider，已改为明确 Claude 并复测通过。
- AR 修复闭包 33 passed；实际 11 角色模板技能校验无错误。
- premerge sentinel 51 passed（34.27 秒）。自动 dev-verify 更大范围确定性层正在执行，真实 fresh/断点验证仍在运行。
- 证据目录 `/tmp/zf-hello-fixes-20261026/evidence/`；旧断点 8/8 集成、Candidate 已冻结；AR invocation 和 loop 已实际执行，首次 loop 的技能预检失败保留原始结果。

## 提交后复测

- 实现提交 `0fb743f7 fix: preserve approval and checkpoint dispatch across parallel workflows`。
- 提交版本聚焦闭包 184 passed；transport caller 闭包 198 passed；registry/telemetry caller 闭包 23 passed。
- 较大确定性层在 30 分钟预算前主动中断并保留详情：10122 passed、18 skipped、5 failed，不能视为全量通过。serial 10 passed，sentinel 51 passed，deterministic flow smoke 10 passed。
- 5 个失败归因：旧 config fixture 已修复；lane override fixture 改用 Claude stream-json；CSafeDumper 暴露 Path 参数需要规范化为字符串，已修复。另两项（未跟踪 runbook 缺失、five-workflow 测试的过期 digest 断言）在原基线 fe6f3bd6 独立复现，未混入本批产品修改。
- Docker Playwright：Graph API 41 节点，实际 Delivery Graph 渲染成功，page_errors=[]。初次只读挂载配置和一次页面等待超时保留为测试基础设施失败，修正后重测成功。
- 真实 fresh / resume 均通过 `zf start --control-plane-only` 加载提交代码，事件确认 `workers_preserved=true`。fresh 已完成一次 Plan Critic→Planner 返工并进入 blocking pre_impl；resume 已完成 8/8 候选 Verify、零产品缺口复扫并进入 Judge。
- 未通过项继续保持 active；AR 真实 loop 初次失败仍保留，不以技能预检通过冒充真实 loop 完成。

## 真实断点闭环结果

- 续跑 Run：`prod-new-prd-20261026T015754Z-tmux`，候选提交 `b8893cf1b4764d151eba44da0a88fb1de73edec9`。
- 8 次 Impl / 8 次 task Verify、8/8 增量集成；8/8 候选 Verify → 产品缺口复扫零阻断 → Judge passed → `run.goal.completed`（`evt-ecc84501c26f`）。续跑没有重复 task pipeline dispatch。
- `prod_flow_terminal_audit.py` 最终 passed：Dossier ready、完成凭据与终态一致、Owner delivery request 存在。产品主分支 `npm test`：9 passed（包含 seed health）。
- 交付审计覆盖本地请求闭环，不代表飞书实际送达：隔离环境未配置 alert 路由，保留 `feishu_route_unconfigured` 结果，没有配置或联系外部接收者。
- `simulation.done` 已记录，旧测试 tmux / Run Manager / watcher 已按 `zf stop --fast --include-run-manager` 停止；候选、产物和证据保留。
- follow-up 提交 `aea63c57 fix: normalize registry paths for safe serialization`；状态/heartbeat/AR 追加闭包 96 passed。
- 实际交接时延仍随账本和投影负载变化：控制进程重载后 old Run 样本 30.8–62.7 秒，fresh 样本约 5.1–15.2 秒。32 hook 回放 1.22 秒只证明局部优化，不能宣称整个控制循环已经毫秒级。

## 新 Run 审批与并行入口

- `prod-new-prd-20261026-fix`：真实 Planner → Critic 拒绝范围验收缺口 → Planner 返工 → Critic approve → OA `pre_impl adopt` → OA `plan_candidate adopt`。
- OA adopt 明确产生 `owner_approval_required=true`，仅一条正式 `plan.approval.requested`，当时 `plan.approved=0`、Impl dispatch=0。证据 `approval-held.json` 与 `browser-approval-held-*`。
- 核对精确 Plan 包、8 个独立任务与16个独占文件后，通过 `zf plan approve evt-4b2e3c758639` 正式批准；并行入口已达到 8 个 Impl，分别为 dev-lane-0…7，审批时间早于所有派发。
- 最终提交的 sentinel 再跑 51 passed（33.33 秒）；`fresh-pipeline-metrics.json` 持续保存实际任务并发与交接证据。

## HW-04 第二处瓶颈修复

真实 8 lane 验证仍观察到 200 秒级事件延迟，因此追加采样，而未将局部注册表优化宣称为完整性能修复。120 秒 py-spy 样本显示 `_capture_logs` → `_active_task_for_instance` → `_latest_dispatched_per_task` 为主要热点：活动账本被每角色重复全量字符串扫描。

两条复现先 RED：新增正文后重复读取历史，以及 spaced JSON 的重排事件被字面匹配遗漏。改为通过 EventLog 完整新增行增量更新派发归属；覆盖重派发、fanout 终态释放、部分行、较大原子替换及截断。相关实际 caller/watchdog/recovery 闭包 109 passed；采样精度存在滞后，性能比例不作精确基准。

## 最终组合验证与边界

新 Run 的峰值 Impl=8、Verify=8，首次重叠为 7 Impl + 1 Verify（04:32:25 UTC）。正式人工审批早于全部派发，8 个任务各有且仅有一次 Impl / task Verify 派发；8/8 task Verify、8/8 集成，零 integration.queue.blocked，已生成 `candidate.ready`（`evt-ce9dfb97a2d2`）。

最终候选 `9169cbdce5f4fa62617d3a4abbb4bd385c787bae`：精确16个新增文件范围检查通过；`node --test 'app/*.test.mjs'` 为8 passed；`npm test` 为9 passed（包含基线）。候选是 detached checkout，首次运维复测脚本按分支名找目录未找到；按正式候选目录及精确 HEAD 校验后复测成功，未修改产品代码。

本轮采用用户允许的断点验证：新 Run 验证入口、审批、OA 顺序、8 lane 重叠、活动控制进程重载和增量集成，停在候选冻结恢复点；旧 Run 验证候选验收、产品复扫、Judge、Goal 和本地交付凭据闭环。**未声称新 Run 已经过候选级 Verify/Judge 或终态。** 新 Run 的后续候选验收可能已开始，停机通过正式 CLI 处理并保留恢复状态。

最终性能修复：相同16,914,638字节账本、20次新增事件后的派发查询，旧实现15.82秒、增量实现2.08秒，归属映射相等。影响闭包109 passed、sentinel51 passed、flow smoke10 passed。该局部基准不代表端到端所有阶段延迟；恢复积压时仍观察到秒至分钟级交接。

明确局限：较大确定性层曾主动中断，结果不是全量通过；两项基线既有失败仍存在。AR 已实测 RM recommendation→invocation→loop，初次 loop 因技能源碰撞失败；修复后实际11角色模板预检与33/96项相关闭包通过，但未复跑完整 AR loop 到成功终态。飞书实际送达未验证（隔离配置无通知路由）。以上不伪装为成功。

证据索引（诊断/恢复归档保留）：
- `/tmp/zf-hello-fixes-20261026/evidence/resume-terminal-audit.json`：旧断点终态交付审计 passed。
- `/tmp/zf-hello-fixes-20261026/evidence/approval-held.json`：OA adopt 后精确审批门保持关闭。
- `/tmp/zf-hello-fixes-20261026/evidence/fresh-pipeline-metrics.json`：并发、重叠和审批顺序。
- `/tmp/zf-hello-fixes-20261026/evidence/fresh-checkpoint.json`：新候选恢复身份，run_terminal=false。
- `/tmp/zf-hello-fixes-20261026/evidence/fresh-candidate-*-tests.log` 与 `fresh-candidate-scope.txt`：冻结版本测试与范围。
- `/tmp/zf-hello-fixes-20261026/evidence/dispatch-query-benchmark.json`、`runtime-profile-top.txt`：性能复现和修复证据。
- `/tmp/zf-hello-fixes-20261026/evidence/dev-verify.json`、`baseline-unrelated-failures.log`：自动层失败/中断与基线归因；后续修复回归分别保存，不覆盖原失败记录。

继续新 Run 时，在 `/tmp/zf-hello-fixes-20261026/fresh/product` 使用本工作树 `.venv/bin/zf start`（环境保留 `ZF_WORKSPACE_HOME=/tmp/zf-hello-fixes-20261026/workspace`、`ZF_TASK_PIPELINE_MODE=blocking`、`PYTHONPATH=/home/min/workspace/public/zaofu-hello-pipeline-fixes/src`，PATH优先该工作树 `.venv/bin`）；由现有 event/store/checkpoint 恢复，不补发批准或重造任务。
