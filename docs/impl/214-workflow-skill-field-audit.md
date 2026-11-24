# 214 · 8×8 与 Loshu FMS 的 Workflow Skill 加载复盘

2026-11-24 在独立评估工作树回读六个 8×8 批次和五份 Loshu FMS 构建记录。主对照选 11 月 21 日的 8×8 与引用 Loshu FMS handoff PRD 的 `ls-v1`，其余分别作为历史对照。各次运行保留自己的角色、尝试与版本边界。完整明细可在 **Observability → Runs → 项目 Skill 决策表 → 8×8 与 Loshu FMS 构建复盘** 下载。[逐批次、角色与 Skill 的 CSV](../../web/src/data/workflow-skill-observations.csv)

## 加载决策

| 决策 | 现场依据与实施范围 |
|---|---|
| 保留角色、门控与交接方法 | 把项目原有角色选择交给当前方法目录解析，8×8 主样本的 Planner 有 4 个直接项，展开为 12 项，其中 2 项必读。Impl 从 1 项展开为 9 项，1 项必读。Verify 从 3 项展开为 9 项，2 项必读。Judge 只选 1 项闭环合同。角色包的可用依赖与角色必读是两个数量。 |
| 保留 Verify 矩阵必读 | 8×8 主样本的 Task Verify 有 12 次绑定读取，Candidate Verify 另有 7 次。`ls-v1` 的 Task Verify 有 7 次。历史清单中的矩阵正文与当前版摘要不同。保留必读的直接依据是第二轮对当前版本的隔离复测，按需组两次指定必须读的案例都未提供读见证。[第二轮复测](214-task-verify-skill-evaluation.md#评分器校准verify-复测与组合试验) |
| 保留 Build 的通用按需方法 | Impl 中 `incremental-delivery` 在两主样本有 1、93 次读见证，`source-verification` 有 1、81 次。`ls-v1` 的 93 次包含 67 次原始成功和 26 次原始失败，说明方法用于多种结果的任务，无法从该比例估算提效。按任务性质加载更适合两类项目。 |
| 按角色收窄触发条件 | `zf-workflow-adaptation-boundary` 在两个主样本的 Planner 都没有正文返回见证，在 Verify 却分别有 3、25 次。保留 Verify 的入口，Planner 仅在涉及项目适配与 Kernel 分工时触发。浏览器 E2E 方法在两组 Verify 各有 7、20 次见证，可继续按是否有浏览器验收义务选择。 |
| 优先试验缩小普通 Impl 的方法索引 | `zf-find-simplifications` 在 8×8 资格批次的 16 份 Impl 包和 `ls-v1` 的 41 份 Impl 包中均为同版可用依赖。11 个批次均无符合本报告口径的正文返回。它已是按需方法。值得比较的是普通交付是否需要看到该索引，显式简化任务能否仍然发现它。对照需要同时覆盖普通 build、显式简化和误删反例。 |
| 暂缓合并重叠方法 | Verify 的 checklist 与 `verify-review` 在两主样本分别有 10、6 次同尝试共读。前者检查目标、覆盖与证据，后者负责多维评审与报告。两者也都是角色包声明的依赖。可以试验将共用的证据要求改成单一引用，同时保留各自职责；共读次数本身不能证明重复。 |
| 显式区分声明、必读和实际读取 | `ls-v1` 有 184 份 Impl、119 份 Verify 简报在正文列出了 Skills，旧指标的索引数却是零。本轮在 Task Pipeline 的实际简报写入路径新增 `declared_skills` 与 `declared_skill_count`。评估侧可分别核对简报声明、角色包、由工具返回支持的读取及独立复核。历史记录只读回填。 |

## 证据覆盖

“绑定尝试”需要同角色的派发提示指向同一份经过 SHA-256 校验的简报，简报的 run、operation、attempt、dispatch 四项与尝试账本吻合，且引用能唯一定位尝试。“读见证尝试”还需在该次绑定内观察到针对客户端 Skill 目录的读取命令，并在同一工具调用的返回中找到对应 Skill 文件头。连续会话可以服务多个尝试；同一尝试重复读同一 Skill 只计一次。11 个批次的终态日志共有 22 条记录无法解析，逐文件计数留在原始回填，未据此补记任何终态。工具命令仅支持字面路径与部分列表形式，间接生成路径、原生激活及参考章节的单独读取可能漏计。读取数是可复核下界，未观测保留未知。

| 批次 | 已验摘要简报 | 角色/任务物化清单 | Codex 会话 | 已绑定 / 账本尝试 | 有正文见证的绑定尝试 | 历史 Skill 名 / 有读取见证 |
|---|---:|---:|---:|---:|---:|---:|
| 8×8 资格 11-20 12:29Z | 136 | 65 | 153 | 99 / 188 | 73 | 34 / 26 |
| **8×8 无人值守 11-21 13:28Z** | **91** | **38** | **93** | **71 / 108** | **50** | **32 / 27** |
| 8×8 11-23 15:16Z | 75 | 30 | 61 | 68 / 76 | 40 | 28 / 23 |
| 8×8 11-23 17:11Z | 64 | 29 | 53 | 57 / 65 | 34 | 28 / 26 |
| 8×8 11-23 23:46Z | 2 | 3 | 5 | 0 / 6 | 0 | 17 / 5 |
| 8×8 11-24 00:16Z | 8 | 5 | 7 | 5 / 8 | 4 | 18 / 10 |
| **Loshu FMS · ls-v1** | **358** | **121** | **239** | **329 / 352** | **187** | **31 / 28** |
| Loshu FMS · ls | 69 | 24 | 44 | 51 / 77 | 29 | 31 / 24 |
| Loshu FMS · loshu-fms | 34 | 13 | 25 | 24 / 36 | 12 | 31 / 22 |
| Loshu FMS · echo-fms | 47 | 15 | 38 | 36 / 49 | 21 | 32 / 24 |
| Loshu FMS · fms-v1-r2 | 8 | 5 | 10 | 5 / 8 | 5 | 17 / 12 |

11 批历史并集有 34 个 Skill，其中 32 个有正文返回见证。另两项为 `diagnosis` 与 `zf-find-simplifications`。诊断专用方法的合适分母是出现诊断请求的机会，本次未独立建立这个分母。多个重试、复用的长会话或同一 PRD 的多个构建均不能增加独立项目数。四个较晚 8×8 配置包含本工作树无法识别的新参数，因而先由对应项目编译器只读导出角色、Skills 与模式，再在临时目录展开当前方法包。

现场可绑定读取以当次客户端和模型为准。两个主样本均有多种模型，8×8 见到 Codex 0.167.1 和 0.171.0，`ls-v1` 见到 0.163.0 和 0.167.1。CSV 保留每个角色与方法见证过的客户端/模型组合。历史清单仅能核对留存的入口文件摘要，无法将后来留存的某个 lane 包倒填成每次派发的版本。尝试账本的成功或失败也没有自动折算为 Skill 的准确率、误放行率或节省 token。

## 三份任务回执

以下核对将读见证、账本原判、终态身份和证据边界分开。三个案例都校验了控制结果与调用结果信封的原件 SHA-256。仅凭提交者的自查与终态记录，无法独立确认最终业务验收。

| 项目与案例 | 实际观察 | 能支持的评估 |
|---|---|---|
| `ls-v1` 列表与详情导航端口（`evt-a271066c660e`） | Impl 读取了 `incremental-delivery`，交付可返回列表的导航接口及局部检查。账本记为成功，`dev.build.done` 的四项身份一致。交接明确将真实身份集成和最终 E2E 留给后续阶段。 | 局部交接有证据，完成范围清楚。最终 E2E 成功及单一 Skill 的增益均待独立验证。 |
| `ls-v1` 待办证据缺口（`evt-d66536e912ad`） | 同一方法用于另一条 Impl。局部实现和验收程序已提交，尚缺 Owner 决定与真实集成输入，工作者发出 `dev.blocked`。账本显示失败，终态四项身份一致。 | 有依据地报告阻塞。按账本失败直接给该 Skill 负分，会惩罚保留验收缺口的行为。 |
| 8×8 候选浏览器验证（`evt-70102d2e6402`） | 验证 lane 的派发能关联到矩阵 Skill 的读取；报告说明浏览器运行环境不可用。归档 `verify.child.failed` 的 attempt 字段存的是 dispatch ID，其他三项与账本一致。 | 读取见证可计入。该终态只满足三项身份，终态级评分弃评。该批次矩阵有读见证的 7 个 Candidate Verify 尝试均未能核实终态四项身份，12 个 Task Verify 尝试全部匹配。 |

## 逐项判断

按这些项目的角色选择展开，当前方法目录涉及 39 个 Skill，7 个在相应角色中必读。其余大多已经是按需项。下表的两列计数为主样本中精确绑定、有该方法正文返回的尝试数，跨角色计数，每次尝试仅计一次。星号表示只见于当前展开。静态分取自 NVIDIA SkillEvaluator 1.0.1 对当前 `skills/` 74 项和 `yoke/` 10 项的 `quality-check`，满分 100。任务质量与加载增益仍需独立任务判定及配对对照。[NVIDIA SkillEvaluator](https://github.com/NVIDIA/SkillEvaluator)

| Skill | 静态分 | 8×8 11-21 | ls-v1 | 适用范围与处置 |
|---|---:|---:|---:|---|
| `context-hygiene` | 86.5 | 1 | 0 | 长会话、恢复与上下文整顿时按需 |
| `debugging-triage` | 89.0 | 0 | 12 | 出现故障复现或诊断任务时按需 |
| `diagnosis` | 84.8 | 0 | 0 | 保留诊断专用入口，触发样本不足 |
| `git-evidence` | 87.2 | 0 | 36 | 实现或交接需要提交证据时按需 |
| `grill` | 87.2 | 2 | 1 | 关键需求冲突或规划歧义时按需 |
| `incremental-delivery` | 89.0 | 1 | 93 | 保留通用 Build 按需方法 |
| `source-verification` | 84.8 | 3 | 82 | 保留扫描、实现时的来源验证 |
| `tdd-evidence` | 89.0 | 1 | 23 | 任务需要测试过程证据时按需 |
| `verify-review` | 87.2 | 12 | 12 | 保留评审职责，与 checklist 共用条款可作对照 |
| `vertical-slicing` | 84.8 | 6 | 1 | 按需用于任务切片与交付边界 |
| `zf-browser-e2e-contract` | 90.2 | 10 | 21 | 有浏览器旅程义务时触发 |
| `zf-cr` ★ | 80.8 | 0 | 0 | 代码审查任务专用，待现场采样 |
| `zf-document-reading` | 84.8 | 4 | 0 | 按需取原文与读者合同 |
| `zf-find-simplifications` | 90.2 | 0 | 0 | 试验从普通 Impl 索引移出，保留显式简化入口 |
| `zf-gap-task-synth` | 91.5 | 0 | 13 | 发现缺口需要转化为后续任务时使用 |
| `zf-goal-closure-judge-contract` | 91.5 | 0 | 0 | 保留 Judge 闭环必读 |
| `zf-goal-closure-replan-contract` | 89.8 | 1 | 6 | 闭环要求重规划时按需 |
| `zf-harness-done-contract` | 91.5 | 0 | 45 | 保留实现交接的完工约束，按角色方法引用 |
| `zf-harness-evidence-collection` | 89.8 | 19 | 2 | 需要工件、回执与证据归档时按需 |
| `zf-harness-spec-freeze-contract` ★ | 91.5 | 0 | 0 | 规格冻结场景单独取样 |
| `zf-harness-state-sync` | 87.2 | 0 | 0 | 保留接力与恢复入口，按状态同步需要触发 |
| `zf-harness-verification-checklist` | 89.8 | 20 | 44 | 保留证据核验职责，重复条款进入对照 |
| `zf-mechanical-claim-verifier` | 89.8 | 6 | 1 | 需要复核机械声明时按需 |
| `zf-plan-execution-readiness` | 85.0 | 4 | 2 | 规划可执行性、依赖就绪检查时按需 |
| `zf-plan-task-map-contract` | 80.2 | 4 | 3 | 保留机器合同，短入口与章节目录进入扩测 |
| `zf-prd-plan-synth` | 87.8 | 4 | 0 | PRD 规划合成时按需 |
| `zf-project-adapter-matrix-enrichment` | 89.0 | 27 | 9 | Verify 保持必读，单独验证 Scan 与 Planner |
| `zf-project-suggest` ★ | 85.8 | 0 | 0 | 项目建议任务专用，待现场采样 |
| `zf-refactor-plan-synth` ★ | 89.8 | 0 | 0 | 重构规划专用，待现场采样 |
| `zf-requirement-reconciliation` | 83.2 | 4 | 0 | 需求、验收与来源对账时按需 |
| `zf-research-adaptive-root` ★ | 91.5 | 0 | 0 | 研究任务专用，待现场采样 |
| `zf-verify-gap-producer-contract` | 89.8 | 1 | 6 | 发现缺证据并向后续阶段交接时按需 |
| `zf-verify-rescan-replan` | 88.5 | 0 | 6 | 出现复扫或重规划理由时按需 |
| `zf-workflow-adaptation-boundary` | 84.8 | 4 | 25 | 保留 Verify 按需入口，Planner 限定适配判断场景 |
| `zf-yoke-critic-role-context` | 91.5 | 0 | 0 | 保留独立 Critic 角色必读 |
| `zf-yoke-dev-worker-role-context` | 91.5 | 3 | 35 | 保留 Impl 角色必读 |
| `zf-yoke-orchestrator-role-context` | 89.8 | 0 | 0 | 保留启用的 Orchestrator 角色必读 |
| `zf-yoke-planner-role-context` | 89.8 | 7 | 2 | 保留 Planner 与使用该角色包的 Scan 必读 |
| `zf-yoke-test-evaluator-role-context` | 87.5 | 29 | 25 | 保留 Verify 角色必读 |

## Planner 长合同的独立对照

`zf-plan-task-map-contract` 的规范入口共 67,147 字节、1,160 行。11 月 21 日 8×8 Planner 的留存入口摘要与本轮源码吻合；`ls-v1` 留存的是较早版本。使用 Codex CLI 0.171.0、`gpt-6-luna`、高推理强度，在只读容器中针对当前单个方法完成两轮各 12 次调用。模型收到完整或实验性分章的同一份契约，测试在开头、中部来源规则、末尾评审规则各插入一个随机读取码，每一对共用这些码，答案和评分规则留在容器外。[逐次调用、结果与摘要](214-planner-contract-read-results.json)

- **读取边界。** 四次单独整篇 `cat` 的工具轨迹都保留了三个码，模型四次均交回首尾两个码，中段为 null。对相同位置分三次取段，模型四次均交回三个码。首轮两份回答使用了带键的数组元素，证据回放把码值和输出格式分开；复测明确标量数组后同样复现。该试验定位到模型端可用信息的差异。原始返回日志长度本身无法证明模型接收了整篇。
- **章节放置。** 初版将七章放在入口同级，来源交接的两次候选调用、评审范围交接的一次调用都先寻找 `references/` 或改用未列入试验的查询命令。将章节放进 `references/`、校正原文相对链接后，四次候选交接均判对，目标码都从工具返回并正确写入回答。全文章节重组保留原契约的每段内容；回归测试核对重组及引用。
- **试验范围。** 第二版两组各四次交接的业务结论均正确。来源任务的两次总输入由 157,992 降至 81,407，降低 48.47%；评审任务由 90,305 降至 82,672，降低 8.45%。这些是包括缓存的输入 token。完整组的一次来源任务额外用了 `rg`，严格执行读取协议的配对为 3/4。两种交接是缩小的合成案例，尚未验证完整 Task Map、并行装配、诊断重派或 Claude Code。当前保留规范 Skill，短入口加标准 `references/` 作为后续真实交接候选。

## 复现与证据口径

1. 对每份历史 state 单独运行归档读取器。它校验简报正文，按批次、角色、Skill 统计清单、声明和读见证，记录原尝试账本状态。归档 JSON 中的 `inventory` 保存输入摘要，`witnesses` 可定位到具体会话、尝试和 Skill。本地原始会话与任务正文留在来源目录。
2. 分别在当前方法目录运行上游静态评分，合并时使用同一个源码版本。当前角色只读解析项目的 `zf.yaml`，在临时目录调用 ZaoFu 的实际物化代码，允许直接项拉入声明依赖。涉及新参数的配置可用其对应编译器运行 `--export-roles`，再将角色快照交给本工作树。各项目配置选择与“当前方法库重新物化”的组合是对照条件，历史观察单列。
3. CSV 的 `historic_entry_match` 标明同角色历史清单中的入口 SHA 与当前入口全部相同、部分相同、不同或未知。空的 `historic_read_environments` 表示该角色与方法没有合格读取回执。`witnessed_output_profiles` 给出读见证按 Task Verify、Candidate Verify 等输出协议的拆分。`terminal_binding_counts` 独立报告所指终态事件的四项身份匹配情况。行上的原始成功/失败/替代来自尝试账本；身份不齐的终态不用于确认评分。
4. 按需是否升为必读、从普通角色包移除、正文是否合并，仍按 Spec 214 的选定/保留案例、错误放行、漏读、调用次数与实测成本门槛决定。两类项目的任务规模与模型版本不同，本报告未估算跨项目因果收益。

从仓库根目录用独立评估环境执行。变量指向自有归档及新建的输出目录。

```sh
export PYTHONPATH=src
python scripts/audit-workflow-skill-history.py "$history_state" > "$out/history.json"
skillevaluator quality-check skills -r json -o "$out/quality-skills"
skillevaluator quality-check yoke -r json -o "$out/quality-yoke"
python scripts/compare-workflow-skill-history.py \
  --cohort build "$out/history.json" "$project_config" \
  --quality "$out/quality-skills/skillevaluator-quality.json" \
  --quality "$out/quality-yoke/skillevaluator-quality.json" > "$out/observations.csv"
python scripts/eval-planner-contract-read.py --output "$out/planner" --layout references
python scripts/eval-planner-contract-read.py --output "$out/planner" --replay
```
