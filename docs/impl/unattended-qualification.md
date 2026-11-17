# 受限授权的无人执行资格

对应 [实施任务](../../tasks/active/2026-11-16-1258-unattended-qualification.md)、本地 ZF-SPEC-003 和 [前序实现](continuity-handoff-spec-001.md)。基线 2137a567。

## 已接入的入口

- `zf recover host-unit --name zf-<project>` 生成用户服务；`--install` 安装并 enable，不自行启动未获授权的 Run。`ExecCondition` 调用现有 host-check；意外退出 restart，pause/stop/completed 拒绝启动。重启上限 5 次/300 秒，超限保留 systemd failure，不能宣称无限自愈。
- 用户服务保留明确的项目、state、Python、源码、PATH 和私有 tmux 位置，不持久化 token。节点启动依赖 user systemd；`loginctl show-user "$USER" -p Linger` 可检查是否无需登录启动。真实节点重启必须独立验收。
- 源码修复 actuator 在受服务托管时先核对 MainPID，只使对应控制器异常退出，由原服务接管启动。不会 exec 一个脱离原服务的新控制器。
- 活跃期的 verified checkpoint apply 同时要求 `source_repair.restart_policy: operator_approved`。
  `never_during_active_run` / `next_run` 在修复写入之前阻断并留下原检查点；验证成功不等于获准重启。
- Run Manager tick 在 `auto_execute` 且 `runtime.host_verification.enabled` 时消费已结束的不可用验证。使用现有合同/currentness/资源锁/manifest 和回执通道，不授予 Worker 宿主权限。

## 预授权测试边界

```yaml
runtime:
  host_verification:
    enabled: true
    approval_ref: owner:<具体批准记录>
    image: sha256:<本机已有镜像完整摘要>
    commands: ["npm test", "npm run build"]
    max_executions_per_run: 12
    timeout_seconds: 180
```

默认关闭。只执行上述精确命令与冻结合同的交集，使用固定镜像，不 pull。
默认执行在无网络、只读根文件系统、无 capabilities、受资源上限约束的容器内，非 root UID；
浏览器需要宿主网络时须在 zf.yaml 显式设置 `network: host`，依然不共享 Docker socket/宿主凭据。
`protected_files` 可固定验收脚本摘要，修改或删除会在启动容器前拒绝。
只拷贝审查过的源文件和声明依赖，不挂载 Git、运行状态、宿主 HOME 或 Docker socket。
目标和依赖摘要在调用前后复查；输出和证据保留目标、命令摘要、镜像、授权和来源。
图片/视频等原始 bytes 以带内容摘要的 base64 sidecar 保存，可无损提取；宿主回执不颁发产品 verdict。
一次 tick 至多一条有界命令；中断且无终态的副作用不可盲目重跑。

Candidate Reader 在原冻结目标的预授权采证完成后，使用现有受控 Reader infrastructure retry，
将回执反馈给独立 Reader。Task Pipeline 保留自身 rework 归属。临时宿主 `host-assist` 的 token gate 保持不变。
成功和失败的已完成采证都要交回 Reader，由它区分产品失败与基础设施失败；不能只消费 exit 0，
让失败证据停在原地。未决执行、目标不稳定或缺少退出码仍拒绝自动交接。

## 工作区和诊断生命周期

普通 Task、Candidate 和 OA 派发都明确：产品 checkout 受原 write scopes 限制；Reader 源码/证据只读；
只有 Kernel 提前创建的当前 operation scratch 可通过原生 Edit/apply_patch 原地更新。
scratch 在 checkout 外不是整个 runtime 目录的写授权。canonical state 仍只能经受控 CLI 提交。
原生 apply_patch 仅以 exec_command/Bash 适配提供时，说明明确指向已有守卫支持的单一、
引号 heredoc Update File patch；不放宽目标、添加/删除/移动或动态 shell 写入。
Task 派发消息显式请求合同要求的本地 writer commit，仅限分配分支与路径；
Reader 不因此获得写权限，不授权创建分支、merge/push 或绕过 Provider 拒绝。

OA 的 periodic liveness sweep 同时检查“进程仍活着但 Provider 回合已经结束”。
只有当前 operation 开始后的同一 session/turn 有明确关闭事件、没有更新的活动回合、
没有已准入结果且 Run 未暂停/终止，才保留原输入/operation 并重投一次检查点。
重投复用读取回执与游标，不能跳过尚未读取的正文；再次缺结果则 operation blocked，
释放 OA 队列供后续诊断，不伪造语义 verdict。仍活跃或身份不明的 Provider 不受干扰。

AR 启动前保存包含工作区、命令、阶段、PID/start ticks/boot ID 的 checkpoint sidecar，账本绑定 digest。
resident 重启后只在证实原 owner 已退出时，将悬空诊断明确关闭为 interrupted，保留候选和检查点并升级；
不将 `loop.started` 当作完成，不重放未经新决定授权的命令。已有 self-repair launch/result、验证及合并回执
继续提供后续阶段检查点，未新建另一套任务状态。

`runtime.autoresearch_resident.on_parent_terminal` 支持 preserve（默认）、cancel、continue。
preserve/cancel 终止进程且保留磁盘成果；继续运行需要新的受控决定，不能自动把不完整报告判成功。
continue 仅在主目标完成后移交已运行的 resident，保留其 singleton lock，队列排空后退出；显式 stop/pause 优先。

## 资格记录

证据写入本 worktree `reports/unattended-qualification-20261116/`。必须区分确定性回归、真实宿主能力和完整真实 PRD。
当前宿主、容器测试不能替代新 PRD 到 `run.goal.completed` 的验收。每轮临时干预记录为该轮无人资格失败；
修复后再启动新轮，不用旧轮结果冒充新源码全流程通过。

资格审查也对照批准正文与实际行为，而非只看 Task done。R9 的数字任务在独立 Verify 中仍采用与批准正文相反的边界 oracle；方法层在 `zf-plan-execution-readiness/references/execution-consistency.md` 增加正文条款到下游必读位置的明确对账，并用 `tests/fixtures/plan-counterexamples/cases.json` 保留正反校准例。夹具和技能物化通过不代表语义判断已改善，仍需真实复审和产品断言验证。

## 自动修复的额外隔离修正

旧 self-repair 启动器曾使用 Provider sandbox bypass 参数；本批移除，Codex 固定 workspace-write。
`runtime.run_manager.source_repair.model/model_reasoning_effort` 显式选择模型，真实验收配置 Terra High。
`publish_candidate: true` 为独立 opt-in：只有已退出进程、冻结合同、原 base/修复分支、精确 scope/allow/deny、
空 index、无活动 Git hooks 都成立，才由 runtime 提交候选。Worker 不需要获得共享 Git 元数据写权限。
briefing 移到 required sidecar，避免污染修复 worktree；创建修复分支不再使用会重置已有分支的 `-B`。
提交只是候选发布；独立 verification、受控 apply、源码加载及续跑保持原有门禁。
RecoveryCase 对 validate/apply 的成功保持 open，不能把中间步骤标为原目标 resolved。

修复候选发布扫描冻结 base 到当前 checkout 的完整差量，而非把既有仓库当作待初始化仓库重新导入。未修改的受信历史文件（如凭据拒绝测试样本）不属于本次发布字节；所有新增/修改文件仍经过同一凭据、路径、symlink 和大小检查。暂存前后及提交后均按原 base 重算差量，出现未授权并发变化保留现场并拒绝确认。该快照函数只由内部修复调用传入，外部 manifest 不能选择或关闭扫描；初始基线、普通 host-assist 和容器采证的原全量检查不变。

自动 repair 使用的 `dev-verify` 保留完整影响闭包，不再因为超过 80 个测试文件而扩大为全仓测试。
仍包含直接调用方、共享契约和串行测试；未映射源码仍拒绝。全量发布验证应显式执行，
不能让文件数量触发无关失败并阻断修复。该修改经 81 文件闭包回归先失败后通过。

修复请求 continuation 与合并后的 `workflow-batch-resume` 必须携带同一 `recovery_case_id`
及原 Run/Task/代际/合同版本/目标提交/attempt 绑定。否则实际恢复后会关闭一个新生成的 case，
留下原 case 悬空；跨修复、合并、续跑及 effect 投影的回归覆盖此问题。

真实 PRD 第一轮暴露了原文交接缺失：验收驱动应传 `source_ref`（不是 CLI 风格的 `from`），
Plan/Critic 输入编译也必须从正式 `source_refs.source_ref` 物化原文，而不是只传带路径的
Requirement 摘要。原文与需求快照同时绑定为 required 输入，保留各自摘要；后续源文件变化
不改变已签发正文。受控需求修订继续按既有 amendment 规则替代旧权威输入。

真实续跑还暴露了 OA 示例歧义：`work_unit_id` 是单元标识，不能用裸 `impl` / `verify`
冒充阶段选择器。pre_impl 的结果模板现在展示 `task_ids` 与 `stage_ids`，要求引用
已批准 Plan 的任务。仅改善生成方法；未知任务仍由原准入规则拒绝，不放宽权限。

## 沙箱内 Writer 的受控候选发布

真实续跑中两个 Worker 都尝试了明确请求的 commit，但共享 Git 元数据为只读，
`index.lock` 创建失败。普通 Task 不能只依赖 AR 专用发布器，也不能放宽 Worker 沙箱。

`runtime.task_publication` 提供独立、默认关闭的预授权策略：`enabled`、`approval_ref`、
精确文件清单 `paths`、`max_publications_per_run`（启用时 1..100）。只适用于当前
已准入、已结算 blocked 的 Impl 候选；不会解析错误字符串来推断权限。
有活动回合、暂停/终态、后继 operation、变更合同、分支或源码、越界路径、活动环境、
现有 index、Git hooks、未决 Git 副作用时拒绝发布或保留等待，不执行任意宿主命令。

Run Manager 使用既有 host-assist preview/apply 门禁，核对 signed changed_files、
当前 HEAD、合同范围、预授权清单、完整文件及依赖摘要，发布一次本地候选提交。
回执绑定策略摘要与批准引用。随后经现有有界 Task Impl retry 请求重新检查精确 commit；
不增加 rework 额度、不直接生成 Impl passed，不跳过独立 Verify/Judge。
发布与后续重试分别留痕；退出在 started/completed 之间时明确升级，不猜测成功后重放。

候选发布绑定检查未通过时，控制器日志保留 operation ID 与拒绝原因；这只增加诊断可见性，不将过期任务升级为可执行，也不自动创建人工升级事件。

真实R3暴露候选发布与空闲Provider依赖租约互斥：Provider整个生命周期持共享环境锁，原host-assist一律排他锁，导致任务已settled仍无限等待。修正为Git候选发布、内部隔离容器快照持共享依赖锁；依赖安装/修改持排他锁，仍阻止采证与发布。普通直接宿主验证仍需排他锁，外部action不接收readonly_snapshot能力。共享锁不替代任务身份、完整差异、源摘要、预算和副作用回执门禁。环境忙的发布延迟会记录operation ID，不再静默跳过。

宿主候选发布在preview与apply重新preview时检查Git作者/提交者可解析；缺身份在暂存和effect.started前失败，保留原工作区。运行时不猜测身份、不修改用户全局Git配置。隔离验收仓库初始化需配置本地测试身份，不能依赖监工shell的临时环境变量。

同一Git身份前置条件也覆盖Autoresearch修复候选发布：缺身份在repair publish started和暂存前拒绝，不留下可避免的未决副作用。

## Owner 决策等待页的同版 Plan 预览

Critic 因业务选择等待 owner 时，也须显示它实际审阅的 Planner 输入。引用仅从同一 operation/attempt/fanout/revision 的不可变 contract 与 source manifest 交叉解析，并校验摘要；不能使用最近任意 Plan。该只读解析不授予 Reader 重试或语义通过权限。新 checkpoint 保存绑定，旧 checkpoint 在读投影中补全，保持原审批 request 与 candidate 身份不变；歧义、缺失或摘要篡改继续失败关闭。

隔离容器的 `/work` tmpfs 必须允许执行预置构建工具：Docker 默认 noexec 会使0755的 tsc/脚本返回126。仅临时构建工作区显式 exec；保留 nosuid/nodev、非root、只读源码挂载、无宿主凭据/socket和命令预授权。真实宿主层用可执行脚本验证此边界，解释器直接执行字符串不足以覆盖这一故障。

Autoresearch loop 的单次内部 runner 继承 loop 已指定的 budget_usd，不能因构造参数遗漏回退到500默认值。此修正仅闭合该调用边界，不代表已实现父目标跨诊断累计预算或未定价模型的真实成本计量；这些必须分别核实。

Task Pipeline 终态归档已终止 transport、轮换 session 后，会产生内核 `role.lifecycle.suspended`（reason=task_stage_session_archived，preserve_session=false）。Provider 回合重放必须关闭该实例在此事件之前的残留回合，否则 Candidate Verify 被 provider_turn_active 永久阻挡。只接受内核 orchestrator 的这一销毁事件；保留会话、其他实例/原因及后续新回合均不受影响。终止失败且进程仍活跃时仍保持占用。

resident 启动的 loop 若解析到的控制面配置确实拥有 parent_state_dir，则继承其 workflow.run_limits.token_budget，经 LoopConfig 与内部 runner 显式传递。0 是有效的不限值，不能通过 truthiness 回退到模板的10000000；未指定父配置或状态目录不匹配的独立实验仍保留模板限制。此变更不复制产品的宿主命令、文件发布或修复权限，成本与超时限制也不因此关闭。

同一归属校验也用于诊断执行参数：loop 的单次美元上限不超过父 workflow/global 已配置的正数上限；同名且同 backend 的角色继承显式 model 与 model_reasoning_effort。不同 backend 或未指定模型保留模板设置。仅复制这两个执行参数，不复制工具、写入路径、凭据或发布策略。该上限仍不是跨多次诊断的剩余额度账本，不能用未定价的0美元记录宣称成本已准确约束。

恢复调度的快路径与周期巡检分开：新的受控副作用回执、operation结算、assistance、RM建议/反思及诊断终态，可在下一次标准tick触发既有Run Manager事务，不再先等待300秒Supervisor巡检。沿用已消费事件游标、终态静止检查及原动作授权；RM自身tick记账、usage/heartbeat和未知事件不进入快路径（未知事件仍由周期巡检处理）。每次事务仍只执行原先允许的有限副作用，不能因加速而重放已完成命令。

Provider 回合关闭也触发该快路径：结果可能先结算，发布/采证仍因活动回合延期；关闭事件使其重新检查当前身份与授权。关闭不代表任务成功，也不绕过暂停、终态和资源门禁。回合开始、usage 与 RM 自身记账不因此触发循环恢复。

静止判定遵循既有升级范围：明确 `blocking_scope=side` 的审批只约束其旁路动作，不得让整个产品恢复tick静止。它不会清除/确认更早的Run级升级；未声明范围的旧事件仍按Run级保守处理。现场 failure-closeout-activate 的旁路审批无需监工代批，预授权浏览器采证可以独立继续。

Candidate Reader 已有内核准入的 verification-result.v1 abstained 时，通用 batch 的 reemit_candidate_ready 不得竞争原 Reader 的恢复路径。按同一fanout、operation/request/run、源事件和control_result_ref绑定准入事实，保留现有采证→stage恢复链。原始超时或缺失准入的legacy runtime失败仍保留原有有界重发能力；不将基础设施失败改判为产品通过。

修复派发的合同在落盘前绑定隔离checkout：将源仓内绝对scope规范为相对路径，拒绝越界/上级遍历/symlink逃逸；仅移除精确源仓cwd前缀，不能可靠转换且仍指向原仓的验证拒绝签发。pytest复用当前解释器依赖但设置PYTHONPATH=src，验收实际导入修复候选。briefing提供不可变合同绝对定位；self-repair方法识别父账本内精确kernel授权事件和dispatched合同摘要，不再要求修复分支复制的zf.yaml重新授权。不放宽沙箱，不设置全局授权环境变量，仍需独立验证和受控apply。

独立repair验证器消费同一冻结命令格式：只接受当前受信sys.executable执行pytest以及精确PYTHONPATH=src，拒绝其它解释器、环境和任意Python脚本。合同生产者的测试还须走此验证调用入口，单独启动子进程通过不能证明白名单准入。

受控采证在已结算结果与 Provider 回合结束之间遇到活动 turn 时，应按暂时占用延期，不生成永久人工升级；后续 tick 在同一授权下重新检查。外部 action 仍拒绝忙碌请求，不绕过回合门禁。

自动候选发布须区分 signed changed_files（相对基线的实现范围）与当前未提交差异。无当前差异、也无本 operation 待处理发布回执时不创建 Git effect，不因此升级或请求重试；已有成功回执仍按精确 commit/文件/依赖校验续跑，未知副作用继续失败关闭。

## 恢复时按需读取原始命令观察

Task handoff 冻结前从当前前驱已结算 attempt 收集原生命令输出，要求 kernel dispatch、operation/task/run/attempt、角色、cwd 与 hook causation 一致；结束后的事件和其它任务不会进入索引。小输出和已校验的 raw sidecar 输出分别保存为脱敏不可变观察，交接正文仅携带索引引用。缺失或篡改的大输出显式 unavailable，不能把截断预览当完整证据；不推断退出码、不把 hook 变为业务通过事实。

接手者使用交接正文提供的 `artifact handoff-evidence --operation <当前op>`，选择 `--event` 按1024字符分页；续页必须绑定摘要。跨多次接力用 `--source-operation`，仅沿当前合同 required_reads 摘要与 kernel handoff.prepared 绑定的同任务历史引用读取。接口复用当前 operation 凭据、Task assignee、canonical lease 与输入版本校验，不开放任意账本或 transcript 查询。required handoff 的读取回执不表示原始观察已独立验收。

OA必读配置避免重复表示：只有标准effective-config-snapshot.v1同时提供完整config与yaml/compiled_graph时，必读路径取同一工件的`$.config`；保留完整原始摘要、受控全文访问以及全部控制字段。无重复表示的运行时快照、legacy工件继续`$`。briefing的逐项命令必须与必读策略使用相同JSONPath；原文缺读/截断仍拒绝准入，不增加continuation次数掩盖输入成本。

Reader采证失败快速交接：同一operation、授权策略、稳定目标和允许命令的完整非零退出回执出现后，可直接经原Reader恢复门交回独立审查，不要求继续运行剩余命令来证明一个已知失败。反馈明确列出仍未验证的命令；成功回执仍须覆盖全部必需命令才可触发该交接。未知退出码、不稳定源、无关命令不触发快速交接，不生成任何产品通过事件。

常驻诊断派生的人工升级必须沿不可变来源对账：仅限kernel升级→单一resident建议→已绑定的kernel诊断请求与派发→同Run/Task canonical失败→后续kernel任务终态。该链完整时，旧诊断在待处理投影与静止判定中退场；账本保留原建议和升级，不伪造人工批准。缺失/歧义来源、其它任务或Run、Worker自报完成、普通安全/业务审批均不适用。重试或进程活动本身不是终态。

失败采证交回Reader后，旧代际必须退出后续采证队列：仅由同Run、同fanout派发及失败绑定的kernel candidate.ready继任事实证明已交接。单纯action请求、Worker自报或其它Run/失败不构成完成。旧代际跳过后继续检查新代际，不占住每tick一次副作用的队首；新Reader仍承担全部未验证命令，预算不增加。

Worker援助请求沿用受控CLI的参数边界：单条`zf recover assistance request`的引号内question/tried是字面数据，其中提及rm、write_text不视为执行删除或Python写入。仅此确定子命令纳入已有结果提交识别路径；真实shell替换、组合、管道、重定向仍进入原守卫，CLI自身operation/attempt/lease授权不变。


### Candidate 环境弃权的提交指引

环境/Provider失败导致未进行产品验收时，使用execution_status=failed、verdict=abstained，保留真实故障及可用工具证据，省略product_acceptance_report及其ref/digest；空模板并非合法的产品报告。若已提供报告，仍校验所有必需身份、旅程、断言与证据。预校验针对这组明确状态补充可执行提示，不替Worker判断产品语义、不修改scratch、不放宽通过门禁。恢复后仍必须完成独立产品验收。R7曾自行补齐失败证据并准入，说明不是永久死锁；该改动减少误入完整报告分支的往返。


Reader失败回执还需覆盖聚合等待窗口：首个稳定、同策略、同operation、允许命令的非零退出回执出现后，剩余命令暂停，即使兄弟Reader尚未结算、aggregate failure还没产生。后续tick继续尝试原有受控交接，不抢先生成聚合失败，也不阻塞其它可执行operation。Task Pipeline、未知退出码、未稳定源码和无效绑定沿用原处理。


宿主采证失败接入既有语义诊断：只消费具有 kernel started→completed 因果链、当前预授权策略摘要、不可变 receipt 摘要、manifest/operation/run/task/attempt 身份一致、稳定源码及允许命令的非零退出回执。每个 run/task/target 合并为一个待诊断请求；重复同目标 Reader 派发不算恢复，可信同命令成功回执解除该故障，新候选或目标终态使旧请求退场。诊断入口复用 Run Manager projection→resident 路由，由 agent 判定产品、依赖还是运行器原因；代码不按错误字符串直接授予源码修复，不新增第二套状态机。


隔离验收的Git身份预检须覆盖两个仓库：产品仓库与实际运行的harness仓库。Autoresearch诊断worktree共享harness的Git公共元数据，只设置产品仓库身份仍会导致Flow source preparation失败。部署者须在相应仓库明确配置认可的本地作者/提交者身份；服务启动前在移除临时GIT_AUTHOR_*/GIT_COMMITTER_*变量的环境中分别执行git var GIT_AUTHOR_IDENT和git var GIT_COMMITTER_IDENT。不得为通过验收修改全局Git配置，也不得把仅本次命令的-c user.name/user.email误当成持久服务配置。临时共享worktree可验证继承，检查干净后移除。预检失败应在资格开始前处理；运行期间补配置必须记录该轮资格失败。

## R10：修复收尾的身份与命令合同

真实R10已越过候选自动发布，但独立验证前被 `repair_contract_continuation_drift` 阻断。收尾不能重建一份缩减的恢复身份：保留冻结请求所有字段，包括 request schema、RecoveryCase、task_map_generation、target_commit、active_attempt_id及空身份槽；额外重启/验证策略由runtime追加。冲突仍在不可变合同核对处拒绝，不能忽略身份漂移。回归从真实Git候选的closeout生产者调用合同消费者，并逐字段验证篡改拒绝。

`contract.verification` 是语义说明，不再按 pytest/uv 等前缀猜测为可执行命令；精确命令显式写入既有 `verification_commands`、`validation.commands` 或 `evidence_contract.success_criteria[kind=command_passed]`。每项只含命令，不在分号后拼接自然语言。Resident RM 的输出示例及要求同步更新。已有仅含说明的合同仍保留说明并执行强制影响范围验证，不把说明当作已执行命令；需要指定聚焦测试的调用者必须迁移到上述结构化字段。明确命令继续绑定共享Python与隔离checkout；现有权限/allowlist/影响闭包门禁不放宽。

这两项修正尚需新一轮真实资格验证，不能追认R10通过。R10的两个业务Task已done，Candidate/Judge/goal与源码应用重启未完成。


## R11：Reader 新派发的提交权限

真实诊断 Reader 将正确子运行 `--state-dir` 改为父 `source_state_dir`，被 operation_missing 拒绝；自动重试又把旧“Never retry or re-send”理解为禁止新 attempt 提交。派发说明明确保留给定提交目录，source_state_dir 仅用于只读证据；每次新 dispatch 授权当前 run_id 的一次提交，前次失败不撤销该授权。成功提交仍不可重复，旧身份不可复用；命令错误时保留原命令和错误并结束本 attempt，由既有有界恢复处理。

该修正覆盖 typed 和 legacy Reader retry 的真实 briefing 调用入口；不新增重试额度、不授予父状态写权限、不忽略 operation/attempt/lease 校验。指令回归通过不等于真实 Provider 已遵循，必须另做冻结新源码的真实复验；R11 继续原版本，不热加载此修正。
