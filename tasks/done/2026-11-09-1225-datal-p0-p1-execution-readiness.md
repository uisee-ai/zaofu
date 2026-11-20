> 状态: done

# datal P0–P1：任务输入、评审交付、并行切片与冷环境

用户于本轮明确批准上一答复的 P0–P1。基线 dev `b19a23ee`，真实运行
`workflow-0ea133e63454375e`；38 done、12 未完成，Impl/Verify 容量各8，
实际2 Impl运行，9任务等待依赖、1旧Dataset任务等待Plan修订；整任务DAG宽度3。
本批使用隔离worktree，不把配置容量当成已实现的并行度。

## 验收

- [x] P0 输入：定位旧任务过量必读的具体来源/selector及版本 -> verify:
  形成完整当前任务、必要共享/上游接口与来源映射，保持AC/命令；新绑定经
  原准入路径生效，旧attempt/已完成任务不被原地篡改，实测读取与首个RED反馈。
- [x] P0 Plan：修复端口预估漏算报告/上下文造成的重复提交 -> verify:
  用实际失败包复现，新增针对性测试；本轮候选通过真实完整manifest预算门禁、
  独立Critic及同版批准，两个既定gap任务正式准入；不抬高预算或伪造局部范围。
- [x] P1 并行：对剩余任务的真实依赖、接口版本和写域作有据审查 -> verify:
  P11B/P12C/P13等候选给出可/不可拆的条款理由；可执行差量保留旧新AC映射，
  经过独立评审和受控准入；记录ready数/实际并行数，无强行凑8或减弱集成验收。
- [x] P1 环境：将已验证的锁定依赖准备纳入正式可重放入口 -> verify:
  新Impl及Verify冷工作区可用，哈希验证、失败非零、无交叉venv；保留网络失败
  实证与缓存来源；安装成功不冒充产品RED/GREEN或独立Verify。

## 实施边界

- 输入选择、任务切片及验收归属由Planner/技能/评审工件表达；Kernel只做
  身份、版本、证据、引用、路径、预算及状态不变式，不硬编码datal领域判断。
- 保留在途P12B/P16B成果和38项done；配置8+8及原mandatory目标不变。
- 历史账本NUL损坏仍有取证案卷，不删行、不伪造备份、不绕过自动恢复gate。
- 只提交本批实现、测试、文档和任务记录；不提交运行态，不push。

## 证据入口

- 真实诊断：`/home/min/uisee/zaofu-echo/datal/diagnostics/reader-triage-20261109/`
- 环境证据：`/home/min/uisee/zaofu-echo/datal/diagnostics/dependency-recovery-20261109/`
- 方法：`skills/zf-plan-execution-readiness/`、`skills/zf-plan-task-map-contract/`。
- 实施记录见 [实施记录](../../docs/impl/datal-p0-p1-execution-readiness-20261109.md)。

四项验收已于2026-11-09 22:03 UTC齐备；下文保留各阶段历史记录，最新结论见文末。

## 实施过程（以下为历史检查点）

- 提交前新增包含报告/附件/冻结规划上下文的非权威成本估计；未知 Critic 输入明确列出。
  TDD 3项RED后转绿，影响闭包571 passed，技能元数据16 passed/1 skipped。
- 复现两个新工作区的原离线bootstrap失败；保留原锁与哈希，加入本地wheel
  override后两个独立环境均通过。自动纳入未来派发仍待合同准入。
- P11B/P12C/P13给出具体可提前验证行为及不可删除的集成依赖，详见实施记录。
- 新Plan完整评审、正式输入版本绑定、真实首个产品RED、差量任务准入尚未关闭。

### 已合入与进行中

首批实现bfd90684（fix: expose complete delivery cost gaps before plan submission），
merge ab9fe996。新Critic已实际派发evt-22d84b82fd17，冻结必读4,188,102bytes；
尚无Critic verdict，不能标记本批done。另补预算诊断阶段匹配和available/required
成本分离，均先复现RED再修复。P1受控差量输入已记录，未改写在途合同。

### 继续实施：六切片与诊断重复交付

- 六切片完整草稿通过Plan preflight（57任务、保留原319 AC/199命令）；保留在途
  两Impl及done。正式独立评审和准入仍未完成，不以草稿数量代替8个运行实例。
- 实际复现并修复成本估计误计已抽离usage、必读机械owner明细重复交付；明细
  保留摘要绑定sidecar，replay兼容原冻结格式。focused 57 passed。
- 当前批次仍active，只有真实准入、独立验证及冷环境调用证据齐全才归档。
- 影响闭包802 passed（含mock E2E）；真实状态+草稿的只读调度forecast分配lane2–7，
  加现有两Impl可达8。forecast不等于正式准入/真实8并发。

### 执行范围与环境入口继续收口

- 旧恢复范围仅2gap，新增owner显式dispatch scope，经rejection→新Plan评审→同版
  批准才更新任务；不覆盖done/active/foreign合同。六片+原2gap+4父任务为12项差量。
- 产品缓存入口已在独立分支提交f5606fcd，基础设施63项通过；两个全新checkout
  在离线环境实测Impl并发锁、Verify入口和venv隔离，详见实施记录。
- 本批仍active：正式独立评审、差量准入及真实8Impl运行尚待验证。

Owner scope影响闭包364 passed（含三个mock E2E），serial10 passed、sentinel53 passed。

- 读取分页耗时进一步定位到活动账本追加导致历史分片重扫；新增RED后修复单次
  CLI内未变归档复用。保留实时取消、签名、替换/rotation校验，仍不改冻结输入。

读取归档缓存影响闭包244 passed（含CLI授权与mock E2E），sentinel53 passed。

- 八切片/十四项差量草稿已补齐并预检：59任务，原319 AC/199命令逐字保留。
  新增量化指标与P0版本阻断独立行为，避免当前两Impl完成后只剩六片可派发。
- 来源校验RED：worker自报operator可冒认新执行范围；CLI写前拦截、受控决定
  kernel来源及resynth来源核验收口。仍须完成实际新Plan批准和真实8Impl验收。

来源校验：先复现CLI负例RED，修复后聚焦27 passed、影响闭包95 passed
（包含三个mock E2E）、serial10 passed、sentinel53 passed。规划器按204文件
数量升级全量，依仓库规则改为显式决策/重规划/CLI/Event来源及其mock调用闭包；
未运行全量或真实provider E2E。

- P0读取继续缩短每页独立进程的重复扫描：新增可选最多4页/次的CLI JSONL输出；
  保留逐页授权、摘要、完整输出和回执。新接口先9项RED再转绿，追加跨页新事件
  可见且历史分片只解码一次的回归；本批仍须完成正式新Plan和8Impl现场验收。

多页读取：9项CLI新行为先RED后GREEN；影响闭包128 passed（含mock E2E、
逐页撤权/篡改/断管、完整provider输出观察及单页/四页resume），serial10 passed、
sentinel53 passed。规划器按134文件数退化全量，依仓库规则运行上述显式边界
及调用闭包，未运行全量pytest或真实provider E2E。


### 监工新增：Critic 通过后的 PAS 重放准入

- Critic 已通过，writer 因 PAS 封装时补入身份字段而误判正文变化，尚未出现新待审批请求。
- 内联/文件重放两项真实调用回归先 RED；统一首次封装和重放的内核身份绑定。
  验收正文变化仍必须拒绝，保留原 Critic 结果，修复后受控重放精确失败事件。
- 验证与真实恢复尚待收口；本批仍 active，不能把当前 2 Impl 或 8 路预测标为完成。

PAS 重放修复验证：两项正向重放先 RED，修复后聚焦24 passed；增加描述符路径后
影响闭包225 passed（含三个 mock E2E）、serial10 passed、sentinel53 passed。
验证规划器按289文件数量建议全量；依 AGENTS 采用明确的封装/重放/恢复/读者调用
闭包，dev-verify run 跳过该数量驱动的 deterministic 全量层，运行 serial 层；
未运行全量 pytest 或真实 provider E2E。

### 继续监工：迟到 OA 决策防护

- 人工拒绝旧候选后，旧 OA 仍可通过摘要检查并重复批准/退回；先复现2 RED，
  再按不可变request源事件与拒绝事件对账，返回plan_candidate_rejected。
- 保留provider独占和真实结果提交；不直接改运行态，也不取消新Planner。
- 本批仍active，真实8Impl、14项准入及产品独立验证未完成。

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


### 真实八路运行与后续监工（2026-11-09 21:25 UTC）

- 新候选完整 Critic 通过（evt-65dcfe5a29a9，准入 evt-80024b037120），同版
  plan evt-b47ee800d5a2 经授权批准 evt-6d5f41a44201；59 项完整任务映射中
  14 项差量正式准入，未改写已完成或在途合同。
- 20:41:57 与 20:43:13 UTC 均核实 8 个不同 Impl task/attempt/role/pane/process，
  心跳身份及 canonical 分配匹配。八片实测必读 200643–255942 bytes，全部覆盖，
  均已产生真实行为 RED→GREEN；配置、预测和读取回执未替代执行证据。
- 后续正常交接独立 Verify；本批仍 active，P19 冷环境修复已正式采用并在
  39a35a7 上通过23项，自测不替代尚待完成的独立 Verify。
- 监工复现多路将外部合同读取与本地证据写入混在一个脚本而被写域守卫拒绝；
  另有二进制记录解码失败、154个 evidence_files 超过每批16条限制。
  补齐两个技能的方法说明并同步分发副本；保留现有守卫、预算与原始证据。
- 现场 receipts 与异常记录位于 datal/diagnostics/parallel-task-admission-20261109/：
  eight-impl-first-observation.json、eight-impl-second-observation.json、
  eight-impl-actual-input-measurement.json、new-slice-behavior-observations.json、
  supervisor-incidents.jsonl。持续监工，不把阶段交接误判为中断。

交付方法补齐验证：dev-verify 文档/技能闭包41 passed、1 skipped；两项技能元数据与三份副本字节一致。此为方法/文档层验证，不替代产品独立Verify。


### 八路运行后的实时监工：调度热点

- 复现重试投影同快照重复折叠Run终态，新增2项RED后改为单次调用内复用。
  两Run与新增block/resume/cancel可见性、无请求不扫描均覆盖，不缓存跨调用状态。
- 真实173939事件同快照输出相同，retry子步骤6.44秒降至0.35秒；整体调度
  仍待加载新代码后观察。保留原始profile和对照receipt，不重写历史事件。
- 三项既有预算测试在原dev复现fixture模式遗漏；显式blocking，保留原拦截断言。
- P16B正式完成并自动解锁Ingest；Metrics独立Verify抓到bool分辨率缺陷并自动
  单任务返工。P19仍待正式独立结果准入，本批保持active。

重试投影验证：130项聚焦通过（含Run准入及Verify重试），76项调用/投影/
多Run/mock E2E闭包通过，dev-verify自动闭包120 passed，sentinel53 passed。
未运行全量pytest或真实provider E2E；真实datal继续运行，产品验收单独跟踪。


## 最终验收与归档（2026-11-09 22:03 UTC）

本批四项验收全部完成；不等同于datal全部59任务完成，后续产品工作继续监工。
实现提交包含：bfd90684 `fix: expose complete delivery cost gaps before plan submission`；
交付方法d8bb1ff6 `chore: clarify bounded work record evidence handoff`；
最终调度修复39390fa8 `fix: reuse terminal scope within operator retry replay`，
已合入dev 8c61467d。其余分阶段提交与RED/GREEN证据保留在上述实施记录。

- Plan：evt-b47ee800d5a2，同版Critic通过/准入evt-65dcfe5a29a9、evt-80024b037120，
  owner批准evt-6d5f41a44201；59任务、14项差量正式准入，保留原319 AC/199命令。
- 输入/并行：20:41:57、20:43:13 UTC真实8 Impl，独立身份/进程/心跳对账；
  八片全部完成实际必读与行为RED→GREEN，详见eight-impl两次观察及输入测量receipt。
- 冷环境：正式实现39a35a755aef59141b8837d67f0b84259608409a；独立Verify
  23项正式测试及9项探针通过，evt-f4180a979cc9，准入evt-286fe9489792，
  集成后done evt-28e7c39e43eb。没有用候选分支自测替代独立验收。
- 最终运行态：46 done、3 in_progress、10 backlog（另23历史cancelled）；
  Metrics独立Verify所发现缺陷已自动进入单任务返工，修复提交456cd504，等待复验。
- 调度修复合并后212项调用/回归/mock E2E、53项sentinel通过；合并前dev-verify
  最终自动闭包128 passed。未运行全量pytest或真实provider E2E。
- 监工独立Verify链：datal/diagnostics/parallel-task-admission-20261109/
  p19-final-independent-verify-chain.json；异常记录supervisor-incidents.jsonl。

限制继续跟踪：历史账本NUL损坏仍使RunManager自动恢复受限；未篡改账本或绕gate。
只读监控负责观察，本session负责取证、修复和受控恢复；不宣称后台脚本能自主改代码。
