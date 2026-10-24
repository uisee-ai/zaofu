# 184/185 最终版本验收与故障修复记录

任务：[最终验收](../../tasks/done/2026-10-24-1247-185-final-acceptance.md)。
证据：`/home/min/workspace/public/zaofu-evidence/185-final-acceptance-20261024T124723Z/`。

## 真实 light 准入失败与修复

冻结 c728b816 的 Kanban 实际 Task `TASK-B95232` 经真实 Codex 建议、浏览器批准，生成 Run `workflow-ef069e8b41472719`。Task Map 继承批准的两文件 scope `counter.py,test_counter.py`，但 light 生成器附加了 `test_path_prefix=tests/`，因此 writer 准入被拒绝。之前仅验证到 Task Map 的测试没有发现此冲突。

修复：单切片 light 继承规范 Task 的明确 scope 时，移除生成器猜测的测试目录约定，保留批准的允许路径、验收、非目标、验证命令以及准入校验。无明确 scope 时仍保留默认约定。双入口 mock 回归扩展到真正消费 Task Map、创建 writer 子任务，增加根目录测试布局案例。修复前新增回归失败，修复后 light/selector 40 passed、1 skipped。

原失败 Run 已暂停、runtime 已停止，未派发 writer；保留原始不可变产物，不覆写其 Task Map。停止后的账本已将原 Run 置为 blocked 终态，受控 cancel 因禁止二次终结而返回 409，未改写终态。修复版本从同 Task 新 generation 的重新预览批准处继续。真正的 dirty writer 中断/同 Run 恢复将单独验证，不能把准入失败重启算作该证据。

## 全分支基线

c728b816 的 dev-verify 选择 813 文件，40 个新进程分片，6 并发，每片 1800 秒/8 GiB，全部执行。10,133 tests：29 failures、19 skipped、无 errors。排除 host/real_provider/perf/serial；后者独立自动层通过，Web、哨兵、三流 mock 同样通过。基线失败原样保存在 `closure-result.json` 和各 JUnit 中，后续回归结果另外记录，不覆盖失败结果。

其余失败涉及故障产品种子误收集、历史计划 fixture 丢失、旧时限断言、mock transport 未报告进程退出、Web 异步投影时序、mock 批准配置与消费者配置不一致、历史 Codex 哈希向量；逐项修复和最终结论见下文。

## 哈希重新资格验证

历史 `793199c9` 改写了十个静态向量；本次没有按被测函数生成期望值。隔离 `CODEX_HOME`，用 Codex 0.163.0 app-server 原生 `hooks/list` 获取两组精确路径的五类 hook `currentHash`，十项与运行时代码及 `e37005f1` 的历史正确值全部一致。原生回执与 SHA256 见 `hook-probe-native.json`、`hook-hash-verification.json`。据此恢复向量并更新已验证版本传感器到 0.163；算法不变。这里只证明 hash identity，真实 hook 执行另外由实际交付事件核对。项目 hooks 发现和信任语义参考 [官方 Hooks 文档](https://learn.chatgpt.com/docs/hooks)。

## 基线修复明细

- 测试选择器：尊重 pytest 对 `tests/e2e/fixtures/` 故障产品的排除，将已知种子变更映射到宿主验收测试；未知 fixture 仍报 unmapped，不静默略过。新增三项回归。
- 计划 fixture：从 `041a9313` 原样恢复在 `6310a5d5` 被删除的 `agent_skills_default/tasks/plan.md`，为 fixture tasks 添加窄 gitignore 例外。
- 配置断言：生产 controller Run 总时限已为 0，更新旧 3000 断言；原有 token、Operation 时限、费用与样本数断言保留。测试项目的零 token/零 Operation 总时限另由隔离 YAML 配置。
- Respawn mock：terminate 后准确报告旧进程已退出，spawn 后恢复 alive，保留 runtime 旧 worker 未退出即拒绝替换的保护。
- Web 详情：该测试验的是列表 slim payload 到详情完整 hydration；显式同步投影后断言，避免依赖异步追赶时序。隔离重跑旧测试通过，未把其当作 API 过滤缺陷。
- 双 lane 验证 mock：将自定义配置写入 YAML 并提交测试产品基线，再走真实批准；不绕过消费者配置一致性和 Ship 干净工作树检查。Judge briefing 新信封使此 fixture 达到约 10.24 KiB；10 KiB 是既有软预算而非交付门禁，测试改为核对最终字节数和超额告警，未扩大预算、删减权限规则或把超额写成未超额。

验证增量：light 与选择器 40 passed/1 skipped；light/writer/准入直接调用闭包 200 passed；Web/respawn/fixture 166 passed（同一中间运行的旧预算断言 13 个失败保留记录，最终预算与 native version 层 66 passed 覆盖）；完整双 lane + briefing metrics 4 passed。所有红灯日志保留，不将基线改为全绿。

## 最终真实验收结果（cfa205ab）

两组都是实际 Codex，没有 provider mock 或伪造终态；三阶段依次为 `fix-lane-0 → verify-lane-0 → judge-issue → Kernel Ship`。原生 multi-agent 禁用，Kimi 未接入。模型任务完成后以账本终态返回，没有为这次运行设置 4 小时总截止。

| 入口 | 规范 Task / Run | 实际交付 main | 完成 / Ship 事件 |
| --- | --- | --- | --- |
| Kanban（含 dirty writer 恢复） | `TASK-B95232` / `workflow-f8367132dbef5bc5` | `471eb33decad178a28d9ee12ef73aa5118ef64d9` | `evt-9b8fb797672a` / `evt-6675894eb19e` |
| Channel 完整 PRD | `TASK-D8B994` / `workflow-1cd9b0f7182f7400` | `082929e66d7e21d60e6cb70b0e8265bb9d1d9054` | `evt-aca41c1a40dc` / `evt-f4932ffc7b60` |

两组各 3 个 Operation、3 个准入结果、唯一一次 Ship；入口 Task 和 writer 子 Task 均为 done（通过 TaskStore 含归档读取）。独立外部 oracle 对实际 main 各执行 7 项检查，全部通过。候选与 main 的 Git tree 完全相同，最终 diff 仅 `counter.py,test_counter.py`；原始需求、AGENTS、README、gitignore 和 zf.yaml 哈希保持不变。每组完整保留 8 条验收。

Channel 先由真实 product-pm 合成 PRD r1，再经独立 16 条评审、Owner 精确 revision/digest 确认；随后由 Kanban Agent 消费该 Channel 的完整 PRD，保留 Channel origin 和 `full_product`，通过另一次明确执行授权、真实浏览器 Plan/Approve 启动。这里验证的是 Channel 来源的任务交付，不把它冒称为单独点击 Channel 的“创建任务”按钮测试。证据：`channel/entry-source/`、`owner-review.json`、`owner-confirm/`、`input-manifest-final.json`。

Kanban 实际中断 PID 1821997，对应 Task `WORKFLOW-F8367132DBEF5BC5-DELIVER-001`、Operation `wop-issue-lanes-impl-fix-lane-0-WORKFLOW-F8367132DBEF-c7cb28554f82`。13:34:38 UTC 确认两源文件均有未提交修改后 SIGTERM；确认旧 provider 及工具进程全部退出，受控 worker-respawn 在 13:34:58 注入恢复。恢复前后补丁 SHA256 均为 `062c416da5c480a12251fe839442074edd5f9150748082615dc30f3b4ab699c5`。恢复后的结果准入同一 Operation，同一 Task/Run 经独立 Verify/Judge 后交付，无重复 Ship。它与之前准入失败后的“同 Task 新 Request”是两次不同事件，不混称同一恢复。

两个原生 provider 运行都观测到五类 hooks。Channel 3 个 SessionStart/3 个 Stop；Kanban 4 个 SessionStart/3 个 Stop（被 SIGTERM 的旧 writer 没有正常 Stop），与真实故障记录一致。

Docker Playwright 使用 `mcp/playwright:latest`，API/UI 在 8123–8126，未操作 8001、未装宿主浏览器。两组保留真实预览/批准网络回执、运行状态、终态 Board 和 Task Detail 截图与 trace。Channel 终态首次通过；Kanban 首次在异步投影追赶窗口读到旧 blocked 卡片，随后 API 和含归档 TaskStore 都为 done。复核探针改为等待可见 Done，`kanban/browser-terminal-r2/lifecycle-result.json` 通过；保留原失败截图及 `terminal-api-snapshot.json`，没有改写 canonical state 或增加 runtime 同步读阻塞。

## 验证边界

- `closure-reconciled.json`：811 个选中文件全有基线分片记录，29 个原始失败逐个对账，最终确定性失败未解决数 0。方法是完整基线加修复影响闭包，不是第二次单进程全仓重跑。
- 增量：40 passed/1 skipped（light/selector），200 passed（writer/light/准入），166 passed（Web/respawn/fixture），66 passed（controller + hooks，包括显式 host version），42 passed（spawn/rotation），4 passed（完整双 lane/metrics），16 passed（文档/指令）。中间错误预算断言等红灯保留，最终对应回归通过。
- 自动层：serial、Web typecheck/unit、premerge sentinel、三流 mock 全通过；不是以 mock 代替真实 provider 交付。
- 本轮未跑 perf、全部 host 传感器、Kimi 或原生 multi-agent。长于 4 小时的真实任务不是这两个 light 用例的证明范围；零总时限的配置与恢复行为已实测，不声称实际运行超过 4 小时。
- 详细真值：每组 `delivery-review.json`、`goal-completion-receipt.json`、`oracle-after.json`、`delivered-diff.patch`。证据目录保留失败、修复、重启、恢复的完整时间线。

## 归档与清理

两组 simulation.done 已通过 EventWriter/模拟生命周期 helper 发出；zf stop 均成功，测试 tmux/API/UI/provider 进程已退出。运行时归档逐成员 SHA256 复核：Kanban 448 个文本成员，Channel 424 个；排除 auth/private/provider-home/Git 元数据、缓存、symlink 和非 UTF-8 运行时文件。产品 Git 历史另存 `product.bundle`，provider transcript 脱敏保存，浏览器 PNG/trace 单独留存。

运行时归档 SHA256：Kanban `654991bb2e19bee9d9e539b12f533a0b7b1e6f8504a6cfb88a513124f5945684`；Channel `a3b7f1e41b29d6e553b951f9b17a3d58b3859fefd2f593ca027985b98d8ff0fb`。`evidence-manifest.json` 记录 797 个证据文件摘要。临时根 `/tmp/zf-185-final-20261024T124723Z` 已删除，最终检查无其下活跃进程；源代码 184 worktree 保留，不 push/merge。
