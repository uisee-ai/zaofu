> 状态: active

2026-11-15 续作：R04-B 显式清理请求/结果持久化与 resume-only CLI 已接线；删除后丢回执、
路径复用、新工作区范围、未交付/占用/损坏账本均有回归。Python/Node 离线实际执行通过，
Go 不在 PATH；完整替换回退及真实 8+8 未关闭。证据：[续作报告](../../reports/continuity-next-20261115-review.md)。

# ZF-SPEC-001 切分、并发与环境复用

用户于2026-11-14明确授权基于 spec 在新 worktree 实施；覆盖 F10/F11。
基线：dev `0625d001`；spec SHA256：`f5a033210875e90fe059236974ca0c4ff2737b7b7fbbf6b7c62f6238bce8c52a`。
来源：`docs/spec/zaofu-continuity-and-handoff.spec.md`；207–209 是设计背景，非目标不自动扩大。

## 验收

- 计划反例审查；8 Impl/8 Verify 独立合格目标并发与2目标窄前沿；环境身份变化失效，准备不能破坏在用工作区。 -> verify: 真实 caller + 定向回归和异常负对照，逐项留原始命令/退出码；已有能力标记已验证保留，不伪称新实现。
- 影响面验证 -> verify: 按 AGENTS.md 执行后端/共享合同/mock E2E 或前端构建与 Docker 浏览器对应层，不用截图替代业务断言。
- 完成对账 -> verify: 记录代码位置、测试证据、资格限制及未完成项；未验收不标 done。

## 执行记录

本轮：setup 准备锁、失败失效及前后输入核验；新增 8 Impl/8 Verify/8+8 与 2 ready 的模拟子进程区间验证。

验证：contracts-final.log、eight-lane.log 与 eight-lane.xml。

2026-11-15 增量：物理工作区安装 mutex、setup/bootstrap 互斥、bootstrap 成功回执、
失败残留拒绝伪就绪、禁止失败后借用其他 checkout venv；`project.setup_inputs` 绑定显式工具/数据身份文件。
818 passed（7 项既有别名 allowlist skipped），281 项调用方回归，sentinel 53、flow smoke 10。
配置与限制见 [环境准备实现](../../docs/impl/continuity-environment-preparation.md)。

未关闭：未声明工具/数据变化的资格，未接线实际执行者与共享依赖目标使用者；真实 DAG/Provider 并发资格。

对账报告：[207–209 / Spec 覆盖](../../reports/continuity-handoff-20261114-review.md)。
实现入口：[实现记录](../../docs/impl/continuity-handoff-spec-001.md)。
首批实现 commit：`2313bf61 feat: harden task continuity and preserve handoff evidence`；未 merge / push / 部署。

## 后续增量：接续与资源占用

新增实际 tmux 原生 Provider 与 stream-json drain 的环境共享锁；安装独占，兼容 setup 只读复用，多 worker 不互斥，不同 worktree 独立。共享依赖目标、主动关闭描述符或脱离 Provider 的工具、SDK 控制器强杀后的孤儿进程仍未完整覆盖。

验证与限制：[本增量报告](../../reports/continuity-runtime-20261115-review.md)。状态保持 active，不将子能力通过视为整项完成。

## 2026-11-15 P0：环境资源与准入

用户已授权下一步优化；Claude 功能降优先级。本批范围：

- 共享依赖按真实目标持锁 → verify: 原生 mock Provider 使用 A，B/source setup 和 source stop 均拒绝修改/删除；两个消费者可以并行。
- 必需环境清单 → verify: 配置往返与注册工作流继承；Task/Role/Candidate 缺输出失败，真实 Task dispatcher 不创建 Provider operation。
- 环境实例回执 → verify: 替换目标或目录使旧启动计划失效；缺目录可通过成功 setup 恢复，未提交候选原样保留。
- 回归与对账 → verify: 影响闭包、配置合同、sentinel、Flow mock E2E；真实 Provider/跨宿主资格不冒充通过。

代码、配置与限制见 [环境资源实现](../../docs/impl/continuity-environment-resources.md)。
B05 仍为 active；未声明外部资源、真实多语言资格和完整环境替换事务未完成。

## 2026-11-15：剩余规格与真实验证

承接用户本轮授权的 [ZF-SPEC-002](../../docs/spec/zaofu-continuity-remaining-qualification.spec.md) R04/R09；原验收与状态 active 保留。

真实多语言环境、完整资源持有者/替换事务、Datal DAG 与真实 8+8 并发仍未完成。本轮两个单次 Verify 并发不充当 8+8 资格。

实现与验证摘要见 [实现记录](../../docs/impl/continuity-handoff-spec-001.md)，本地完整 [缺口与对账报告](../../reports/continuity-qualification-20261115-review.md)。不把局部真实资格当整项完成。

## 2026-11-15：按成功条件收口

用户已批准修正后的实施顺序；本任务保持 active，逐条验收以 [剩余关闭清单](../../docs/impl/continuity-handoff-spec-001.md#2026-11-15按成功条件推进的剩余关闭清单) 为索引。只关闭有真实调用方和适用证据的条款，其他剩余项不因本批提交自动完成。

本轮修正：资源声明、安装、清理和新 writer 接管检查真实进程占用，覆盖关闭继承 fd、父进程退出的孤儿及未登记 cwd 持有者；未知外部资源、持久清理流程和多语言完整资格待验。 详见[修正报告](../../reports/continuity-closure-20261115-review.md)。
