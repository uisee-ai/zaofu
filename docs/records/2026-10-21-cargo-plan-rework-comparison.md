# Cargo Plan 返修与历史计划直接对照

> 评审状态：本轮返修、独立评审和直接对照已完成；精确Plan待人工批准。这是Plan/Task Map层评估，产品实现与产品测试仍为NOT_RUN。

本轮按用户批准执行：定位规划返修中断、修复四类计划问题、独立Critic复核、与既有22任务
Plan直接对照，并停在人工Plan审批。旧Plan直接作为历史参照，没有重新生成旧实验臂。
候选已接受定向反馈并经过返修，因此本报告不是无反馈冷启动A/B或统计显著性证明。

## 精确对象与结论边界

输入为《行泊一体数据闭环工具链_总体设计与UIUX规格_v1.4_需求补齐与验收追踪版.md》及
其51文件配套包。主PRD SHA256为
`e8675f00443b9ee76bef23a15c73faf325b31b68966ca864965c2848f1d6bec7`。
149项要求、36项指标不是整份PRD的全部语义；§17、AT/T12、页面状态及交付条款也在范围内。

| 对象 | 状态 | 任务 / AC / 命令 / 测试条目 |
| --- | --- | --- |
| 历史Plan `evt-477684e8999c` | Critic通过、人工未批准 | 22 / 273 / 49 / 262 |
| 冷启动27任务草稿 | Critic拒绝；返修被运行时缺陷中断 | 27 / 439 / 68 / 383 |
| 首次承接返修 | 宿主磁盘满，未生成Plan | 不适用 |
| 中间Plan `evt-31991deb2ef0` | Critic通过，但operator复现PC-04仍未修复；未批准 | 27 / 482 / 69 / 425 |
| 本轮Plan `evt-6557e6ef9305` | 独立Critic通过、operator正式包复核通过、人工未批准 | 27 / 482 / 69 / 425 |

本轮harness固定在`a05153cbd8ec604dc43ba1f2795e28f6f6d80460`，项目为
`/home/min/workspace/cargo-worktrees/plan-phase-fix-20261021`，Task为`TASK-6A8F85`。
最终包摘要：`ad5d7bcbfe450564c4771a64f0c98e867d10d47794a8404a61d8f843baeea428`。独立Critic事件：`evt-ffaa38f2cc6c（聚合evt-6a061a062714）`。

结论：可以进入精确Plan人工审阅。新计划在接收器分责、事务反例和annotation→embedding
失效链方面更具体；四类已知规划问题在当前正式包中关闭，未发现新的规划阻断。
这证明了这份经过反馈返修的计划更便于实施和检查，不能证明原始生成能力、总体效率
或最终产品效果全面优于旧版。规模与独立Verify时序属于恢复旧版已有能力。

## 四类问题与真实修订

| 问题 | 修订与核对 | 与历史22任务Plan的关系 |
| --- | --- | --- |
| PC-01：真实规模验收混入300秒本片测试 | `VC-qualification-runtime`仅验证本片门禁/插件；`VC-live-scale`承担真实TB/千段/跨OS资格，总timeout14400秒，原业务窗口600秒不变 | 旧Plan已有正确边界；这是恢复能力，不能计为新收益 |
| PC-02：回调来源/签名及重试死信责任不完整 | 25个具名接收器分别承担身份、scope、重放/异体、乱序、耗尽DLQ、重启和恢复重放；拒绝不改业务向量 | 旧Plan已有伪造签名拒绝；新版本把责任展开到各接收域，更便于逐项实施和审查 |
| PC-03：事务动作前后态与权限不足 | 48个实际动作列出最小主体、完整状态向量、逐写失败、幂等、撤权、并发；插件登记/启用/回滚分开 | 比旧版通用failure_invariants更具体；仍须实现真实处理器并执行测试 |
| PC-04：Verify读取自己的未来收据 | 将产品交付检查保留为`AC-assembly-04`/`VC-delivery-contents`；最终独立证据义务留在mandatory `GOAL-DELIVERY`/`GOAL-APPROVAL`，接入既有根目标裁决 | 旧Plan已有正确时序；本轮消除新草稿回归，不据此宣称超越旧版 |

PC-04不是靠`execution_stage=after_independent_verify`实现后置：真实选择器不消费该注释。
中间Plan的命令和AC实际上仍被candidate Verify选择，故其“Critic通过”不构成关闭证据。
新命令仍被candidate Verify选择，但其输入只含候选源码、交付资料及已产生的产品收据，
不要求当前Verify/PAR/根目标最终报告。最终义务的实际路线为：

`verify.passed → flow.discovery.requested → prd-post-verify-discovery → flow.goal.closed → prd-lanes-final/judge-prd`。

`build_goal_claim_set`读取mandatory条款正文；`goal_closure_bridge`提供已准入结果；
`claim_set_issues`检查必需claim与证据引用；完成门通过`bind_canonical_goal_verification`
绑定候选、commit与generation。语义裁决仍由agent承担，没有新增cargo专用运行时分支、
自定义owner、第二套task schema或用额外人工批准豁免最终独立验证。

正式包与中间27任务Plan逐项比较：任务/AC/测试ID均无删除；26个任务、481条AC、424个
测试条目原样保持。只改CARGO-27、AC-assembly-04、TC-assembly-04，68个其他命令原样，
旧收据命令替换为新交付内容命令。25个TC-EVENT、48个TC-TXN、149个T14及29项待决事项
全部逐项相同；根目标claim由11项增至12项（本轮需求增加实际选择核对），原claim未删除。
最终任务图无环/缺失依赖/重复ID/悬空命令/外片RED-GREEN引用。
正式task_map SHA256：`2f0ec974ab0e5aafc5cbf5e85e13ee291f5eaf525f41bf71ad04ec5515288c5d`。

## 六个维度的评估

1. **范围与追溯**：核对来源摘要、149/36声明映射、AT01–20、T12-01–24；另审查§17事件协议、
   §32的未知输入/单源可读/执行与质量状态、回归分母、上下文返回、键盘和多宽度、派生数据权限。
   这些责任存在于任务/执行规格/测试矩阵/PAS；不是根据ID数量推出语义100%覆盖。
   operator对四类修复和上述边界作重点审查；逐源CSV的默认语义状态仍为NOT_REVIEWED，
   不冒称已为每条原文出具独立语义认证。
2. **任务划分与图**：27片按可见业务动作组织，首片有真实登记入口，最终片负责装配；
   任务图及引用闭包机械检查结果见最终facts。最长声明链旧15片、新16片，不能据任务数
   或跨任务引用数减少推断更快。CARGO-13、25、27仍有较大工作量，只有实施数据能检验粒度。
3. **SDD/TDD**：具名RED/GREEN绑定本片命令；局部消费者允许明确标识的TEST_ONLY协议输入，
   最终Candidate必须连接真实生产者。annotation→embedding链增加了具名版本失效、拒绝旧向量、
   重建后绑定新digest及历史引用保持的责任，比旧矩阵更明确。计划里的测试条目不是已运行测试。
4. **Impl/Verify对齐**：本片自检、候选独立Verify、真实资格、根目标证据裁决分别核对。
   本配置没有逐片独立Verify；`task_verify`字段不会凭空创建该阶段。禁止将未来行为塞进本片GREEN，
   也禁止最终完整验收继承本片排除。
5. **独立评审与防回归**：保留中间Critic漏检证据，最终同时看独立Critic、真实选择器和工件差异。
   执行事实报告只报告选择结果，不把确定性代码升级为语义判官。最终评审详情为approve、fix_items为空；仍保留完整Candidate后才有独立Verify、29项外部决定及实物未复核等限制。
6. **效率与环境**：全量记录失败生成、磁盘中断和每轮承接返修；不能只挑成功的一次。
   本轮没有证明耗时/成本改善，也没有证明其他业务上的稳定提升。当前dev/8001未部署这些改动。

## Harness修复及验证

- `5b6ebb2c fix: defer failure closeout while workflow operations are running`：使用现有operation reducer，
  运行中workflow不被附属失败收尾误终止；终态后仍保留owner审批。历史快照误收尾动作从1变0。
  RED为2个行为失败，影响闭包GREEN为379 passed；哨兵51 passed、确定性Flow smoke 10 passed。
- `a05153cb fix: expose actual verification selection in plan reviews`：现有CLI/提交预检报告逐条显示
  command/AC的实际选择owner，并同步规范技能reference及两份分发副本。未改变命令执行选择行为。
  CLI回归RED 1 failed/5 passed；增量dev-verify为75 passed/1 skipped；哨兵51 passed、Flow smoke 10 passed。
- 原生技能解析/依赖物化/reference一致性检查通过。通用skill-creator quick_validate不支持仓库既有
  `stages/dependencies`等frontmatter扩展，记录该限制，未删除这些元数据来迎合通用校验器。
- 已运行dev-verify计划；run_manager保守映射会选择249文件，采用显式379测试影响闭包替代该广泛层，
  自动执行contract/mock E2E层。完整分支计划和增量执行收据均保存；没有执行全pytest或产品E2E。

上述数字来自不同验证轮次，不相加当作去重覆盖数。实际provider Plan运行用于评估规划行为；
它不等同产品build、浏览器或真实数据/硬件资格测试。
最终报告/任务记录增量执行`dev-verify run --base a05153cb`，instruction contracts为8 passed；
`git diff --cached --check`通过。

## 成本、异常与后续边界

| Provider运行轮次 | goal开始至待批/阻断或最后有效事件（分钟） |
| --- | ---: |
| 历史22任务Plan | 68.94 |
| 新27任务冷启动失败 | 51.36 |
| 磁盘中断的承接运行 | 11.80 |
| 中间待批但仍有PC-04的返修 | 36.96 |
| 本轮最终返修 | 31.20 |
| 新方案上述四轮累计 | 131.32 |

最新累计usage按每条series只取最后样本。历史fresh input/output为999,721/108,714；
新方案四轮合计为2,142,557/226,673。combined input分别20,871,977与41,935,325，
其中已含cache，不能再与fresh/cache相加。磁盘中断可能漏报，实际账单金额UNKNOWN；
operator诊断、编码、测试及轮次间等待时间不在这些运行时间中。
因此不能宣称效率提高；这次质量修复支付了明显的返修成本。

首次承接运行遭遇宿主磁盘写满，保留2条不完整原始事件，没有手改或截断账本。经受控停止，
清理可重建缓存/未使用旧镜像后另建项目继续，并增加低空间保护。旧项目、失败现场和全部返修轮次保留。
本轮作用域写入拒绝数量为7次（受控拒绝后完成）；这些拒绝及恢复耗时也属于结果，不能算零摩擦执行。

29项CR/Q/D决定保留；缺真实Schema、媒体/标定/GT、训练、板端、仿真及规模输入时，相关资格仍
BLOCKED/NOT_RUN。人工Plan批准不替代这些业务决定。当前没有运行Impl，没有产品build通过证据。

下一步在用户审阅并批准精确Plan后，从首个可执行纵向切片验证锁依赖安装、真实API/UI和持久化，
再逐片积累TDD/集成证据；全产品构建及真实资格须按最终完整分母验收，不能以首片成功结清根目标。

## 证据索引

本机证据根目录：`/home/min/workspace/public/zaofu-evidence/20261021-plan-regen/`。

- `historical/`：原正式包；`regenerated-draft/`：被拒草稿。
- `repair/`：历史重放、运行时回归、磁盘异常记录、所有轮次usage汇总。
- `repair-retry/`：中间正式Plan与operator实际选择器反例。
- `phase-fix/`：当前注册、Scan/Planner/Critic事件、正式包、机械审查、实际选择事实、差异与方法读取记录。
- 最终预览：[打开本轮待批Plan](http://10.9.100.143:8003/?project=plan-phase-fix-20261021-6cd363ba&page=inbox&plan=evt-6557e6ef9305)。

结束检查（2026-10-21 14:54 UTC）：主dev仍为`5191c9e6`，只保留既有
`docs/design/00-index.md`未提交修改；8001及8003均HTTP200/ok。评估tmux和runtime PID已停止，
仅观察到prd-scan/prd-plan阶段，没有Plan批准事件或Impl派发。未重启、部署或合并到dev。
通过既有token保护API注册了本轮本地预览项目，未发送Plan决定。

运行时修复说明见[实现说明](../impl/failure-closeout-active-operation.md)。
