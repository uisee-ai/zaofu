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
