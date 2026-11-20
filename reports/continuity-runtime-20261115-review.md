# 连续执行增量：Claude 回合接续与环境占用

> 状态：本增量实现并定向验证；B04/B05/B06 保持 active，整份 Spec 未完成。

承接 [前批交接回执](continuity-handoff-20261115-review.md) 和
[环境准备资格](../docs/impl/continuity-environment-preparation.md)。
分支 `wip/codex-20261114-continuity-handoff`，本批基线 `f48ace57`。
对应 Spec F06/F09/F17 的接续及 F11/F13 的资源占用；不改变 Datal 暂停状态。

## 修复的问题和实际入口

| 问题 | 实现与调用方 | 验收边界 |
| --- | --- | --- |
| Claude stream-json 回合依赖内存通知，控制器恢复后无法凭持久事实判断接续 | `StreamJsonTransport._send_task_sync` → `run_stream_turn` → EventWriter；`Orchestrator.run_once` 的 idle replay 消费 | 仅身份完整的 operation-v2 Task Pipeline 调用；原 attempt、dispatch、lease 不变 |
| 关闭事件不足以证明当前调用已结束 | 绑定 operation/attempt/dispatch/lease/instance/run/task/session/turn 与时间，检查当前 operation 状态 | 无最终结果、错误/异会话结果、旧身份、未闭合回合、未知/未决工具不接续；业务完成仍由原 gate 判定 |
| 安装器互斥，但实际 worker 没有占用保护 | tmux Codex/Claude 启动命令经 stdlib exec wrapper 继承共享锁；stream-json 在 SDK drain 期间持共享锁 | 本机 advisory lock；多个读者可共存；不授予完整跨进程隔离资格 |
| 共享锁会误阻止兼容环境复用 | `run_project_setup` 在共享锁内只读校验就绪 marker 和输入摘要 | 相同身份不重装；force、缺失/改变输入均需独占锁；不同物理 worktree 独立 |
| 清理只依赖状态计数，无法看到实际使用者 | Role/Task cleanup、`zf stop` 在保留检查到 Git remove 整段持独占锁 | 持有者存在或锁不可用时保留；仍保留脏成果、ignored 成果和未集成提交的既有检查 |

`provider.turn.opened/closed` 是结构化调用事实，不是第二份任务状态。
新 opened 事件只观察，不额外唤醒；closed 沿用原唤醒路径。
异常记录恢复责任与下一核查动作，不写异常正文、prompt、native transcript 或凭据。
完成接续仍遵循发送前持久 claim；未知发送效果不自动重发。

## TDD 与验证

- RED：实际 stream transport 未产生可重放关闭事实；真实 mock Provider/长 stream query 执行时安装器仍可运行；有占用者时 stop 仍删除工作区。
- 第二轮 RED：加执行锁后，兼容 setup 也被误阻止；继续检查发现现成 provision 链接与合格 bootstrap 同样受影响。增加只读复用路径，缺失依赖/变更输入仍须等待。安装器持独占锁时保持原 TimeoutError 合同；曾发生异常类型回归，已修复，未弱化旧测试。
- 初始 stream fixture 带未关闭的 Codex 回合，误将它当成 Claude 的生产缺陷；已显式关闭 fixture 回合，未放宽生产侧未知执行检查。Role 清理测试也修正为真实 worktree 模式，未将 dry-run 目录缺失算作产品缺陷。
- 最终后端影响闭包 **396 passed**：stream transport、完成接续、progress、provider outcome、call result、tmux、setup/provision、Task/Role workspaces、stop、Candidate、SpawnCoordinator、autoscale/retirement、Event/Lock 合同。
- 此前入口/结构复核 **38 passed**，包含真实 mock 子进程与注入 SDK 查询。最终 396 项已经包括新增 provision 复用测试及结构检查，历史多轮数字不相加。
- 原 sentinel 首次为 52 passed / 1 failed：stdlib 包装器以动态路径执行，静态调用方检查未识别。改为导入模块后取得其入口路径；未放宽检查或添加白名单。
- Flow mock E2E **10 passed**。最终自动 sentinel **53 passed**，结果见同目录原始 dev-verify receipt；没有执行全仓 pytest、真实 Provider/宿主 tmux 或 Datal 产品验收。
- `dev-verify plan --base dev` 检查了累计变更；本轮基线 `f48ace57` 的计划同样审阅。planner 因共享合同映射选中宽全仓层，本轮以列明的影响闭包替代 deterministic/serial；Web 无新增改动，沿用前批已记录的构建/浏览器证据。本轮自动执行 contract/mock_e2e，跳过项不计为通过。
- 一次测试命令误引用不存在的 `tests/test_project_setup.py`，退出 4、零测试；改用实际存在的 worktree_env/setup_concurrency 用例，最终影响闭包通过。

原始日志、命令、退出码与源码摘要保存在本地 `reports/continuity-runtime-20261115/`。
该目录是未入库验证材料；本报告与 impl/task 链接入库，不将临时运行状态或大日志混入源码。

## 仍未完成及原因

1. **F02/F03 活跃输入修订事务**：需要保留原不可变 request、停止旧执行、受控创建新输入代次并重新绑定读取证明；不能原地修改正在执行的输入冒充完成。
2. **F05 跨 Provider 真实接力与旧 writer 隔离**：交接与接收已落地，Claude 完成回合也可接续，但未证明任意会话/CLI 版本转换、旧执行物理隔离及真实接管。Spec OQ-02 尚未指定正式资格组合。
3. **F06/F09/F17 完整无人监工**：未闭合回合、未知外部效果仍保守阻塞；宿主独立故障域、所有失败类型自动核查和全局子调用消费旅程未在本批完成。写入 recovery_owner 不等于 Run Manager 已完成修复。
4. **F11 资源持有者全集**：共享依赖目标、主动关闭继承 FD 的 Provider、独立后台工具和未接线进程仍未覆盖。stream-json 锁随控制器存活；控制器强杀后孤儿进程安全不能由正常 drain 测试推断。
5. **F12/F13 收尾完整性**：本次只是持有者互斥，不是 durable cleanup/部署全阶段事务；Task cleanup 的直接测试不代表已有完整生产调用链。视频/上传补交不重入 Impl 的整链资格尚缺。
6. **F14–F16 全量材料阅读体验**：人类材料目录、后端按需正文、重连与大材料真实完整性旅程仍待验收。
7. **F18 Datal 原始产品对账**：本轮未恢复 Datal；正式数据、硬件和 AutoDataLoop v1.4 的产品资格不能以 harness 单测替代。

这份记录完成本增量的实现对账，不将“局部能力通过”改写成“18 项规格全部完成”。
没有合并、推送或部署；继续保留原 worktree 与 active 任务。
