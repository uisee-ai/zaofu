> 状态: active

# datal P0–P1：任务输入、评审交付、并行切片与冷环境

用户于本轮明确批准上一答复的 P0–P1。基线 dev `b19a23ee`，真实运行
`workflow-0ea133e63454375e`；38 done、12 未完成，Impl/Verify 容量各8，
实际2 Impl运行，9任务等待依赖、1旧Dataset任务等待Plan修订；整任务DAG宽度3。
本批使用隔离worktree，不把配置容量当成已实现的并行度。

## 验收

- [ ] P0 输入：定位旧任务过量必读的具体来源/selector及版本 -> verify:
  形成完整当前任务、必要共享/上游接口与来源映射，保持AC/命令；新绑定经
  原准入路径生效，旧attempt/已完成任务不被原地篡改，实测读取与首个RED反馈。
- [ ] P0 Plan：修复端口预估漏算报告/上下文造成的重复提交 -> verify:
  用实际失败包复现，新增针对性测试；本轮候选通过真实完整manifest预算门禁、
  独立Critic及同版批准，两个既定gap任务正式准入；不抬高预算或伪造局部范围。
- [ ] P1 并行：对剩余任务的真实依赖、接口版本和写域作有据审查 -> verify:
  P11B/P12C/P13等候选给出可/不可拆的条款理由；可执行差量保留旧新AC映射，
  经过独立评审和受控准入；记录ready数/实际并行数，无强行凑8或减弱集成验收。
- [ ] P1 环境：将已验证的锁定依赖准备纳入正式可重放入口 -> verify:
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

验收尚未全部完成，不得提前归档done。

## 当前落地（尚未全部验收）

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
