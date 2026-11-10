> 状态: done
> 实现提交：`38327936 feat: improve PRD design guidance and readable handoffs`

# 205-02 参考调研、设计与多 Agent 交接落地

## 来源与授权

205 §7.11–7.19；在现有 PRD/Plan Skill 引用中实现设计输入、按需研究、图示、公共规范、页面交接、适配、就绪及验收方法。

用户已授权基于 dev 创建 worktree、生成 backlog 并实施。源 205 SHA256：`d2ab4fb1e944a850ae19c3a13ac5adff5cd715bf9e9b2e55b77fcd47ad0dd7d5`。
基线：`ee49dfb0`；不包含 merge/push、真实 Provider 实验或外部用户研究。

## 验收

- 作者/Planner/Critic 读取可复用方法 -> verify: Skill 入口精确引用新增文件，分发副本一致，角色技能物化测试通过。
- 设计关联现有合同 -> verify: 方法要求同版 Requirement/AC/决定/Task 引用，区别候选、采纳和实现；不新增固定阶段或状态机。
- 设计验收可执行 -> verify: 方法明确基准审阅、环境、动态区域理由及偏差修订，合成页面交接样例标记 NOT_RUN。

## 结果

实现与最终验证闭包已完成，并已本地提交。具体变更、测试范围和未完成部分见 `docs/impl/205-prd-methods-and-design-handoff.md`。

方法效果、真实读者与具体产品验收未执行，不计为本任务测试通过。

验证：`uv run python scripts/dev-verify.py run --base dev`（具体显式测试路径见实施记录）→ 375 passed、1 skipped（外部技能目录缺失）；Web typecheck + 79 个 unit 文件通过；生产构建通过。
