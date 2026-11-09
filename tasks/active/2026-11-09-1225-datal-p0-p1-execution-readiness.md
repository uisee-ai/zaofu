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
