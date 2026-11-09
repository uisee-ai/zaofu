# datal P0/P1 执行就绪修复记录

关联[活动任务](../../tasks/active/2026-11-09-1225-datal-p0-p1-execution-readiness.md)。
本记录区分代码修复、环境实验和真实工作流准入；后三者不能互相替代。

## P0：提交前输入成本

真实失败 `evt-2b3000888026` 的 Critic 完整必读包为 4,194,524 bytes，
超过 4,194,304 bytes 上限 220 bytes。原 `input_cost` 明确只估算机器端口，
却容易被使用者误读为完整预算。`zf result validate` 现在另给
`input_cost.delivery_estimate`：统计作者报告、可解析附件/Markdown、执行审查、
与请求摘要绑定的冻结规划上下文；按实际渲染摘要去重，中文按字符计算分页。

该诊断始终 `authoritative=false`、`complete=false`；尚未生成的 Critic
合同、当前状态/局部选择、admitted envelope/prepared report 单列为未知。
已知估计超限为 `review_required`，未超限为 `incomplete_estimate`，不声称可派发。
最终冻结 manifest 的预算门禁保持不变。附件缺失和摘要损坏不静默计为零成本通过。
验证入口不产生事件、read receipt 或 sidecar。规划技能同步要求检查这些字段。

新增测试先失败 3 项，再通过；相关入口和影响闭包 571 项通过。
技能元数据 16 passed、1 skipped。此为机械诊断改进，不是独立 Critic 语义通过。

## P0：任务输入版本

原在途 P12B、P16B 仍绑定旧版输入，不改写其冻结 manifest。
对最新候选 `fanout-prd-plan-evt-21238a11` 的七端口 task-local 选择只读测量：
P12B 210,349 bytes/130 selections，P16B 160,127 bytes/83 selections。
此数不含最终全部上下文，不是派发预算或读取完成证据。
当前 Kernel 已支持声明式任务输入选择；下一步应正式审查和批准版本，不能再写
针对 datal 的字段过滤器或让重试偷偷切到新合同。

## P1：可拆范围及必须保留的集成

依据上述候选中的完整 task-map 条款；当前未完成整任务 DAG 宽度 3，
Impl/Verify 配置各 8，不能由容量推导实际可运行数。以下是供 Planner/Critic
裁决的具体切片，尚非批准任务：

| 原任务 | 可以提前实现并独立验证的行为 | 保留在原集成任务的责任 |
| --- | --- | --- |
| P11B | 比较条件判定与差异报告核心；不同 GT/评分器/板卡不出同条件提升，缺值不填 FPS，报告草稿缺证据标记 | 实际 Dataset/Quality、PC/INT8 运行结果接入，HTML/PDF 导出与权限拒绝不变集，实板性能验收 |
| P12C | 六态计分纯行为；空集不为100%，固定分母，20项14PASS/2FAIL/四态各1的80%/87.5%/70%，逐场景不回退判断 | P12B真实结果输入、P11B比较结果、冻结plan_version与重试历史、API/UI/数据库集成 |
| P13 | Case登记、候选组/发生记录/Case区分、三轴标签与历史记录模型 | 回流许可、冻结回归+复核关闭、P0下一版阻断、复发关联及撤权拒绝；不能移除P12C集成依赖 |

原 LOCAL-P11B/P12C/P13 均包含真实 API/UI/worker/数据库责任，不能直接删其依赖。
新增核心片必须提供独立 AC/命令，旧 AC 仍归完整集成片；fixture 不冒充真实数据。
优先让三个新增核心片分别独占 comparison/regression/cases 内的新模块及测试，
后续集成片依赖其精确版本。不要让新旧片同时写共享 api.py/manifest/migrations。
最终由独立 Critic 检查来源、接口、路径及 AC 映射，再通过受控 Plan 版本准入。

## P1：锁定依赖的冷环境实证

从 `1f82931345fa728fd627eb5e3818b4fee989f89e` 导出两个独立临时 checkout，
开始时均无 `.venv`。原 `tools/bootstrap.py --locked` 在离线缓存下均失败：
`av==16.0.1` 的远程 URL 对应缓存缺失。之前本地 wheel 安装不等于此 URL 缓存就绪。

对同一正式 bootstrap 设置 `UV_OVERRIDE=<verified-local-wheels.txt>`，
保留 `--require-hashes`、原 requirements.lock，并开启 UV/npm 离线模式后，
Impl/Verify 冷环境分别 6.23s、6.28s 返回0。两环境导入 av/PIL/pytest/psycopg/
fastapi 成功，venv 各自独立。原锁摘要均为
`00df069a28ffe43d2d7bbb3865751a7934dc298ce41c32d3973d9a68ecdf575a`。

这证明可重放准备方法可行，尚未证明后续 worker 自动采用。准备步骤需要正式纳入
新任务共享环境合同，提供 wheel 来源/哈希/持久路径、Python3.11及失败非零要求，
再以新 Impl 和独立 Verify 的真实派发验收。当前冻结运行配置不热改。
安装与导入成功不计为产品 RED/GREEN。

## 证据及未关闭项

真实项目 `diagnostics/reader-triage-20261109/` 保存 DAG、候选输入成本和问题记录；
`diagnostics/dependency-recovery-20261109/cold-environment-run.json` 保存冷环境
来源提交、命令结果、日志和导入证据。暂存于 `/tmp/zf-datal-coldenv-*` 的实验目录
只用于本次验证，不能当生产依赖缓存。

当前新 Planner `fanout-prd-plan-evt-8e7c080d` 负责保持九机器端口不变的表示修复。
其完整预算、独立 Critic、同版 approve、两个 gap 正式准入尚待验证。
新增并行切片与环境合同需后续受控差量，不能给当前冻结 attempt 临时扩范围。
历史账本 NUL 损坏仍阻挡自动恢复的全局 gate，未删除原始记录或绕过该 gate。

## 12:44 UTC：首批合入及真实 Critic 派发

首批实现 `bfd90684`，合入dev `ab9fe996`；合并后55项focused和53项sentinel通过。
新 Planner 提交已准入 `evt-163f68b9fe5a`，Critic 实际启动
`evt-22d84b82fd17`，operation `wop-prd-plan-synth-trig-evt-8e7c080d-a0a03e72fb2d`。
冻结清单实测4,188,102 bytes、456页，低于原4MiB上限6,202 bytes；
模式仍为full，尚未取得独立Critic verdict及同版批准。

现场另复现 inspect-review 使用 `stage_id` 查询operation，而operation保存
`parent_stage_id`，导致预算误显示0。修正诊断的阶段查找并增强真实CLI回归；
该命令仍明确预算来自当前operator配置，不冒充历史派发时的冻结配置。

同轮观察到 Critic 将 artifact costs 的全部可见11,751,974 bytes称为需要完整
读取。新增 `selected_bytes_scope` 注明该数是所有可见选择；新增 `required_input`
单列冻结必读的行数、去重字节、最低页数和未知行。部分ACL可见或策略未知时返回
null，不能当成零成本。真实CLI/API回归证明无读回执、无越权正文或provider归因变化。

## 下一次受控差量的明确输入

1. 保留当前38个done及在途attempt；已有采样、权限、PC运行结果接口按已集成
   版本引用。当前两个gap先完成独立评审并准入，避免被后续优化反复覆盖。
2. 将P11B/P12C/P13各自核心行为形成独立可验证片，原任务保留完整集成AC；
   新片限定新模块/测试路径，不与集成片并发写API注册及迁移。
3. 原始source_index、mandatory AC、candidate_verify责任保持；新增片从相应
   LOCAL/AT条款细化具名行为及RED/GREEN命令，不能把单元fixture改称实板验收。
4. 新任务的冷环境准备使用原bootstrap，按原锁校验两份本地wheel的hash；
   显式绑定其持久manifest及Python3.11入口。Impl和Verify使用各自venv。
   `UV_OVERRIDE`只替换同版本包来源，不改依赖版本、验收命令或原锁。
5. 输出旧新任务/AC/命令映射与完整输入成本；任务成员变化不能伪装成原合同未变，
   通过当前正式评审策略后才允许派发。

第二批诊断修复：先复现阶段预算1项RED及成本字段2项RED；修复后CLI/API focused
25项通过，影响闭包783 passed，premerge sentinel53 passed。

## P1 并行差量与 P0 诊断交付修正

本轮继续形成六个有真实入口的切片：P11B-CONDITIONS-R1、P11B-REPORT-R1、
P12C-SCORING-R1、P13-TAXONOMY-R1、P13-REGISTRY-R1、P19-LOCKED-ENV-R1。
原比较/回归/Case/交付任务保留全部集成验收并增加前置依赖；新模块、迁移、测试和
证据目录与集成写域分离。原319条AC和199条命令逐字保持，新增18条具名行为AC。
草稿九端口、task_execution_input别名、来源闭包、路径互斥、滚动smoke通过完整
read-only Plan preflight；这不是正式准入或独立语义批准。

v4 TaskPipelineReconciler按空闲池和能力选role，作者owner_role不固定占用实例。
草稿保留独立assembly owner，新增片可复用作者owner，不能据此推断只会运行一个
worker。依赖波次和外部wheel只读路径已补齐，未改实际在途合同。

真实完整预估暴露了诊断自身的重复交付：execution review仍重复列出全部任务的
机械owner选择，而作者成本估计还计入已被正式派发抽出的usage groups。
现在 `plan_execution_delivery` 为成本估计和派发共用纯投影：

- 必读review保留input_binding、完整图、容量、门禁接线、环境事实及计数。
- 每任务命令/AC的机械stage选择保存在原子、摘要绑定的
  `plan-execution-verification` sidecar，带原task-map/policy/generation绑定，
  进入同一Source Manifest供按需读取；原Task Map、AC、命令语义端口不变。
- 成本估计使用与正式派发相同的摘要格式，不写sidecar或read receipt；最终预算仍
  由冻结manifest判定，未知Critic上下文不假装已计入。
- replay先展开校验机械明细比较语义；相同输入保留原冻结inline/detail格式，
  真实命令owner变化仍触发request divergence，篡改明细fail closed。

先复现两项RED（机械明细重复必读、估计与派发正文不一致）及旧格式replay回归，
修复后focused 57 passed。新草稿估计4,012,533 bytes/430最低页，仍为非权威估计；
真实Critic dispatch预算与批准尚待验证。实施草稿和逐项preservation-proof存于真实
项目 `diagnostics/parallel-task-admission-20261109/`。

13:30 UTC，用真实TaskStore/operation/attempt及现行v4配置，仅在内存加入草稿后，
调度器将六片分别分到lane2–7；加当前lane0/1共8，且无backpressure或未满足依赖。
这是可重放forecast，不是实际派发。证据为 `proposal/scheduler-forecast.json`。
影响闭包（含实际preflight/prepare-call caller、旧格式replay和mock artifact-package
E2E）802 passed；未运行全量pytest或真实provider E2E。

## Owner 显式执行范围与冷环境验证

旧恢复请求固定两个gap，`--input-task`只控制阅读优先级，不能扩充writer派发范围。
新增 `zf plan reject <plan-id> --reason ... --dispatch-task <id>`（可重复）及同名
controlled-action字段 `dispatch_task_ids`，将owner选择绑定到真实pending request，
经plan.rejected、replan trigger、冻结规划上下文传到writer选择。只允许新ID或同
workflow的backlog；拒绝active/terminal/异workflow任务。approve不能改范围，
新范围必须重新Plan/Critic/同版批准；命令本身不创建任务、不改依赖或canonical合同。

本次提案保留原2gap，加入6新片及4个集成父任务，共12个执行差量ID。父任务需要
正式更新依赖与写域，不能只加入子任务而保留冲突的旧父合同；38done和2在途不在
更新范围。完整草稿preflight为57任务、0错误，仍不是正式准入。

产品修复独立提交f5606fcd（chore: reuse hash-verified local wheels for isolated
bootstrap），位于datal-locked-env-20261109。cache按原lock摘要寻址，校验版本、
sha、路径逃逸，保留require-hashes和原flock；失败非零，Verify继续调用同bootstrap。
产品基础设施63项通过。两个新clone清除继承UV_OVERRIDE、启用offline：同一Impl
工作区两个并发bootstrap分别10.24/10.35秒成功，独立Verify入口11.04秒成功，
venv隔离、原lock摘要不变。临时clone已清理，证据存真实项目diagnostics的
`parallel-task-admission-20261109/cold-environments.json`。产品提交须经正式
P19-LOCKED-ENV-R1采用及独立Verify；环境验证不抵扣产品验收。

验证规划器因共享入口递归选中448文件并退化为全量。遵守仓库不因文件数自动全量
的规则，使用显式Plan决策/重规划/冻结上下文/writer/contract authority/operation
及三个mock E2E影响闭包，另执行dev-verify的serial与contract tier；未运行全量
pytest或真实provider E2E。

Owner scope影响闭包364 passed（含三个mock E2E），serial10 passed、sentinel53 passed。

## 读取期间活跃账本使历史分片反复扫描

真实read-ledger中的每页host_elapsed_ms约4–14秒。ArtifactContextEvents原来只缓存
整个账本快照，授权和required_reads之间追加一条事件，就重新扫描所有历史分片。
新增回归先复现重复解码旧归档，修复为单次CLI内按path/device/inode/mtime/size
缓存未变的unsigned归档；活动文件仍重新读取，签名账本不使用该缓存，分片替换
或读取期间rotation仍走完整校验/回退。没有持久化授权缓存，没有改变必读清单。

只读真实账本实验：旧版两次读取4.91/3.93秒；新版5.56/0.44秒（第二次确有活动
追加），分别返回87692/87692及87692/87694条保留事件。两个样本负载不同，不能
把此数值当普遍性能保证；确定性回归验证归档不重解码而新取消事件立即生效。
证据位于diagnostics/parallel-task-admission-20261109/reader-view-before.json及
reader-view-after.json。

读取归档缓存影响闭包244 passed（含CLI授权与mock E2E），sentinel53 passed。

## 执行范围事件来源与八切片储备

使用真实 `zf emit plan.rejected --actor operator` 测试复现：worker 来源声明仅凭
actor/request 绑定即可被下游接纳为执行范围。修复为 CLI 在写账本前拒绝带
`dispatch_task_ids` 的自报决定；受控 Plan 决定显式标记 kernel 来源，resynth
同时检查来源、operator、原 pending 请求及 run/plan 绑定。普通历史决定不要求
补写来源，只有新范围变更必须满足来源约束。未改写历史事件或在途任务。

八切片草稿新增 P11B-METRICS-R1、P13-PRIORITY-R1，明确量化指标口径未定时
UNKNOWN、P0 下一版本验证阻断等原始义务；与原六片写域隔离、前置已完成。
完整草稿59任务，逐字保留原319 AC/199命令，新增25 AC/8命令。owner请求为
原2gap+8新片+4集成父任务共14项，排除done/在途。预检通过不是正式准入。
只读调度模拟验证当前两Impl存续及两者完成释放slot两种情形均有8路容量来源；
真实状态仍2Impl+1Critic，本批保持active。最终批准前仍须核验新包语义、digest、
真实冻结输入成本和原mandatory验收责任，禁止将模拟计数作为并发运行证据。

来源校验：先复现CLI负例RED，修复后聚焦27 passed、影响闭包95 passed
（包含三个mock E2E）、serial10 passed、sentinel53 passed。规划器按204文件
数量升级全量，依仓库规则改为显式决策/重规划/CLI/Event来源及其mock调用闭包；
未运行全量或真实provider E2E。

## 单次有界多页读取

新增可选 `zf artifact read --required --pages 4`：默认1页行为不变，范围1..4，
每页维持原1024..8192字节预算并独立输出一行JSON、flush、记录回执。完整调用
最大输出 `pages * page-bytes` 字节；续读使用最后一行的 `next_cursor`，complete
即停止。执行工具须给足输出容量（四页建议至少16000 tokens），消费全部正文后
再继续，截断内容必须补读，不能将生产回执视为理解或历史观察证据。

同次调用复用未变历史分片，每页仍重新校验操作授权、当前required policy及文件
摘要；取消、凭据失效、篡改、管道失败立即非零停止，已成功输出的前页回执保留。
`--resume`只作用于首个页，之后沿返回游标继续；不修改冻结必读范围或身份。

多页读取：9项CLI新行为先RED后GREEN；影响闭包128 passed（含mock E2E、
逐页撤权/篡改/断管、完整provider输出观察及单页/四页resume），serial10 passed、
sentinel53 passed。规划器按134文件数退化全量，依仓库规则运行上述显式边界
及调用闭包，未运行全量pytest或真实provider E2E。


### 监工新增：PAS 身份绑定的重放误拒绝

真实 Critic 已经完成独立评审并获准入（`evt-4a5d2bfbc41b`），但 writer
处理 `evt-be582073225e` 时触发 `evt-98a00e9e02da`，没有形成新的待审批请求。
`product_acceptance_spec` 原始正文与已接纳包只有内核补入的 Run、Plan revision、
Task Map generation 不同；首次封装会绑定这些字段，重放却直接比较未绑定正文。

修复将 PAS 的内核身份绑定提取为同一个纯函数，首次封装与重放都调用它。
重放使用摘要验证后的已接纳包身份，并继续比较完整正文；不忽略验收条款、
mandatory、入口或真实资格要求。保持原包、原语义评审和冻结输入，修复后通过
受控 `stage-retrigger` 重放精确失败的 `task_map.ready`，仍须人工批准才进入 writer。

回归先复现内联/文件两条路径 RED；修复后覆盖描述符重放及四类验收正文变化拒绝。
完整验证结果随实现提交补充。本批仍 active，实际 8 Impl 尚未达成。

PAS 重放修复验证：两项正向重放先 RED，修复后聚焦24 passed；增加描述符路径后
影响闭包225 passed（含三个 mock E2E）、serial10 passed、sentinel53 passed。
验证规划器按289文件数量建议全量；依 AGENTS 采用明确的封装/重放/恢复/读者调用
闭包，dev-verify run 跳过该数量驱动的 deterministic 全量层，运行 serial 层；
未运行全量 pytest 或真实 provider E2E。


### 拒绝旧 Plan 后的迟到 OA 决策

现场旧 Plan 已有 operator `plan.rejected`，新 Planner 正在处理 14 项调整，
旧 `plan_candidate` OA 仍在读取输入。旧准入仅比较当前包摘要；新包尚未
接纳时，迟到的 adopt/revise 仍能作用于已拒绝候选。隔离测试复现两项 RED。

准入现在从不可变 OA request 的 source_event_id 对账 plan.rejected：同一
候选已被拒绝时返回 `plan_candidate_rejected`，不产生新的批准、拒绝或升级。
其他候选的拒绝、不带 plan_id 的记录及其他 checkpoint 不改变已有语义。
通过实际 apply 调用入口测试，而非只测试未接线 helper。

此修复不强杀仍持有 transport 的 provider，不伪造其结果；旧评审自然结束
后由准入拒绝迟到决策，新候选保留独立评审。真实 8 Impl 尚待正式 Plan 准入。

验证：2 RED→39 focused；扩展直接调用闭包146 passed；dev-verify自动闭包
309 passed/7 skipped、sentinel53 passed、flow-smoke10 passed。非全量或真实provider E2E。


### Critic 已结束但派发仍等待 provider_turn_active

真实旧 Critic 已受理结果并空闲，三个更早的回合在原始 transcript 中记录
turn_aborted，但 session tailer 仅识别正常完成；迟到结束只清除最后一轮，
旧轮仍打开，挡住新 Critic。新增2项RED后补充turn_aborted投影，状态为
cancelled，沿既有provider.turn.closed路径只关闭对应turn，不发布任务完成
或语义通过。新回合保持活跃；无turn_id的中断不能释放占用。

恢复必须绑定原始session和turn记录，按读取器重放遗漏事实；不得凭tmux
空闲伪造stop或直接改canonical状态。8Impl里程碑仍未完成，本批保持active。

验证：2 RED→36 focused，reader/恢复/mock E2E闭包171 passed，
dev-verify自动闭包528 passed，sentinel53 passed。首次自动验证8项CLI测试
因PATH缺zf失败，补入现有venv/bin后整组重跑通过，未改生产行为或测试断言。


### 实际八路验收与证据交付方法补齐

2026-11-09 20:41:57 和 20:43:13 UTC，正式批准后的八个 Impl 实例均通过
operation/attempt/任务分配、独立 pane/进程与新鲜心跳核对。同版 Plan 的完整
Critic 已通过，14项差量正式准入；每片实际必读约0.20–0.26MB，全部完成读取并
执行了行为 RED→GREEN。详情及现场 receipts 见关联 active task 的最新记录。
后续 Impl 下降是任务转交独立 Verify；调度投影没有遗漏 ready Impl 或反压。

现场证据交付暴露的方法缺口：外部只读合同与本地写日志混成单个 mutating shell
会触发写域守卫；work-record 并非附件仓库，当前限制每列表16条、record16KiB、
单证据64KiB且只支持UTF-8文本。旧技能未说明这些限制，导致多次重复入口失败。
此次仅补齐开发方法与交接技能，同步两套分发副本；未放宽权限、增加预算、改写
运行态或删除失败证据。大证据按已有合同入口保留，阶段记录分批发布并对账引用。

P19已在正式任务中采用缓存修复，39a35a7上的23项自测覆盖冷checkout、并发、
篡改拒绝及真实安装失败；独立Verify尚待完成，整个P0/P1批次不能提前归档done。

交付方法补齐验证：dev-verify 文档/技能闭包41 passed、1 skipped；两项技能元数据与三份副本字节一致。此为方法/文档层验证，不替代产品独立Verify。


### 实时监工：重试投影重复扫描历史账本

八路 Impl 交接 Verify 后，控制器仍存活但部分结果交接需等待数分钟。只读
cProfile 定位到 `derive_impl_rework_requests` 的 operator retry 子步骤：
同一次投影对同一事件列表重复折叠 Run 终态18次，累计占约20.4秒（含profile开销）。
修复只在一次 `operator_impl_retry_requests` 调用内复用终态/别名结果；下一次
投影重新折叠，受控重试命令仍读取当前状态，不增加跨调用缓存。

经真实调用方新增两项回归先 RED（10次扫描而非1次），覆盖两个Run、最后请求
胜出、新block/resume/cancel事件立即可见；无operator retry时不执行终态折叠。
身份、当前代次、active owner、来源准入、预算与终态拒绝规则保持原样。

同一173939条真实事件快照的只读对比：旧retry子步骤6.441711秒、新0.352407秒，
输出完全一致（该快照无当前可重试任务）；非空多Run及授权边界由确定性测试覆盖。
此为单次局部计时，不代表整轮控制器延迟已解决。取证位于datal诊断目录的
`read-only-pipeline-profile.txt`、`retry-scope-comparison.json`。

验证闭包另复现两个旧测试文件的3项预算失败：其fixture省略blocking模式或沿用
shadow默认，却期待blocking模式预算拦截；在原dev同样失败。仅明确fixture的
`mode=blocking`，保留所有原拒绝、授权一次、禁止修改原verdict断言。

P16B已通过正式独立Verify（固定时钟4项、smoke8、runtime166、12consumer410，
另9项对照探针），21:51 UTC完成后Ingest自动接续。Metrics独立Verify发现布尔
分辨率被当作整数的真实缺陷，内核已自动发起单任务返工；未重启全局Plan。
P19仍等待正式独立结果准入，批次继续active。

重试投影验证：130项聚焦通过（含Run准入及Verify重试），76项调用/投影/
多Run/mock E2E闭包通过，dev-verify自动闭包120 passed，sentinel53 passed。
未运行全量pytest或真实provider E2E；真实datal继续运行，产品验收单独跟踪。


### P0/P1验收收口

2026-11-09 22:03 UTC，P19正式独立Verify通过并集成：实现39a35a7、正式23项
与独立9项探针通过；verdict evt-f4180a979cc9，准入evt-286fe9489792，
done evt-28e7c39e43eb。至此本批输入、Plan、实际8Impl、冷环境四项验收齐备。
详细证据及全部阶段记录已归档至
[完成任务](../../tasks/done/2026-11-09-1225-datal-p0-p1-execution-readiness.md)。

调度修复39390fa8已合入dev8c61467d，最终自动闭包128 passed，合并后212项
回归/mock与53项sentinel通过。datal整体仍46 done、13未完成；本批归档不代表
产品交付完成。Metrics单任务返工、Registry独立验收、Ingest实施继续监工。
