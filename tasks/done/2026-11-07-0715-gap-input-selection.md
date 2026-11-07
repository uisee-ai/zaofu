# 防止替代任务丢失输入消费契约并退回全量读取

> 状态: done；实现 `3152497a` — `fix: require explicit input consumption decisions for replacement tasks`

依据：持续监工修复授权；[199](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。

## 现场与责任

TODO-PORT 06:47 派发后约 21 分钟仍在逐页读取；它的原任务有明确 plan_input_selection，仅需要共享输入和自身分区。Discovery 生成两个替代任务时遗漏选择声明，适配器忠实保留了遗漏，按 legacy 规则全量必读：完整 Task Map 267 KB、source_index 1.90 MB 等。原 Task Map/输入正文未丢失，当前问题是消费契约退化。OA/Discovery 未对账前驱输入选择和读取成本；机械准入检查了字段合法性，却未发现声明消失；RM 看到持续读取/心跳而没有该成本异常的恢复动作。

## 验收

1. 对已声明输入消费契约的前驱，替代任务必须显式提供重新审核的选择 -> verify: 真实 build_gap_task_map_amend caller 拒绝遗漏，不静默继承或缩减；原 legacy 任务兼容、显式选择保持原样。
2. 补齐 Discovery/Replan 方法 -> verify: 写明来源/AC/验证责任与字节成本对账，声明可明确扩大到全部需要的端口，不改变 Candidate 全量验收。技能 source/distribution 一致。
3. 真实 TODO 两任务按现有审批/新代次路径修正消费声明 -> verify: 完整 35 项地图保留，仅两任务重新绑定；新 attempt 使用有摘要的共享与前驱分区，原读证据和失败事实保留，其余任务继续执行。不得改写已准入 map/manifest/required policy，不能伪造读取收据。
4. 记录测试、合并加载与实际恢复效果 -> verify: 完成源代码与现场两类证据后归档；若现有控制路径不支持，补合法有界入口，不直接改 canonical JSON。

## 软件验证与现场准备

- 基线回归 **1 failed / 3 passed**，遗漏选择未拒绝；修复后聚焦 **51 passed**，前驱/消费/代次/package caller **44 passed**。两次测试命令误引用不存在的文件而零测试退出，已改用实际路径并重跑，不计通过。
- dev-verify plan 选择 191 个文件，显式运行全部影响闭包 **2,755 passed / 1 skipped**（114.19 秒）；未升级为全量。sentinel **53 passed**、serial **10 passed**、mock flow **10 passed**，ruff/diff check 与技能三份字节一致性通过。
- 现场已准备不可变新 Task Map 与 task_execution_input：35 项全保留，仅两个 TODO task 增加显式选择；保留原输入全部 34 个分区和 shared，新增两个包含当前任务完整定义及前驱正文的分区。原 AC/依赖/allowed_paths/命令逐对象相等。source_index 仅在 DERIVED-02-TODO 增加两个消费者，原记录均保留。尚未发布/批准/派发。
- inspect-inputs 中原 shared refs 未全部枚举到 traceability 的 unknown 继续记录；未宣称全局语义审核全部通过。当前修复通过完整旧正文保留与新合同完整绑定证明没有删除原上下文。真实证据位于 LS-v1 docs/records/2026-11-07-todo-input-selection-proposal.json。

## 实机效果验收

- dev bb6df545 已加载，watcher PID 2216726 / runtime-load/467cc119576d4850a0e95b2dd16a8b96.json。合并后聚焦 56、sentinel 53、mock 10 通过。重载前全部 worker pane/PID 保留；之后新增 pane 为正常新阶段派发。源 worktree/branch 无活跃进程，1,508 个缓存/空锁审计后正常清理，未 push。
- 旧只读 attempt ta-30a8d4c7a8c33c3d3234 通过 WorkflowOperationService.cancel evt-af1c4b4f38ee 退役；evt-372cd9878987 确认 superseded，封存 325,411 bytes 读账本。没有修改旧合同、输入或收据，也未伪造失败/完成。旧 worker 自行结束本轮，工作区零代码改动。
- 第一版新增两个原文分区导致 4,206,182 bytes 超过 sidecar 默认 4 MiB，真实 evt-2fa03cb661bf 拒绝。第二版改为沿已有前驱绑定继承完整原 34 分区输入（sha256 91dfc2bd…），无需复制正文；两新任务当前完整合同、原 shared 与前驱正文全部保留。前驱包来源和两任务选择在实际 package 上复核通过。
- v2 ready evt-dfb37fea993c，package evt-f5e5d1ec7c77，07:31:26 新批准 evt-1ad6d326117c，07:31:51 generation evt-f33b007e35ec；generation 9a2c15e8，仅两项重绑定，其余 33 项逐对象不变。07:32:04 PORT 新 Impl evt-47520e021d04 / op …69c3e2035821 / attempt ta-716a00028a772e49668a / dev-lane-0 实际派发。
- 新 attempt manifest 仅要求 contract、task_execution_input 的 $.shared / $.tasks[8] 与关键输入；没有全量 Task Map/source_index。07:33:42 已有五条真实读取收据，包含前驱共享分区三页。证明按需派发/读取生效，不代表产品已通过。原无关 Verify/返工继续运行。
- 第一版准入失败触发的重复 Discovery 已被新批准代次取代，受控 fanout/operation 取消 evt-790ebb3e635d / evt-dc7f64a0cd53，保留旧失败。当前 LS 冻结技能仍是旧副本；机械门禁已加载，方法修订已进入 ZaoFu 源码和发行副本，不声称旧 session 已消费新方法。

实机证据保存在 LS-v1 docs/records/2026-11-07-todo-input-selection-proposal.json、gap-input-selection-reload-before/after.json、todo-old-attempt-retirement.json、todo-redundant-discovery-retirement.json 与持续监工问题记录。这里关闭输入恢复的软件义务；产品任务仍由独立 Verify 验收，Q09 业务决定仍待用户批准。
