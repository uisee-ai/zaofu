# Discovery gap 方法与实际技能来源

关联任务：`tasks/active/2026-11-07-1231-gap-skill-source-preflight.md`。

2026-11-07 Privacy Discovery 报告 `evt-a51e8a6939cf` 提出 MEDIA-LIFECYCLE-R1 → PRIVACY-R6，两个任务均缺显式 rolling smoke 标记。真实报告通过 `validate_module_gap_plan_payload` 的 gap 封套检查，但同一任务数组调用 `rolling_smoke_command_errors`（当前 required 策略）返回两条 missing。最终 `evt-4c6a5e3109fd` 拒绝 amendment；12:28:20 的 `human.escalate` / `evt-d79af9d29e04` 证明有界恢复入口已执行并报告 cap exhausted，不能继续增加无界重试。

## 方法变更及边界

仅将既有主工作区方法段补入 canonical `skills/zf-gap-task-synth/SKILL.md` 并同步两个 provider 分发副本：明确 rolling smoke 的具名命令、deterministic/reusable/static-or-runtime 声明；保留结构化验收及 mandatory goal claims；为新增任务补齐必需输入端口分片、来源索引和矩阵责任。命令选择、业务验收和拆分由 agent 决定。没有修改 runtime、自动标记任意命令、放宽 gate 或改写已派发不可变包。

## 为什么主源码更新没有生效

`src/zf/core/skills/provenance.py::find_skill_candidates` 按 state、project、配置 skill_sources 顺序选源；项目显式源优先于外部源。本次 runtime skills manifest 记录 `source_name=project`，`source=skills/zf-gap-task-synth/SKILL.md`，SHA256 为 `860e487b73ce29a21598f33ed79558ad77b075b6c202bc94c4b7b2296cd41e0d`。产品项目源与 Discovery codex-home 副本完全一致：虽有 Split-quality Preflight 标题，但正文仅包含 AC 数量限制。主源码新增段的 SHA256 为 `043c348cd3d8802052b28d23e37fdee270943dc3b3e681055c751b6eaac6d3d9`，不是该项目当时选中的来源。控制面 reload 本身不会把另一个仓库的源覆盖进产品项目。

## 正式部署与新派发核验

1. 从已提交修订提取该技能的完整文件，显式升级产品项目选中的 `skills/zf-gap-task-synth/SKILL.md`，记录原/新 SHA256 与提交号；先检查产品是否有有意定制，禁止按同名批量覆盖。若 state 中另有同名覆盖，先核实实际来源，不能只改低优先级副本。
2. 保留在途 Core operation、attempt、generation、workdir。不要修改冻结 Discovery 输入、旧报告或现有 worker codex-home。本轮需要新的 execution_spec 正文，使用已授权 `candidate-rework-apply` / replan 路线，由 Planner/Critic 提交完整新包，引用真实 rejection/escalation 和已确认诊断，补 rolling smoke 与必需端口；不提高 cap、不伪造 escalation、不重新广泛扫描。`stage-replan-new-generation` 对本次 source 仅重发 Discovery，不能代替完整包生产。
3. 新 dispatch 后检查 `workdirs/<role>/runtime/skills-manifest.json` 的 source/source_name/sha256；检查 materialized_to 对应 SKILL.md 与升级源逐字节相同，并确有新增正文。仅看到同名技能或旧标题不算部署成功。
4. 核实新 briefing 列出该 dependency；检查该次 provider 的实际技能读取证据及其路径，绑定新 operation/dispatch。新的结果必须明确提供命令标记、完整 task-scoped 输入与当前 accepted Candidate 基线；后置准入仍须通过，不因方法已同步而签收结果。
5. 审批前重新核对 canonical done、当前 Candidate、新选择任务集合与 Core 在途绑定；方法升级不授权取消或重新执行无关任务。

## 后续改善，未在本批实施

`SemanticResultSubmitService.validate_plan_candidate` 当前没有 global-rescan 分支；本次 Discovery 使用该 profile，因此现有 `zf result validate` 不提供 gap 策略预检。聚合 `writer_fanout_data` 只检查 gap 封套，`module_gap_plan` 在 amendment 时才执行 rolling smoke gate。建议另批用 pinned workflow metadata 复用既有校验器，增加只读的 Discovery gap preflight；不得用字符串猜测命令语义或自动设置标记。必需端口检查应在真正拥有完整或合成包的边界进行，不能要求诊断角色凭空构造未绑定包。若改提交拒绝行为，必须同时证明原有有界返工链不会被截断。

端口边界证据：`plan_gap_input_partitions.py::rebind_gap_port_partitions` 只对 `gap_tasks_only` 复制已有端口正文，并将新 task id 映射到显式 parent/supersedes 的已有 paths；没有生成新 execution_spec 业务正文的步骤。函数末尾仍逐个调用 `task_input_paths`。因此父分片别名不能凭空补齐新任务的原生分片；本轮采用 Planner 完整包路线，而不再消耗一次相同 Discovery。

该后续批次应先 RED：真实缺标记报告给出两个明确字段错误；合法任务通过；非 required 策略兼容；缺必需新任务分片拒绝；调用不消耗 submit capability、不落盘、不改变任务；实际 CLI/submit caller 与 bounded recovery 闭包保持。当前仅方法文档修订，采用技能分发一致性与实际 materialization 测试验证，不新增对文案逐词打分的测试。
