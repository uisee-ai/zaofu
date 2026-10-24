# 184/185 最终版本验收与故障修复记录

任务：[最终验收](../../tasks/active/2026-10-24-1247-185-final-acceptance.md)。
证据：`/home/min/workspace/public/zaofu-evidence/185-final-acceptance-20261024T124723Z/`。

## 真实 light 准入失败与修复

冻结 c728b816 的 Kanban 实际 Task `TASK-B95232` 经真实 Codex 建议、浏览器批准，生成 Run `workflow-ef069e8b41472719`。Task Map 继承批准的两文件 scope `counter.py,test_counter.py`，但 light 生成器附加了 `test_path_prefix=tests/`，因此 writer 准入被拒绝。之前仅验证到 Task Map 的测试没有发现此冲突。

修复：单切片 light 继承规范 Task 的明确 scope 时，移除生成器猜测的测试目录约定，保留批准的允许路径、验收、非目标、验证命令以及准入校验。无明确 scope 时仍保留默认约定。双入口 mock 回归扩展到真正消费 Task Map、创建 writer 子任务，增加根目录测试布局案例。修复前新增回归失败，修复后 light/selector 40 passed、1 skipped。

原失败 Run 已暂停、runtime 已停止，未派发 writer；保留原始不可变产物，不覆写其 Task Map。停止后的账本已将原 Run 置为 blocked 终态，受控 cancel 因禁止二次终结而返回 409，未改写终态。修复版本从同 Task 新 generation 的重新预览批准处继续。真正的 dirty writer 中断/同 Run 恢复将单独验证，不能把准入失败重启算作该证据。

## 全分支基线

c728b816 的 dev-verify 选择 813 文件，40 个新进程分片，6 并发，每片 1800 秒/8 GiB，全部执行。10,133 tests：29 failures、19 skipped、无 errors。排除 host/real_provider/perf/serial；后者独立自动层通过，Web、哨兵、三流 mock 同样通过。基线失败原样保存在 `closure-result.json` 和各 JUnit 中，后续回归结果另外记录，不覆盖失败结果。

其余失败涉及故障产品种子误收集、历史计划 fixture 丢失、旧时限断言、mock transport 未报告进程退出、Web 异步投影时序、mock 批准配置与消费者配置不一致、历史 Codex 哈希向量；修复和最终结论待逐项补齐。

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
