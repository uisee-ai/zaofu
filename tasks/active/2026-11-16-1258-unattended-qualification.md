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
