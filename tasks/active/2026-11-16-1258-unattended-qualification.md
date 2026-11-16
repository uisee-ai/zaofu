# 无人恢复 P0/P1 与新 PRD 资格验收

> 状态: active

用户已批准建议中的 P0/P1 实施及真实 PRD 两轮评估。基线 2137a567；独立 worktree unattended-qualification-20261116。规格：docs/spec/unattended-recovery-qualification.spec.md（本地忽略）。前序证据位于 continuity-handoff-20261114/reports/unattended-repair-20261116T1053Z/review.md。实现记录：docs/impl/unattended-qualification.md。

## Backlog / 验收

- [ ] B01 / U01：标准宿主监督部署入口 → verify: CLI 测试、真实用户 service 控制器退出恢复；pause/stop/terminal 拒绝重启；配置与重启前置可诊断。
- [ ] B02 / U02：预授权测试 → verify: 精确合同/源码/依赖绑定、限定预算与命令、旧授权/变更/未决副作用拒绝；调用入口和证据消费测试。
- [ ] B03 / U03：统一 workspace/scratch 权限 → verify: 实际派发说明及原生结果提交无需人工澄清；canonical state 仍拒绝直接改写。
- [ ] B04 / U04：修复链路配置和集成 → verify: AR 补丁、独立验证、受控 apply、加载新源码、恢复原 checkpoint；越界失败关闭。
- [ ] B05 / U05：恢复效果 → verify: 诊断建议/应用不能冒充原目标进展；重复恢复幂等且旧检查点退出。
- [ ] B06 / U06：诊断生命周期 → verify: 阶段/报告落盘，终止不冒充成功，跨进程重启可重建未完成工作。
- [ ] B07 / U07：冻结最终源码，最小真实 PRD 2+2 → verify: 原业务断言及 Docker 浏览器证据，账本到 run.goal.completed；规定故障矩阵与人工介入计数独立记录。

## 执行纪律

先 RED→GREEN，影响闭包、sentinel 和 mock E2E 后真实运行。轮内监工只观察，预定注入脚本记录故障；一旦临时回复/执行宿主命令/恢复/改码则该轮无人资格失败。轮间修复后新轮复验，不抹去历史失败。不存在通过的真实证据不能归档为 done。源仓仅本地提交，未授权本轮 merge/push。

## 进展

- 新 worktree 与依赖准备完成；已确认 source_repair_apply/restart/continuation 现有链路，不重复创建状态机。

### 实现与测试检查点（未宣称全流程完成）

B01–B06 的入口与安全边界已补齐第一批实现，B07 尚待真实 PRD。安装版监督服务第一轮发现 WorkingDirectory 引号错误，修正后 2 个真实宿主测试通过。容器第一轮发现 tmpfs 根目录 cp 保留时间戳无权，改用容器用户子目录后真实执行通过。

影响闭包 1,101 文件：13,059 passed / 19 skipped / 17 failed；在 2137a567 干净导出基线复现其中 16 项，另一 signed_board_delivery 当前单独复测通过。保留 baseline-failures.json/log，不修无关历史失败。最后定向 187 passed、交接 8 passed；sentinel 53、mock E2E 10、serial 10、Web typecheck/unit 通过。非全量通过，非新真实 PRD 通过。

复核还发现旧 self-repair sandbox bypass、修复分支 -B 重置及运行 briefing 污染 worktree；本批移除这些路径，提供默认关闭的受控候选发布，并验证其 CLI/tick 既有调用链。真实 Provider 故障资格仍不能用此处单测替代。

### 真实流程复验与后续修正

实现提交 `7bbd61ad feat: add bounded unattended recovery execution`。后续修正：

- `48274873`：修复验证保留完整影响闭包，不因文件数超过 80 扩展为全仓；27 passed / 1 skipped。
- `45e6f959`：repair → apply → continuation 保留原 RecoveryCase 身份；186 定向通过，影响闭包 4990 passed / 7 skipped，3 项 PATH 环境失败补齐虚拟环境后逐项通过。
- `af1ff7f9`：已完成的失败容器回执也交回独立 Reader；36 passed + sentinel 53。
- `9e1e4393`：Plan/Critic 显式物化原始 source_ref，不能只读包含路径的 Requirement 摘要；35 定向、930 影响测试及 sentinel 53 通过。
- `459bfbb6`：活跃期 repair apply 在修改源码之前核对 operator_approved 重启策略；187 定向、5013 影响测试 / 7 skipped、sentinel 53、mock E2E 10 通过。

第 1 轮隔离真实 PRD 在 Plan 补证阶段停止，NOT_QUALIFIED，未进入资格计时。入口驱动错误传入 `from` 而非 `source_ref` 属于本 session 的问题，不能算作框架故障；正确嵌套 source_ref 的运行时交接缺口另行复现并修复。Planner 自动返工已发生，但不替代完整产品验收。

第 2 轮 `/tmp/zf-unattended-r2-20261116T141114Z` 冻结 `459bfbb6`，使用正式 source_ref 和显式 operator_approved。14:29 UTC Planner 已准入，独立 Critic 开始；尚未批准 Plan 或进入 Impl，B07 继续未完成。详细失败历史、日志与故障脚本摘要保存在 reports/unattended-qualification-20261116/ 及隔离根 receipts/，不能把能力测试计作无人真实 PRD 通过。

第二轮后续：14:36 UTC 同版 Plan/Critic/Preview 审阅批准，开始资格计时。控制器启动窗口异常退出后约 9 秒恢复并并行派发两项 Impl；带未提交测试的 Worker 退出后自动 respawn，原文件摘要保留；结果通知期间控制器中断后，原 blocked 结果正确准入。

本轮未通过：两个 Impl 报告提交要求与 Provider 的“未明确请求不 commit”规则冲突；一个 Worker 因未说明既有 literal apply_patch 适配而停止填写 scratch。OA 在只读完部分输入后结束回合，无结果却持续占据 running operation，Run Manager 已提交的恢复请求无法继续。未到独立 Verify、Candidate 或 F4–F6；没有人工代提交、代测试或宣布完成。

下一修复批次显式传达合同内的本地提交请求、说明已有精确 scratch 适配，且为 OA 正向结束回合但缺结果增加同检查点一次重投；暂停、终态、已准入结果及活动新回合均拒绝重投。RED 已复现，准备以修复后源码从原断点验证，不改写本轮失败历史。

该批定向 136 + 33 passed；297 文件影响闭包 4212 passed / 9 skipped / 1 failed，唯一失败是已在 2137a567 复现的 wake 数量快照（194 vs 193），本批未修改 wake 集合。sentinel 53 与 mock E2E 10 通过。新增说明曾导致 12 KiB briefing 回归，已压缩说明后复测通过，没有放宽阈值。真实断点续跑仍待验收。

`a686f199` 后的 epoch 3 确实自动重投、结算旧 OA operation，并继续 semantic_failure。
OA 正式升级旧提交授权阻塞；它没有凭空推断新源码赋予权限。监工通过现有
`zf recover task-impl` 对两项 settled blocked 操作先 preview 再 apply；两 Impl 已并行续跑。
记录为人工介入，不记无人通过。真实 OS 诊断中断测试证实活 owner 保留、死 owner
只关闭一次、dirty 修改保留、不重放命令；仍不能替代真实 Provider 整条修复链。

旧 pre_impl 结果另因裸阶段 work_unit 被拒绝。补全结果模板的 task_ids/stage_ids，
不降低 execution_task_outside_plan 门禁。RED→GREEN 定向 78 passed；相对已验证
HEAD 的增量影响闭包 172 passed / 7 skipped、sentinel 53、mock E2E 10 passed。
这次方法修正留给下一冻结轮次，未热改运行中的 epoch 3；B07 继续 active。
