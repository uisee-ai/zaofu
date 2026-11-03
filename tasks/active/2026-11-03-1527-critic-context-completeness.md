# 按需审查上下文完整性保障

> 状态: active

来源：设计 198 与用户批准的五道保障；接续 `a0490566`，不得改写已有 attempt 或凭旧 session 推断当前记忆。语义适用范围由 Planner/Critic 的证据决定；Kernel 只验证绑定、集合、权限、版本和消费，不新建 Task 真相。

## 范围与验收

1. 来源/需求/任务/验收映射 -> verify: 复用 source_index、coverage_ledger、Task Map 和现有规划技能，明确逐来源责任、负面要求与未映射项；缺失与不完整不得视为无关。
2. 影响闭包 -> verify: 新旧 Task Map、受影响任务及前置/下游声明在既有受控范围合同内对账；未知任务、共享/全局变化或声明外变更回退完整输入。
3. 本轮必读正文 -> verify: 增量仅在可验证范围成立时选择共享规则、受影响任务与依赖的旧新原文，仍完整保留原始需求、Task Map、固定矩阵和独立 Critic；不得仅凭旧 verdict、摘要或差异省略这些原文。
4. 版本与消费 -> verify: 新操作绑定当前 manifest、selector 和策略；本轮缺项/截断拒绝准入，旧 session 及读取回执不能冒充当前消费；不修改已固定运行中合同，无法兼容须受控恢复。
5. 回退与独立验证 -> verify: 原始资料缺失、索引不完整、scope/证明未知、跨任务关系未覆盖时完整回退并可诊断；独立反例对照共享规则、未变合同、异常与设计材料，真实 Provider 质量单列，不将确定性测试称为语义保证。

此前 LS 模拟只有差量与旧审查证据，未建立当前任务原文的必读闭包，不能作为完整性的证明。LS 暂停；不部署或改写线上状态。

## 实施与已验证边界

- 原先“只凭旧 verdict 与结构差异缩减当前必读”的路径关闭；完整差异仍作为受控可选导航。可验证的局部审查复用已有 scoped-plan-review、原始要求与当前任务范围，不新增 Task 状态。
- 局部审查先核对 source_index、coverage_ledger、source_inventory 和当前 Task Map 的完整声明、来源/任务/验收归属；需要保留原始需求、固定矩阵、受影响任务及其依赖闭包的旧新正文。未知映射、相关待决和不可证明的视觉材料回退全文。无关任务的已知待决不因此阻塞。
- 独立的 100 Task 样例验证仅改变 T12 时，T12 与下游 T13、shared 保留，其他任务分区不注入；反例包括未映射来源、错误归属、缺失验收、截断、相关待决和缺失视觉证据。回执测试刻意缺失 T13 本轮正文时拒绝准入。
- 已对暂停的 LS 原始 manifest 只读预检：无局部范围证书，身份/设计涉及的声明覆盖尚不完整，策略为 `full` / `impact_scope_unavailable`，31 项原必读保留。历史 32→16 的仅差异实验不是安全策略效果；未启动 LS，真实 Provider 和视觉语义质量仍未验证。
- 规划技能三份分发逐字同步；当前 attempt 必读与旧历史观察隔离，变更不能原地覆盖已固定政策。

## 验证

- 影响闭包规划：`python scripts/dev-verify.py plan --base dev` -> 337 个相关测试文件，自动 deterministic / serial / sentinel。
- `.../python scripts/dev-verify.py run --base dev --workers 4 --keep-going` -> 14,321 passed / 20 skipped / 7 failed；7 项为已在纯 dev 复现的相同基线失败（缺 export 脚本四项、缺 prompt 一项、Feishu 与 Channel mock 各一项）。serial 10 passed，sentinel 53 passed。
- 规划及来源、索引、任务、恢复、writer 和技能调用闭包 -> 377 passed、1 skipped；新增范围和声明反例专项 -> 85 passed；flow mock E2E 10 passed。
- `read_skill_metadata` 无 warning，canonical、Codex 和 Claude 副本字节一致；最终 `git diff --check` 无空白错误。
- 尚无真实 Provider 的完整/局部盲评及视觉消费资格；不把输入覆盖或旧回执解释为模型理解。LS 现场证据不满足 scope，按 full 安全回退。
