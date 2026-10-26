# Hello World 剩余验证与缺陷闭环

> 状态: active

用户已批准继续执行上一轮未验证部分。基于 `a841ef5c`、当前工作树 `zaofu-hello-pipeline-fixes`。继续使用 zf-backlog-batch-closeout；只提交本分支，不合并或推送。上一轮证据及局限保留，不覆盖原失败结果。

- [ ] RV-01 新 Run 完整终态 -> verify: 从 `prod-new-prd-20261026-fix` 的 candidate.ready 恢复，候选 Verify、产品复扫、Judge、blocking OA pre_closeout 以及 Goal/交付审计；核对无重复 Impl、原批准不被重造。
- [ ] RV-02 真实 AR loop -> verify: 实际 Codex/provider 跑过技能预检、内层任务、eval/reflection、loop 终态；若失败，定位并修复后重新验证，不用单元测试代替真实 loop。
- [ ] RV-03 广域确定性回归 -> verify: 先复现两项基线问题并解决正确性/测试基建问题；按分片记录全部 deterministic 测试的结果与范围，host/real-provider 独立分层，不覆盖旧失败。
- [ ] RV-04 连续交接性能 -> verify: 保存新控制进程的处理延迟、任务交接和停顿原因；有明确热点则修复并回归，不将局部查询基准当作全链路延迟。
- [ ] RV-05 收尾 -> verify: 提交实现与验证记录，只有已通过项标 done；停止本轮临时 runtime/tmux/Web，保留可审计产物与恢复依据。

证据目录：`/tmp/zf-hello-followup-20261026T050741Z/evidence/`。前轮详细记录：[已归档批次](../done/2026-10-26-0339-hello-pipeline-validation-fixes.md)。新 Run 保留原目录 `/tmp/zf-hello-fixes-20261026/fresh/product`，与真实 dev 的 8001 和其他运行项目隔离。

## 已复现并修复（等待整轮闭环）

1. 广域基线：缺少 scoped-fast-teardown runbook；任务执行元数据被旧测试误当语义摘要变化。补文档并保留真正语义变化的拒绝断言。
2. AR 实弹首次失败：实验 PRD 路径被 gitignore 拦截（0/1，未注入 stuck）；反思 CLI 将顶层 -a 放在 exec 后，返回参数错误。分别修复精确路径加入和参数位置。旧失败日志保留在 ar-loop。
3. AR loop 原先用 parent_state_dir 的八个已完成任务生成本次失败实验的指标；现从实验配置解析 state_dir，在 cleanup 前绑定。
4. 收尾脚本 fallback 使用失效 --data；改为正确 JSON --payload，并用真实 emit 入口验证引号/换行及精确 session，12 tests passed。
5. 新 Run 的候选 Verify：上轮 fast/graceful stop 留下五个 suspended reader operation，重启只恢复另外三个。增加受控 reader checkpoint redrive；49 项入口/重放/结果回归通过后，仅对该五项执行 recover workflow，保留原审批、request hash、冻结候选与已结算 sibling。

初步验证：基础相关43 passed；AR loop/CLI/reflection35 passed；reader resume/call result49 passed。广域回归使用 dev-verify 四进程按文件调度，单进程地址空间上限12GiB、90分钟测试层预算；该预算不改变产品 long-horizon 配置。

补充验证：recovery-closure 34 passed；AR CLI 最终22 passed；本机 Codex 实际参数解析（--help）通过，真实反思结论仍等 loop 终态。五项 reader checkpoint 受控恢复均返回 applied=1，实际已观察到重新派发。

结构门禁第一次失败（CLI 1005 行）后，将单次实验执行完整职责移至 loop_iteration.py，不增加超限豁免；CLI+结构39 passed，最终 sentinel51 passed。整个批次仍 active，真实终态及广域回归未结束。

首批实现提交：`e8510bf0 fix: recover interrupted readers and isolate autoresearch validation`。

RV-04 取样：直接 py-spy attach 被宿主 ptrace 权限拒绝，未修改权限；改为对真实只读恢复 CLI 做 cProfile。24.3 秒 profiler 样本中 config load 约6.9秒，纯 Python YAML 为主要热点之一。配置主文件/profile_sources/feature 解析改用 C safe loader（保留 safe fallback），相关862 passed、7 skipped。固定96,115 bytes/23 roles/6 stages配置三次实测中位数2.026→0.333秒；仅证明配置加载优化，不把它当作整个 long-horizon 流程的延迟证明。运行中控制进程未为这个优化打断，后续 CLI 子进程可加载新代码。证据：config-loader-before/after.json、recovery-inspect.prof/txt、config-closure.log。

配置优化补充：sentinel51 passed、flow-smoke10 passed。

## AR 第二次实弹：配置限额阻断（保留失败）

`ar-loop-retry` 跑通扫描、Plan/Critic、blocking OA、真实 Impl 派发及受控 stuck 注入。05:33:38 注入、05:33:45 worker.stuck、05:33:46 worker.stuck.recovered（completion_nudge_requested）；dev 随后产出文档提交52a96f08。但05:37 operation因模板 token_budget=1,500,000 被阻断（total_tokens1,559,758，其中cache_read1,473,536）。内层runner检测 run.goal.blocked 后停止；loop结果 incomplete0/1、max_iter_unmet。真实 Codex 反思已返回有效JSON，verdict=unknown是对未完成证据的语义结论，并非命令失败fallback。

这是本次实验遗漏了用户已授权的 YAML 无token上限，不是门禁失效。Run级 goal patch 不能改写已冻结 operation 的上限，保留旧状态与报告，不造成功证据。新建独立 ar-experiment-unlimited，使用 ar-no-token-limit.yaml：仅将execution profile token_budget1,500,000与Run token_budget10,000,000改为0，其他门禁保持原值；没有删除代码限额。修改记录在evidence/ar-config-budget-amendment.json。继续等新loop完整结束。

配置优化提交：`62b10b9d fix: reduce safe config parsing overhead in worker commands`；真实运行配置的 C/Python解析完整对象相等（live-config-parity.json）。

### 06:06 UTC 后续真实发现

- 新 Run 的 8/8 candidate Verify、product discovery 均完成；Judge 因缺少
  同版 Plan 的正式批准时序证据给出 blocked/hold，OA pre_closeout 随后 halt。
  原审批 `evt-7b3c4740d080` 实际早于全部 16 次任务阶段派发，缺陷是必读证据传递。
  修复将包/Run/代次约束的原账本事实纳入 closure fact，未伪造批准或完成。
  focused 25 passed；包含真实 bridge 必读契约与 AR 调用闭包的检查 39 passed。
- 原 discovery `evt-420f9be3d4ef` 的受控 aggregate rebuild 已接受
  `evt-b0f65f078708`；run-resume `evt-81cfd06a70af`。仅重启隔离 fresh runtime。
  Web 不开放该修复动作（404 allowlist），改走既有 ControlledActionService。
- AR 第三次演练的 Critic 揭示 seed 将最终 done 误写进中途产品验收，形成循环依赖。
  两次独立 reject 后停止该隔离实验并保留证据；修正 scenario 产品输入，外层
  故障注入、关联恢复及最终 done 的强制审计仍保留。新实验验证待完成。

- 生命周期/AR 影响闭包：114 passed；sentinel：51 passed；flow mock E2E：10 passed。
- 停机期间提交的 aggregate rebuild 重启后未产生新 aggregate；运行后通过已有
  `stage-retrigger` 重放原 discovery（`evt-7b7eff4f545a`），06:06 已派发新的
  Judge `fanout-prd-lanes-final-evt-8d29743e`。此恢复未产生新的实现/任务验证派发。
  后续查明原 discovery 被历史错误 rework 判为 stale，rebuild handler 因此返回；
  不能据此认定启动 catch-up 丢失请求。真实问题是下文的兼容事件重复返工权限。
- 第四次独立 AR：`ar-experiment-product-seed`，使用修订后的产品 seed 与同一
  token_budget=0 配置，保留全部外层终态与故障恢复检查；进行中。

## 广域确定性回归完整结果

`dev-verify run --base dev --workers 4 --keep-going` 完整结束：Python deterministic
12,291 passed / 20 skipped / 10 failed，1836.45 秒；serial 10 passed、sentinel 51
passed、flow-smoke 10 passed。这里不包含 host/real_provider，也不冒充全绿。

10 个失败中，2 个环境测试硬编码不存在的 `/path/to/zaofu`，2 个周期机制预算
仍为18但现有源码自 a06712238 已为19，1 个 Channel mock 的3秒超时在并行负载
下不稳定。经复现、机制审查及修复，相关15项在四进程复测通过。

另外5项仍未解决：4个 export_public 测试依赖 `tools/export-public.sh`（公开化
提交5fa09bef明确删除），1个 prompt_source_closure 依赖从未进入此公开源码的
`prompt/2026-08-05-1559-kanban-agent-five-workflow-real-e2e.md`。最终独立复测为
5 failed / 1 passed，日志 public-assets-final.log。触发后续处理的条件：公开仓库
发布维护者恢复经过审查的资产，或明确调整公开投影的测试契约；本批次不凭空
重建私有发布工具或历史需求，不删除/skip断言掩盖失败。

## 真实恢复进一步发现：兼容失败事件被误当返工权限

06:06的新Judge被内核取消，原因为05:50已存在的 `task_map.ready` 返工。
追溯至正式 blocked/hold 的兼容投影 `judge.failed(authority=compat_projection)`，
被 Run Manager 的 legacy candidate rework planner 误当第二个语义裁决。
修复在 planner、受控动作准入、reader currentness 三处维持同一边界：兼容投影
不能授权返工；历史错误派生记录不能消耗真实产品失败的重试预算或取消恢复reader。
不修改既有账本、终态结果、候选提交或审批。该发现保留在真实失败记录中。
